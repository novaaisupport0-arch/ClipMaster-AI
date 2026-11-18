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

  function checkLink() {
    const id = getYouTubeId(url);
    setVideoId(id || "");
    setMessage(id
      ? "YouTube link recognized. If the video allows embedding, its preview will appear below."
      : "Please enter a valid YouTube video URL (watch, youtu.be, Shorts, live or embed link).");
    setMessageType(id ? "success" : "error");
  }

  function startGeneration() {
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
    setMessage(selectedFile
      ? "Your video is selected and previewed locally. Secure upload, AI clipping and export are not connected yet; the file has not been sent to a server."
      : "Your link is ready, but AI clipping and video export are not connected yet. No video has been downloaded or processed.");
    setMessageType("info");
  }

  return (
    <main style={{ minHeight: "100vh", background: "#080b14", color: "#f7f9ff", padding: "28px 18px 36px", fontFamily: "Arial, sans-serif" }}>
      <div style={{ maxWidth: 960, margin: "0 auto" }}>
        <header style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 38, height: 38, display: "grid", placeItems: "center", borderRadius: 11, background: "#287cff", fontSize: 22 }}>✂</div>
          <span style={{ color: "#69aaff", fontSize: 21, fontWeight: 800 }}>ClipMaster AI</span>
          <span style={{ marginLeft: "auto", color: "#aab2c5", fontSize: 12, border: "1px solid #303b52", borderRadius: 20, padding: "7px 10px" }}>BETA</span>
        </header>

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
              <button onClick={startGeneration} style={{ width: "100%", padding: 15, marginTop: 15, background: "#287cff", color: "white", border: 0, borderRadius: 10, fontWeight: 800, fontSize: 15, cursor: "pointer" }}>
                Generate AI Clips →
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
              <button onClick={startGeneration} style={{ width: "100%", padding: 15, marginTop: 15, background: "#287cff", color: "white", border: 0, borderRadius: 10, fontWeight: 800, fontSize: 15, cursor: "pointer" }}>
                Prepare uploaded video →
              </button>
            </div>
          )}
        </section>

        <section style={{ marginTop: 28, background: "#101a2a", border: "1px solid #294365", padding: 20, borderRadius: 15 }}>
          <h2 style={{ margin: "0 0 10px", fontSize: 21 }}>Free beta processing</h2>
          <p style={{ color: "#b8c9e8", fontSize: 14, lineHeight: 1.7, margin: 0 }}>
            Planned engine: open-source Whisper for transcription, FFmpeg for video cutting and export, and an open-source model for finding highlights. No paid AI API is planned. Free processing may have daily limits or pauses when computing capacity is unavailable.
          </p>
          <p style={{ color: "#ffcf85", fontSize: 13, lineHeight: 1.6, margin: "12px 0 0" }}>
            Status: YouTube link checking and local video preview are available. Secure upload, AI clipping and export are not active yet.
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
