#!/usr/bin/env python3
"""Dane's Fixes restore, no-app edition — runs the Linux manifest over a plain
SSH session. Stock Mint deps only (python3, dpkg, apt-get, sudo).

Usage:
  python3 danes-restore.py                 # status: what's installed / missing
  python3 danes-restore.py install         # install everything missing
  python3 danes-restore.py install git vlc # just these manifest ids
  python3 danes-restore.py --manifest ./daneappmanifest-linux.json status

Entry handling by manager:
  apt    -> batched into ONE `sudo apt-get install -y ...` (one password prompt)
  deb    -> download to ~/Downloads, `sudo apt-get install -y ./file.deb`
  github -> resolve latest release asset via the GitHub API, download, chmod +x
  script / manual -> never run automatically; printed with their notes
"""

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

MANIFEST_URL = "https://raw.githubusercontent.com/davisdane2/devmachine-app-manifest/main/daneappmanifest-linux.json"
DOWNLOADS = Path.home() / "Downloads"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "danes-restore"})
    with urllib.request.urlopen(req) as res:
        return res.read()


def load_manifest(source: str) -> list[dict]:
    raw = Path(source).read_bytes() if Path(source).exists() else fetch(source)
    data = json.loads(raw)
    return data["apps"] if isinstance(data, dict) else data


def is_installed(app: dict) -> bool:
    cmd = app.get("detectCommand")
    if not cmd:
        return False
    return subprocess.run(cmd, shell=True, capture_output=True).returncode == 0


def resolve_latest_asset(repo: str, pattern: str) -> tuple[str, str]:
    """Same semantics as resolveLatestAsset in src/main/installer.ts."""
    release = json.loads(fetch(f"https://api.github.com/repos/{repo}/releases/latest"))
    regex = re.compile(
        "^" + ".*".join(re.escape(p) for p in pattern.split("*")) + "$", re.IGNORECASE
    )
    for asset in release.get("assets", []):
        if regex.match(asset["name"]):
            return asset["name"], asset["browser_download_url"]
    raise SystemExit(f"no asset matching {pattern!r} in {repo} latest release")


def download(url: str, filename: str) -> Path:
    DOWNLOADS.mkdir(exist_ok=True)
    dest = DOWNLOADS / filename
    print(f"  downloading {url}\n  -> {dest}")
    dest.write_bytes(fetch(url))
    return dest


def main() -> None:
    args = sys.argv[1:]
    source = MANIFEST_URL
    if "--manifest" in args:
        i = args.index("--manifest")
        source = args[i + 1]
        del args[i : i + 2]
    mode = args[0] if args else "status"
    only = set(args[1:])

    apps = load_manifest(source)
    if only:
        unknown = only - {a["id"] for a in apps}
        if unknown:
            raise SystemExit(f"unknown manifest ids: {', '.join(sorted(unknown))}")
        apps = [a for a in apps if a["id"] in only]

    if mode == "status":
        for app in apps:
            state = "installed" if is_installed(app) else (
                "manual" if app.get("manager") in ("manual", "script") else "missing"
            )
            print(f"{state:>9}  {app['id']:<28} [{app.get('manager', '?')}]")
        return

    if mode != "install":
        raise SystemExit(f"unknown mode {mode!r} — use status or install")

    missing = [a for a in apps if not is_installed(a)]
    for app in [a for a in apps if a not in missing]:
        print(f"already installed: {app['id']}")

    apt_pkgs = []
    for app in [a for a in missing if a.get("manager") == "apt"]:
        # installScript is pkexec-wrapped for the GUI app; over SSH we batch
        # the raw package names into one sudo call instead.
        apt_pkgs += app["installScript"].split("apt-get install -y ")[1].split()
    if apt_pkgs:
        cmd = ["sudo", "apt-get", "install", "-y", *dict.fromkeys(apt_pkgs)]
        print("\n==>", " ".join(cmd))
        subprocess.run(cmd, check=True)

    for app in [a for a in missing if a.get("manager") == "deb"]:
        print(f"\n==> {app['id']} (.deb)")
        dest = download(app["downloadUrl"], app["downloadUrl"].rsplit("/", 1)[-1])
        subprocess.run(["sudo", "apt-get", "install", "-y", str(dest)], check=True)

    for app in [a for a in missing if a.get("manager") == "github"]:
        print(f"\n==> {app['id']} (latest GitHub release)")
        name, url = resolve_latest_asset(app["githubRepo"], app["assetPattern"])
        dest = download(url, name)
        dest.chmod(0o755)
        print(f"  ready to run: {dest}")

    skipped = [a for a in missing if a.get("manager") in ("manual", "script")]
    if skipped:
        print("\nnot automated (by design) — do these yourself if wanted:")
        for app in skipped:
            how = app.get("installScript") or app.get("url") or ""
            print(f"  {app['id']}: {app.get('notes', '')} {how}".rstrip())


if __name__ == "__main__":
    main()
