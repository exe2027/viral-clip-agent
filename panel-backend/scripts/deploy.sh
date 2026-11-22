#!/usr/bin/env bash
# deploy.sh — despliega la instalación ÚNICA avc (una sola página,
# https://avc.pages.dev, con login por usuario).
# Requisitos: wrangler login (o CLOUDFLARE_API_TOKEN), recursos ya
# creados (ver DEPLOY-avc.md), migraciones aplicadas, secretos puestos.
# Uso: ./scripts/deploy.sh [--skip-tests]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ "${1:-}" != "--skip-tests" ]; then
  echo "== dependencias del worker =="
  (cd worker && npm ci --no-audit --no-fund)
  echo "== typecheck =="
  (cd worker && npx tsc --noEmit -p tsconfig.json)
  echo "== tests =="
  (cd worker && bun test test/)
fi

echo "== desplegando avc =="
(cd worker && npx wrangler deploy --config ../wrangler.toml)
echo "== health avc =="
./scripts/health-check.sh https://avc.pages.dev || echo "AVISO: health-check falló; revisar antes de cantar victoria"
echo "OK: https://avc.pages.dev"
