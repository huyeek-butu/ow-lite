# ow-lite

> A minimal, working bridge that puts **OpenCode's free models** into **WorkBuddy**.
> Pure Python standard library. No third-party dependencies. No changes to OpenCode itself.
> No Electron shell to install.

---

## 1. What it does

WorkBuddy lets you add custom models through an "OpenAI-compatible" endpoint. OpenCode ships a
set of **zero-cost free models**, but its API is *not* OpenAI-shaped.

ow-lite does exactly one thing: **translate between the two.**

```
WorkBuddy  ──OpenAI wire──▶  ow-lite  ──OpenCode session API──▶  opencode serve  ──▶  free models
           ◀──OpenAI wire──           ◀──session response─────
```

---

## 2. Three facts that force you to write this yourself

All three were **measured**, not assumed:

| # | Test | Result |
|---|---|---|
| 1 | Call `opencode.ai/zen/v1/chat/completions` directly with `public` as the key | **12 of 13** `*-free` models returned **403**: `FreeTierError: OpenCode's free tier can only be used from within OpenCode` |
| 2 | Call the same free model **through a local OpenCode process** | **HTTP 200**, correct answer, no 403 at all |
| 3 | Does OpenCode expose an OpenAI-compatible endpoint? | **No.** `/doc` lists 162 native endpoints (`/session/*`, `/provider/*`, …); `/v1/chat/completions` is just the SPA HTML fallback |

**Conclusion:** the free tier only trusts the OpenCode process itself, so you must run it locally —
but it does not speak OpenAI, so you must write the translation layer. Any tool that advertises
"just edit your config" no longer works today (see §11).

---

## 3. Layout

```
ow-lite/
├── ow-lite.py              CLI entry point (init / probe / serve / install / uninstall / status)
├── config.json             Configuration (omit it to use built-in defaults)
├── owlite/
│   ├── config.py           Config loading
│   ├── opencode.py         Isolated `opencode serve` runtime + HTTP client
│   ├── relay.py            ★ Protocol translation layer (OpenAI ↔ OpenCode)
│   ├── server.py           HTTP service exposing the OpenAI-compatible endpoints
│   └── workbuddy.py        models.json read / backup / rollback
└── .data/                  Runtime data (auto-created, fully isolated from your daily OpenCode)
    ├── opencode/           config / data / cache / state / project
    └── logs/               opencode.log, proxy logs
```

---

## 4. Quick start

Prerequisite: a standalone `opencode.exe` on disk (the agent build bundles Node — no separate
Node install needed). Its path lives in `config.json` under `opencode_binary`.

```bash
cd D:/OW-Bridge/ow-lite

# 1) Discover the free models currently available
python ow-lite.py probe --stop

# 2) Start the proxy (detached, does not hold your terminal)
python ow-lite.py start

# 3) Write the models into WorkBuddy (timestamped backup taken first)
python ow-lite.py install

# 4) Fully quit and relaunch WorkBuddy (tray → Quit, not just closing the window)
#    The models appear as "OC · xxx"
```

To undo:

```bash
python ow-lite.py stop          # stop the proxy and clean up the OpenCode it spawned
python ow-lite.py uninstall     # removes only ow-lite entries; everything else is preserved
```

---

## 5. Command reference

| Command | Purpose |
|---|---|
| `init [--force]` | Generate `config.json` |
| `probe [--stop]` | List available free models (tool-calling / image / context info) |
| `start [--port N]` | **Start the proxy detached** (recommended; frees the terminal) |
| `stop` | Stop the proxy and clean up the OpenCode it spawned (leaves your own opencode processes alone) |
| `serve [--port N]` | Start the proxy in the foreground (debugging; Ctrl+C to stop) |
| `install [--limit N] [--stop]` | Write the models into the WorkBuddy config |
| `uninstall` | Remove them again (rollback) |
| `status` | Show proxy / runtime / config status |

> The proxy **must stay running while you use WorkBuddy**. `start` spawns a detached process
> (survives closing the terminal). Logs live in `.data/logs/proxy.log` and
> `.data/logs/opencode.log`.

---

## 6. Configuration

| Key | Default | Meaning |
|---|---|---|
| `proxy_host` / `proxy_port` | `127.0.0.1` / `41985` | Proxy listen address |
| `opencode_binary` | `D:/OW-Bridge/runtime-standalone/pkg/package/bin/opencode.exe` | Standalone runtime path |
| `data_dir` | `<project>/.data` | Isolated runtime data directory |
| `opencode_port` | `0` | `0` = pick a free port on each start |
| `model_id_prefix` | `ow-` | Id prefix written into WorkBuddy (**used to recognise "our" entries**) |
| `model_name_prefix` | `OC · ` | Display-name prefix |
| `model_allowlist` | `[]` | Expose only these models (empty = all free models) |
| `request_timeout` | `300` | Per-request timeout (s). Free models can be slow |
| `delete_session_after` | `true` | Delete the session when a request ends (stateless mode) |

---

## 7. How the translation works

OpenCode's `POST /session/{id}/message` only accepts **its own** tools (the `tools` field is just a
`{name: bool}` switch — you cannot inject arbitrary tool definitions). So to make it serve
WorkBuddy, ow-lite uses **prompt-level tool calling**:

1. **Request side** (`relay.prepare`)
   - The entire WorkBuddy `messages` array (system, tool-call history, tool results) is
     **serialised to JSON** and sent as a single `text` part;
   - A `system` field injects the **adapter contract**: "you are the reasoning component, the
     external client executes everything, choose only from this turn's tools", with the tool
     definitions appended as JSON;
   - OpenCode's `format: {type: "json_schema"}` **forces** a single envelope:
     ```json
     {"content": "text to the user", "calls": [{"name": "tool", "arguments": {}}]}
     ```
     where `calls.items` uses `anyOf` to enumerate exactly the tool names and argument schemas
     allowed this turn.

2. **Response side** (`relay.decode_model_output`)
   - Prefer the `StructuredOutput` call's `state.input` (the structured envelope);
   - Fall back to parsing JSON out of text; then to plain text;
   - Convert the envelope into OpenAI `message.tool_calls` (stringifying `arguments`).

3. **Discipline** (enforced in the system contract *and* re-validated by the proxy)
   - The model may only use tools from **this** turn, names copied verbatim;
   - Any **native tool call** other than `StructuredOutput` is treated as
     `native_tool_activity` and the whole response is rejected — it means the model tried to act
     on the local machine.

---

## 8. Security design

| Area | Approach |
|---|---|
| Runtime isolation | `XDG_*_HOME` all point at `.data/opencode/`; your daily OpenCode config is untouched |
| Environment | Allow-listed pass-through (PATH/HOME/TEMP/SystemRoot…); **no** other provider keys leak in |
| External injection | `OPENCODE_DISABLE_PROJECT_CONFIG` / `_CLAUDE_CODE` / `_EXTERNAL_SKILLS` all on |
| Permissions | Session permissions set to deny; the proxy **never answers** permission prompts → any native action stalls = fully blocked |
| WorkBuddy writes | Timestamped backup first; **only** add/remove entries with our prefix; temp file + atomic replace |
| Network | Only `registry.npmjs.org` / `registry.npmmirror.com` (runtime download) and `opencode.ai`; no telemetry |

> The `apiKey` written into WorkBuddy is a local placeholder (`ow-lite-local`) — **not** a real
> credential. The proxy performs no auth check (it binds to `127.0.0.1` only).

---

## 9. Measured results (2026-10-05)

| Case | Result |
|---|---|
| Plain text Q&A | HTTP 200 / 3.9 s / correct Chinese answer |
| Tool call | HTTP 200 / 1.6 s / correctly returned `Read({"file_path":"D:/demo/notes.txt"})` |
| Tool result round-trip | HTTP 200 / 5.8 s / answered from the tool result |
| Streaming SSE | 5 events + `data: [DONE]`, wire-format compliant |

**10 free models discovered** in this run: `big-pickle`, `fledge-alpha-free`,
`ling-3.0-flash-fin-free`, `ling-3.1-flash-free`, `longcat-2.5-preview-free`,
`mimo-v2.6-flash-free`, `muse-spark-1.3-contributor-free`, `nemotron-3-ultra-free`,
`nemotron-3.5-lightning-free`, `space-bunny-free`. **All support tool calling.**

> Counts and availability drift with the upstream catalogue — trust the current `probe` output.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Models don't show up after `install` | WorkBuddy wasn't restarted, or `--stop` killed the runtime. **Run `serve` and fully restart WorkBuddy** |
| `model_not_found` | The requested model isn't in this run's catalogue. Re-run `probe` and `install` |
| "Native tool activity detected" | The model ignored the adapter contract (rare). Try another model or exclude it via `model_allowlist` |
| "Upstream model error" | The free model is queued / rate-limited / temporarily offline. Retry or switch models |
| Very slow responses | Expected. Free models queue; observed latency ranges from 1 s to 2 minutes. Treat as **non-urgent work only** |
| Context seems large | The adapter contract + tool definitions cost ~4.5–5K prompt tokens. Normal |

---

## 11. Comparison

| | `free-buddy-skills` | `ow-bridge` | **ow-lite** |
|---|---|---|---|
| Form | Plain skill (one .py) | Electron tray app (152 MB) | **Python scripts, no shell** |
| Still works today | ❌ direct calls are blocked upstream | ✅ | ✅ |
| Protocol translation | None | Yes (~40 KB) | Yes (slimmed) |
| Dependencies | Python | Bundled Node | **Python** |
| Config writing | Blind append, no backup | Backup / own-entries-only | Backup / own-entries-only |
| Always-on | No | Must keep the app open | **Starts on demand** |
| Maintenance | ★0, abandoned at creation | ★107, active | This repo |

---

## 12. Known limitations

- **No image input** — passing `image_url` fails loudly instead of being silently dropped.
- **Reasoning content is not forwarded** — reasoning parts are discarded from the response.
- **Streaming is buffered** — the full result is validated before being emitted as SSE chunks.
  This trades token-by-token latency for never treating an unvalidated tool call as executed.
- **No SLA on free models** — queuing, rate limits and removal are normal.
- **Data goes to `opencode.ai`** — do not send anything sensitive through free models.

---

## 13. License

The code in this directory is free to use and modify. The OpenCode runtime it launches, and the
free-model service it consumes, are governed by their own licenses and terms of service.
