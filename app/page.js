
"use client";

import { useState } from "react";

export default function Home() {
  const [url, setUrl] = useState("");

  return (
    <main style={{
      minHeight: "100vh",
      background: "#080b14",
      color: "white",
      padding: "30px 18px",
      fontFamily: "Arial"
    }}>
      <header style={{ maxWidth: "900px", margin: "auto" }}>
        <h2 style={{ color: "#54a5ff" }}>✂ ClipMaster AI</h2>

        <h1 style={{ fontSize: "38px", marginTop: "65px" }}>
          Turn long videos into
          <span style={{ color: "#54a5ff" }}> viral Shorts.</span>
        </h1>

        <p style={{ color: "#aab2c5", lineHeight: "1.7" }}>
          AI-powered video clipping made simple.
          Create engaging YouTube Shorts in minutes.
        </p>

        <section style={{
          background: "#151b2b",
          padding: "22px",
          borderRadius: "16px",
          marginTop: "35px"
        }}>
          <h3>Paste your YouTube video link</h3>

          <input
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder="https://youtube.com/watch?v=..."
            style={{
              width: "100%",
              padding: "15px",
              boxSizing: "border-box",
              borderRadius: "9px",
              border: "1px solid #38445c",
              background: "#080b14",
              color: "white",
              marginTop: "12px"
            }}
          />

          <button
            onClick={() => alert(
              url
                ? "Video link received! AI processing is not connected yet."
                : "Paste a video link first."
            )}
            style={{
              width: "100%",
              padding: "15px",
              marginTop: "15px",
              background: "#287cff",
              color: "white",
              border: 0,
              borderRadius: "9px",
              fontWeight: "bold"
            }}
          >
            Generate AI Clips →
          </button>
        </section>

        <h2 style={{ marginTop: "45px" }}>Choose your plan</h2>

        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))",
          gap: "15px"
        }}>
          {[
            ["Free", "₹0", "480p"],
            ["Starter", "₹79", "720p"],
            ["Pro", "₹149", "1080p"]
          ].map(plan => (
            <div key={plan[0]} style={{
              background: "#151b2b",
              padding: "20px",
              borderRadius: "14px",
              border: "1px solid #29344a"
            }}>
              <h3>{plan[0]}</h3>
              <h2>{plan[1]}</h2>
              <p>{plan[2]} Export</p>
            </div>
          ))}
        </div>

        <p style={{
          textAlign: "center",
          color: "#7e8ba5",
          marginTop: "55px"
        }}>
          © 2026 ClipMaster AI
        </p>
      </header>
    </main>
  );
}
