"use client";

import { useEffect, useState } from "react";

function getYouTubeId(value) {
  try {
    const parsed = new URL(value.trim());
    const host = parsed.hostname.replace(/^www\./, "").replace(/^m\./, "");
    if (host === "youtu.be") {
      const id = parsed.pathname.split("/").filter(Boolean)[0];
      return /^[a-zA-Z0-9_-]{11}$/.test(id || "") ? id : null;
    }
    if (host !== "youtube.com" && host !== "youtube-nocookie.com") return null;
    const parts = parsed.pathname.split("/").filter(Boolean);
    const id = parsed.pathname === "/watch"
      ? parsed.searchParams.get("v")
      : ["shorts", "embed", "live", "v"].includes(parts[0]) ? parts[1] : null;
    return /^[a-zA-Z0-9_-]{11}$/.test(id || "") ? id : null;
  } catch {
    return null;
  }
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "https://clipmaster-ai-backend.onrender.com";

function formatEta(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return "Calculating…";
  const value = Math.max(0, Math.ceil(Number(seconds)));
  if (value < 60) return `about ${value} sec`;
  const minutes = Math.floor(value / 60);
  const remainingSeconds = value % 60;
  return remainingSeconds ? `about ${minutes} min ${remainingSeconds} sec` : `about ${minutes} min`;
}

const plans = [
  { name: "Free", price: "₹0", clips: "6 clips", detail: "6 clips every month" },
  { name: "Creator", price: "₹49", clips: "22 clips", detail: "20 clips + 2 bonus clips every month" },
  { name: "Pro", price: "₹79", clips: "50 clips", detail: "50 clips every month" },
  { name: "Studio", price: "₹199", clips: "115 clips", detail: "115 clips every month" }
];

const topUps = [
  { price: "₹15", clips: "1 clip" },
  { price: "₹20", clips: "3 clips" },
  { price: "₹50", clips: "15 clips" }
];

export default function Home() {
  const [url, setUrl] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);
  const [filePreview, setFilePreview] = useState("");

  useEffect(() => {
    if (!selectedFile) {
      setFilePreview("");
      return;
    }
    const previewUrl = URL.createObjectURL(selectedFile);
    setFilePreview(previewUrl);
    return () => URL.revokeObjectURL(previewUrl);
  }, [selectedFile]);
  const [videoId, setVideoId] = useState("");
  const [permission, setPermission] = useState(false);
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState("info");
  const [backendStatus, setBackendStatus] = useState("checking");
  const [isGenerating, setIsGenerating] = useState(false);
  const [progress, setProgress] = useState(0);
  const [etaSeconds, setEtaSeconds] = useState(null);
  const [generatedClips, setGeneratedClips] = useState([]);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE_URL}/health`, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error("Backend health check failed");
        return response.json();
      })
      .then(data => setBackendStatus(data.status === "healthy" ? "online" : "unavailable"))
      .catch(error => {
        if (error.name !== "AbortError") setBackendStatus("unavailable");
      });
    return () => controller.abort();
  }, []);

  function checkLink() {
    const id = getYouTubeId(url);
    setVideoId(id || "");
    setMessage(id
      ? "YouTube link recognized. If the video allows embedding, its preview will appear below."
      : "Please enter a valid YouTube video URL (watch, youtu.be, Shorts, live or embed link).");
    setMessageType(id ? "success" : "error");
  }

  async function startGeneration() {
    if (!videoId && !selectedFile) {
      setMessage("Paste a YouTube link or choose a video file first.");
      setMessageType("error");
      return;
    }
    if (!permission) {
      setMessage("Please confirm that you have permission or another lawful basis to make and use edits of this video.");
      setMessageType("error");
      return;
    }

    setIsGenerating(true);
    setProgress(0);
    setEtaSeconds(null);
    setGeneratedClips([]);
    setMessage("Sending your video to ClipMaster AI…");
    setMessageType("info");

    try {
      const form = new FormData();
      form.append("permission", "true");
      if (selectedFile) {
        form.append("video", selectedFile);
      } else {
        form.append("youtube_url", url.trim());
      }

      const createResponse = await fetch(`${API_BASE_URL}/jobs`, {
        method: "POST",
        body: form
      });
      const created = await createResponse.json();
      if (!createResponse.ok) {
        throw new Error(created.detail || "Could not start video processing.");
      }

      setMessage("Video received. Creating up to five 30-second vertical clips. Please keep this page open…");
      const jobId = created.job_id;
      setProgress(2);

      for (let attempt = 0; attempt < 120; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 3000));
        const statusResponse = await fetch(`${API_BASE_URL}/jobs/${jobId}`);
        const job = await statusResponse.json();
        if (!statusResponse.ok) {
          throw new Error(job.detail || "Could not check processing status.");
        }
        setProgress(Number(job.progress ?? 0));
        setEtaSeconds(job.eta_seconds ?? null);
        if (job.status === "completed") {
          setProgress(100);
          setEtaSeconds(0);
          setGeneratedClips(job.clips || []);
          setMessage(job.message || "Your clips are ready.");
          setMessageType("success");
          return;
        }
        if (job.status === "failed") {
          throw new Error(job.message || "Video processing failed.");
        }
        setMessage(job.message || "Your video is processing…");
      }
      throw new Error("Processing is taking longer than expected. Check again in a moment; temporary jobs may not survive a Render restart.");
    } catch (error) {
      setMessage(error.message || "Something went wrong while processing your video.");
      setMessageType("error");
    } finally {
      setIsGenerating(false);
    }
  }

  return (
    <main style={{ minHeight: "100vh", background: "#080b14", color: "#f7f9ff", padding: "28px 18px 36px", fontFamily: "Arial, sans-serif" }}>
      <div style={{ maxWidth: 960, margin: "0 auto" }}>
        <header style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 38, height: 38, display: "grid", placeItems: "center", borderRadius: 11, background: "#287cff", fontSize: 22 }}>✂</div>
          <span style={{ color: "#69aaff", fontSize: 21, fontWeight: 800 }}>ClipMaster AI</span>
          <span style={{ marginLeft: "auto", color: "#aab2c5", fontSize: 12, border: "1px solid #303b52", borderRadius: 20, padding: "7px 10px" }}>BETA</span>
        </header>

        <div role="status" style={{ marginTop: 18, display: "inline-flex", alignItems: "center", gap: 8, border: "1px solid #303b52", borderRadius: 20, padding: "8px 12px", color: backendStatus === "online" ? "#8ee5b2" : backendStatus === "unavailable" ? "#ff9b9b" : "#b8c9e8", fontSize: 12 }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: backendStatus === "online" ? "#38c982" : backendStatus === "unavailable" ? "#ff7777" : "#d6ad52" }} />
          Backend: {backendStatus === "online" ? "Connected" : backendStatus === "unavailable" ? "Unavailable" : "Checking…"}
        </div>

        <section style={{ padding: "58px 0 24px", maxWidth: 760 }}>
          <p style={{ color: "#69aaff", fontWeight: 700, letterSpacing: 2, fontSize: 12 }}>YOUTUBE TO SHORTS</p>
          <h1 style={{ fontSize: "clamp(34px, 7vw, 58px)", lineHeight: 1.08, margin: "12px 0 16px" }}>
            Turn long videos into <span style={{ color: "#54a5ff" }}>Shorts.</span>
          </h1>
          <p style={{ color: "#aab2c5", lineHeight: 1.7, fontSize: 16, maxWidth: 620 }}>
            Paste a YouTube link or upload your own video to prepare it for Shorts. ClipMaster AI is being prepared as a free, capacity-limited beta with no paid AI processing APIs.
          </p>
        </section>

        <section style={{ background: "#151b2b", border: "1px solid #27334a", padding: 22, borderRadius: 18 }}>
          <label htmlFor="youtube-url" style={{ display: "block", fontWeight: 700, marginBottom: 11 }}>Paste your YouTube video link</label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
            <input
              id="youtube-url"
              type="url"
              value={url}
              onChange={e => { setUrl(e.target.value); setVideoId(""); setMessage(""); }}
              onKeyDown={e => { if (e.key === "Enter") checkLink(); }}
              placeholder="https://www.youtube.com/watch?v=..."
              style={{ flex: "1 1 260px", minWidth: 0, padding: 15, boxSizing: "border-box", borderRadius: 10, border: "1px solid #38445c", background: "#080b14", color: "white", fontSize: 15 }}
            />
            <button onClick={checkLink} style={{ padding: "0 22px", minHeight: 50, background: "#287cff", color: "white", border: 0, borderRadius: 10, fontWeight: 700, cursor: "pointer" }}>Check link</button>
          </div>

          {message && <p role="status" style={{ color: messageType === "error" ? "#ff9b9b" : messageType === "success" ? "#8ee5b2" : "#b8c9e8", lineHeight: 1.6, fontSize: 14, margin: "14px 0 0" }}>{message}</p>}
          {isGenerating && <div style={{ marginTop: 16, padding: 14, borderRadius: 12, background: "#0c1322", border: "1px solid #303b52" }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 9, fontSize: 14 }}>
              <strong>Video processing</strong><strong>{progress}%</strong>
            </div>
            <div style={{ height: 10, overflow: "hidden", borderRadius: 20, background: "#303b52" }}>
              <div style={{ width: `${progress}%`, height: "100%", borderRadius: 20, background: "#287cff", transition: "width 0.5s ease" }} />
            </div>
            <p style={{ margin: "9px 0 0", color: "#b8c9e8", fontSize: 13 }}>Estimated time remaining: {formatEta(etaSeconds)}. This is an estimate and may change with server speed.</p>
          </div>}
          {generatedClips.length > 0 && <div style={{ marginTop: 18, display: "grid", gap: 10 }}><h3 style={{ margin: 0 }}>Your clips</h3>{generatedClips.map(clip => <a key={clip.name} href={`${API_BASE_URL}${clip.url}`} target="_blank" rel="noreferrer" download style={{ display: "block", padding: 13, borderRadius: 10, background: "#20334e", color: "#9dccff", textDecoration: "none", fontWeight: 700 }}>Download {clip.name} · {(clip.size_bytes / (1024 * 1024)).toFixed(1)} MB</a>)}</div>}

          {videoId && (
            <div style={{ marginTop: 20 }}>
              <div style={{ position: "relative", width: "100%", aspectRatio: "16 / 9", overflow: "hidden", borderRadius: 12, background: "#05070c" }}>
                <iframe
                  title="YouTube video preview"
                  src={`https://www.youtube-nocookie.com/embed/${videoId}`}
                  style={{ position: "absolute", inset: 0, width: "100%", height: "100%", border: 0 }}
                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                  allowFullScreen
                  referrerPolicy="strict-origin-when-cross-origin"
                />
              </div>
              <p style={{ color: "#9daac0", fontSize: 12, lineHeight: 1.5 }}>Preview availability depends on the video's YouTube embedding settings.</p>
              <label style={{ display: "flex", alignItems: "flex-start", gap: 10, color: "#c4ccda", fontSize: 13, lineHeight: 1.5, marginTop: 18 }}>
                <input type="checkbox" checked={permission} onChange={e => setPermission(e.target.checked)} style={{ marginTop: 3 }} />
                I have permission or another lawful basis to create and use edits of this video.
              </label>
              <button onClick={startGeneration} disabled={isGenerating} style={{ width: "100%", padding: 15, marginTop: 15, background: isGenerating ? "#52627d" : "#287cff", color: "white", border: 0, borderRadius: 10, fontWeight: 800, fontSize: 15, cursor: isGenerating ? "wait" : "pointer" }}>
                {isGenerating ? "Processing video…" : "Generate Clips →"}
              </button>
            </div>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "22px 0", color: "#9daac0", fontSize: 12 }}>
            <span style={{ flex: 1, height: 1, background: "#303b52" }} />
            OR UPLOAD YOUR OWN VIDEO
            <span style={{ flex: 1, height: 1, background: "#303b52" }} />
          </div>
          <label htmlFor="video-file" style={{ display: "block", fontWeight: 700, marginBottom: 10 }}>Choose a video from your device</label>
          <input
            id="video-file"
            type="file"
            accept="video/mp4,video/quicktime,video/webm,video/*"
            onChange={e => {
              const file = e.target.files?.[0] || null;
              setSelectedFile(file);
              setVideoId("");
              setUrl("");
              setMessage(file ? "Video selected. Preview it below before continuing." : "");
              setMessageType("info");
            }}
            style={{ display: "block", width: "100%", boxSizing: "border-box", padding: 13, border: "1px dashed #52627d", borderRadius: 10, background: "#080b14", color: "#dce6f7" }}
          />
          {selectedFile && (
            <div style={{ marginTop: 16 }}>
              <p style={{ color: "#b8c9e8", fontSize: 13, overflowWrap: "anywhere" }}>
                Selected: {selectedFile.name} · {(selectedFile.size / (1024 * 1024)).toFixed(1)} MB
              </p>
              {filePreview && (
                <video
                  controls
                  playsInline
                  src={filePreview}
                  style={{ display: "block", width: "100%", maxHeight: 420, borderRadius: 12, background: "#05070c" }}
                />
              )}
              <button
                onClick={() => { setSelectedFile(null); setMessage(""); }}
                style={{ marginTop: 10, padding: "9px 13px", background: "#27334a", color: "white", border: 0, borderRadius: 8, cursor: "pointer" }}
              >
                Remove selected video
              </button>
              <label style={{ display: "flex", alignItems: "flex-start", gap: 10, color: "#c4ccda", fontSize: 13, lineHeight: 1.5, marginTop: 18 }}>
                <input type="checkbox" checked={permission} onChange={e => setPermission(e.target.checked)} style={{ marginTop: 3 }} />
                I own this video or have permission or another lawful basis to create and use edits of it.
              </label>
              <button onClick={startGeneration} disabled={isGenerating} style={{ width: "100%", padding: 15, marginTop: 15, background: isGenerating ? "#52627d" : "#287cff", color: "white", border: 0, borderRadius: 10, fontWeight: 800, fontSize: 15, cursor: isGenerating ? "wait" : "pointer" }}>
                {isGenerating ? "Processing video…" : "Generate Clips →"}
              </button>
            </div>
          )}
        </section>

        <section style={{ marginTop: 28, background: "#101a2a", border: "1px solid #294365", padding: 20, borderRadius: 15 }}>
          <h2 style={{ margin: "0 0 10px", fontSize: 21 }}>Free beta processing</h2>
          <p style={{ color: "#b8c9e8", fontSize: 14, lineHeight: 1.7, margin: 0 }}>
            Current beta engine: FFmpeg creates up to five 30-second vertical clips from the beginning of a video. AI transcription and highlight detection are not enabled yet. Free processing may have daily limits or pauses when computing capacity is unavailable.
          </p>
          <p style={{ color: "#ffcf85", fontSize: 13, lineHeight: 1.6, margin: "12px 0 0" }}>
            Status: video upload and supported YouTube link processing are connected. Clips are temporarily stored on the server for up to five hours; download your clips to keep them.
          </p>
        </section>

        <section style={{ marginTop: 46 }}>
          <h2 style={{ fontSize: 25 }}>Monthly clip plans</h2>
          <p style={{ color: "#9daac0", marginTop: -7, fontSize: 14 }}>Planned subscription prices and monthly clip allowances. Payments are not active yet.</p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 14 }}>
            {plans.map(plan => (
              <article key={plan.name} style={{ background: "#151b2b", border: "1px solid #29344a", padding: 20, borderRadius: 15 }}>
                <h3 style={{ margin: "0 0 8px" }}>{plan.name}</h3>
                <div style={{ fontSize: 30, fontWeight: 800 }}>{plan.price}<span style={{ fontSize: 13, color: "#9daac0", fontWeight: 400 }}>/month</span></div>
                <p style={{ color: "#69aaff", fontWeight: 700, fontSize: 19 }}>{plan.clips}</p>
                <p style={{ color: "#9daac0", fontSize: 13 }}>{plan.detail}</p>
              </article>
            ))}
          </div>
          <h2 style={{ fontSize: 22, marginTop: 30 }}>Clip top-ups</h2>
          <p style={{ color: "#9daac0", marginTop: -7, fontSize: 14 }}>Extra clips when you need them.</p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
            {topUps.map(item => (
              <article key={item.price} style={{ background: "#151b2b", border: "1px solid #29344a", padding: 18, borderRadius: 14 }}>
                <div style={{ fontSize: 25, fontWeight: 800 }}>{item.price}</div>
                <p style={{ color: "#69aaff", fontWeight: 700, marginBottom: 0 }}>{item.clips}</p>
              </article>
            ))}
          </div>
          <p style={{ color: "#ffcf85", fontSize: 12, lineHeight: 1.6, marginTop: 14 }}>All prices and clip allowances are displayed for planning only. Checkout, subscriptions, renewals and top-up purchases are not enabled.</p>
        </section>

        <footer style={{ textAlign: "center", color: "#71809a", marginTop: 55, fontSize: 12, lineHeight: 1.7 }}>
          © 2026 ClipMaster AI · Use videos you have the right to edit. YouTube preview availability is controlled by the video owner.
        </footer>
      </div>
    </main>
  );
}
