#!/usr/bin/env bash
# health-check.sh — salud de UN Worker por usuario (avc-sebastian o avc-kaez).
# Uso: ./scripts/health-check.sh [https://avc-sebastian.<cuenta>.workers.dev] [sebastian|kaez]
# Comprueba: /api/health responde, app = "avc", tenant = el esperado,
# y si ese Worker está iniciado o no.
# No comprueba credenciales de plataformas (eso es /api/accounts/:id/verify).
set -euo pipefail
BASE="${1:-https://avc-sebastian.vikingosfoodspa.workers.dev}"
TENANT="${2:-sebastian}"
URL="${BASE%/}/api/health"

echo "== avc-$TENANT → $URL =="
BODY="$(curl -fsS -m 15 "$URL")"
echo "$BODY"
APP_OK="$(echo "$BODY" | grep -o "\"app\":\"avc\"" || true)"
[ -n "$APP_OK" ] || { echo "FALLO: no es la app avc (¿URL equivocada o sin desplegar?)"; exit 1; }
TENANT_OK="$(echo "$BODY" | grep -o "\"tenant\":\"$TENANT\"" || true)"
[ -n "$TENANT_OK" ] || { echo "FALLO: el Worker no sirve al tenant $TENANT (¿TENANT mal configurado?)"; exit 1; }
echo "$BODY" | grep -o "\"worker_running\":\(true\|false\)" || true
echo "OK: app avc responde para el tenant $TENANT."
echo "Nota: worker_running=false solo dice que el botón Iniciar no está pulsado; no es un error."
