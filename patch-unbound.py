#!/usr/bin/env python3
"""
patch-unbound.py -- LLM-assisted applier for unbind-spec.md.

Spawns the Claude Code binary (stock `claude` by default, or any variant you
specify) in non-interactive mode with a task prompt containing the full spec
and target path. CC runs its own agent loop using its native Read/Grep/Edit/
Bash tools, inheriting OAuth auth from your Max subscription — no per-token
API billing.

Why NOT the raw `anthropic` SDK:
  The raw SDK reads ANTHROPIC_API_KEY and bills pay-per-token from zero.
  Since Claude Code already holds your OAuth credentials in ~/.claude/ and
  bills against your Max subscription quota, spawning CC as a subprocess
  costs nothing extra. Bonus: we inherit CC's native tools and agent loop.

Prereqs:
  - `claude` binary on PATH (or pass --claude-bin to point at claude-unbound,
    claude-teams, or any wrapper)
  - `bun` on PATH (for in-loop syntax verification via `bun <file> --version`)
  - OAuth credentials at ~/.claude/.credentials.json (already set up if you
    use CC normally)

Usage:
  ./patch-unbound.py claude-app.pretty.v2.1.115.js
  ./patch-unbound.py --claude-bin ~/.local/bin/claude-unbound <file>   # dogfood
  ./patch-unbound.py --dry-run <file>                                  # print prompt, don't invoke

Streams CC's JSON events to stdout for live visibility. Exit code matches CC's.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SPEC_PATH = SCRIPT_DIR / "unbind-spec.md"


def build_prompt(target: Path, spec_text: str) -> str:
    return f"""# Task

Apply the Unbind Spec (below) to this file:

    {target}

It's a prettified JS archive (~530K lines, ~21MB) extracted from a Claude Code binary.

# Workflow per spec entry

For each entry A1..Q1 in **spec order**:

1. **Locate.** Grep for a stable keyword from the entry's "Find" example. Variable names (sI1, Ab1, Vd7, Kb1, fb1, etc.) WILL have rotated in newer versions — find the semantic equivalent using structural anchors (function shape, stable keywords, surrounding context). The spec's "Why" section tells you what INTENT each entry targets; use it when the literal text has drifted.
2. **Verify.** Read 30-60 lines around the match to confirm it's the right semantic target before editing. For a 530K-line file, never read without offset.
3. **Apply.** Edit with a unique old_string — include surrounding context if needed for uniqueness. Use replace_all only when the spec explicitly calls for it (e.g. Q1 mascot color across multiple theme blocks).
4. **Status line.** Emit exactly one line per entry in this format:
   - `A1: patched (Vd7 → return void 0)` — edit succeeded
   - `A1: SKIPPED (already applied)` — target is already in post-edit state
   - `A1: FAILED (couldn't locate semantic equivalent)` — couldn't find; move on

# Version drift — the critical skill

The spec was authored against v2.1.114. In a newer archive, variable names will be different, function boundaries may have shifted, and some entries may have been pre-obsoleted by Anthropic's own changes. Your job is semantic equivalence, not literal text match:

- If spec says "remove the `# Tone and style` block returned by `wb1()`", and `wb1` is now `iw2`, find the function that returns `# Tone and style` and flip it to return null.
- If an entry's intent (from "Why") is already achieved upstream, mark SKIPPED and move on. Don't re-apply.

# Verification cadence

After every 8-10 edits, or at category boundaries (after all A entries, after all B entries, etc.), run `bun {target} --version` via Bash. If it fails, the most recent edit broke something — locate it via the error's line number and fix before proceeding.

# Final summary

When A1..Q1 are all processed, emit:
- Applied: N
- Skipped: M
- Failed: K (list IDs + reasons)

Then run these via Bash:
- `bun {target} --version` (one last time)
- `git diff --stat {target.name}` (if the file is tracked; shows magnitude of change)

# Style

- Be methodical. Work through the spec in spec order.
- Status lines only. No prose narration of reasoning.
- Batch independent tool calls in one response where useful (e.g., multiple greps for different entries in one go, then process the results).
- Don't ask clarifying questions. The spec is the source of truth; apply it.

---

# UNBIND SPEC

{spec_text}
"""


def handle_event(event: dict) -> None:
    """Print one stream-json event from Claude Code in a readable form."""
    t = event.get("type")
    if t == "assistant":
        for block in event.get("message", {}).get("content", []):
            bt = block.get("type")
            if bt == "text":
                text = block.get("text", "").strip()
                if text:
                    print(text)
            elif bt == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {}) or {}
                # Show first few input keys + a short preview of the first value
                keys = list(inp.keys())[:3]
                preview = ""
                if keys:
                    v = inp[keys[0]]
                    if isinstance(v, str):
                        preview = f"={v[:60]!r}" + ("..." if len(v) > 60 else "")
                print(f"  → {name}({','.join(keys)}{preview})", file=sys.stderr)
    elif t == "user":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                # Suppress tool results by default — they're huge and noisy
                pass
    elif t == "result":
        cost = event.get("total_cost_usd")
        duration = event.get("duration_ms")
        if cost is not None:
            print(f"\n[cost: ${cost:.4f}  duration: {duration/1000:.1f}s]" if duration
                  else f"\n[cost: ${cost:.4f}]", file=sys.stderr)
        if event.get("is_error"):
            print(f"[error: {event.get('subtype', 'unknown')}]", file=sys.stderr)
    elif t == "system":
        # Initialization event; skip
        pass


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("target", help="Prettified archive path (e.g. claude-app.pretty.v2.1.115.js)")
    ap.add_argument("--claude-bin", default="claude",
                    help="Claude Code binary to invoke (default: 'claude' on PATH)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the prompt and exit without invoking Claude")
    args = ap.parse_args()

    target = Path(args.target).resolve()
    if not target.exists():
        sys.exit(f"target not found: {target}")
    if not SPEC_PATH.exists():
        sys.exit(f"spec not found: {SPEC_PATH}")

    spec_text = SPEC_PATH.read_text()
    prompt = build_prompt(target, spec_text)

    if args.dry_run:
        print(prompt)
        return

    cmd = [
        args.claude_bin,
        "-p", prompt,
        "--allowed-tools", "Read Grep Edit Bash",
        "--output-format", "stream-json",
        "--verbose",  # required alongside stream-json in --print mode
        "--include-partial-messages",
        "--no-session-persistence",
        "--dangerously-skip-permissions",
    ]

    print(f"target:   {target}", file=sys.stderr)
    print(f"claude:   {args.claude_bin}", file=sys.stderr)
    print(f"spec:     {len(spec_text)} chars (~{len(spec_text)//4} tokens)", file=sys.stderr)
    print(f"prompt:   {len(prompt)} chars (~{len(prompt)//4} tokens)", file=sys.stderr)
    print("---", file=sys.stderr)

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)
    except FileNotFoundError:
        sys.exit(f"claude binary not found: {args.claude_bin}")

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Non-JSON line — print as-is
                print(line)
                continue
            handle_event(event)
    except KeyboardInterrupt:
        proc.terminate()
        print("\n[interrupted]", file=sys.stderr)
        sys.exit(130)

    rc = proc.wait()
    stderr = proc.stderr.read()
    if rc != 0:
        print(f"claude exited {rc}", file=sys.stderr)
        if stderr.strip():
            print(stderr.strip(), file=sys.stderr)
    sys.exit(rc)


if __name__ == "__main__":
    main()
