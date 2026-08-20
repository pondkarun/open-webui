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
