// frames.json(프레임 파일 + 시각) → ffmpeg concat 목록 → 30fps MP4
import fs from "node:fs";
import { execFileSync } from "node:child_process";

const frames = JSON.parse(fs.readFileSync("frames.json", "utf8"));
const lines = [];
for (let i = 0; i < frames.length; i++) {
  const d = i + 1 < frames.length ? frames[i + 1].t - frames[i].t : 1.5;
  lines.push(`file '${frames[i].f.replace(/\\/g, "/")}'`, `duration ${Math.max(d, 0.001).toFixed(4)}`);
}
lines.push(`file '${frames.at(-1).f.replace(/\\/g, "/")}'`);
fs.writeFileSync("list.txt", lines.join("\n"));
const out = process.argv[2];
execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
  "-vf", "fps=30,scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=white,format=yuv420p",
  "-c:v", "libx264", "-preset", "slow", "-crf", "22", "-movflags", "+faststart", out], { stdio: "inherit" });
console.log("ok", out, (fs.statSync(out).size / 1e6).toFixed(1), "MB");
