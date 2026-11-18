
export const metadata = {
  title: "ClipMaster AI",
  description: "Turn long videos into engaging YouTube Shorts with AI."
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body style={{ margin: 0 }}>
        {children}
      </body>
    </html>
  );
}
