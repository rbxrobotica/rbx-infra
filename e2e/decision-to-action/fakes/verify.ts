// Fake verify_command target for the E2E: `bun run scripts/verify.ts --mission <dir>`.
// The real rbx-creatives verify.ts is deterministic offline; this stand-in checks
// the same shape (manifest.json exists, every artifact hash matches) and exits 0/1.
import { createHash } from "crypto";
import { existsSync, readFileSync } from "fs";
import { join } from "path";
const idx = process.argv.indexOf("--mission");
const ref = idx >= 0 ? process.argv[idx + 1] : "";
const dir = ref.startsWith("/") ? ref : join(process.cwd(), "missions", ref.replace(/^missions\//, ""));
if (!existsSync(join(dir, "manifest.json"))) { console.error("missing manifest"); process.exit(1); }
const m = JSON.parse(readFileSync(join(dir, "manifest.json"), "utf8"));
for (const a of m.artifacts) {
  const bytes = readFileSync(join(dir, "out", a.file));
  const h = createHash("sha256").update(bytes).digest("hex");
  if (h !== a.sha256) { console.error(`hash mismatch ${a.file}`); process.exit(1); }
}
if (m.verdict !== "PASSED") { console.error("verdict not PASSED"); process.exit(1); }
console.log("[VERIFY SUCCESS]");
