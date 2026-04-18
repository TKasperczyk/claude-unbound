#!/usr/bin/env python3
"""
Patch the context-dump injection into a prettified Claude Code archive.

Finds the payload-build site via a structural fingerprint that has held stable
across v2.1.34 -> v2.1.44 -> v2.1.114, then inserts a dump line immediately
after it. Idempotent: skips files already patched.

Usage:
    ./patch-context-dump.py                 # patch the highest-version archive
    ./patch-context-dump.py <file.js>       # patch a specific file
"""
import argparse
import pathlib
import re
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ARCHIVE_GLOB = "claude-app.pretty.v*.js"
ARCHIVE_RE = re.compile(r"^claude-app\.pretty\.v(\d+\.\d+\.\d+(?:-[\w.]+)?)\.js$")

# Structural fingerprint of the payload-build site:
#   let <VAR> = <fn>(<arg>);
#   ... (<=600 chars, possibly including our own patch) ...
#   <validator>(<VAR>, <x>.querySource)
#
# The `<VAR>, <x>.querySource` validator call is near-unique in the bundle --
# it's how Anthropic tags the payload with its originating query source before
# dispatch. This double-anchor (same-var reuse) nails down one site unambiguously
# across all observed versions.
SITE_RE = re.compile(
    r"let\s+(?P<var>[A-Za-z_$][\w$]*)\s*=\s*[A-Za-z_$][\w$]*\([^)]*\);"
    r".{0,600}?"
    r"[A-Za-z_$][\w$]*\(\s*(?P=var)\s*,\s*[A-Za-z_$][\w$]*\.querySource\s*\)",
    re.DOTALL,
)

ALREADY_PATCHED_MARKER = "/tmp/claude-context-"  # catches both legacy and versioned formats


def pick_archive() -> pathlib.Path:
    """Return the archive with the highest semver among claude-app.pretty.v*.js."""
    candidates = []
    for p in SCRIPT_DIR.glob(ARCHIVE_GLOB):
        m = ARCHIVE_RE.match(p.name)
        if m:
            version_tuple = tuple(int(x) for x in m.group(1).split("-")[0].split("."))
            candidates.append((version_tuple, p))
    if not candidates:
        sys.exit(f"No archives matching {ARCHIVE_GLOB} found in {SCRIPT_DIR}")
    candidates.sort()
    return candidates[-1][1]


def extract_version(path: pathlib.Path) -> str:
    m = ARCHIVE_RE.match(path.name)
    if not m:
        sys.exit(f"Cannot parse version from filename: {path.name}")
    return m.group(1)


def find_injection_point(src: str):
    """Return (end_offset_of_let_statement, payload_var_name, indent_str)."""
    matches = list(SITE_RE.finditer(src))
    if not matches:
        sys.exit("Payload-build site not found -- fingerprint may need updating")
    if len(matches) > 1:
        sys.exit(f"Ambiguous: {len(matches)} sites matched, expected exactly 1")
    m = matches[0]
    # End of the `let X = fn(arg);` statement (position after the `;`)
    let_end = src.index(";", m.start()) + 1
    # Detect the indentation used on the `let` line so our injection matches
    line_start = src.rfind("\n", 0, m.start()) + 1
    indent = src[line_start:m.start()]
    return let_end, m.group("var"), indent


def build_patch_line(var: str, version: str, indent: str) -> str:
    return (
        f"\n{indent}try {{ let _fs = require('fs'), _d = '/tmp/claude-context-v{version}'; "
        f"_fs.mkdirSync(_d, {{ recursive: true }}); "
        f"_fs.writeFileSync(_d + '/' + Date.now() + '.json', "
        f"JSON.stringify({var}, null, 2)); }} catch {{}}"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", nargs="?", type=pathlib.Path,
                    help="Archive to patch (default: highest-version claude-app.pretty.v*.js)")
    args = ap.parse_args()

    target = args.file.resolve() if args.file else pick_archive()
    if not target.exists():
        sys.exit(f"File not found: {target}")

    version = extract_version(target)
    src = target.read_text()

    if ALREADY_PATCHED_MARKER in src:
        print(f"{target.name}: already patched (contains {ALREADY_PATCHED_MARKER!r})")
        return

    insert_at, var, indent = find_injection_point(src)
    patch = build_patch_line(var, version, indent)
    new_src = src[:insert_at] + patch + src[insert_at:]

    target.write_text(new_src)
    line_no = src[:insert_at].count("\n") + 1
    print(f"{target.name}: patched after line {line_no} "
          f"(payload_var={var}, version={version})")


if __name__ == "__main__":
    main()
