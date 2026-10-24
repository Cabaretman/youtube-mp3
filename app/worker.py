"""Download and encode one job in a cancellable subprocess."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import imageio_ffmpeg
import yt_dlp


def emit(**fields):
    print(json.dumps(fields), flush=True)


def encode(source, target, bitrate, duration, title, progress=emit):
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-y", "-i", str(source),
        "-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", f"{bitrate}k",
        "-metadata", f"title={title}", "-progress", "pipe:1", "-nostats",
        "-loglevel", "error", str(target),
    ]
    with target.with_suffix(".log").open("w+", encoding="utf-8") as log:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, text=True) as process:
            for line in process.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and duration and value.isdigit():
                    progress(state="converting", progress=min(99, int(value) / duration / 10000))
            if process.wait():
                log.seek(0)
                raise RuntimeError("Audio conversion failed: " + log.read()[-1500:])
    target.with_suffix(".log").unlink(missing_ok=True)


def run(url, directory, bitrate):
    directory = Path(directory)
    max_bytes = int(os.environ.get("MAX_SOURCE_GB", "8")) * 1024**3
    last_update = 0

    def check_space():
        if shutil.disk_usage(directory).free < 512 * 1024**2:
            raise RuntimeError("Disk space is low. Free some space and retry.")

    def download_progress(event):
        nonlocal last_update
        if event["status"] != "downloading":
            return
        now = time.monotonic()
        if now - last_update < 0.5:
            return
        last_update = now
        check_space()
        downloaded = event.get("downloaded_bytes", 0)
        if downloaded > max_bytes:
            raise RuntimeError("Audio exceeds the configured source size limit.")
        total = event.get("total_bytes") or event.get("total_bytes_estimate")
        emit(state="downloading", progress=min(99, downloaded * 100 / total) if total else None)

    def filter_video(info, *, incomplete=False):
        if info.get("is_live") or info.get("live_status") == "is_upcoming":
            return "Live and upcoming videos are not supported."
        duration = info.get("duration")
        if duration and duration > int(os.environ.get("MAX_DURATION_HOURS", "24")) * 3600:
            return "Video exceeds the configured duration limit."

    check_space()
    options = {
        "format": "bestaudio/best", "outtmpl": str(directory / "source.%(ext)s"),
        "noplaylist": True, "quiet": True, "no_warnings": True,
        "progress_hooks": [download_progress], "match_filter": filter_video,
        "max_filesize": max_bytes, "socket_timeout": 30, "retries": 3,
        "fragment_retries": 3, "js_runtimes": {"node": {}},
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
        if not info or info.get("_type") in {"playlist", "multi_video"}:
            raise RuntimeError("Paste a link to one public YouTube video.")
        reason = filter_video(info)
        if reason:
            raise RuntimeError(reason)
        duration = info.get("duration")
        if duration and shutil.disk_usage(directory).free < duration * bitrate * 125 + 512 * 1024**2:
            raise RuntimeError("Not enough disk space for the MP3 output.")
        title = info.get("title") or "YouTube audio"
        emit(state="downloading", title=title, duration=duration, progress=None)
        downloader.process_info(info)
        source = Path(downloader.prepare_filename(info))
        if not source.exists():
            raise RuntimeError("No audio file was downloaded. Check the size limit and retry.")
    emit(state="converting", progress=0)
    target = directory / "audio.mp3"
    encode(source, target, bitrate, duration, title)
    source.unlink(missing_ok=True)
    emit(state="ready", progress=100, size=target.stat().st_size)


if __name__ == "__main__":
    try:
        run(sys.argv[1], sys.argv[2], int(sys.argv[3]))
    except Exception as error:
        emit(state="failed", error=str(error)[-2000:])
        sys.exit(1)
