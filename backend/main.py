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

DEFAULT_ALLOWED_ORIGINS = [
    "https://clip-master-ai-oh8r.vercel.app",
    "https://clip-master-ai-oh8r-blue-ray.vercel.app",
]
CONFIGURED_ORIGINS = os.getenv("CORS_ORIGINS", "").split(",")
ALLOWED_ORIGINS = sorted({
    origin.strip().rstrip("/")
    for origin in [*DEFAULT_ALLOWED_ORIGINS, *CONFIGURED_ORIGINS]
    if origin.strip()
})
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    # Permit this Vercel project's production, branch, and preview deployment URLs.
    allow_origin_regex=r"^https://clip-master-ai-oh8r(?:-[a-z0-9-]+)?\.vercel\.app$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT = Path(os.getenv("CLIP_STORAGE_DIR", "/tmp/clipmaster"))
JOBS_DIR = ROOT / "jobs"
MAX_UPLOAD_BYTES = 150 * 1000 * 1000
MAX_SOURCE_BYTES = MAX_UPLOAD_BYTES
CLIP_SECONDS = 30
MAX_CLIPS = 3
RETENTION_SECONDS = 5 * 60 * 60
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "10"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "600"))
FREE_DAILY_REQUESTS = int(os.getenv("FREE_DAILY_REQUESTS", "3"))
FREE_DAILY_WINDOW_SECONDS = 24 * 60 * 60
REQUESTS_BY_IP = defaultdict(deque)
FREE_REQUESTS_BY_IP = defaultdict(deque)
MAX_VIDEO_DURATION_SECONDS = 15 * 60
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
        raise HTTPException(status_code=429, detail="Too many requests in a short period. Please wait and try again.")
    requests.append(now)

    # Anonymous Free-tier quota until accounts and persistent subscription storage exist.
    # Users sharing a public IP also share this provisional quota.
    daily_requests = FREE_REQUESTS_BY_IP[client_ip]
    while daily_requests and now - daily_requests[0] > FREE_DAILY_WINDOW_SECONDS:
        daily_requests.popleft()
    if len(daily_requests) >= FREE_DAILY_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="Free beta limit reached: 3 processing requests per rolling 24 hours per network. Please try again later."
        )
    daily_requests.append(now)


SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv",
    ".flv", ".mpeg", ".mpg", ".mpe", ".3gp", ".3g2", ".ts",
    ".mts", ".m2ts", ".ogv", ".ogg", ".vob", ".asf", ".mxf",
}


def probe_video_file(source):
    """Ask FFmpeg to inspect the container and confirm that it has a video stream."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(source)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    details = result.stderr or ""
    if not re.search(r"Stream #\d+:\d+.*Video:", details, re.IGNORECASE):
        raise HTTPException(
            status_code=415,
            detail="This file is not a readable video, or its video codec is unsupported. Try MP4, MOV, MKV, AVI or WebM.",
        )


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



def detect_audio_peaks(source, ffmpeg):
    """Return one-second audio RMS samples, used as a lightweight energy signal."""
    command = [
        ffmpeg, "-hide_banner", "-i", str(source), "-vn",
        "-af", "aresample=8000,asetnsamples=n=8000:p=1,astats=metadata=1:reset=1,ametadata=print:file=-",
        "-f", "null", "-"
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    samples = []
    current_time = None
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        time_match = re.search(r"pts_time:([0-9]+(?:\.[0-9]+)?)", line)
        if time_match:
            current_time = float(time_match.group(1))
        rms_match = re.search(r"lavfi\.astats\.Overall\.RMS_level=(-?[0-9]+(?:\.[0-9]+)?)", line)
        if rms_match and current_time is not None:
            samples.append((current_time, float(rms_match.group(1))))
    return samples

def make_clips(source, output_dir, job):
    """Detect visually active moments, then render up to five vertical highlight clips."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    duration = get_media_duration(source, ffmpeg) or float(CLIP_SECONDS)
    duration = max(float(duration), 0.1)
    clip_length = min(float(CLIP_SECONDS), duration)
    job["progress"] = 3
    job["eta_seconds"] = None
    job["message"] = "Analyzing scene changes and audio peaks to find highlights…"
    started = time.time()

    scene_events = []
    audio_samples = []
    try:
        scan = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-i", str(source),
                "-vf", "select='gt(scene,0.18)',metadata=print:file=-",
                "-an", "-f", "null", "-"
            ],
            capture_output=True, text=True, timeout=1800
        )
        current_time = None
        for line in (scan.stdout + "\n" + scan.stderr).splitlines():
            time_match = re.search(r"pts_time:([0-9]+(?:\.[0-9]+)?)", line)
            if time_match:
                current_time = float(time_match.group(1))
            if "lavfi.scene_score=" in line and current_time is not None:
                score_match = re.search(r"lavfi\.scene_score=([0-9]+(?:\.[0-9]+)?)", line)
                if score_match:
                    scene_events.append((current_time, float(score_match.group(1))))
    except Exception:
        logger.exception("Scene-change analysis failed; continuing with audio analysis")
    try:
        audio_samples = detect_audio_peaks(source, ffmpeg)
    except Exception:
        logger.exception("Audio peak analysis failed; continuing with scene analysis")

    # Rank 30-second windows by scene changes and audio energy.
    if duration <= clip_length:
        starts = [0.0]
    else:
        candidates = []
        latest = max(0.0, duration - clip_length)
        candidate = 0.0
        while candidate <= latest + 0.001:
            events = [score for timestamp, score in scene_events if candidate <= timestamp < candidate + clip_length]
            density = min(1.0, len(events) / max(3.0, clip_length / 5.0))
            strongest = max(events, default=0.0)
            levels = [level for timestamp, level in audio_samples if candidate <= timestamp < candidate + clip_length and level > -65]
            audio_score = 0.0
            if levels:
                average_db = sum(levels) / len(levels)
                audio_score = max(0.0, min(1.0, (average_db + 55.0) / 35.0))
            candidates.append((density * 0.55 + audio_score * 0.35 + strongest * 0.10, candidate))
            candidate += 5.0
        candidates.sort(reverse=True)
        starts = []
        for score, candidate in candidates:
            if all(abs(candidate - chosen) >= max(15.0, clip_length * 0.55) for chosen in starts):
                starts.append(candidate)
            if len(starts) >= MAX_CLIPS:
                break
        starts = sorted(starts or [0.0])

    clips = []
    for index, clip_start in enumerate(starts):
        clip_duration = min(clip_length, duration - clip_start)
        output_path = output_dir / f"clip_{index:02d}.mp4"
        result = subprocess.run(
            [
                ffmpeg, "-y", "-ss", f"{clip_start:.3f}", "-i", str(source),
                "-t", f"{clip_duration:.3f}", "-map", "0:v:0", "-map", "0:a?",
                "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
                str(output_path)
            ],
            capture_output=True, text=True, timeout=900
        )
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 10_000:
            clips.append(output_path)
        job["progress"] = min(95, 5 + int(((index + 1) / len(starts)) * 90))
        job["eta_seconds"] = max(0, int((time.time() - started) / (index + 1) * (len(starts) - index - 1)))
        job["message"] = f"Creating highlight clip {index + 1} of {len(starts)}…"

    if not clips:
        raise RuntimeError("No highlight clips could be created from this video. Please try another video.")
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
            raise RuntimeError("The video is too large. The Free beta upload limit is 150 MB.")
        source_duration = get_media_duration(source, imageio_ffmpeg.get_ffmpeg_exe())
        if source_duration is not None and source_duration > MAX_VIDEO_DURATION_SECONDS:
            raise RuntimeError("The Free beta video duration limit is 15 minutes.")
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
        "max_upload_mb": MAX_UPLOAD_BYTES // 1_000_000,
        "max_source_mb": MAX_SOURCE_BYTES // (1024 * 1024),
        "free_daily_requests_per_ip": FREE_DAILY_REQUESTS,
        "free_max_video_minutes": MAX_VIDEO_DURATION_SECONDS // 60,
        "free_max_clips_per_video": MAX_CLIPS,
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
        extension = Path(video.filename or "").suffix.lower()
        if extension not in SUPPORTED_VIDEO_EXTENSIONS:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(
                status_code=415,
                detail="Unsupported file extension. Try MP4, MOV, MKV, WebM, AVI, WMV, FLV, M4V, MPEG, 3GP, TS or OGV.",
            )
        source_path = job_dir / f"upload{extension}"
        # Check the spooled upload size before copying or queueing any processing.
        try:
            video.file.seek(0, os.SEEK_END)
            declared_file_size = video.file.tell()
            video.file.seek(0)
        except (OSError, AttributeError):
            declared_file_size = 0
        if declared_file_size > MAX_UPLOAD_BYTES:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(status_code=413, detail="Upload limit is 150 MiB. Please choose a smaller video.")
        total = 0
        try:
            with source_path.open("wb") as destination:
                while True:
                    chunk = await video.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="Upload limit is 150 MiB. Please choose a smaller video.")
                    destination.write(chunk)
            if total == 0:
                raise HTTPException(status_code=400, detail="The selected video file is empty.")
            probe_video_file(source_path)
            logger.info("Upload saved and verified; job_id=%s size_bytes=%s extension=%s", job_id, total, extension)
        except Exception:
            logger.exception("Upload failed validation or receiving; job_id=%s", job_id)
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
