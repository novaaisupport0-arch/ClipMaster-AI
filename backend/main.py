import asyncio
import glob
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict, deque

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("clipmaster.api")

import imageio_ffmpeg
import yt_dlp
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="ClipMaster AI API", version="1.2")

ALLOWED_ORIGINS = [
    item.strip()
    for item in os.getenv(
        "CORS_ORIGINS",
        "https://clip-master-ai-oh8r.vercel.app,https://clip-master-ai-oh8r-blue-ray.vercel.app"
    ).split(",")
    if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

ROOT = Path(os.getenv("CLIP_STORAGE_DIR", "/tmp/clipmaster"))
JOBS_DIR = ROOT / "jobs"
MAX_UPLOAD_BYTES = 150 * 1024 * 1024
MAX_SOURCE_BYTES = 200 * 1024 * 1024
CLIP_SECONDS = 30
MAX_CLIPS = 5
RETENTION_SECONDS = 5 * 60 * 60
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "10"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "600"))
REQUESTS_BY_IP = defaultdict(deque)
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


def enforce_rate_limit(client_ip):
    now = time.time()
    requests = REQUESTS_BY_IP[client_ip]
    while requests and now - requests[0] > RATE_LIMIT_WINDOW_SECONDS:
        requests.popleft()
    if len(requests) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(status_code=429, detail="Too many processing requests. Please wait and try again.")
    requests.append(now)


def valid_video_signature(header, extension):
    # Validate container signatures rather than trusting the browser-supplied MIME type.
    if extension in {".mp4", ".m4v", ".mov"}:
        return len(header) >= 12 and header[4:8] == b"ftyp"
    if extension == ".webm":
        return header.startswith(bytes.fromhex("1A45DFA3"))
    if extension == ".mkv":
        return header.startswith(bytes.fromhex("1A45DFA3"))
    return False


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


def get_media_duration(source, ffmpeg):
    result = subprocess.run(
        [ffmpeg, "-i", str(source)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    import re
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr or "")
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def make_clips(source, output_dir, job):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    output_pattern = str(output_dir / "clip_%02d.mp4")
    duration = get_media_duration(source, ffmpeg)
    target_duration = min(duration, CLIP_SECONDS * MAX_CLIPS) if duration else None
    started = time.time()
    job["progress"] = 3
    job["eta_seconds"] = None
    job["message"] = "Preparing video for processing…"

    command = [
        ffmpeg, "-y", "-i", str(source),
        "-t", str(CLIP_SECONDS * MAX_CLIPS),
        "-map", "0:v:0", "-map", "0:a?",
        "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-c:a", "aac", "-b:a", "96k",
        "-f", "segment", "-segment_time", str(CLIP_SECONDS),
        "-reset_timestamps", "1", "-segment_format", "mp4",
        "-progress", "pipe:1", "-nostats",
        output_pattern,
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    last_out_time = 0.0
    for line in process.stdout:
        key, separator, value = line.strip().partition("=")
        if not separator:
            continue
        if key in ("out_time_us", "out_time_ms"):
            try:
                last_out_time = max(last_out_time, int(value) / 1_000_000)
            except ValueError:
                pass
            if target_duration and target_duration > 0:
                fraction = min(0.99, last_out_time / target_duration)
                progress = min(95, max(job.get("progress", 3), 3 + int(fraction * 92)))
                job["progress"] = progress
                elapsed = time.time() - started
                job["eta_seconds"] = max(0, int(elapsed * (1 - fraction) / fraction)) if fraction > 0.01 else None
                job["message"] = "Creating vertical clips…"
        elif key == "progress" and value == "end":
            job["progress"] = max(job.get("progress", 3), 95)
            job["eta_seconds"] = 0

    return_code = process.wait(timeout=900)
    clips = sorted(output_dir.glob("clip_*.mp4"))
    clips = [path for path in clips if path.exists() and path.stat().st_size > 10_000]
    if return_code != 0 and not clips:
        raise RuntimeError("FFmpeg could not process this video. Please try another video.")
    if not clips:
        raise RuntimeError("No clips could be created from this video.")
    return clips[:MAX_CLIPS]


def process_job(job_id, source_path=None, youtube_url=None):
    job_dir = JOBS_DIR / job_id
    output_dir = job_dir / "clips"
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        if job_id not in JOBS:
            raise RuntimeError("This job is no longer available. Please submit the video again.")
        JOBS[job_id]["status"] = "processing"
        logger.info("Job %s started; source_type=%s", job_id, "upload" if source_path else "youtube")
        source = Path(source_path) if source_path else download_youtube(youtube_url, job_dir)
        if not source.exists():
            raise RuntimeError("The uploaded source video is missing.")
        if source.stat().st_size > MAX_SOURCE_BYTES:
            raise RuntimeError("The video is too large. The current limit is 200 MB.")
        JOBS[job_id]["progress"] = 2
        JOBS[job_id]["message"] = "Video received. Preparing processing…"
        clips = make_clips(source, output_dir, JOBS[job_id])
        JOBS[job_id]["clips"] = [
            {
                "name": clip.name,
                "url": f"/jobs/{job_id}/clips/{clip.name}",
                "size_bytes": clip.stat().st_size,
            }
            for clip in clips
        ]
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["progress"] = 100
        JOBS[job_id]["eta_seconds"] = 0
        logger.info("Job %s completed; clips=%s", job_id, len(clips))
        JOBS[job_id]["message"] = "Your vertical clips are ready to download."
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        if job_id in JOBS:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["eta_seconds"] = None
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
    return {"status": "healthy", "service": "ClipMaster AI API", "version": app.version}


@app.get("/diagnostics")
def diagnostics():
    """Non-secret runtime checks to verify the deployed processing dependencies."""
    try:
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        ffmpeg_ok = Path(ffmpeg_path).is_file()
    except Exception as exc:
        logger.exception("FFmpeg diagnostic failed")
        ffmpeg_ok = False
    try:
        import yt_dlp.version
        ytdlp_version = yt_dlp.version.__version__
    except Exception:
        ytdlp_version = "unknown"
    try:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        probe = JOBS_DIR / f".write-test-{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        storage_writable = True
    except Exception:
        logger.exception("Storage diagnostic failed")
        storage_writable = False
    return {
        "status": "healthy" if ffmpeg_ok and storage_writable else "degraded",
        "version": app.version,
        "ffmpeg_available": ffmpeg_ok,
        "yt_dlp_version": ytdlp_version,
        "storage_writable": storage_writable,
        "active_jobs_in_memory": len(JOBS),
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
    }


@app.post("/jobs")
async def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
    permission: bool = Form(False),
    youtube_url: str = Form(""),
    video: UploadFile | None = File(None),
):
    cleanup_expired()
    enforce_rate_limit(request.client.host if request.client else "unknown")
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
        "progress": 0,
        "eta_seconds": None,
        "created_at": int(time.time()),
    }

    if video:
        logger.info("Upload request received; filename=%s content_type=%s", Path(video.filename or "video").name, video.content_type or "unknown")
        if not (video.content_type or "").startswith("video/"):
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Please upload a supported video file.")
        extension = Path(video.filename or "video.mp4").suffix.lower()
        if extension not in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(status_code=415, detail="Upload an MP4, MOV, WebM or MKV video.")
        source_path = job_dir / f"upload{extension}"
        total = 0
        try:
            with source_path.open("wb") as destination:
                while True:
                    chunk = await video.read(1024 * 1024)
                    if not chunk:
                        break
                    if total == 0 and not valid_video_signature(chunk[:32], extension):
                        raise HTTPException(status_code=415, detail="The uploaded file does not match a supported video format.")
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="Upload limit is 150 MB.")
                    destination.write(chunk)
            logger.info("Upload saved; job_id=%s size_bytes=%s", job_id, total)
        except Exception:
            logger.exception("Upload failed while receiving file; job_id=%s", job_id)
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        finally:
            await video.close()
        background_tasks.add_task(process_job, job_id, str(source_path), None)
        logger.info("Upload job queued; job_id=%s", job_id)
    else:
        if not valid_youtube_url(youtube_url):
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Only standard YouTube video links are supported.")
        background_tasks.add_task(process_job, job_id, None, youtube_url.strip())
        logger.info("YouTube job queued; job_id=%s", job_id)

    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID.")
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found. Temporary jobs may expire after five hours or a server restart.")
    return job


@app.get("/jobs/{job_id}/clips/{filename}")
def download_clip(job_id: str, filename: str):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID.")
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
