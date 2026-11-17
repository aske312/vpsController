import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { hardenPicomatch } from "./harden-picomatch.mjs";

const allowedAdvisories = new Set([
  // No patched braces release exists yet. The dependency is pulled in by
  // vinext's build-time glob stack and does not process public request input.
  "https://github.com/advisories/GHSA-vfj7-8cjw-p6xm",
]);

let report;
try {
  const input = process.argv.indexOf("--input");
  if (input !== -1) {
    if (!process.argv[input + 1]) throw new Error("--input requires a report path");
    report = JSON.parse(readFileSync(process.argv[input + 1], "utf8"));
  } else {
    const audit = process.platform === "win32"
      ? spawnSync(process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", "npm audit --omit=dev --json"], {
          encoding: "utf8",
        })
      : spawnSync("npm", ["audit", "--omit=dev", "--json"], { encoding: "utf8" });
    if (audit.error) throw audit.error;
    if (audit.signal || ![0, 1].includes(audit.status)) throw new Error("npm audit did not complete");
    report = JSON.parse(audit.stdout || "{}");
  }
  if (!report || typeof report !== "object" || Array.isArray(report) || report.error ||
      !report.vulnerabilities || typeof report.vulnerabilities !== "object" || Array.isArray(report.vulnerabilities)) {
    throw new Error("Incomplete npm audit report");
  }
} catch {
  console.error("npm audit returned an invalid or unavailable report");
  process.exit(1);
}

const vulnerabilities = report.vulnerabilities || {};
const picomatchAdvisory = "https://github.com/advisories/GHSA-fc5q-39xc-vjpf";
const picomatch = vulnerabilities.picomatch;
if (Array.isArray(picomatch?.nodes) && picomatch.nodes.length && picomatch.via?.some(item => item?.url === picomatchAdvisory)) {
  try {
    hardenPicomatch(process.cwd(), true, picomatch.nodes);
    allowedAdvisories.add(picomatchAdvisory);
    console.warn("Mitigated picomatch advisory: verified repeating-extglob guard in every installed copy");
  } catch (cause) { console.error(`Picomatch mitigation not verified: ${cause.message}`); }
}
const allowedCache = new Map();
function isAllowed(name, visiting = new Set()) {
  if (allowedCache.has(name)) return allowedCache.get(name);
  if (visiting.has(name)) return false;
  const vulnerability = vulnerabilities[name];
  if (!vulnerability || !Array.isArray(vulnerability.via) || vulnerability.via.length === 0) return false;
  const next = new Set(visiting).add(name);
  const allowed = vulnerability.via.every((item) => {
    if (typeof item === "string") return ["info", "low", "moderate"].includes(vulnerabilities[item]?.severity) || isAllowed(item, next);
    if (["info", "low", "moderate"].includes(item?.severity)) return true;
    return item && typeof item.url === "string" && allowedAdvisories.has(item.url);
  });
  allowedCache.set(name, allowed);
  return allowed;
}

const blocking = Object.entries(vulnerabilities)
  .filter(([, value]) => ["high", "critical"].includes(value.severity))
  .filter(([name]) => !isAllowed(name))
  .map(([name, value]) => `${name} (${value.severity})`);

if (blocking.length) {
  console.error(`Blocking runtime vulnerabilities: ${blocking.join(", ")}`);
  process.exit(1);
}

const exceptions = Object.entries(vulnerabilities)
  .filter(([, value]) => ["high", "critical"].includes(value.severity))
  .filter(([name]) => isAllowed(name))
  .map(([name]) => name);
if (exceptions.length) {
  console.warn(`Allowed runtime advisory without a patched release: ${exceptions.join(", ")}`);
}
