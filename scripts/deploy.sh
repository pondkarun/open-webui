#!/usr/bin/env bash
# One-command deploy for Pond AI (ai.pondkarun.dev) — run ON THE HOST.
#
# Pulls latest pondkarun-custom, rebuilds the image, recreates the container
# with the SAME env/ports/volumes as the currently running one (captured via
# docker inspect — secrets never live in this repo), then health-checks.
#
# Usage:  bash scripts/deploy.sh
set -euo pipefail

REPO_DIR="$HOME/projects/open-webui"
NAME="pond-open-webui"
IMAGE="pond-open-webui:latest"
PORT_PUB="127.0.0.1:3000"

cd "$REPO_DIR"
echo "==> git pull"
git fetch origin
git reset --hard origin/pondkarun-custom
git log --oneline -1

echo "==> docker build"
docker build -t "$IMAGE" .

# Hermes session continuity (clarify/approval flows): forward chat_id to the
# upstream OpenAI-compatible API (Hermes) as X-Hermes-Session-Key.
export EXTRA_ENV_ARGS=(
  -e ENABLE_FORWARD_USER_INFO_HEADERS=true
  -e FORWARD_SESSION_INFO_HEADER_CHAT_ID=X-Hermes-Session-Key
)

# Capture env + volumes from the running container (if any) so we never
# hardcode secrets in this repo.
ENV_ARGS=()
VOL_ARGS=()
if docker inspect "$NAME" >/dev/null 2>&1; then
  mapfile -t ENV_ARGS < <(docker inspect "$NAME" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep -vE '^(PATH|HOSTNAME|HOME|TERM|LANG)=' | sed 's/^/-e /')
  mapfile -t VOL_ARGS < <(docker inspect "$NAME" --format '{{range .Mounts}}{{println .Source ":" .Destination}}{{end}}' \
    | sed 's/^/-v /')
  echo "==> removing old container"
  docker rm -f "$NAME" >/dev/null
fi

echo "==> starting container"
docker run -d --name "$NAME" \
  --add-host host.docker.internal:host-gateway \
  --restart unless-stopped \
  -p "$PORT_PUB:8080" \
  "${EXTRA_ENV_ARGS[@]}" \
  "${ENV_ARGS[@]}" "${VOL_ARGS[@]}" \
  "$IMAGE"

echo "==> waiting for /health"
for i in $(seq 1 30); do
  if curl -sf --max-time 3 http://localhost:3000/health >/dev/null 2>&1; then
    echo "health: OK (attempt $i)"; break
  fi
  [ "$i" = 30 ] && { echo "ERROR: health check failed"; docker logs --tail 30 "$NAME"; exit 1; }
  sleep 2
done

echo "==> smoke tests"
curl -sf --max-time 8 -o /dev/null "http://localhost:3000/manifest.json" && echo "manifest.json: OK" || echo "manifest.json: FAIL"
code=$(curl -s --max-time 8 -o /dev/null -w "%{http_code}" "http://localhost:3000/serviceworker.js")
echo "serviceworker.js: HTTP $code (want 200)"

echo "==> deploy complete: $(docker ps --filter name=$NAME --format '{{.Status}}')"
