import { Router } from "express";
import path from "path";
import fs from "fs";

const router = Router();
const PLOTS_DIR = path.join(process.cwd(), "..", "qmclaw-web", "public", "plots");

// Ensure plots directory exists
if (!fs.existsSync(PLOTS_DIR)) {
  fs.mkdirSync(PLOTS_DIR, { recursive: true });
}

/** Serve a plot PNG for a given jobId */
router.get("/:jobId", (req, res) => {
  const { jobId } = req.params;
  const plotPath = path.join(PLOTS_DIR, `${jobId}.png`);

  if (!fs.existsSync(plotPath)) {
    // Try alternative: any file with the jobId as prefix
    const files = fs.readdirSync(PLOTS_DIR).filter((f) => f.startsWith(jobId));
    if (files.length > 0) {
      return res.sendFile(path.join(PLOTS_DIR, files[0]));
    }
    res.status(404).json({ error: "Plot not found" });
    return;
  }

  res.setHeader("Content-Type", "image/png");
  res.sendFile(plotPath);
});

/** List all available plots */
router.get("/", (_req, res) => {
  if (!fs.existsSync(PLOTS_DIR)) {
    return res.json([]);
  }
  const files = fs.readdirSync(PLOTS_DIR).filter((f) => f.endsWith(".png"));
  res.json(files);
});

export default router;