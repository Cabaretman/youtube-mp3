import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


ACTIVE = {"queued", "fetching", "downloading", "converting"}
TERMINAL = {"ready", "failed", "cancelled"}
ROOT = Path(__file__).resolve().parent.parent


def normalize_url(value):
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.port:
            raise ValueError
        host = (parsed.hostname or "").lower()
        parts = parsed.path.strip("/").split("/")
        if host == "youtu.be" and len(parts) == 1:
            video_id = parts[0]
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if parsed.path == "/watch":
                video_id = parse_qs(parsed.query).get("v", [""])[0]
            elif len(parts) == 2 and parts[0] in {"shorts", "embed", "live"}:
                video_id = parts[1]
            else:
                raise ValueError
        else:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError
        return f"https://www.youtube.com/watch?v={video_id}"
    except ValueError:
        raise ValueError("Enter a link to one YouTube video.") from None


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


class Jobs:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.process = None
        self.current = None
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        for job in self.list():
            if job["state"] in ACTIVE:
                self.update(job["id"], state="failed", error="App restarted. Retry this conversion.")
        self.thread = threading.Thread(target=self.work, daemon=True)

    def connect(self):
        return sqlite3.connect(self.directory / "jobs.sqlite3")

    def list(self):
        with self.lock, self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM jobs ORDER BY rowid DESC")]

    def get(self, job_id):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def update(self, job_id, **fields):
        with self.lock, self.connect() as db:
            job = self.get(job_id)
            job.update(fields, updated=time.time())
            db.execute("UPDATE jobs SET payload = ? WHERE id = ?", (json.dumps(job), job_id))
            return job

    def create(self, url, bitrate):
        with self.lock, self.connect() as db:
            if sum(job["state"] in ACTIVE for job in self.list()) >= 10:
                raise ValueError("Queue is full. Wait for a conversion to finish.")
            job = dict(id=uuid4().hex, url=url, bitrate=bitrate, state="queued", title=None,
                       duration=None, progress=None, size=None, error=None,
                       created=time.time(), updated=time.time())
            db.execute("INSERT INTO jobs VALUES (?, ?)", (job["id"], json.dumps(job)))
            return job

    def cancel(self, job_id):
        with self.lock:
            job = self.get(job_id)
            if job and job["state"] in ACTIVE:
                self.update(job_id, state="cancelled", progress=None)
                if self.current == job_id and self.process:
                    stop_process(self.process)
            return self.get(job_id)

    def cleanup(self):
        cutoff = time.time() - int(os.environ.get("RETENTION_HOURS", "24")) * 3600
        for job in self.list():
            if job["state"] in TERMINAL and job["updated"] < cutoff:
                try:
                    shutil.rmtree(self.directory / job["id"], ignore_errors=False)
                except FileNotFoundError:
                    pass
                except OSError:
                    continue
                self.update(job["id"], state="expired", progress=None)

    def execute(self, job):
        job_id = job["id"]
        directory = self.directory / job_id
        with self.lock:
            if self.get(job_id)["state"] != "queued" or self.stopping.is_set():
                return
            directory.mkdir(exist_ok=True)
            self.update(job_id, state="fetching")
            self.current = job_id
            kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            log = (directory / "worker.log").open("w", encoding="utf-8")
            try:
                self.process = subprocess.Popen(
                    [sys.executable, "-u", "-m", "app.worker", job["url"], str(directory), str(job["bitrate"])],
                    cwd=ROOT, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8", **kwargs,
                )
            except Exception:
                log.close()
                raise
            process = self.process
        try:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                with self.lock:
                    if self.get(job_id)["state"] == "cancelled":
                        continue
                    allowed = {key: value for key, value in event.items()
                               if key in {"state", "title", "duration", "progress", "size", "error"}}
                    self.update(job_id, **allowed)
            process.wait()
            with self.lock:
                if self.get(job_id)["state"] in ACTIVE:
                    self.update(job_id, state="failed", error="Conversion stopped unexpectedly. Retry the job.")
        finally:
            log.close()
            with self.lock:
                self.process = None
                self.current = None
            if self.get(job_id)["state"] != "ready":
                shutil.rmtree(directory, ignore_errors=True)

    def work(self):
        while not self.stopping.is_set():
            self.cleanup()
            queued = [job for job in reversed(self.list()) if job["state"] == "queued"]
            if queued:
                try:
                    self.execute(queued[0])
                except Exception as error:
                    self.update(queued[0]["id"], state="failed", error=str(error)[-1500:])
            else:
                self.stopping.wait(1)

    def close(self):
        self.stopping.set()
        with self.lock:
            if self.process:
                stop_process(self.process)
        self.thread.join(timeout=10)
