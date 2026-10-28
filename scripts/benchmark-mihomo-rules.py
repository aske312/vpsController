"""Compare identical domain feed snapshots as text/YAML and MRS on Linux.

Uses temporary files, loopback listeners and an isolated core, without TUN or
production credentials. Requires PyYAML and curl. Does not modify subscriptions.
"""
import argparse
import hashlib
import http.server
import json
import re
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import yaml


FEEDS = {
    "ads": ("yaml", "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/reject.txt"),
    "threats": ("text", "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/tif.mini-onlydomains.txt"),
    "phishing": ("text", "https://phishing.army/download/phishing_army_blocklist_extended.txt"),
}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def memory(pid):
    status = Path(f"/proc/{pid}/status").read_text()
    return {key: int(re.search(rf"^{key}:\s+(\d+)", status, re.M)[1])
            for key in ("VmRSS", "VmHWM")}


class Origin(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_):
        pass


def fetch_json(port, endpoint):
    # Ignore any proxy environment settings for loopback control traffic.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}{endpoint}", timeout=2) as response:
        return json.load(response)


def run_case(core, root, selected, kind, hosts, origin_port, seconds):
    with tempfile.TemporaryDirectory(prefix="mrs-core-", dir=root) as folder:
        folder = Path(folder)
        api, mixed = free_port(), free_port()
        providers = {}
        for name in selected:
            fmt = "mrs" if kind == "mrs" else FEEDS[name][0]
            source = root / f"{name}.{fmt}"
            target = folder / source.name
            target.write_bytes(source.read_bytes())
            providers[name] = {"type": "file", "behavior": "domain", "format": fmt, "path": source.name}
        config = {"mixed-port": mixed, "bind-address": "127.0.0.1", "allow-lan": False,
                  "external-controller": f"127.0.0.1:{api}", "log-level": "info",
                  "mode": "rule", "ipv6": False, "tun": {"enable": False},
                  "dns": {"enable": False}, "hosts": {host: "127.0.0.1" for host in hosts.values()},
                  "rule-providers": providers,
                  "rules": [*[f"RULE-SET,{name},REJECT" for name in selected], "MATCH,DIRECT"]}
        file = folder / "config.yaml"
        file.write_text(yaml.safe_dump(config))
        log_path = folder / "core.log"
        with log_path.open("w+") as log:
            proc = subprocess.Popen([str(core), "-d", str(folder), "-f", str(file)], stdout=log, stderr=log)
            started = time.monotonic()
            peak = 0
            try:
                while True:
                    if proc.poll() is not None:
                        raise RuntimeError(log_path.read_text())
                    peak = max(peak, memory(proc.pid)["VmHWM"])
                    try:
                        loaded = fetch_json(api, "/providers/rules")["providers"] or {}
                        if all((loaded.get(name) or {}).get("ruleCount", 0) > 0 for name in selected):
                            break
                    except (OSError, ValueError):
                        pass
                    if time.monotonic() - started > 30:
                        raise TimeoutError("Providers did not load: " + log_path.read_text())
                    time.sleep(.05)
                ready_seconds = time.monotonic() - started
                samples = []
                until = time.monotonic() + seconds
                while time.monotonic() < until:
                    sample = memory(proc.pid)
                    samples.append(sample["VmRSS"])
                    peak = max(peak, sample["VmHWM"])
                    time.sleep(.05)
                checks = {}
                for name in ["allowed", *selected]:
                    result = subprocess.run(
                        ["curl", "--noproxy", "", "-sS", "--max-time", "3", "--proxy",
                         f"http://127.0.0.1:{mixed}", "-o", "/dev/null", "-w", "%{http_code}",
                         f"http://{hosts[name]}:{origin_port}/"], capture_output=True, text=True, timeout=5)
                    expected = "204" if name == "allowed" else "502"
                    assert result.stdout == expected, (name, result.stdout, result.stderr, log_path.read_text())
                    checks[name] = result.stdout
                return {"case": "+".join(selected) or "baseline", "format": kind,
                        "rss_kib": samples[-1], "peak_kib": peak, "ready_seconds": round(ready_seconds, 3),
                        "counts": {name: loaded[name]["ruleCount"] for name in selected}, "checks": checks}
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--sample-seconds", type=float, default=3)
    args = parser.parse_args()
    core = args.core.resolve()
    report = {"core": subprocess.check_output([str(core), "-v"], text=True).strip(),
              "platform": "Linux; RSS and high-water RSS, not iOS footprint", "feeds": {}, "results": []}
    with tempfile.TemporaryDirectory(prefix="mrs-benchmark-") as folder:
        root = Path(folder)
        hosts = {"allowed": "mrs-benchmark.invalid"}
        for name, (fmt, url) in FEEDS.items():
            with urllib.request.urlopen(url, timeout=45) as response:
                data = response.read(16 * 1024 * 1024 + 1)
            assert 0 < len(data) <= 16 * 1024 * 1024
            source = root / f"{name}.{fmt}"
            source.write_bytes(data)
            values = yaml.safe_load(data)["payload"] if fmt == "yaml" else data.decode().splitlines()
            hosts[name] = next(value.lstrip("+*.") for value in values
                               if re.fullmatch(r"[+*.]*[a-z0-9-]+(?:\.[a-z0-9-]+)+", value))
            target = root / f"{name}.mrs"
            result = subprocess.run([str(core), "convert-ruleset", "domain", fmt, str(source), str(target)],
                                    capture_output=True, text=True, timeout=45)
            assert result.returncode == 0, result.stderr + result.stdout
            report["feeds"][name] = {"url": url, "sha256": hashlib.sha256(data).hexdigest(),
                                     "source_bytes": len(data), "mrs_bytes": target.stat().st_size}
            print("CONVERTED", name, json.dumps(report["feeds"][name]), flush=True)
        origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        thread = threading.Thread(target=origin.serve_forever, daemon=True)
        thread.start()
        try:
            for repeat in range(args.runs):
                cases = [((), "original"), *((names, fmt) for names in
                         [("ads",), ("threats", "phishing"), tuple(FEEDS)] for fmt in ("original", "mrs"))]
                if repeat % 2:
                    cases.reverse()
                for selected, kind in cases:
                    row = run_case(core, root, selected, kind, hosts, origin.server_port, args.sample_seconds)
                    row["run"] = repeat + 1
                    report["results"].append(row)
                    args.output.write_text(json.dumps(report, indent=2) + "\n")
                    print("RESULT", json.dumps(row), flush=True)
        finally:
            origin.shutdown()
            origin.server_close()
            thread.join()
        for names in [("ads",), ("threats", "phishing"), tuple(FEEDS)]:
            rows = [r for r in report["results"] if r["case"] == "+".join(names)]
            assert all(r["counts"] == rows[0]["counts"] for r in rows), "Rule counts changed on conversion"
    print("PASS: identical snapshot counts, blocked domains and allowed loopback HTTP requests", flush=True)


if __name__ == "__main__":
    main()
