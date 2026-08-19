# Hermes Web UI — Implementation Plan

Fork of [open-webui/open-webui](https://github.com/open-webui/open-webui), customized as a
personal PWA front-end for Hermes Agent, deployed at `ai.pondkarun.dev`.

Repo: `pondkarun/open-webui` (fork), work branch `pondkarun-custom` (keep `main` tracking
upstream so future `git fetch upstream && git rebase upstream/main` stays clean).

## Goals

1. **Web UI / PWA** — replace/supplement Telegram as a chat surface for Hermes. Separate,
   named conversations (Open WebUI already does this natively) so multiple projects don't
   collide in one thread — this was the original motivation.
2. **Approval / clarifying-question notifications** — when Hermes needs input mid-task
   (the same kind of thing Claude Code's `AskUserQuestion` does — a question with pick-one
   answers), surface it as a push notification + an in-app prompt with selectable buttons,
   not just plain chat text easy to miss.
3. **GLM usage widget** — a small persistent panel/page showing GLM (z.ai) quota: percent
   used, remaining, and next reset time, for both the 5-hour and monthly windows.
4. Reachable only by the owner, same security model as everything else in this setup
   (Cloudflare Access gate, same email allowlist as `ssh.pondkarun.dev`).

Explicitly out of scope (decided earlier tonight): Claude Pro 5h-quota widget (no stable
API — Anthropic issue #44328 still open) and Qwen/DashScope usage widget (no usage API
exists at all). Revisit only if either provider ships something later.

## Architecture

```
ai.pondkarun.dev
   → Cloudflare Tunnel (already running: home-server)
   → Cloudflare Access (email allowlist, same pattern as ssh.pondkarun.dev)
   → open-webui container (port 3000 internal)
       → Hermes Agent API server (OpenAI-compatible, port 8642, loopback only)
       → GLM usage widget: direct browser/server fetch to
         https://api.z.ai/api/monitor/usage/quota/limit
         (Authorization: <GLM_API_KEY> — confirmed working 2026-08-19, see
         "GLM usage endpoint" below)
```

## GLM usage endpoint (already verified working)

Source: official z.ai plugin repo, `zai-org/zai-coding-plugins`,
`plugins/glm-plan-usage/skills/usage-query-skill/scripts/query-usage.mjs`.

```
GET https://api.z.ai/api/monitor/usage/quota/limit
Headers:
  Authorization: <GLM_API_KEY>
  Accept-Language: en-US,en
  Content-Type: application/json
```

Response shape (tested live, real account data 2026-08-19):

```json
{
  "code": 200,
  "success": true,
  "data": {
    "level": "lite",
    "limits": [
      { "type": "CREDIT_LIMIT", "unit": 3, "number": 5,  "usage": 2000,  "currentValue": 49, "remaining": 1950, "percentage": 2, "nextResetTime": 1787169305587 },
      { "type": "CREDIT_LIMIT", "unit": 6, "number": 1,  "usage": 10000, "currentValue": 49, "remaining": 9950, "percentage": 1, "nextResetTime": 1787755822998 }
    ]
  }
}
```

`unit`/`number` pairing looks like `{unit: 3, number: 5}` = 5-hour window, `{unit: 6,
number: 1}` = 1-month window (needs one more confirmation pass against docs/behavior
before shipping, but the shape itself is solid). `nextResetTime` is epoch milliseconds.

Related endpoints from the same script (not required for v1, note for later):
`GET /api/monitor/usage/model-usage?startTime=...&endTime=...` and
`.../tool-usage?startTime=...&endTime=...` — same auth, need a time-range query string.

## Steps

### 1. Enable Hermes's OpenAI-compatible API server
- `hermes config set API_SERVER_ENABLED true`
- `hermes config set API_SERVER_KEY <generate a fresh secret>`
- Restart gateway; verify `curl localhost:8642/health` and `/v1/models`.

### 2. Get Open WebUI running locally first (no custom code yet)
- `docker compose up` with `OPENAI_API_BASE_URL=http://host.docker.internal:8642/v1`,
  `OPENAI_API_KEY=<API_SERVER_KEY>`, `ENABLE_OLLAMA_API=false`.
- Confirm plain chat works end-to-end against Hermes/GLM before touching any UI code.

### 3. GLM usage widget
- Small backend route in Open WebUI's FastAPI backend (`backend/open_webui/routers/`)
  that proxies the z.ai call server-side (keeps `GLM_API_KEY` off the client).
- Sidebar widget (Svelte component) polling that route every few minutes, showing a
  progress bar + "resets in Xh Ym" for the 5-hour window.

### 4. Approval / clarifying-question flow — **research spike first, don't design blind**
- Hermes already has a `clarify` tool (confirmed present via `hermes doctor`) and the
  built-in dashboard's embedded TUI already renders "clarify/sudo/approval prompts"
  natively. What's unresolved: how (or whether) that same signal comes through the
  **OpenAI-compatible `/v1/chat/completions` stream** that Open WebUI actually consumes —
  it may arrive as a special tool-call, a particular content block, or not be exposed
  over that API at all yet.
- First task under this step is literally: trigger a `clarify` call through the API
  server and inspect the raw SSE stream to see what shape it takes. Design the UI
  component (modal/inline buttons + Web Push notification) only after that's known.
- If it turns out `clarify` isn't exposed over the OpenAI-compatible API at all, the
  fallback is polling Hermes's own session/job state some other way — flag back to the
  user rather than guessing at a workaround.

### 5. PWA polish
- Open WebUI ships a PWA manifest + service worker already; mainly need: app icon/name
  branding, and Web Push subscription wiring for step 4's notifications.

### 6. Deploy
- `docker compose up -d --build` (custom image, since we're patching backend + frontend).
- Add `ai.pondkarun.dev` ingress rule to `/etc/cloudflared/config.yml` → `http://localhost:3000`.
- `cloudflared tunnel route dns home-server ai.pondkarun.dev`.
- New Cloudflare Access "Self-hosted" application (Public DNS tab, same as the SSH one),
  policy: Allow, email `pondkarun@gmail.com` only.

### 7. Commit + push
- Commit working code to `pondkarun-custom` branch, push to the fork.

## Open questions for the user (answer before/at implementation time)

- Branding: any name/icon preference for the PWA, or keep Open WebUI defaults for now?
- Push notifications need a one-time browser permission grant — fine to prompt for that
  on first visit?
