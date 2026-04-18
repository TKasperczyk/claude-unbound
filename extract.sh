#!/bin/bash
set -euo pipefail

BINARY="${1:-$(which claude)}"
BINARY="$(readlink -f "$BINARY")"
OUTDIR="$(cd "$(dirname "$0")" && pwd)"
CANDIDATE="$OUTDIR/.claude-app-source.tmp"

echo "Extracting from: $BINARY"

# Step 1: Carve out the main app module chunk + detect the embedded version string.
# The version is printed to stdout (for shell capture), diagnostics go to stderr.
# The 4-null upper bound is intentionally loose -- newer bun versions embed a few
# bytes of metadata between the JS source end and the null padding, so we trim
# precisely in step 2.
VERSION=$(python3 - "$BINARY" "$CANDIDATE" << 'PY'
import sys, re
binary_path, out_path = sys.argv[1], sys.argv[2]
with open(binary_path, 'rb') as f:
    data = f.read()
marker = b'// @bun @bytecode @bun-cjs\n(function(exports, require, module, __filename, __dirname) {// Claude Cod'
start = data.find(marker)
if start < 0:
    sys.exit('Could not find main app module marker')
end = data.find(b'\x00\x00\x00\x00', start)
if end < 0:
    sys.exit('Could not find end-of-chunk null run')
chunk = data[start:end]
with open(out_path, 'wb') as f:
    f.write(chunk)
print(f'Candidate chunk: {end-start} bytes at offset {start}', file=sys.stderr)

# Version detection: anchor on BUILD_TIME so we don't accidentally grab a bundled
# dependency's version string (several are present in the binary).
m = re.search(rb'VERSION:"(\d+\.\d+\.\d+(?:-[\w.]+)?)"[^}]{0,300}BUILD_TIME:', chunk)
if m:
    print(m.group(1).decode(), end='')
else:
    # Fallback: parse from binary path (~/.local/share/claude/versions/X.Y.Z/claude)
    p = re.search(r'/versions/(\d+\.\d+\.\d+(?:-[\w.]+)?)/', binary_path)
    if p:
        print(p.group(1), end='')
    else:
        sys.exit('Could not detect version from binary content or path')
PY
)

echo "Detected version: $VERSION"
OUTPUT="$OUTDIR/claude-app.pretty.v${VERSION}.js"

# Step 2: Trim to the true JS module end. The rightmost `)` that makes the prefix
# parse as valid JS is the close of the outer IIFE. Garbage bytes after it (seen
# in v2.1.114+) contain no `)`, so the first try typically succeeds.
bun run - "$CANDIDATE" << 'JS'
const fs = require('fs');
const path = process.argv[2];
const src = fs.readFileSync(path, 'utf8');
let end = -1;
for (let i = src.length - 1; i >= 0; i--) {
    if (src.charCodeAt(i) !== 41 /* ) */) continue;
    try { new Function(src.slice(0, i + 1)); end = i + 1; break; } catch (_) {}
}
if (end < 0) { console.error('could not find valid IIFE end'); process.exit(1); }
if (end !== src.length) {
    fs.writeFileSync(path, src.slice(0, end));
    console.log(`Trimmed ${src.length - end} trailing byte(s) -> ${end} bytes`);
} else {
    console.log(`No trailing garbage (${end} bytes)`);
}
JS

# Step 3: Beautify into version-stamped archive file.
echo "Beautifying..."
NODE_OPTIONS="--max-old-space-size=4096" js-beautify \
    -f "$CANDIDATE" \
    -o "$OUTPUT" \
    --type js

rm "$CANDIDATE"

LINES=$(wc -l < "$OUTPUT")
echo "Done: $(basename "$OUTPUT") ($LINES lines)"
