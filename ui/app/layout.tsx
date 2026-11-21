import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Carthij | AI Funding Intake & Readiness Assessment",
  description: "AI-augmented funding assessment for startups and non-profits powered by Gemini multi-agent workflows.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="bg-slate-50 text-slate-900 min-h-screen flex flex-col antialiased">
        {children}
      </body>
    </html>
  );
}
