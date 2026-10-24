# YouTube to MP3

A small, ad-free YouTube-to-MP3 app that runs on your computer. Paste a video link,
choose 128, 192, or 320 kbps, and download the MP3 when it is ready.

## Run on Windows

Install Python 3.12+ and Node.js 22+ with their commands on PATH. Then, from this
directory:

```powershell
.\run.ps1
```

Open **http://127.0.0.1:8000**. The launcher creates a virtual environment and
installs dependencies on first run. FFmpeg is supplied by `imageio-ffmpeg`;
yt-dlp's JavaScript challenge scripts are included in its `default` extra.

If PowerShell blocks scripts, run the equivalent commands directly:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-lock.txt
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Stop with Ctrl+C. Keep the computer awake while conversions run. To restart an
already installed app, only the final command is needed.

On Linux/macOS, use `python3 -m venv .venv`, then `.venv/bin/python` in the commands
above. The bundled FFmpeg wheel must support the host platform.

## Behavior

- One active conversion, with up to ten active/queued jobs total.
- Individual public YouTube videos, including Shorts and short links.
- Downloads audio-only where available and encodes MP3 on disk.
- Browser refresh or closing the tab does not stop a conversion.
- Cancel stops the downloader and its FFmpeg child process.
- Completed downloads support HTTP byte ranges for clients that resume transfers.
- Job state survives server restarts; interrupted/queued jobs become failed and can
  be retried. They are not automatically resumed after a server restart.
- Failed/cancelled job files are removed; completed files expire after 24 hours.
  Cleanup runs between jobs. Expired job entries remain visible.
- No advertisements, analytics, third-party frontend scripts, or external fonts.

Quality cannot exceed the source. 320 kbps creates larger files without restoring
detail lost in YouTube's audio encoding. Downloads are limited to content you own
or have permission to save.

## Configuration

Set environment variables before starting the server:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATA_DIR` | `data/` in this repository | SQLite database and temporary audio |
| `MAX_SOURCE_GB` | `8` | Maximum downloaded source size in GiB |
| `MAX_DURATION_HOURS` | `24` | Maximum video duration |
| `RETENTION_HOURS` | `24` | Completed file retention |

Use positive integers for limits. The UI currently displays the default 24-hour
retention. Jobs check available disk space and reserve 512 MiB headroom. These are
per-job limits, not a reserved disk quota; leave space for source and output files.

This build binds to loopback and validates localhost hosts and browser origins.
It has no user authentication and is intended for local use. Remote hosting needs
authentication, HTTPS, and appropriate storage and request limits first.

## Development

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
node --check static/app.js
```

Tests cover URL validation, queue limits, persistence/restart recovery,
cancellation of subprocess trees, origin/host checks, range downloads, and actual
FFmpeg encoding of a two-hour audio fixture. Live YouTube extraction is checked
manually because availability and network challenges can change.

The FastAPI app serves the frontend and API together. SQLite stores job metadata;
a single background thread starts one disposable worker process per job. That
worker uses yt-dlp for extraction and the native FFmpeg binary for encoding.
Runtime files live under ignored `data/`; source and output audio are never pushed.

## Updating YouTube support

YouTube changes can temporarily break extraction. Update yt-dlp together with its
challenge scripts inside the environment, then restart the app:

```powershell
.venv\Scripts\python -m pip install --upgrade "yt-dlp[default]"
```

Some videos require sign-in, are restricted, or become unavailable. This app does
not import browser cookies. Failed jobs show the extraction error and offer retry.
The checked-in lock file records the initially tested dependencies; updating a
dependency should include rerunning tests and refreshing that file.
