#!/usr/bin/env bash
# deploy-all.sh — despliega EL MISMO commit a la instalación única.
#
# Modelo vigente (decisión del usuario 17 nov 2026, 15:05 y 15:33):
# UNA sola página https://avc-em9.pages.dev con login por tenant,
# UN Worker, UNA D1 y UN R2. No hay paneles por tenant ni workflows
# por tenant: el cerebro es genérico (workflows parametrizados por
# tenant) y las credenciales viven en el panel, no en GitHub.
# El archivo anterior desplegaba "a Sebastián y al colega" con
# PANELS="sebastian colega" y panel-deploy/<tenant>/ (modelo revocado
# 2026-11-17); se reescribió el 2026-11-19 para el despliegue único.
# El tenant "colega" se eliminó el 2026-11-19: nunca fue un usuario.
#
# Vía principal (decisión 17 nov 2026 "Todo automático"): Cloudflare
# + GitHub Actions. El panel se despliega aquí (wrangler); el cerebro
# va por git push (los workflows corren desde la rama por defecto).
# Este script VERIFICA que los workflows del cerebro existen y son
# YAML válido, pero no los despliega: el push a GitHub está bloqueado
# por 403 (permiso Contents: Write) hasta que se resuelva; sin push,
# los workflows no llegan al repo y el cerebro no corre.
#
# Uso:
#   ./deploy-all.sh [--check-only] [--commit <sha>]
# Variables:
#   PANEL_URL  (p. ej. https://avc-em9.pages.dev; default ese)
#   CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID (para wrangler)
#   Además, antes de desplegar: wrangler d1 migrations apply clips-avc
#   (las migraciones pendientes van primero, el código después).
set -euo pipefail

CHECK_ONLY=0
COMMIT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --check-only) CHECK_ONLY=1; shift ;;
    --commit) COMMIT="$2"; shift 2 ;;
    *) echo "Uso: $0 [--check-only] [--commit <sha>]"; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

if [ -z "$COMMIT" ]; then
  COMMIT="$(git rev-parse HEAD)"
fi
echo "Commit a desplegar: $COMMIT ($(git log -1 --oneline "$COMMIT"))"

if [ -n "$(git status --porcelain)" ]; then
  echo "AVISO: hay cambios sin commitear; se despliega el commit, no el árbol sucio."
fi

PANEL_URL="${PANEL_URL:-https://avc-em9.pages.dev}"
PANEL_DIR="panel-backend"
FAILED=0

health_check() {
  local url="$1"
  if [ -z "$url" ]; then
    echo "  sin PANEL_URL: health check omitido."
    return 0
  fi
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$url" || echo 000)"
  if [ "$code" = "200" ]; then
    echo "  OK ($url -> HTTP 200)"
  else
    echo "  FALLO ($url -> HTTP $code)"
    FAILED=1
  fi
}

# Verifica la vía principal de cómputo: los workflows del cerebro
# existen y son YAML válido. No los despliega (van por git push).
check_workflows() {
  echo "== Workflows del cerebro (vía principal) =="
  local missing=0
  for wf in cerebro cerebro-live; do
    if [ ! -f ".github/workflows/$wf.yml" ]; then
      echo "  [$wf] FALLO: no existe .github/workflows/$wf.yml."
      FAILED=1
      missing=1
    fi
  done
  [ "$missing" = "1" ] && return 0
  if command -v python3 >/dev/null 2>&1 \
      && python3 -c "import yaml" 2>/dev/null; then
    if python3 -c "import yaml; [yaml.safe_load(open(f'.github/workflows/{n}.yml')) for n in ('cerebro', 'cerebro-live')]"; then
      echo "  [workflows] OK: cerebro.yml y cerebro-live.yml son YAML válido."
    else
      echo "  [workflows] FALLO: YAML inválido en un workflow del cerebro."
      FAILED=1
    fi
  else
    echo "  [workflows] AVISO: sin python3/PyYAML no se pudo validar el YAML (archivos presentes)."
  fi
}

deploy_panel() {
  echo "== Panel único ($PANEL_URL) =="
  if [ "$CHECK_ONLY" = "1" ]; then
    echo "  --check-only: sin despliegue."
  elif [ -f "$PANEL_DIR/wrangler.toml" ]; then
    if ! command -v wrangler >/dev/null 2>&1; then
      echo "  FALLO: wrangler no instalado (npm i -g wrangler)."
      FAILED=1
    else
      (cd "$PANEL_DIR" && wrangler deploy)
      echo "  [panel] desplegado (commit $COMMIT)."
    fi
  else
    echo "  SIN wrangler.toml en $PANEL_DIR: no se despliega nada."
    FAILED=1
  fi
  health_check "$PANEL_URL"
}

check_workflows
deploy_panel

if [ "$FAILED" = "1" ]; then
  echo "RESULTADO: FALLO (commit $COMMIT)."
  exit 1
fi
echo "RESULTADO: OK — mismo commit $COMMIT en el panel único."
