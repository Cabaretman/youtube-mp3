import os
import struct
import subprocess
import sys
import time

from fastapi.testclient import TestClient
from mutagen.mp3 import MP3
import pytest

from app.jobs import Jobs, normalize_url, stop_process
from app.main import create_app
from app.worker import encode


@pytest.mark.parametrize("url", [
    "https://youtu.be/BaW_jenozKc?t=5",
    "https://www.youtube.com/watch?v=BaW_jenozKc&list=ignore",
    "https://m.youtube.com/shorts/BaW_jenozKc",
    "https://music.youtube.com/watch?v=BaW_jenozKc",
])
def test_normalizes_video_links(url):
    assert normalize_url(url) == "https://www.youtube.com/watch?v=BaW_jenozKc"


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "http://127.0.0.1/", "https://youtube.com.evil.test/watch?v=BaW_jenozKc",
    "https://youtube.com/playlist?list=abc", "https://youtube.com/watch?v=--help",
    "https://user@youtube.com/watch?v=BaW_jenozKc", "https://youtube.com:444/watch?v=BaW_jenozKc",
])
def test_rejects_non_video_urls(url):
    with pytest.raises(ValueError):
        normalize_url(url)


def test_jobs_survive_restart_and_recover_interrupted_work(tmp_path):
    jobs = Jobs(tmp_path)
    first = jobs.create("https://www.youtube.com/watch?v=BaW_jenozKc", 192)
    jobs.update(first["id"], state="downloading")
    recovered = Jobs(tmp_path).get(first["id"])
    assert recovered["state"] == "failed"
    assert "restarted" in recovered["error"]


def test_queue_capacity_cancel_and_cleanup(tmp_path, monkeypatch):
    jobs = Jobs(tmp_path)
    entries = [jobs.create("https://www.youtube.com/watch?v=BaW_jenozKc", 192) for _ in range(10)]
    with pytest.raises(ValueError, match="Queue is full"):
        jobs.create("https://www.youtube.com/watch?v=BaW_jenozKc", 192)
    job_id = entries[0]["id"]
    assert jobs.cancel(job_id)["state"] == "cancelled"
    folder = tmp_path / job_id
    folder.mkdir()
    (folder / "partial").write_bytes(b"partial audio")
    monkeypatch.setenv("RETENTION_HOURS", "0")
    jobs.cleanup()
    assert jobs.get(job_id)["state"] == "expired"
    assert not folder.exists()


def test_api_validation_origin_and_resumable_download(tmp_path, monkeypatch):
    monkeypatch.setattr(Jobs, "work", lambda self: self.stopping.wait())
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.post("/api/jobs", json={"url": "https://example.com"}).status_code == 400
        assert client.post("/api/jobs", json={"url": "https://youtu.be/BaW_jenozKc", "bitrate": 999}).status_code == 422
        assert client.post("/api/jobs", json={"url": "https://youtu.be/BaW_jenozKc"}, headers={"Origin": "https://example.com"}).status_code == 403
        assert client.get("/api/jobs", headers={"Host": "evil.test"}).status_code == 400
        response = client.post("/api/jobs", json={"url": "https://youtu.be/BaW_jenozKc"})
        assert response.status_code == 202
        job_id = response.json()["id"]
        assert client.get(f"/api/jobs/{job_id}/download").status_code == 409
        folder = tmp_path / job_id
        folder.mkdir()
        (folder / "audio.mp3").write_bytes(b"0123456789")
        app.state.jobs.update(job_id, state="ready", title='A title / with "quotes"', size=10)
        download = client.get(f"/api/jobs/{job_id}/download", headers={"Range": "bytes=2-5"})
        assert download.status_code == 206
        assert download.content == b"2345"
        assert download.headers["content-range"] == "bytes 2-5/10"
        assert "attachment" in download.headers["content-disposition"]
        assert client.get("/api/jobs/missing").status_code == 404
        assert client.post(f"/api/jobs/{job_id}/cancel").json()["state"] == "ready"


def test_real_ffmpeg_encodes_two_hour_audio(tmp_path):
    source, target = tmp_path / "source.wav", tmp_path / "audio.mp3"
    # A sparse silent WAV exercises a multi-hour timeline without a large fixture.
    duration = 2 * 3600
    size = duration * 8000 * 2
    with source.open("wb") as output:
        output.write(struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + size, b"WAVE",
                                 b"fmt ", 16, 1, 1, 8000, 16000, 2, 16, b"data", size))
        output.seek(size - 1, 1)
        output.write(b"\0")
    encode(source, target, 128, duration, "Long audio test", progress=lambda **event: None)
    audio = MP3(target)
    assert abs(audio.info.length - duration) < 2
    assert audio.tags["TIT2"].text == ["Long audio test"]


def test_cancel_stops_worker_and_child(tmp_path):
    marker = tmp_path / "child-finished"
    child = "import time; from pathlib import Path; time.sleep(3); Path(" + repr(str(marker)) + ").touch()"
    script = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); print('ready',flush=True); time.sleep(30)"
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE, text=True, **options)
    try:
        assert process.stdout.readline().strip() == "ready"
        stop_process(process)
        time.sleep(3.5)
        assert process.poll() is not None
        assert not marker.exists()
    finally:
        stop_process(process)
