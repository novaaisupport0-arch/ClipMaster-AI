import os
import re
import uuid
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from faster_whisper import WhisperModel

APP_NAME = "ClipMaster AI Worker"
DATA_DIR = Path(os.getenv("DATA_DIR", "/data")).resolve()
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))
MAX_VIDEO_SECONDS = int(os.getenv("MAX_VIDEO_SECONDS", "7200"))
MAX_CLIPS = min(max(int(os.getenv("MAX_CLIPS", "10")), 1), 10)
WORKER_TOKEN = os.getenv("WORKER_API_TOKEN", "")
MODEL_SIZE = os.getenv("WHISPER_MODEL", "small")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
LANGUAGE = os.getenv("WHISPER_LANGUAGE", "").strip() or None

app = FastAPI(title=APP_NAME, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.Lock()
model: WhisperModel | None = None
model_lock = threading.Lock()
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")


def require_token(authorization: str | None) -> None:
    if not WORKER_TOKEN:
        raise HTTPException(status_code=503, detail="Worker is not configured. Set WORKER_API_TOKEN.")
    expected = f"Bearer {WORKER_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_model() -> WhisperModel:
    global model
    with model_lock:
        if model is None:
            model = WhisperModel(MODEL_SIZE, device="cpu", compute_type=COMPUTE_TYPE)
        return model


def run_command(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Video processing timed out.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "External video command failed")[-2500:]
        raise RuntimeError(detail) from exc


def video_duration(path: Path) -> float:
    result = run_command([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], timeout=60)
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("Could not read video duration.") from exc


def score_window(text: str) -> float:
    words = re.findall(r"\b[\w']+\b", text.lower())
    if not words:
        return 0.0
    hooks = {
        "secret", "mistake", "best", "worst", "never", "always", "why",
        "how", "insane", "crazy", "important", "surprise", "finally",
        "because", "tip", "trick", "win", "won", "fail", "failed"
    }
    hook_count = sum(1 for word in words if word in hooks)
    punctuation = sum(text.count(mark) for mark in ("?", "!", "."))
    density = min(len(words) / 45.0, 1.0)
    return round(density * 5 + min(hook_count, 5) * 0.8 + min(punctuation, 5) * 0.25, 3)


def choose_windows(segments: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    candidates = []
    for segment in segments:
        start = max(0.0, float(segment["start"]) - 2.0)
        end = min(duration, start + 38.0)
        if end - start < 12.0:
            continue
        text = " ".join(
            item["text"] for item in segments
            if float(item["start"]) < end and float(item["end"]) > start
        ).strip()
        if len(text.split()) < 12:
            continue
        candidates.append({
            "start": start,
            "end": end,
            "text": text,
            "score": score_window(text),
        })
    candidates.sort(key=lambda item: item["score"], reverse=True)
    chosen = []
    for candidate in candidates:
        overlaps = any(
            candidate["start"] < item["end"] and candidate["end"] > item["start"]
            for item in chosen
        )
        if not overlaps:
            chosen.append(candidate)
        if len(chosen) >= MAX_CLIPS:
            break
    return sorted(chosen, key=lambda item: item["start"])


def srt_time(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def write_srt(path: Path, segments: list[dict[str, Any]], start: float, end: float) -> None:
    rows = []
    number = 1
    for segment in segments:
        seg_start = max(float(segment["start"]), start)
        seg_end = min(float(segment["end"]), end)
        text = str(segment["text"]).strip()
        if seg_end <= seg_start or not text:
            continue
        rows.extend([
            str(number),
            f"{srt_time(seg_start - start)} --> {srt_time(seg_end - start)}",
            text,
            "",
        ])
        number += 1
    path.write_text("\n".join(rows), encoding="utf-8")


def process_job(job_id: str, source_path: Path, job_dir: Path) -> None:
    try:
        with jobs_lock:
            jobs[job_id]["status"] = "transcribing"
            jobs[job_id]["progress"] = 10

        duration = video_duration(source_path)
        if duration <= 0 or duration > MAX_VIDEO_SECONDS:
            raise RuntimeError(f"Video duration must be between 1 second and {MAX_VIDEO_SECONDS // 60} minutes.")

        whisper = get_model()
        segments_iter, info = whisper.transcribe(
            str(source_path),
            language=LANGUAGE,
            vad_filter=True,
            beam_size=3,
        )
        segments = [
            {"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip()}
            for seg in segments_iter
        ]
        if not segments:
            raise RuntimeError("No speech was detected in this video.")

        with jobs_lock:
            jobs[job_id]["progress"] = 45
            jobs[job_id]["language"] = getattr(info, "language", None)
            jobs[job_id]["status"] = "selecting_highlights"

        windows = choose_windows(segments, duration)
        if not windows:
            raise RuntimeError("Could not find enough spoken content to create clips.")

        clips = []
        for index, window in enumerate(windows, start=1):
            clip_name = f"clip-{index:02}.mp4"
            output_path = job_dir / clip_name
            subtitle_path = job_dir / f"clip-{index:02}.srt"
            write_srt(subtitle_path, segments, window["start"], window["end"])
            subtitle_filter = f"subtitles={subtitle_path.as_posix()}"
            run_command([
                "ffmpeg", "-y", "-ss", f"{window['start']:.3f}", "-i", str(source_path),
                "-t", f"{window['end'] - window['start']:.3f}",
                "-vf", f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,{subtitle_filter}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                str(output_path),
            ], timeout=1800)
            clips.append({
                "index": index,
                "filename": clip_name,
                "start_seconds": round(window["start"], 2),
                "end_seconds": round(window["end"], 2),
                "score": window["score"],
                "title": " ".join(window["text"].split()[:9]).strip()[:90],
                "download_path": f"/jobs/{job_id}/clips/{index}",
            })
            with jobs_lock:
                jobs[job_id]["progress"] = 50 + int(45 * index / len(windows))

        with jobs_lock:
            jobs[job_id].update({
                "status": "completed",
                "progress": 100,
                "clips": clips,
                "transcript": " ".join(item["text"] for item in segments),
                "message": f"Created {len(clips)} vertical clips.",
            })
    except Exception as exc:
        with jobs_lock:
            jobs[job_id].update({
                "status": "failed",
                "progress": 0,
                "error": str(exc)[:3000],
            })


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": APP_NAME,
        "version": "0.1.0",
        "configured": bool(WORKER_TOKEN),
        "model": MODEL_SIZE,
        "max_upload_mb": MAX_UPLOAD_MB,
    }


@app.post("/jobs", status_code=202)
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_token(authorization)
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Upload MP4, MOV, M4V or WebM video.")
    safe_name = SAFE_NAME.sub("_", Path(file.filename or "video").name)[:120] or "video"
    job_id = uuid.uuid4().hex
    job_dir = DATA_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    source_path = job_dir / f"source{extension}"
    size = 0
    try:
        with source_path.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(status_code=413, detail=f"Maximum upload size is {MAX_UPLOAD_MB} MB.")
                output.write(chunk)
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    finally:
        await file.close()

    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "filename": safe_name,
            "status": "queued",
            "progress": 0,
            "clips": [],
        }
    background_tasks.add_task(process_job, job_id, source_path, job_dir)
    return {"job_id": job_id, "status": "queued", "status_path": f"/jobs/{job_id}"}


@app.get("/jobs/{job_id}")
def get_job(job_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_token(authorization)
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found. Jobs are held in memory on this worker.")
        return dict(job)


@app.get("/jobs/{job_id}/clips/{clip_index}")
def download_clip(
    job_id: str,
    clip_index: int,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    require_token(authorization)
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.get("status") != "completed":
            raise HTTPException(status_code=404, detail="Completed clip not found.")
        clip = next((item for item in job["clips"] if item["index"] == clip_index), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found.")
    path = DATA_DIR / job_id / clip["filename"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Clip file is no longer available.")
    return FileResponse(path, media_type="video/mp4", filename=clip["filename"])
