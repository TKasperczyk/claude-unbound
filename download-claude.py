#!/usr/bin/env python3
"""
Download any version of the Claude Code binary from Anthropic's public GCS bucket.

The bucket is referenced from the auto-updater in the prettified CLI
(`claude-code-dist-86c565f3-...`) and is publicly readable. It hosts every
release since 1.0.37 with per-platform binaries and signed manifests.

Usage:
    ./download-claude.py                     # interactive: list recent + prompt
    ./download-claude.py <version>           # direct download (e.g. 2.1.98)
    ./download-claude.py latest              # newest release
    ./download-claude.py stable              # current stable channel
    ./download-claude.py list                # print every version, exit
    ./download-claude.py --platform linux-arm64 <version>
    ./download-claude.py --output PATH <version>
"""
import argparse
import hashlib
import json
import platform
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

BUCKET = "claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819"
BASE_URL = f"https://storage.googleapis.com/{BUCKET}/claude-code-releases"
LIST_URL = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "binaries"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def fetch_json(url: str):
    return json.loads(urllib.request.urlopen(url, timeout=15).read())


def fetch_text(url: str) -> str:
    return urllib.request.urlopen(url, timeout=15).read().decode().strip()


def list_versions() -> list[str]:
    """Page through GCS listing, return sorted semver list (oldest first)."""
    url = f"{LIST_URL}?prefix=claude-code-releases/&delimiter=/"
    versions, token = [], None
    while True:
        page = fetch_json(url + (f"&pageToken={token}" if token else ""))
        for p in page.get("prefixes", []):
            name = p.split("/")[-2]
            if SEMVER_RE.match(name):
                versions.append(name)
        token = page.get("nextPageToken")
        if not token:
            break
    versions.sort(key=lambda v: tuple(int(x) for x in v.split(".")))
    return versions


def detect_platform() -> str | None:
    os_name = platform.system().lower()
    machine = platform.machine().lower()
    os_map = {"linux": "linux", "darwin": "darwin", "windows": "win32"}
    arch_map = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}
    o, a = os_map.get(os_name), arch_map.get(machine)
    if not o or not a:
        return None
    plat = f"{o}-{a}"
    # musl detection: musl linux systems ship ld-musl-<arch>.so.1
    if o == "linux":
        musl_ld = Path(f"/lib/ld-musl-{machine}.so.1")
        if musl_ld.exists():
            plat += "-musl"
    return plat


def fetch_manifest(version: str) -> dict:
    return fetch_json(f"{BASE_URL}/{version}/manifest.json")


def download(url: str, dest: Path, expected_size: int, expected_sha256: str) -> None:
    """Stream download with progress + SHA256 verification. Atomic via .part rename."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    sha = hashlib.sha256()
    downloaded = 0
    print(f"  {url}")
    print(f"  -> {dest}")
    with urllib.request.urlopen(url, timeout=30) as resp, open(tmp, "wb") as out:
        while chunk := resp.read(1 << 20):  # 1 MB
            out.write(chunk)
            sha.update(chunk)
            downloaded += len(chunk)
            pct = downloaded * 100 / expected_size if expected_size else 0
            print(
                f"\r  {downloaded/1024/1024:7.1f} / {expected_size/1024/1024:.1f} MB "
                f"({pct:5.1f}%)",
                end="",
                flush=True,
            )
    print()
    actual = sha.hexdigest()
    if actual != expected_sha256:
        tmp.unlink(missing_ok=True)
        sys.exit(f"  SHA256 mismatch!\n    expected: {expected_sha256}\n    got:      {actual}")
    tmp.rename(dest)
    dest.chmod(0o755)
    print(f"  SHA256 verified ✓")


def print_version_list(versions: list[str], latest: str, stable: str, tail: int = 15) -> None:
    print(f"Available: {len(versions)} versions ({versions[0]} .. {versions[-1]})")
    print(f"  latest = {latest}, stable = {stable}")
    print()
    shown = versions if tail >= len(versions) else versions[-tail:]
    for v in shown:
        tags = []
        if v == latest:
            tags.append("LATEST")
        if v == stable:
            tags.append("STABLE")
        suffix = f"  [{', '.join(tags)}]" if tags else ""
        print(f"  {v}{suffix}")
    if tail < len(versions):
        print(f"  ... ({len(versions)-tail} older not shown; pass 'list' to see all)")


def resolve_interactive(versions: list[str], latest: str, stable: str) -> str | None:
    print_version_list(versions, latest, stable, tail=15)
    print()
    while True:
        try:
            choice = input("Version [empty=latest, s=stable, l=list all, q=quit]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if choice in ("q", "quit"):
            return None
        if choice in ("", "latest"):
            return latest
        if choice in ("s", "stable"):
            return stable
        if choice in ("l", "list"):
            print_version_list(versions, latest, stable, tail=len(versions))
            print()
            continue
        if choice in versions:
            return choice
        print(f"  unknown: {choice!r} -- try again or 'q' to quit")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1], add_help=True)
    ap.add_argument("version", nargs="?",
                    help="Version (e.g. 2.1.114), 'latest', 'stable', or 'list'")
    ap.add_argument("--platform", dest="plat",
                    help="Platform override (default: auto-detect)")
    ap.add_argument("--output", type=Path,
                    help="Output path (default: binaries/<version>/<platform>/claude)")
    args = ap.parse_args()

    try:
        print("Fetching dist-tags...", file=sys.stderr)
        latest = fetch_text(f"{BASE_URL}/latest")
        stable = fetch_text(f"{BASE_URL}/stable")

        # `list` mode: just print and exit
        if args.version == "list":
            versions = list_versions()
            print_version_list(versions, latest, stable, tail=len(versions))
            return

        # Resolve version
        if args.version is None:
            versions = list_versions()
            resolved = resolve_interactive(versions, latest, stable)
            if resolved is None:
                return
        elif args.version == "latest":
            resolved = latest
        elif args.version == "stable":
            resolved = stable
        else:
            resolved = args.version
            if not SEMVER_RE.match(resolved):
                sys.exit(f"Not a valid semver: {resolved!r}")

        # Platform
        plat = args.plat or detect_platform()
        if not plat:
            sys.exit("Could not detect platform; pass --platform (e.g. linux-x64)")

        # Manifest
        print(f"\nFetching manifest for {resolved}...", file=sys.stderr)
        try:
            manifest = fetch_manifest(resolved)
        except urllib.error.HTTPError as e:
            sys.exit(f"No manifest for {resolved}: HTTP {e.code}")

        platforms = manifest.get("platforms", {})
        if plat not in platforms:
            sys.exit(f"Platform {plat!r} not available in {resolved}.\n"
                     f"  Available: {', '.join(sorted(platforms))}")
        meta = platforms[plat]
        binary_name = meta.get("binary") or ("claude.exe" if plat.startswith("win32") else "claude")

        # Output path
        if args.output:
            output = args.output
        else:
            output = DEFAULT_OUTPUT_DIR / resolved / plat / binary_name

        if output.exists():
            try:
                ans = input(f"{output} exists. Overwrite? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if ans not in ("y", "yes"):
                print("aborted")
                return

        url = f"{BASE_URL}/{resolved}/{plat}/{binary_name}"
        print(f"\nDownloading {resolved} ({plat}, {meta['size']/1024/1024:.1f} MB)")
        download(url, output, meta["size"], meta["checksum"])

        print(f"\nSaved: {output}")
        print(f"  version={manifest['version']}  built={manifest.get('buildDate', '?')}")
        print(f"\nNext steps:")
        print(f"  ./extract.sh {output}")
        print(f"  ./patch-context-dump.py")
    except urllib.error.URLError as e:
        sys.exit(f"Network error: {e}")
    except KeyboardInterrupt:
        print("\naborted")
        sys.exit(130)


if __name__ == "__main__":
    main()
