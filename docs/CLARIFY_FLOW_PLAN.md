# Clarify / Approval Flow — Research Findings + Implementation Plan

Research spike from IMPLEMENTATION_PLAN.md step 4, now resolved. Written after
reading `tools/clarify_tool.py`, `tools/clarify_gateway.py`, and
`gateway/platforms/api_server.py` in the hermes-agent source directly (not
guessed).

## What we now know for certain

1. **`clarify` already works over the API server with zero backend changes,**
   via a generic fallback every platform adapter inherits from
   `BasePlatformAdapter.send_clarify()` (`gateway/platforms/base.py:4263`):
   when an adapter doesn't override it (ours doesn't), the question renders
   as a plain numbered-list text message (`❓ question / 1. .../ 2. ...`),
   and the gateway's text-intercept resolves it from the user's *next*
   message in the same session (a number, the choice text, or free text).
   `APIServerAdapter` (`gateway/platforms/api_server.py:1352`) extends
   `BasePlatformAdapter`, so it gets this for free.

2. **The catch: session continuity requires a header Open WebUI doesn't send
   by default.** The text-intercept only works if consecutive HTTP requests
   are recognized as "the same conversation" — that's the
   `X-Hermes-Session-Key` header (`api_server.py:2120`,
   `_parse_session_key_header`). Without it, each request is a fresh session
   and the intercept has nothing to resolve against. Requires
   `API_SERVER_KEY` auth to be honored (already set up).

3. Richer per-platform UIs (Telegram inline buttons, Discord buttons) are
   just adapter overrides of the same `send_clarify()` — not a different
   mechanism, so the text-fallback and a future button UI are two renderings
   of the same underlying primitive, not two different systems to build.

## Plan (two-phase, ship the working thing first)

### Phase A — make the existing fallback actually usable (small, backend-side)

1. Patch our fork's OpenAI-compatible request path (wherever it builds the
   `fetch`/SDK call for `/v1/chat/completions`) to send
   `X-Hermes-Session-Key: <chat_id>` — Open WebUI already has a stable chat
   id per conversation, so this is a one-line header addition, not new
   state to invent.
2. Manually verify: start a chat, ask something that plausibly triggers
   `clarify` (or force it via a system-prompt nudge for the test), confirm
   the numbered list appears as a normal assistant message and replying
   with "2" resolves it and the agent continues. This is the real
   end-to-end test — everything above is source-reading, not yet observed
   live.

At this point clarify **functionally works** in the web UI already — just as
plain text, no buttons yet. That alone may be enough; don't build Phase B
until Phase A is confirmed live and found lacking.

### Phase B — nicer UI + push notification (frontend-only, optional)

1. A small Svelte action/component that scans a rendered assistant message
   for the fallback's exact pattern (`❓ ` prefix, `  N. ` lines) and renders
   real buttons in its place instead of raw text. Clicking a button sends
   its choice text as the next user message — i.e. it's a *display*
   enhancement over the same text-intercept protocol, not a new backend
   API. Low risk: if the pattern doesn't match (model phrased something
   differently), it just falls back to showing the plain text, which still
   works via Phase A.
2. Push notification: Open WebUI's PWA service worker already exists
   (`static/serviceworker.js`, done in the first build). Add a Web Push
   subscription + have the backend fire a push when a new assistant message
   matches the clarify pattern and arrives while the client is backgrounded.
   Needs VAPID keys generated once and stored server-side.

## Explicitly not doing

- No new backend "approval queue" API — would duplicate what
  `clarify_gateway.py` already does correctly.
- Not touching Telegram/Discord adapters — they already have their own
  native button UIs via the same mechanism; nothing to fix there.

---

## Bonus finding (same session, 2026-08-20): tool-progress indicator is silently dropped

Not part of the clarify flow, but found while testing streaming for a related UX
complaint ("can't tell the AI is doing anything" during long tool-calling turns).
Root cause fully diagnosed — implementation not yet started.

**What's actually happening:**
- The API server backend already emits real progress signals mid-turn as a
  named SSE event, confirmed live via curl:
  ```
  event: hermes.tool.progress
  data: {"tool": "terminal", "emoji": "💻", "label": "pwd", "toolCallId": "...", "status": "running"}
  ...
  event: hermes.tool.progress
  data: {"tool": "terminal", "toolCallId": "...", "status": "completed"}
  ```
- Our fork's SSE consumer, `src/lib/apis/streaming/index.ts`
  (`openAIStreamToIterator`), only reads `value.data` from each parsed SSE
  event and never looks at `value.event` (the named-event field the
  `eventsource-parser` library already exposes). Since
  `{"tool":"terminal",...}` doesn't match the expected
  `parsedData.choices[0].delta.content` shape, it falls through to `?? ''`
  — an empty yield. The signal arrives and is silently discarded every time.
- Plain streaming (no tool calls) works fine and looks like normal
  token-by-token typing — this only affects turns where Hermes calls a tool
  mid-response, which for this agent (terminal, memory, etc.) is common and
  can take a long time per call.

**Fix (not yet implemented):**
1. In `openAIStreamToIterator`, branch on `value.event === 'hermes.tool.progress'`
   (alongside the existing `sources`/`selectedModelId`/`usage` special
   cases) and yield a new field, e.g. `toolProgress: {tool, emoji, label, status}`.
2. In `Chat.svelte` (and `MultiResponseMessages.svelte`, same pattern) where
   the generator from `createOpenAITextStream` is consumed: on a
   `toolProgress` update with `status: "running"`, show a transient
   "{emoji} {label}…" status line (NOT appended into the saved message
   content — needs its own reactive variable, cleared on `status: "completed"`
   or when real content starts arriving). Do not persist this text into chat
   history.
3. Test with a prompt that forces multiple sequential tool calls, confirm
   the status line updates per tool and clears correctly, and confirm a
   plain non-tool response still looks identical to today (no regression).

Deliberately not attempted tonight — touching `Chat.svelte`'s streaming
consumption logic carelessly late at night risks a worse regression than the
current "silent gap" UX. Do this with full attention, not as a rushed patch.
