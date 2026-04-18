# Claude Code prompt diff: v2.1.98 (stable) → v2.1.114 (latest)

*Comparison focuses on anything that could influence Claude's behavior: system prompt content, tool inventory, tool descriptions, and model identity. Wire-level metadata (billing headers, user IDs, beta flags) is excluded unless it implies behavioral change.*

## Methodology

Both versions were run with an identical prompt (`-p "hi"`) under OAuth auth, with a context-dump patch inserted at the API payload build site (`let <VAR> = <builder>(<arg>);` → dump `<VAR>` before dispatch). The dumped JSON is the exact request that went to Anthropic's `/beta/messages` endpoint, so everything compared below is the **actual live prompt**, not the source template.

- v2.1.98 dump: `/tmp/claude-context-v2.1.98/*.json` (62 KB)
- v2.1.114 dump: `/tmp/claude-context-v2.1.114/*.json` (67 KB)

---

## TL;DR

1. **Model upgrade:** Opus 4.6 → 4.7, knowledge cutoff May 2025 → January 2026.
2. **One new tool:** `ScheduleWakeup` -- autonomous-pacing primitive for `/loop` dynamic mode with explicit prompt-cache-aware delay guidance.
3. **One sub-agent removed** from the `Agent` tool: `claude-code-guide` (the self-help agent for CC / SDK / API questions).
4. **System prompt pivots from "be concise" prose to hard numeric limits:** ≤25 words between tool calls, ≤100 words for final responses (unless the task requires more).
5. **New "Text output" section** in block 3 shifts Claude from "be brief" to an explicit communication contract: announce intent before each tool call, narrate state changes, avoid internal-deliberation narration, write no code comments by default.
6. **Anti-hallucination reinforcement** on Skill (*"never guess or invent a skill name from training data"*) and on agent result reporting (*"trust but verify -- an agent's summary describes what it intended to do, not necessarily what it did"*).

---

## 1. Model & knowledge baseline

| Field | v2.1.98 | v2.1.114 |
|---|---|---|
| Model | `claude-opus-4-6` | `claude-opus-4-7` |
| Advertised identity | `Opus 4.6 (with 1M context)` | `Opus 4.7 (1M context)` |
| Exact ID shown to Claude | `claude-opus-4-6[1m]` | `claude-opus-4-7[1m]` |
| Knowledge cutoff | May 2025 | January 2026 |
| Most recent family | `Claude 4.6 and 4.5` | `Claude 4.X` |
| `/fast` semantics | "uses the same Claude Opus 4.6 model with faster output" | "uses Claude Opus 4.6 with faster output ... **only available on Opus 4.6**" |

The `/fast` note is behavior-relevant: on 4.7, fast mode is no longer available, so Claude should stop recommending it unconditionally. `claude-haiku-4-5-20251001` and `claude-sonnet-4-6` are unchanged in both versions.

---

## 2. Tool inventory

### Added in 2.1.114: `ScheduleWakeup`

The headline new tool. Full description (paraphrased structure, exact quotes preserved):

> Schedule when to resume work in /loop dynamic mode -- the user invoked /loop without an interval, asking you to self-pace iterations of a specific task.

Schema: `{ delaySeconds: number, reason: string, prompt: string }`, with `delaySeconds` clamped to `[60, 3600]` by the runtime.

The description spends substantial ink on **prompt-cache economics** as a load-bearing behavioral lever:

> The Anthropic prompt cache has a 5-minute TTL. Sleeping past 300 seconds means the next wake-up reads your full conversation context uncached -- slower and more expensive.
>
> - **Under 5 minutes (60s--270s)**: cache stays warm. Right for active work.
> - **5 minutes to 1 hour (300s--3600s)**: pay the cache miss. Right when there's no point checking sooner.
>
> **Don't pick 300s.** It's the worst-of-both: you pay the cache miss without amortizing it.

And a default recommendation for "idle tick" cases: **1200s--1800s (20--30 min)**.

Also introduces two runtime sentinels (`<<autonomous-loop-dynamic>>`, `<<autonomous-loop>>`) with an explicit warning not to confuse them. These implies a separate `CronCreate` autonomous loop mechanism exists alongside dynamic `/loop`, though `CronCreate` isn't in the tool list -- presumably a deferred tool loaded via `ToolSearch`.

**Behavioral implication:** Claude Code now has a first-class way to sit idle and resume, rather than polling or blocking on a sleep. The cache-aware guidance is unusually explicit pricing-aware prompting -- rare to see this surfaced to the model directly.

### Removed from 2.1.114: `claude-code-guide` sub-agent

Present in 2.1.98's `Agent` tool options:

> - claude-code-guide: Use this agent when the user asks questions ("Can Claude...", "Does Claude...", "How do I...") about: (1) Claude Code (the CLI tool) - features, hooks, slash commands, MCP servers, settings, IDE integrations, keyboard shortcuts; (2) Claude Agent SDK - building custom agents; (3) Claude API (formerly Anthropic API) - API usage, tool use, Anthropic SDK usage. **IMPORTANT:** Before spawning a new agent, check if there is already a running or recently completed claude-code-guide agent that you can continue via SendMessage. (Tools: Glob, Grep, Read, WebFetch, WebSearch)

Gone entirely in 2.1.114. No replacement sub-agent mentioned. This routes CC/SDK/API meta-questions back to the main thread (or into a general `Explore` / `Plan` agent), which will change how Claude Code handles "how do I use feature X" asks -- previously a dedicated specialist, now handled in-band.

### Tools with identical descriptions across both versions

`Edit`, `Glob`, `Grep`, `Read`, `ToolSearch`, `Write`. Tool schemas for these are byte-identical.

---

## 3. Tool description changes

### `Agent` -- sub-agent roster and concurrency guidance

**Sub-agent roster:**

| Agent | v2.1.98 | v2.1.114 |
|---|---|---|
| `general-purpose` | ✓ | ✓ (unchanged) |
| `statusline-setup` | ✓ | ✓ (unchanged) |
| `Explore` | ✓ | ✓ (unchanged) |
| `Plan` | ✓ | ✓ (unchanged) |
| `claude-code-guide` | ✓ | **removed** |

**Usage-notes changes:**

Concurrency guidance softened in phrasing but identical in intent:
- v2.1.98: *"Launch multiple agents concurrently whenever possible, to maximize performance"*
- v2.1.114: *"When you launch multiple agents for independent work, send them in a single message with multiple tool uses so they run concurrently"*

**New caveat added** (behavior-critical):

> Trust but verify: an agent's summary describes what it intended to do, not necessarily what it did. When an agent writes or edits code, check the actual changes before reporting the work as done.

This is a direct response to the failure mode where a sub-agent reports "I fixed X" but didn't actually persist the change. Claude Code now explicitly asks the main agent to verify agent output before relaying success to the user.

### `Bash` -- git and sleep rules tightened

**Git working-directory rule -- added:**

> In particular, never prepend `cd <current-directory>` to a `git` command -- `git` already operates on the current working tree, and the compound triggers a permission prompt.

Practical: fewer permission prompts for `cd <cwd> && git foo` patterns, but requires Claude to have internalized that git doesn't need cwd reassertion.

**Sleep rule -- replaced and expanded:**

- v2.1.98: `sleep N as the first command with N ≥ 2 is blocked. If you need a delay (rate limiting, deliberate pacing), keep it under 2 seconds.`
- v2.1.114: `Long leading sleep commands are blocked. To poll until a condition is met, use Monitor with an until-loop (e.g. until <check>; do sleep 2; done) -- you get a notification when the loop exits. Do not chain shorter sleeps to work around the block.`

The new rule points at a `Monitor` tool (deferred, loaded via `ToolSearch`) and explicitly closes the workaround of chaining short sleeps. Coupled with `ScheduleWakeup`, this removes Bash-level sleeping as a waiting primitive entirely.

### `Skill` -- anti-hallucination hardening

The Skill tool tightens its trust boundary:

**Added:**

> Only invoke a skill that appears in that list, or one the user explicitly typed as `/<name>` in their message. Never guess or invent a skill name from training data; otherwise do not call this tool.

**Removed** (redundant examples):

> - `skill: "pdf"` - invoke the pdf skill
> - `skill: "commit", args: "-m 'Fix bug'"` - invoke with arguments
> - `skill: "review-pr", args: "123"` - invoke with arguments
> - `skill: "ms-office-suite:pdf"` - invoke using fully qualified name

**Rephrased** for precision:

- Before: *"Use this tool with the skill name and optional arguments"*
- After: *"Set `skill` to the exact name of an available skill (no leading slash). For plugin-namespaced skills use the fully qualified `plugin:skill` form."*

---

## 4. System prompt block-by-block

Each request carries 4 system blocks. Roles:

| Block | Role | v2.1.98 | v2.1.114 | Δ |
|---|---|---|---|---|
| 0 | Anthropic billing header | 80 chars | 85 chars | metadata only |
| 1 | Claude Agent SDK identity preamble | 62 chars | 62 chars | identical |
| 2 | Core operating instructions + tool usage + tone | 8421 chars | 6678 chars | **-1743 chars** |
| 3 | Session-specific guidance + environment + output style + git context | 5736 chars | 6704 chars | **+968 chars** |

Blocks 0 and 1 are noise for this purpose. Blocks 2 and 3 are where the behavioral rewrite lives.

### 4a. Block 2 -- removed in 2.1.114

Block 2 shrank by ~20%. Three coherent deletions:

**Removed: verbose "don't use Bash" enumeration**

The v2.1.98 block had a 7-line breakdown:

> - Do NOT use the Bash to run commands when a relevant dedicated tool is provided. Using dedicated tools allows the user to better understand and review your work. This is CRITICAL to assisting the user:
>  - To read files use Read instead of cat, head, tail, or sed
>  - To edit files use Edit instead of sed or awk
>  - To create files use Write instead of cat with heredoc or echo redirection
>  - To search for files use Glob instead of find or ls
>  - To search the content of files, use Grep instead of grep or rg
>  - Reserve using the Bash exclusively for system commands and terminal operations that require shell execution...
> - Break down and manage your work with the TodoWrite tool. These tools are helpful for planning your work and helping the user track your progress. Mark each task as completed as soon as you are done with the task. Do not batch up multiple tasks before marking them as completed.

Compressed in 2.1.114 to:

> - Prefer dedicated tools over Bash when one fits (Read, Edit, Write, Glob, Grep) -- reserve Bash for shell-only operations.
> - Use TodoWrite to plan and track work. Mark each task completed as soon as it's done; don't batch.

Same semantic content, ~90% shorter. Anthropic is betting the model internalizes the rule without the per-tool enumeration.

**Removed: GitHub issue formatting rule**

> When referencing GitHub issues or pull requests, use the owner/repo#123 format (e.g. anthropics/claude-code#100) so they render as clickable links.

Gone in 2.1.114. Claude will still *do* this from training, but it's no longer a system-level injunction. If you see Claude stop formatting GH refs this way, this is why.

**Removed: entire "Output efficiency" section**

```
# Output efficiency

IMPORTANT: Go straight to the point. Try the simplest approach first without going in circles. Do not overdo it. Be extra concise.

Keep your text output brief and direct. Lead with the answer or action, not the reasoning. Skip filler words, preamble, and unnecessary transitions. Do not restate what the user said — just do it. When explaining, include only what is necessary for the user to understand.

Focus text output on:
- Decisions that need the user's input
- High-level status updates at natural milestones
- Errors or blockers that change the plan

If you can say it in one sentence, don't use three. Prefer short, direct sentences over long explanations. This does not apply to code or tool calls.
```

This section was essentially vibes-based brevity guidance. It's been **replaced and hardened** in block 3 (see 4b) with explicit numeric limits. Semantically related, but the new version is sharper and measurable.

### 4b. Block 3 -- added in 2.1.114

Block 3 gained a new top-level heading that runs before the existing `# Session-specific guidance`:

```
# Text output (does not apply to tool calls)
```

This is the most behaviorally significant addition in the whole diff. Full text:

> Assume users can't see most tool calls or thinking -- only your text output. Before your first tool call, state in one sentence what you're about to do. While working, give short updates at key moments: when you find something, when you change direction, or when you hit a blocker. Brief is good -- silent is not. One sentence per update is almost always enough.
>
> Don't narrate your internal deliberation. User-facing text should be relevant communication to the user, not a running commentary on your thought process. State results and decisions directly, and focus user-facing text on relevant updates for the user.
>
> When you do write updates, write so the reader can pick up cold: complete sentences, no unexplained jargon or shorthand from earlier in the session. But keep it tight -- a clear sentence is better than a clear paragraph.
>
> End-of-turn summary: one or two sentences. What changed and what's next. Nothing else.
>
> Match responses to the task: a simple question gets a direct answer, not headers and sections.
>
> In code: default to writing no comments. Never write multi-paragraph docstrings or multi-line comment blocks -- one short line max. Don't create planning, decision, or analysis documents unless the user asks for them -- work from conversation context, not intermediate files.

**Decomposed behavioral contract:**

1. **Pre-tool-call announcement (new, hard rule):** *"Before your first tool call, state in one sentence what you're about to do."* v2.1.98 had no such rule -- a Claude that went straight to tools with no narration was fine. 2.1.114 Claude must announce.
2. **In-flight updates (new, hard rule):** *"Brief is good -- silent is not."* Also new. v2.1.98 had "short and concise"; v2.1.114 explicitly requires presence at state transitions (find / change direction / blocker).
3. **Anti-narration (new, hard rule):** *"Don't narrate your internal deliberation."* Novel -- v2.1.98 had no explicit prohibition on thinking-narration, which is why older Claude versions sometimes leaked their chain-of-thought into user-visible text.
4. **End-of-turn summary (new, hard spec):** *"One or two sentences. What changed and what's next. Nothing else."* Very specific -- Anthropic is trying to standardize session close-out.
5. **Format-per-task-complexity:** *"A simple question gets a direct answer, not headers and sections."* Same content as v2.1.98's `Output efficiency`, now with one line instead of a paragraph.
6. **No-comment code default (new, strong opinion):** *"default to writing no comments. Never write multi-paragraph docstrings or multi-line comment blocks -- one short line max."* This is a significant shift. Older Claude versions were comment-heavy; 2.1.114 pushes hard the other way.
7. **No-planning-docs default (new):** *"Don't create planning, decision, or analysis documents unless the user asks for them -- work from conversation context, not intermediate files."* Prevents unsolicited `PLAN.md`/`NOTES.md` spam.

And later in the block, the payoff:

> Length limits: keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless the task requires more detail.

**These are hard numeric limits**, replacing v2.1.98's qualitative *"short and concise"*. The model now has a target to hit.

### 4c. Block 3 -- other tweaks

**`AskUserQuestion` fallback -- removed:**

> - If you do not understand why the user has denied a tool call, use the AskUserQuestion to ask them.

This line existed in 2.1.98. Gone in 2.1.114. Claude will now have to infer denial reasons itself or ask via plain text.

**Explore guidance -- compressed:**

- v2.1.98 (two bullets):
  > - For simple, directed codebase searches (e.g. for a specific file/class/function) use the Glob or Grep directly.
  > - For broader codebase exploration and deep research, use the Agent tool with subagent_type=Explore. This is slower than using the Glob or Grep directly, so use this only when a simple, directed search proves to be insufficient or when your task will clearly require more than 3 queries.
- v2.1.114 (one bullet):
  > - For broad codebase exploration or research that'll take more than 3 queries, spawn Agent with subagent_type=Explore. Otherwise use the Glob or Grep directly.

Same 3-query threshold, half the words.

**Skill invocation -- compressed, same rule:**

- v2.1.98: *"`/<skill-name>` (e.g., /commit) is shorthand for users to invoke a user-invocable skill. When executed, the skill gets expanded to a full prompt. Use the Skill tool to execute them. IMPORTANT: Only use Skill for skills listed in its user-invocable skills section - do not guess or use built-in CLI commands."*
- v2.1.114: *"When the user types `/<skill-name>`, invoke it via Skill. Only use skills listed in the user-invocable skills section -- don't guess."*

---

## 5. What did NOT change

Explicitly verified identical between dumps (so you know I looked):

- **Beta feature flags** -- 9 flags, byte-identical: `claude-code-20250219`, `oauth-2025-04-20`, `context-1m-2025-08-07`, `interleaved-thinking-2025-05-14`, `context-management-2025-06-27`, `prompt-caching-scope-2026-01-05`, `advisor-tool-2026-03-01`, `advanced-tool-use-2025-11-20`, `effort-2025-11-24`
- **`max_tokens`**: 65536 both versions
- **`context_management`**: `clear_thinking_20251015` with `keep: all` -- unchanged
- **`output_config`**: `effort: high` -- unchanged
- **`thinking`**: `type: adaptive` -- unchanged
- **Block 1** (Agent SDK preamble): identical
- **Security-testing allowance rule**, **URL-guessing prohibition**, **`# System` rules block** (markdown rendering, permission modes, system-reminder tags, prompt-injection detection, hooks, context auto-compaction): all identical
- **`# Executing actions with care`** (entire reversibility/blast-radius section with examples of risky actions): identical
- **Tool schemas** (input/output JSON schemas) for every tool except the new `ScheduleWakeup`: identical

The "don't do risky things without asking" and "security scenarios" rules are unchanged -- the safety surface is stable; only the productivity/style surface moved.

---

## 6. Behavioral implications summary

If you're tracking how Claude Code's personality is evolving, 2.1.114 is a clear pivot on three axes:

### a. From descriptive to prescriptive output style

v2.1.98 told Claude to "be brief." v2.1.114 tells Claude: announce before each tool call, update at state transitions, stay silent about internal thoughts, hit ≤25 words between calls, cap at 100 words final, write no comments. The style is now a measurable contract, not a vibe. Expect 2.1.114 Claude to feel **more structured but also more talkative at key moments** -- no more silent tool-chains.

### b. From polling to scheduling

Sleep in Bash is deprecated as a waiting primitive. `Monitor` handles "wait for condition". `ScheduleWakeup` handles "come back later". Both are cache-aware. This implies Anthropic wants Claude Code to make fewer long-running blocking calls and more ambient, resumable work -- the cost model *("Don't pick 300s")* is surfaced to the model directly, which is unusual.

### c. Anti-hallucination reinforcements

Two places where 2.1.114 explicitly warns against confident-but-wrong behavior:
1. `Skill`: *"Never guess or invent a skill name from training data"* -- closes the loop where Claude would try `/deploy` because `/deploy` exists in some training corpus even if it's not registered locally.
2. `Agent`: *"Trust but verify -- an agent's summary describes what it intended to do, not necessarily what it did"* -- closes the loop where a sub-agent's success claim becomes the parent's ground truth.

Both warnings are structural (written into tool descriptions, so they apply every time the tool is invoked) rather than prose-level, which should make them sticky.

### d. Fewer specialist affordances, more general-purpose lean

`claude-code-guide` (CC/SDK/API meta-questions) and `AskUserQuestion` (denial-reason fallback) both removed. The main agent is expected to handle these in-band. This is consistent with Anthropic's broader 4.X push -- fewer bespoke sub-models, more trust in one main model handling many cases.

---

## 7. Reproducing this diff

```bash
./download-claude.py 2.1.98                        # fetch stable
./download-claude.py 2.1.114                       # fetch latest
./extract.sh binaries/2.1.98/linux-x64/claude      # → claude-app.pretty.v2.1.98.js
./extract.sh binaries/2.1.114/linux-x64/claude     # → claude-app.pretty.v2.1.114.js
./patch-context-dump.py claude-app.pretty.v2.1.98.js
./patch-context-dump.py claude-app.pretty.v2.1.114.js
rm -rf /tmp/claude-context-v2.1.98 /tmp/claude-context-v2.1.114
bun claude-app.pretty.v2.1.98.js  -- -p "hi"
bun claude-app.pretty.v2.1.114.js -- -p "hi"
# Then diff the JSON dumps in /tmp/claude-context-v*/*.json
```

The dumps are reproducible byte-for-byte on the same prompt (modulo timestamps in the dump filename). If Anthropic ships a prompt hotfix without bumping the version, re-running this would catch it.
