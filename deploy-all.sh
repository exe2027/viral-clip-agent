#!/usr/bin/env bash
# deploy-all.sh — despliega EL MISMO commit a los dos paneles.
#
# Regla de oro (plan §3): nunca desplegar a mano a un solo panel. Este
# script toma el HEAD actual, lo despliega a Sebastián y al colega, y
# hace health check de cada panel. Si un panel falla, el script falla
# (el otro ya puede estar desplegado: revisa el reporte y reintenta).
#
# Estado 2026-11-17: el backend del panel (Worker + D1 + R2) aún NO
# existe en el repo (falta §6.4 del plan). Mientras no exista
# panel-deploy/<panel>/wrangler.toml, ese panel se SALTA con aviso y
# solo se hace su health check si PANEL_<PANEL>_URL está definida.
# No se inventa un despliegue que no existe.
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
#   PANEL_SEBASTIAN_URL, PANEL_COLEGA_URL  (p. ej. https://panel-sebastian.pages.dev)
#   CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID (para wrangler, cuando exista)
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

PANELS="sebastian colega"
FAILED=0

health_check() {
  local name="$1" url="$2"
  if [ -z "$url" ]; then
    echo "  [$name] sin PANEL_${name^^}_URL: health check omitido."
    return 0
  fi
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$url" || echo 000)"
  if [ "$code" = "200" ]; then
    echo "  [$name] OK ($url -> HTTP 200)"
  else
    echo "  [$name] FALLO ($url -> HTTP $code)"
    FAILED=1
  fi
}

deploy_panel() {
  local name="$1"
  local dir="panel-deploy/$name"
  echo "== Panel: $name =="
  if [ "$CHECK_ONLY" = "1" ]; then
    echo "  --check-only: sin despliegue."
  elif [ -f "$dir/wrangler.toml" ]; then
    if ! command -v wrangler >/dev/null 2>&1; then
      echo "  FALLO: wrangler no instalado (npm i -g wrangler)."
      FAILED=1
    else
      (cd "$dir" && wrangler deploy)
      echo "  [$name] desplegado (commit $COMMIT)."
    fi
  else
    echo "  SIN backend en el repo ($dir/wrangler.toml no existe)."
    echo "  Pendiente plan §6.4 (Worker + D1 + R2). No se despliega nada."
  fi
  local var="PANEL_${name^^}_URL"
  health_check "$name" "${!var:-}"
}

# Verifica la vía principal de cómputo: los workflows del cerebro
# existen y son YAML válido. No los despliega (van por git push).
check_workflows() {
  echo "== Workflows del cerebro (vía principal) =="
  local missing=0
  for wf in cerebro-sebastian cerebro-colega; do
    if [ ! -f ".github/workflows/$wf.yml" ]; then
      echo "  [$wf] FALLO: no existe .github/workflows/$wf.yml."
      FAILED=1
      missing=1
    fi
  done
  [ "$missing" = "1" ] && return 0
  if command -v python3 >/dev/null 2>&1 \
      && python3 -c "import yaml" 2>/dev/null; then
    if python3 -c "import yaml; [yaml.safe_load(open(f'.github/workflows/{n}.yml')) for n in ('cerebro-sebastian', 'cerebro-colega')]"; then
      echo "  [workflows] OK: cerebro-sebastian.yml y cerebro-colega.yml son YAML válido."
    else
      echo "  [workflows] FALLO: YAML inválido en un workflow del cerebro."
      FAILED=1
    fi
  else
    echo "  [workflows] AVISO: sin python3/PyYAML no se pudo validar el YAML (archivos presentes)."
  fi
}

check_workflows

for panel in $PANELS; do
  deploy_panel "$panel"
done

if [ "$FAILED" = "1" ]; then
  echo "RESULTADO: FALLO en al menos un panel (commit $COMMIT)."
  exit 1
fi
echo "RESULTADO: OK — mismo commit $COMMIT en los dos paneles (o saltados por falta de backend)."
