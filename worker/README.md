# ClipMaster AI processing worker

This is a separate Python service for **videos uploaded by users who have the right to edit them**. It does not download YouTube videos or bypass YouTube restrictions.

## What it does

1. Accepts an authenticated MP4, MOV, M4V or WebM upload.
2. Transcribes speech with Faster-Whisper on CPU.
3. Selects up to 10 non-overlapping candidate moments using a simple transcript heuristic.
4. Uses FFmpeg to render vertical 9:16 MP4 clips with burned-in subtitles.
5. Exposes job status and clip-download endpoints.

The highlight selector is an initial heuristic, not a trained virality model. Review clips before publishing.

## Run locally or in a Docker host

Build and run:

```bash
docker build -t clipmaster-worker ./worker
docker run --rm -p 8000:8000 \
  -e WORKER_API_TOKEN='replace-with-a-long-random-secret' \
  -e CORS_ORIGINS='http://localhost:3000' \
  -v clipmaster-data:/data \
  clipmaster-worker
```

Health check: `GET /health`

The first processing request downloads the configured Whisper model, so allow time and disk space. The default `small` model uses CPU/int8 and may be slow on free or low-power machines.

## API

Every processing endpoint requires:

```
Authorization: Bearer YOUR_WORKER_API_TOKEN
```

Create a job with multipart form field `file`:

```bash
curl -X POST http://localhost:8000/jobs \
  -H 'Authorization: Bearer YOUR_WORKER_API_TOKEN' \
  -F 'file=@my-video.mp4'
```

Poll the returned `status_path` with the same authorization header. When the job is completed, each clip has a `download_path`.

## Environment

- `WORKER_API_TOKEN`: required secret; without it, processing endpoints return 503.
- `DATA_DIR`: persistent storage path; default `/data`.
- `MAX_UPLOAD_MB`: upload cap; default 500.
- `MAX_VIDEO_SECONDS`: duration cap; default 7200 (2 hours).
- `MAX_CLIPS`: maximum clips; default 10.
- `WHISPER_MODEL`: default `small`.
- `WHISPER_COMPUTE_TYPE`: default `int8`.
- `WHISPER_LANGUAGE`: optional language code; auto-detect when empty.
- `CORS_ORIGINS`: comma-separated allowed browser origins; default localhost.

## Important deployment notes

- Use a persistent disk. The job registry is currently in memory, so restarting the worker loses job status even if files remain.
- This is a single-worker beta implementation. Do not run multiple replicas without replacing the in-memory job registry with shared storage/queue.
- Keep `WORKER_API_TOKEN` on the server only. Never put it in Next.js client-side code.
- Add user authentication, per-user quotas, rate limiting, cleanup/retention, persistent job storage and a queue before opening this endpoint publicly.
- CPU transcription and video rendering are resource-intensive. A free hosting tier may sleep, limit CPU, disk, request size or runtime; do not promise unlimited free processing.
- The Vercel frontend is not connected to this worker yet. Configure a real worker host and a secure server-side integration before showing processing as enabled.
