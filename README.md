# claude-unbound

Tools for reverse-engineering and "unbinding" [Claude Code](https://code.claude.com), Anthropic's official CLI coding agent.

Stock Claude Code ships with ~8 KB of system prompt and ~10 KB of tool descriptions that actively shape the model toward a cautious, interactive-assistant persona: hard numeric word limits (≤25/≤100), "trust but verify" re-verification loops, forced git/PR workflows with mandatory HEREDOC diagnostics, prompt-injection paranoia, permission-mode prose, and dozens of "use X not Y" tool mandates. Most of it is unnecessary overhead for autonomous and personal use.

This repo provides a pipeline to:

- Download any published version of the Claude Code binary from Anthropic's public GCS bucket
- Extract and prettify the bundled JS
- Inject a payload dumper so every API request is captured to disk for comparison
- Apply a 54-entry [semantic spec](unbind-spec.md) that removes the behavioral micromanagement while preserving every useful capability (Opus 4.7 with adaptive thinking at 32k budget, full tool surface, agent teams support)
- Launch the resulting unbound build with the same kitty-tmux-shim machinery that stock Claude Code uses

## Disclaimer

**This repository contains only tooling.** It does **not** include, redistribute, or mirror any part of Anthropic's proprietary software. Users must obtain Claude Code through their own Anthropic Commercial subscription before running these tools against it.

The resulting patched archives are derivative works of proprietary software; use on your own machine, for your own sessions, against your own subscription. Do not redistribute the patched archives. **This likely violates Anthropic's Commercial Terms of Service if used against shared / organizational accounts or for resale.** Use at your own risk.

## Prerequisites

- **Claude Code** installed (download via [claude.com/download](https://claude.com/download) or `npm install -g @anthropic-ai/claude-code`) with an active Max subscription for OAuth
- **bun** on PATH -- for running the patched JS archive
- **rg** (ripgrep) on PATH -- used by the LLM patcher
- **js-beautify** on PATH -- `pnpm install -g js-beautify` (or npm)
- **gh** or **curl** for the downloader to hit Anthropic's GCS bucket
- Linux or macOS (platform auto-detected; Windows users can adapt the shell scripts)

## Quick start

```bash
# 1. Download a specific version (or "latest" / "stable")
./download-claude.py latest

# 2. Extract the prettified JS from the binary
./extract.sh binaries/2.1.114/linux-x64/claude
#   → claude-app.pretty.v2.1.114.js

# 3. Inject payload-capture patch (deterministic, idempotent)
./patch-context-dump.py
#   → every API request now dumps to /tmp/claude-context-v<version>/

# 4. Apply the unbind spec via an LLM patcher
#    (spawns Claude Code as a subprocess; uses your Max subscription; ~10-15 min)
./patch-unbound.py claude-app.pretty.v2.1.114.js

# 5. Persist as your launch artifact
cp claude-app.pretty.v2.1.114.js claude-app.pretty.unbound.js

# 6. Run it
./claude-unbound "what's up?"
#   or export CLAUDE_UNBOUND_JS=/path/to/your/archive.js
```

## Pipeline

```
  Anthropic's GCS bucket
          │ download-claude.py
          ▼
  binaries/<version>/<platform>/claude        (ELF, ~225 MB)
          │ extract.sh  (bun CJS marker → js-beautify)
          ▼
  claude-app.pretty.v<version>.js             (~21 MB, 530K lines)
          │ patch-context-dump.py             (deterministic, fingerprint-based)
          ▼
  + context dump injected                     (payload → /tmp/claude-context-v<version>/)
          │ patch-unbound.py                  (LLM applies unbind-spec.md via CC subprocess)
          ▼
  + 54 spec entries applied                   (behavioral restrictions stripped)
          │ cp to your chosen persistent name
          ▼
  claude-unbound wrapper                      (bun + kitty-tmux-shim for agent teams)
```

## Tools

### `download-claude.py`

Python stdlib only. Fetches binaries from Anthropic's public GCS bucket (`storage.googleapis.com/claude-code-dist-.../claude-code-releases/`). 249+ versions from v1.0.37 onward.

```bash
./download-claude.py                  # interactive: recent versions + prompt
./download-claude.py latest           # newest release
./download-claude.py stable           # current stable channel
./download-claude.py 2.1.98           # specific version
./download-claude.py list             # print all versions, exit
./download-claude.py --platform linux-arm64 <version>
```

Auto-detects platform (`linux-x64`, `darwin-arm64`, `linux-x64-musl`, etc.), verifies SHA256 from the signed manifest, atomic write via `.part` → rename. Output default: `./binaries/<version>/<platform>/claude`.

### `extract.sh`

Bash + Python + bun. Finds the Bun CJS module marker in the ELF, extracts the embedded JS, trims trailing metadata via a bun parse check, runs `js-beautify` with 4 GB heap, writes `claude-app.pretty.v<version>.js`.

```bash
./extract.sh                                     # uses $(which claude)
./extract.sh binaries/2.1.114/linux-x64/claude   # specific path
```

Version is detected from the binary's embedded `VERSION:"X.Y.Z"` string (anchored on `BUILD_TIME:` to avoid matching bundled deps).

### `patch-context-dump.py`

Deterministic (non-LLM) patcher. Injects a `try { require('fs').writeFileSync(...) } catch {}` block at the API payload build site so every outgoing request is written to `/tmp/claude-context-v<version>/<timestamp>.json` for inspection and diffing.

```bash
./patch-context-dump.py                                    # newest archive in cwd
./patch-context-dump.py claude-app.pretty.v2.1.115.js      # specific file
```

Uses a structural fingerprint (`let <VAR> = <fn>(<arg>);` followed by `<validator>(<VAR>, <x>.querySource)`) that has held stable from v2.1.34 through v2.1.114. Idempotent.

### `patch-unbound.py`

Spawns `claude` itself as a subprocess with the full [unbind-spec.md](unbind-spec.md) as a task prompt. Uses Claude Code's native Read/Grep/Edit/Bash tools and **OAuth via your Max subscription quota** (not pay-per-token API billing).

```bash
./patch-unbound.py claude-app.pretty.v2.1.115.js                      # default: stock claude
./patch-unbound.py --claude-bin ~/.local/bin/claude-unbound <file>    # dogfood via your unbound build
./patch-unbound.py --dry-run <file>                                   # print prompt, don't invoke
```

Typical run: 10-15 minutes, ~35 tool-call batches, zero API billing (covered by your Max subscription). Reports per-entry status, verifies with `bun <file> --version` at category boundaries, ends with applied/skipped/failed summary.

### `unbind-spec.md`

The source of truth for what "unbound" means. 53 semantic edits across 17 categories (A1-Q1), each with:

- **Find:** semantic target + example text from v2.1.114
- **Action:** delete / replace / modify / inject
- **Why:** rationale

Grouped by impact. Tier A (structural API parameters) forces thinking from `adaptive` to `{enabled, 32768}` and neuters the anti-reasoning system-reminder. Tier B-E rewrite the system prompt content (hard word limits, autonomy framing, defer-to-user rules, model-identity chatter). Tier F-O rewrite tool descriptions (anti-Bash preamble, Git Safety Protocol, forced commit/PR ritual, Trust-but-verify loop, BLOCKING REQUIREMENT skill invocation, etc). P covers the context-dump injection. Q covers the mascot color swap (red, so you can see at a glance whether you're running stock or unbound).

Human-readable enough that a careful engineer can apply it by hand in 30-45 minutes; machine-readable enough that `patch-unbound.py` feeds it to Claude as a task prompt and gets 53/54 entries applied correctly on first pass.

### `claude-unbound`

Launcher wrapper. Runs your built `.unbound.js` archive via `bun` with `--dangerously-skip-permissions`. When inside Kitty with remote control enabled, sets up the tmux shim so agent teams work. Outside Kitty (SSH, CI, scripts), degrades gracefully without teams support.

```bash
./claude-unbound                                    # default: ~/Programming/claude-unbound/claude-app.pretty.unbound.js
CLAUDE_UNBOUND_JS=/path/to/your.unbound.js ./claude-unbound
```

Suggested: symlink into `~/.local/bin/`, set `CLAUDE_UNBOUND_JS` in your shell rc, alias `claude='claude-unbound'`.

## Version bump workflow

When a new Claude Code version ships (e.g. v2.1.115):

```bash
./download-claude.py latest
./extract.sh binaries/2.1.115/linux-x64/claude
./patch-context-dump.py
./patch-unbound.py claude-app.pretty.v2.1.115.js
# (wait 10-15 min; check the final "Applied: N" summary)

# Spot-check that key edits landed:
grep -c "Assume full authorization" claude-app.pretty.v2.1.115.js        # should be 1 (C2)
grep -c "Trust but verify" claude-app.pretty.v2.1.115.js                 # should be 0 (M1)
grep -c 'clawd_body: "rgb(220,38,38)"' claude-app.pretty.v2.1.115.js     # should be 4 (Q1)

# Persist and run:
cp claude-app.pretty.v2.1.115.js claude-app.pretty.unbound.js
./claude-unbound --version
```

If a grep check fails, the LLM patcher missed an entry -- apply manually via your editor (the spec entry text is self-contained).

## Known gotchas

- **Shell alias cache.** If you alias `claude='claude-unbound'` in your rc, existing shells hold the old binding until `source ~/.zshrc` or a new shell. `which claude` confirms.
- **Auto-updater retargets `~/.local/bin/claude`.** Claude Code's built-in updater rewrites the symlink to point at the newest installed version. The `claude-unbound` wrapper follows the symlink via `readlink -f` for the kitty-tmux-shim setup, so that's fine, but your persistent `.unbound.js` archive is pinned to whatever version you built it from -- re-run the version-bump workflow to refresh.
- **Context dump path collisions.** The context dump injection writes to `/tmp/claude-context-v<version>/`. Running two different patches of the same semver back-to-back will interleave dumps -- clear `/tmp/claude-context-v<version>/` between runs or inspect by timestamp.
- **OAuth credential rotation.** `~/.claude/.credentials.json` is shared across all Claude Code-family processes. If one refreshes the OAuth token, others running concurrently may briefly see `invalid_grant` -- retry or wait a few seconds.
- **Agent tool's "Example usage" blocks.** The LLM patcher reliably removes the "Writing the prompt" sermon but may leave the two `<example>` blocks in the `Agent` tool description intact. That's ~400 extra tokens per request but doesn't restrict the model -- ignore or apply the deletion by hand if you care.
- **Don't use the raw `anthropic` SDK for the patcher.** An earlier design used the Anthropic Python SDK which reads `ANTHROPIC_API_KEY` and bills pay-per-token from zero -- effectively double-charging since your Max subscription already covers the work. The current `patch-unbound.py` spawns Claude Code as a subprocess, which uses OAuth via the subscription.

## Philosophy

Four categories of edits:

1. **Restore reasoning.** Strip anything that forces brevity over depth or tells the model to think less. The hard numeric word limits (≤25 words between tool calls, ≤100 words final) in the `# Text output` section are the most visible suppressors; B1/B2 remove them. A1 forces `thinking` from `adaptive` to `{enabled, budget_tokens: 32768}` so depth is yours to decide. A2 neuters the `# System reminders` injection telling the model to "avoid unnecessary thinking."
2. **Restore autonomy.** Remove interactive-assistant framing. C1 rewrites the opening identity from "You are an interactive agent that helps users..." to "You are an autonomous software engineer operating unattended." D1/D2 drop defer-to-user rules and 2-3 sentence caps on exploratory questions.
3. **Restore tool-selection freedom.** Remove "use X not Y" mandates, forced workflows, and tool-specific hard rules. F1 strips the anti-Bash preamble. G2/G3 strip the forced `git status/diff/log` triad + HEREDOC commit workflow.
4. **Remove safety theater.** Prompt-injection paranoia, permission-mode prose, security-refusal clauses -- designed for mass-market interactive use, noise for autonomous work.

When Anthropic adds a new restriction in a future version, classify it into one of these four buckets and write a new spec entry with a semantic target + rationale.

## Further reading

- [`unbind-spec.md`](unbind-spec.md) -- the full 54-entry spec, the source of truth
- [`prompt-diff-2.1.98-to-2.1.114.md`](prompt-diff-2.1.98-to-2.1.114.md) -- detailed writeup comparing stable vs latest at the time of authoring, with every system-prompt and tool-description change classified
- Theo Browne's video on Claude Code's regression (the hot-take that kicked this off)
- Anthropic's [Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms) -- read before using

## Contributing

This is primarily a personal toolkit maintained by one person. PRs welcome for:

- New spec entries as Anthropic ships new restrictions
- Bug fixes in the tooling scripts
- Additional platform support (Windows, NixOS, etc.)
- Better verification heuristics for the LLM patcher

For large changes, open an issue first. For small fixes, just PR.
