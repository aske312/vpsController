import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export function lightReleaseVersion(base, branch, head, latest) {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(base);
  if (!match || !["light", "test-light"].includes(branch)) throw new Error("Invalid Light release version or branch");
  const series = `${match[1]}.${match[2]}`;
  const floor = Number(match[3]);
  if (!latest) return `v${base}`;
  const tag = /^light-v(\d+\.\d+)\.(\d+)$/.exec(latest.tag);
  if (!tag || tag[1] !== series) throw new Error("Light release tag belongs to a different version series");
  const previous = Number(tag[2]);
  const increment = branch === "light" && latest.commit !== head ? 1 : 0;
  return `v${series}.${Math.max(floor, previous + increment)}`;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const base = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")).version;
  const branch = process.env.GITHUB_REF_NAME;
  const head = process.env.GITHUB_SHA;
  if (!head) throw new Error("Release commit is missing");
  const series = base.slice(0, base.lastIndexOf("."));
  const tag = execFileSync("git", ["tag", "--list", `light-v${series}.*`, "--sort=-v:refname"], { encoding: "utf8" }).trim().split("\n")[0];
  const latest = tag ? { tag, commit: execFileSync("git", ["rev-list", "-n", "1", tag], { encoding: "utf8" }).trim() } : null;
  process.stdout.write(lightReleaseVersion(base, branch, head, latest) + "\n");
}
