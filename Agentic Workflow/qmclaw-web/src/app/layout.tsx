import type { Metadata } from "next";
import "@/styles/variables.css";
import "@/styles/animations.css";

export const metadata: Metadata = {
  title: "qmclaw - Quantum Measurement & Calibration Workflow",
  description: "Intelligent quantum measurement and calibration control system",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body style={{ margin: 0, padding: 0 }}>{children}</body>
    </html>
  );
}