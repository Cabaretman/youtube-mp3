from contextlib import asynccontextmanager
import os
from pathlib import Path
import re
import shutil
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.jobs import Jobs, ROOT, normalize_url


class Conversion(BaseModel):
    url: str = Field(max_length=2048)
    bitrate: Literal[128, 192, 320] = 192


def create_app(directory=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.jobs = Jobs(directory or os.environ.get("DATA_DIR", ROOT / "data"))
        app.state.jobs.thread.start()
        yield
        app.state.jobs.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Open the app on localhost to create or cancel jobs."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "node": bool(shutil.which("node")),
                "free_bytes": shutil.disk_usage(app.state.jobs.directory).free}

    @app.get("/api/jobs")
    def list_jobs():
        return app.state.jobs.list()

    @app.post("/api/jobs", status_code=202)
    def create_job(payload: Conversion):
        try:
            return app.state.jobs.create(normalize_url(payload.url), payload.bitrate)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    def get_job(job_id):
        job = app.state.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Conversion not found.")
        return job

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        return get_job(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        get_job(job_id)
        return app.state.jobs.cancel(job_id)

    @app.get("/api/jobs/{job_id}/download")
    def download(job_id: str):
        job = get_job(job_id)
        if job["state"] != "ready":
            raise HTTPException(409, "MP3 is not ready or has expired.")
        path = app.state.jobs.directory / job["id"] / "audio.mp3"
        if not path.is_file():
            raise HTTPException(410, "File is no longer available. Convert again.")
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", job["title"] or "audio").strip(" .")[:120] or "audio"
        return FileResponse(path, media_type="audio/mpeg", filename=f"{title}.mp3")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "static", check_dir=False), name="static")
    return app


app = create_app()
