# Deployment Runbook — ai.pondkarun.dev

Custom Open WebUI (this fork, branch `pondkarun-custom`) chatting with Hermes Agent
via its OpenAI-compatible API server.

All Hermes config keys below verified against hermes-agent source
(`hermes_cli/config_defaults.py`, `gateway/platforms/api_server.py`).

## 1. Host: enable the Hermes API server

```bash
# Config (hermes config set <KEY> <VALUE>)
hermes config set API_SERVER_ENABLED true
hermes config set API_SERVER_KEY "$(openssl rand -hex 24)"   # REQUIRED — server refuses to start without one
hermes config set API_SERVER_PORT 8642                        # default
# Bind so the Open WebUI container can reach it, without exposing to the LAN.
# 172.17.0.1 is the docker0 bridge IP — containers reach it, the outside cannot.
hermes config set API_SERVER_HOST 172.17.0.1
```

> If you prefer `API_SERVER_HOST 127.0.0.1` (default), then run the Open WebUI
> container with `network_mode: host` instead — but the bridge bind above is cleaner.

Then restart the gateway: `hermes gateway restart`
Verify on host: `curl -H "Authorization: Bearer <API_SERVER_KEY>" http://172.17.0.1:8642/v1/models`

Note: Open WebUI calls the model endpoint **server-side** (backend container →
Hermes), so no CORS configuration is needed for this integration.

## 2. Host: run Open WebUI (this fork)

```bash
git clone -b pondkarun-custom https://github.com/pondkarun/open-webui.git
cd open-webui

docker build -t pond-open-webui .

docker run -d --name pond-open-webui \
  --add-host host.docker.internal:host-gateway \
  -p 127.0.0.1:3000:8080 \
  -e WEBUI_NAME="Pond AI" \
  -e OPENAI_API_BASE_URL=http://host.docker.internal:8642/v1 \
  -e OPENAI_API_KEY=<API_SERVER_KEY from step 1> \
  -e ENABLE_OPENAI_API=true \
  -e GLM_API_KEY=<z.ai key — for the sidebar usage widget> \
  -v open-webui:/app/backend/data \
  pond-open-webui
```

- `-p 127.0.0.1:3000:8080` keeps OWUI off the LAN; cloudflared proxies to it.
- First signup becomes admin.

## 3. Host: cloudflared → ai.pondkarun.dev

`cloudflared` is already running on the host. Add:

1. Cloudflare dashboard → DNS for `pondkarun.dev`: CNAME `ai` → the tunnel
   (`<tunnel-id>.cfargotunnel.com`, proxied). *(This record did not exist yet as
   of 2026-08-20 — "Name or service not known".)*
2. Tunnel ingress (cloudflared config or Zero Trust dashboard):

   ```yaml
   ingress:
     - hostname: ai.pondkarun.dev
       service: http://localhost:3000
     - service: http_status:404
   ```

3. Optional but recommended: Cloudflare Access policy on `ai.pondkarun.dev`
   (email OTP) so the OWUI login page isn't public.

## 4. Verify end-to-end

```bash
curl -s https://ai.pondkarun.dev/health          # {"status": true}
```

Then in the browser: sign up → admin settings → the model from
`http://host.docker.internal:8642/v1` should already be connected — send a chat,
confirm Hermes answers. The GLM usage widget appears in the sidebar once
`GLM_API_KEY` is set on the container.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `connection refused` from OWUI container | API server not running, or bound to `127.0.0.1` (unreachable from containers). Use `API_SERVER_HOST 172.17.0.1`. |
| API server refuses to start | `API_SERVER_KEY` missing — it is mandatory. |
| Model list empty in OWUI | Wrong `OPENAI_API_KEY` (must equal `API_SERVER_KEY`), or wrong base URL. |
| GLM widget shows error | `GLM_API_KEY` missing on the OWUI container. |
| DNS fails for ai.pondkarun.dev | CNAME record not created yet (step 3.1). |
