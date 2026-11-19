import asyncio
import glob
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import imageio_ffmpeg
import yt_dlp
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="ClipMaster AI API", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT = Path(os.getenv("CLIP_STORAGE_DIR", "/tmp/clipmaster"))
JOBS_DIR = ROOT / "jobs"
MAX_UPLOAD_BYTES = 150 * 1024 * 1024
MAX_SOURCE_BYTES = 200 * 1024 * 1024
CLIP_SECONDS = 30
MAX_CLIPS = 5
RETENTION_SECONDS = 5 * 60 * 60
JOBS = {}
JOBS_DIR.mkdir(parents=True, exist_ok=True)


def cleanup_expired():
    cutoff = time.time() - RETENTION_SECONDS
    for folder in JOBS_DIR.iterdir():
        try:
            if folder.is_dir() and folder.stat().st_mtime < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                JOBS.pop(folder.name, None)
        except OSError:
            pass


def valid_youtube_url(value):
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower()
        return parsed.scheme in ("http", "https") and host in {
            "youtube.com", "www.youtube.com", "m.youtube.com",
            "music.youtube.com", "youtu.be", "www.youtu.be"
        }
    except ValueError:
        return False


def download_youtube(url, job_dir):
    options = {
        "outtmpl": str(job_dir / "source.%(ext)s"),
        "format": "best[height<=720][ext=mp4]/best[height<=720]/best",
        "noplaylist": True,
        "max_filesize": MAX_SOURCE_BYTES,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "socket_timeout": 30,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.download([url])

    candidates = [
        Path(name) for name in glob.glob(str(job_dir / "source.*"))
        if not name.endswith((".part", ".ytdl", ".json"))
    ]
    if not candidates:
        raise RuntimeError("The video could not be downloaded. Check that it is public and available, and that you have permission to use it.")
    return max(candidates, key=lambda path: path.stat().st_size)


def make_clips(source, output_dir):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    output_pattern = str(output_dir / "clip_%02d.mp4")
    command = [
        ffmpeg, "-y", "-i", str(source),
        "-t", str(CLIP_SECONDS * MAX_CLIPS),
        "-map", "0:v:0", "-map", "0:a?",
        "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-c:a", "aac", "-b:a", "96k",
        "-f", "segment", "-segment_time", str(CLIP_SECONDS),
        "-reset_timestamps", "1", "-segment_format", "mp4",
        output_pattern,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    clips = sorted(output_dir.glob("clip_*.mp4"))
    clips = [path for path in clips if path.exists() and path.stat().st_size > 10_000]
    if result.returncode != 0 and not clips:
        detail = (result.stderr or "FFmpeg could not process this video.")[-1200:]
        raise RuntimeError(detail)
    if not clips:
        raise RuntimeError("No clips could be created from this video.")
    return clips[:MAX_CLIPS]


def process_job(job_id, source_path=None, youtube_url=None):
    job_dir = JOBS_DIR / job_id
    output_dir = job_dir / "clips"
    output_dir.mkdir(parents=True, exist_ok=True)
    JOBS[job_id]["status"] = "processing"
    try:
        source = Path(source_path) if source_path else download_youtube(youtube_url, job_dir)
        if not source.exists():
            raise RuntimeError("The uploaded source video is missing.")
        if source.stat().st_size > MAX_SOURCE_BYTES:
            raise RuntimeError("The video is too large. The current limit is 200 MB.")
        JOBS[job_id]["message"] = "Creating vertical clips. This can take a few minutes."
        clips = make_clips(source, output_dir)
        JOBS[job_id]["clips"] = [
            {
                "name": clip.name,
                "url": f"/jobs/{job_id}/clips/{clip.name}",
                "size_bytes": clip.stat().st_size,
            }
            for clip in clips
        ]
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["message"] = "Your vertical clips are ready to download."
    except Exception as exc:
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["message"] = str(exc)[:1200]
    finally:
        # Keep generated clips for up to five hours on temporary server storage.
        if source_path:
            try:
                Path(source_path).unlink(missing_ok=True)
            except OSError:
                pass


@app.get("/")
def home():
    return {"status": "online", "project": "ClipMaster AI", "version": "1.1"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    permission: bool = Form(False),
    youtube_url: str = Form(""),
    video: UploadFile | None = File(None),
):
    cleanup_expired()
    if not permission:
        raise HTTPException(status_code=400, detail="Please confirm you have the right to edit this video.")
    if bool(youtube_url.strip()) == bool(video):
        raise HTTPException(status_code=400, detail="Provide either a YouTube URL or one video upload.")
    
    job_id = uuid.uuid4().hex
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "message": "Your video was received and is waiting to be processed.",
        "clips": [],
        "created_at": int(time.time()),
    }

    if video:
        if not (video.content_type or "").startswith("video/"):
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Please upload a supported video file.")
        extension = Path(video.filename or "video.mp4").suffix.lower()
        if extension not in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
            extension = ".mp4"
        source_path = job_dir / f"upload{extension}"
        total = 0
        try:
            with source_path.open("wb") as destination:
                while True:
                    chunk = await video.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="Upload limit is 150 MB.")
                    destination.write(chunk)
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        finally:
            await video.close()
        background_tasks.add_task(process_job, job_id, str(source_path), None)
    else:
        if not valid_youtube_url(youtube_url):
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Only standard YouTube video links are supported.")
        background_tasks.add_task(process_job, job_id, None, youtube_url.strip())

    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found. Temporary jobs may expire after five hours or a server restart.")
    return job


@app.get("/jobs/{job_id}/clips/{filename}")
def download_clip(job_id: str, filename: str):
    if not filename.startswith("clip_") or not filename.endswith(".mp4") or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid clip filename.")
    clip_path = JOBS_DIR / job_id / "clips" / filename
    if not clip_path.is_file():
        raise HTTPException(status_code=404, detail="Clip not found or its temporary storage has expired.")
    return FileResponse(clip_path, media_type="video/mp4", filename=filename)


@app.on_event("startup")
def on_startup():
    cleanup_expired()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
