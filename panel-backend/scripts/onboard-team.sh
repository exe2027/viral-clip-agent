#!/usr/bin/env bash
# onboard-team.sh — Alta de equipo multi-cuenta para el panel AVC.
#
# PROBLEMA QUE RESUELVE: hoy todos los tenants viven en UNA sola cuenta de
# Cloudflare (la de Sebastián) y la cuota D1 gratis (100.000 escrituras/día)
# y el R2 gratis (10 GB/mes) se cuentan POR CUENTA, no por base ni por
# bucket. Separar D1 por tenant en la misma cuenta NO multiplica nada.
# Con 5 cuentas propias (una por persona) cada una trae su propia cuota:
# 5 x 100.000 escrituras D1/día y 5 x 10 GB R2/mes, y si una se pasa, solo
# se cae ella.
#
# LO QUE HACE: lee una lista de tenants (CSV, ver
# scripts/tenants-team.example.csv) y, EN SECUENCIA (nunca en paralelo:
# provision-tenant.sh parchea auth.ts, router.ts, workers.json y
# wrangler.router.toml, que son archivos compartidos), por cada tenant:
#   1. Si el Worker avc-<tenant> ya existe en SU cuenta, lo verifica
#      (salud + entrada en Pages) y lo SALTA, o solo repara el routing.
#   2. Llama a scripts/provision-tenant.sh con el token y la cuenta de
#      ESE tenant, que crea el bucket R2 `clips-avc` en ESA cuenta si no
#      existe (paso 1c, idempotente), la D1 propia, migraciones,
#      secretos, Worker propio, usuario del panel, settings base,
#      routing local y verificación. Se le pasa --r2-public-url con la
#      URL r2.dev DE ESA CUENTA y --skip-pages-deploy (ver abajo).
#   3. Verifica /api/health de su Worker y que el Pages compartido
#      sirva workers.json con su entrada; si el routing quedó pendiente,
#      lo repara con el token ADMIN vía provision-tenant.sh
#      --repair-pages-deploy.
# Al final imprime un resumen por tenant: OK / FALLÓ / SALTADO, con su
# Worker URL, login y pendientes. Un tenant que falla NO aborta a los
# demás; el exit code final es 1 si alguno falló.
#
# USO:
#   scripts/onboard-team.sh --config scripts/tenants-team.csv [--dry-run]
#   scripts/onboard-team.sh --help
#
#   --config <csv>   Lista de tenants (obligatorio). Ver el .example.csv.
#   --dry-run        Valida la config y ejecuta provision-tenant.sh
#                    --dry-run por tenant. NO toca Cloudflare, NO cambia
#                    archivos, NO crea buckets, NO llama a /api/health.
#   --help           Esta ayuda.
#
# QUÉ DEBE APORTAR CADA PERSONA (antes de ejecutar, por persona):
#   - Una cuenta de Cloudflare PROPIA (su email, no una cuenta creada por
#     otro para esquivar límites: son 5 personas reales, 5 cuentas).
#   - Su Account ID (32 hex, Dashboard) y su subdominio workers.dev.
#   - Un token API en SU cuenta con permisos: Workers Edit, D1 Edit,
#     R2 Edit. Se entrega por variable de entorno (solo el NOMBRE va en
#     el CSV) o por archivo fuera del repo (permisos 600). El token
#     NUNCA se escribe en el CSV ni se imprime en logs.
#   - Checkout GRATUITO de R2 hecho en su dashboard (Storage & databases
#     > R2 > Overview; Cloudflare lo exige antes de crear buckets, pide
#     método de pago en archivo pero no cobra dentro del free tier).
#   - Bucket `clips-avc` creado (lo crea este script) con acceso público
#     r2.dev activado (R2 > bucket > Settings > Public access / r2.dev
#     subdomain > Enable) y su URL https://pub-<hash>.r2.dev copiada al
#     CSV como clip_public_base_url. Sin esa URL el script falla: no se
#     puede derivar de forma fiable por API y nunca se inventa una.
#   - Los secretos COMPARTIDOS los aporta el operador UNA vez por entorno
#     (ver abajo); no se pueden leer del Worker existente porque
#     Cloudflare no devuelve valores de secretos.
#
# SECRETOS COMPARTIDOS (una vez, por entorno, mismo valor para todos):
#   AVC_GITHUB_TOKEN, AVC_TELEGRAM_BOT_TOKEN, AVC_TELEGRAM_WEBHOOK_SECRET,
#   AVC_BRIDGE_TOKEN. Si falta uno, el script falla ANTES de crear nada.
#   Por tenant se generan frescos SESSION_SECRET y
#   CREDENTIAL_ENCRYPTION_KEY (los genera provision-tenant.sh).
#
# TOKEN ADMIN (para reparar el routing compartido, ver BUG 2 resuelto arriba):
#   CLOUDFLARE_API_TOKEN_ADMIN (entorno) o CLOUDFLARE_ADMIN_TOKEN_FILE
#   (archivo 600). Si no se da ninguno, se intenta obtener el de
#   Sebastián vía dynamic_credential_entry("custom.cloudflare"), igual
#   que hace provision-tenant.sh. Es el token de la cuenta dueña del
#   Pages `avc` y del router `avc`.
#
# QUÉ QUEDA MANUAL DESPUÉS (por persona, guiado por el onboarding):
#   - OAuth Google/Meta: crear el cliente OAuth web de Google y la app
#     de Meta (o que el admin añada a la persona como Tester si la app
#     está en Desarrollo), y poner GOOGLE_CLIENT_ID/SECRET y
#     META_APP_ID/SECRET como secretos de SU Worker (el panel lo guía).
#   - Conectar y verificar YouTube, TikTok (vía Zernio), Meta
#     (Página de Facebook + Instagram vinculado), Twitch y Gemini.
#   - Elegir influencers con permiso de clipping.
#   - Pulsar Iniciar en la pestaña Worker (el Worker nace parado).
#   - Cron APAGADO al nacer (A5): se enciende solo tras verificar, con
#     scripts/provision-tenant.sh <tenant> --enable-cron.
#   - La contraseña del panel la muestra provision-tenant.sh UNA vez en
#     pantalla y NO se guarda en ningún archivo: hay que anotarla.
#
# BUG 1 RESUELTO EN provision-tenant.sh (2026-11-21): antes generaba
#   wrangler.<tenant>.toml copiando la CLIP_PUBLIC_BASE_URL de Sebastián
#   y no creaba el bucket R2 (este script sustituía temporalmente la
#   plantilla para parchearlo). Ahora provision acepta --r2-public-url
#   (obligatorio con otra cuenta; si falta, falla antes de crear nada)
#   y --r2-bucket, crea el bucket en la cuenta destino si no existe
#   (paso 1c, idempotente) y escribe ambos valores en el toml generado.
#   Este script ya NO toca la plantilla: solo pasa los flags. Si tras
#   provision el toml no trae la URL del tenant, el tenant se marca FALLÓ.
#
# BUG 2 RESUELTO EN provision-tenant.sh (2026-11-21): el paso 7 ya no
#   redespliega Pages/router con el token del tenant cuando se pasa
#   --skip-pages-deploy (este script lo pasa siempre): parchea el
#   routing local, lo verifica, e indica el comando exacto para
#   repararlo con el token admin; además reporta si la cuenta del
#   tenant tiene un Pages/Worker `avc` duplicado (solo lo reporta,
#   nunca lo borra solo). La reparación del routing se hace con el modo
#   provision-tenant.sh <tenant> --repair-pages-deploy (paso D de
#   abajo), que verifica que ESTA cuenta sea la dueña del Pages antes
#   de desplegar nada. Si un tenant ya tenía duplicados de una
#   ejecución anterior, provision los reporta en su paso 7 y este
#   script los vuelve a avisar en el paso D, con el comando para
#   borrarlos a mano.
#
# LIMITACIÓN 3 — SOLO TENANTS NUEVOS: esto sirve para tenants nuevos en
#   su propia cuenta. Migrar a sebastian/kaez (hoy en la cuenta de
#   Sebastián) a cuentas propias es OTRO trabajo aparte: exige
#   re-cifrar sus credenciales (la CREDENTIAL_ENCRYPTION_KEY vieja no
#   es recuperable) y migrar sus D1. El script rechaza sebastian/kaez
#   en el CSV. NO lo trata como automático.
#
# IDEMPOTENCIA / REANUDACIÓN: re-ejecutar es seguro. Un tenant con
#   Worker en vivo, salud OK y entrada en Pages se SALTA. Un tenant con
#   Worker en vivo y salud OK pero sin entrada en Pages solo repara el
#   routing. Un tenant con Worker existente pero salud KO se marca
#   FALLÓ con instrucciones (provision-tenant.sh no se puede
#   re-ejecutar: su paso 1 muere si el Worker ya existe).
#
# SEGURIDAD: set -euo pipefail, umask 077. Ningún token se imprime en
#   logs ni se guarda en archivos: viajan solo en memoria (variables de
#   entorno de sub-shells) y por stdin donde corresponde. La salida de
#   provision-tenant.sh NO se guarda en ningún log (su resumen muestra
#   la contraseña generada UNA vez). Los secretos compartidos tampoco
#   se imprimen: solo se comprueba que existen.
#
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER_DIR="$ROOT/worker"
PROVISION="$ROOT/scripts/provision-tenant.sh"
HEALTH_CHECK="$ROOT/scripts/health-check.sh"
WRANGLER="$ROOT/worker/node_modules/.bin/wrangler"
[ -x "$WRANGLER" ] || WRANGLER="wrangler"
TEMPLATE_TOML="$ROOT/wrangler.sebastian.toml"
WORKERS_JSON="$ROOT/frontend/dist/workers.json"
ROUTER_TOML="$ROOT/wrangler.router.toml"
PAGES_PROJECT="avc"
PAGES_URL="https://avc-em9.pages.dev"
# URL pública de R2 de Sebastián: si un tenant la trae en el CSV es un
# copia-pega erróneo (apuntaría al bucket de otro). Se rechaza.
SEBASTIAN_PUB_URL="https://pub-5f09f432fcae41dcb9e4d114ce8ff987.r2.dev"

CONFIG=""
DRY_RUN=0

# ---------------------------------------------------------------------------
# Salida y utilidades (nunca se imprime un secreto)
# ---------------------------------------------------------------------------
log()  { printf '== %s\n' "$*"; }
warn() { printf 'AVISO: %s\n' "$*" >&2; }
die()  { printf 'FALLO: %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,/^set -euo pipefail/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//; /^$/d' | head -n -1
  echo "Uso: $0 --config <tenants.csv> [--dry-run]"
  echo "Ejemplo: $0 --config scripts/tenants-team.csv"
  echo "Prueba en seco: $0 --config scripts/tenants-team.example.csv --dry-run"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "falta el comando '$1' en el PATH"
}

# trim: quita espacios y \r (los campos del CSV no llevan espacios).
trim() { printf '%s' "$1" | tr -d '[:space:]'; }

# ---------------------------------------------------------------------------
# Argumentos
# ---------------------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --) shift; break ;;
    -*) echo "FALLO: opción desconocida: $1 (usa --help)" >&2; exit 2 ;;
    *) echo "FALLO: argumento posicional inesperado: $1 (usa --config <csv>)" >&2; exit 2 ;;
  esac
done
[ -n "$CONFIG" ] || { usage >&2; die "falta --config <tenants.csv>"; }
[ -f "$CONFIG" ] || die "no existe el archivo de config: $CONFIG"
[ -f "$PROVISION" ] || die "falta $PROVISION"
[ -f "$TEMPLATE_TOML" ] || die "falta la plantilla $TEMPLATE_TOML"
[ -f "$WORKERS_JSON" ] || die "falta $WORKERS_JSON"
[ -f "$ROUTER_TOML" ] || die "falta $ROUTER_TOML"
for c in python3 jq curl; do require_cmd "$c"; done
[ "$DRY_RUN" = 1 ] || [ -x "$WRANGLER" ] || die "falta wrangler en $WRANGLER (ejecuta: (cd worker && npm ci --no-audit --no-fund))"

# ---------------------------------------------------------------------------
# Secretos compartidos: se exigen UNA vez, antes de crear nada.
# (provision-tenant.sh los pediría por TTY por tenant; en un bucle eso
# no es viable. Solo se comprueba que existen, nunca se imprimen.)
# ---------------------------------------------------------------------------
for s in AVC_GITHUB_TOKEN AVC_TELEGRAM_BOT_TOKEN AVC_TELEGRAM_WEBHOOK_SECRET AVC_BRIDGE_TOKEN; do
  [ -n "${!s:-}" ] || die "falta el secreto compartido $s en el entorno (no se puede leer del Worker existente: Cloudflare no devuelve valores de secretos)"
done
export AVC_GITHUB_TOKEN AVC_TELEGRAM_BOT_TOKEN AVC_TELEGRAM_WEBHOOK_SECRET AVC_BRIDGE_TOKEN
log "Secretos compartidos presentes (4/4; solo se comprobó que existen)"

# ---------------------------------------------------------------------------
# Token ADMIN (cuenta de Sebastián, dueña de Pages + router). Solo se
# resuelve si hace falta reparar el routing; en --dry-run basta avisar.
# ---------------------------------------------------------------------------
ADMIN_TOKEN=""
ADMIN_TOKEN_OK=0
resolve_admin_token() {
  if [ -n "${CLOUDFLARE_API_TOKEN_ADMIN:-}" ]; then
    printf '%s' "$CLOUDFLARE_API_TOKEN_ADMIN"; return 0
  fi
  if [ -n "${CLOUDFLARE_ADMIN_TOKEN_FILE:-}" ]; then
    [ -f "$CLOUDFLARE_ADMIN_TOKEN_FILE" ] || return 1
    tr -d '\r\n' < "$CLOUDFLARE_ADMIN_TOKEN_FILE"; return 0
  fi
  python3 - <<'PY' 2>/dev/null || return 1
import sys
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import dynamic_credential_entry
print(dynamic_credential_entry("custom.cloudflare", "access_token")["surrogate"])
PY
}
if ADMIN_TOKEN="$(resolve_admin_token)" && [ -n "$ADMIN_TOKEN" ]; then
  ADMIN_TOKEN_OK=1
  log "Token admin disponible (cuenta dueña de Pages/router; no se muestra)"
elif [ "$DRY_RUN" = 1 ]; then
  warn "--dry-run: no se pudo resolver el token admin (CLOUDFLARE_API_TOKEN_ADMIN / CLOUDFLARE_ADMIN_TOKEN_FILE / dynamic_credential_entry); en una ejecución real la reparación de routing no podría hacerse"
else
  die "no se pudo resolver el token admin (CLOUDFLARE_API_TOKEN_ADMIN, CLOUDFLARE_ADMIN_TOKEN_FILE o dynamic_credential_entry custom.cloudflare). Sin él no se puede reparar el routing compartido (Pages/router)."
fi

# ---------------------------------------------------------------------------
# Lectura y validación PREVIA de todo el CSV (fail fast: nada se crea si
# una fila está mal). Filas guardadas en arrays paralelos.
# ---------------------------------------------------------------------------
declare -a T_TENANT T_LOGIN T_ACCOUNT T_SUB T_PUBURL T_TOKENENV T_TOKENFILE
N=0
declare -A SEEN_TENANT SEEN_LOGIN

while IFS=, read -r tenant login account_id subdomain pub_url token_env token_file || [ -n "${tenant:-}" ]; do
  tenant="$(trim "${tenant:-}")"
  login="$(trim "${login:-}")"
  account_id="$(trim "${account_id:-}")"
  subdomain="$(trim "${subdomain:-}")"
  pub_url="$(trim "${pub_url:-}")"
  token_env="$(trim "${token_env:-}")"
  token_file="$(trim "${token_file:-}")"
  # Saltar cabecera, comentarios y líneas vacías.
  [ -z "$tenant" ] && continue
  case "$tenant" in \#*) continue ;; esac
  [ "$tenant" = "tenant" ] && continue
  # Validaciones por fila (mismas reglas que provision-tenant.sh + las
  # propias de multi-cuenta).
  case "$tenant" in sebastian|kaez)
    die "el CSV trae el tenant '$tenant': ya existe en la cuenta de Sebastián. Migrarlo a su propia cuenta es otro trabajo (re-cifrado de credenciales); no se incluye aquí."
    ;;
  esac
  printf '%s' "$tenant" | grep -Eq '^[a-z0-9][a-z0-9-]{0,30}$' \
    || die "tenant inválido '$tenant': solo [a-z0-9-], máx 31 caracteres"
  printf '%s' "$login" | grep -Eq '^[a-z0-9][a-z0-9_-]{0,39}$' \
    || die "login inválido '$login' (tenant $tenant): solo [a-z0-9_-], máx 40 caracteres"
  printf '%s' "$account_id" | grep -Eq '^[0-9a-f]{32}$' \
    || die "cloudflare_account_id inválido para '$tenant': se esperan 32 caracteres hex (Dashboard → Account ID)"
  printf '%s' "$subdomain" | grep -Eq '^[a-z0-9][a-z0-9-]*$' \
    || die "workers_subdomain inválido para '$tenant': solo [a-z0-9-]"
  [ -n "$pub_url" ] || die "falta clip_public_base_url para '$tenant': es obligatoria (R2 → bucket clips-avc → r2.dev subdomain). Nunca se inventa una URL."
  printf '%s' "$pub_url" | grep -Eq '^https://pub-[A-Za-z0-9]+\.r2\.dev/?$' \
    || die "clip_public_base_url inválida para '$tenant': '$pub_url' (se espera https://pub-<hash>.r2.dev)"
  [ "$pub_url" != "$SEBASTIAN_PUB_URL" ] && [ "$pub_url" != "${SEBASTIAN_PUB_URL}/" ] \
    || die "clip_public_base_url de '$tenant' es la URL de Sebastián: copiarla apuntaría al bucket de otro. Cada cuenta tiene su propia URL pub-*.r2.dev."
  if [ -n "$token_env" ] && [ -n "$token_file" ]; then
    die "tenant '$tenant': token_env y token_file a la vez; usa solo uno"
  fi
  if [ -z "$token_env" ] && [ -z "$token_file" ]; then
    die "tenant '$tenant': falta token_env o token_file (nombre de variable o ruta de archivo con SU token; nunca el valor)"
  fi
  if [ -n "$token_env" ]; then
    printf '%s' "$token_env" | grep -Eq '^[A-Z][A-Z0-9_]*$' \
      || die "token_env inválido para '$tenant': '$token_env' (nombre de variable en mayúsculas)"
  fi
  if [ -n "$token_file" ]; then
    [ -f "$token_file" ] || die "tenant '$tenant': token_file no existe: $token_file"
    case "$token_file" in "$ROOT"/*|"$ROOT"/scripts/*)
      die "tenant '$tenant': token_file dentro del repo ($token_file): el token debe vivir FUERA del repo"
      ;;
    esac
  fi
  [ -z "${SEEN_TENANT[$tenant]:-}" ] || die "tenant duplicado en el CSV: '$tenant'"
  [ -z "${SEEN_LOGIN[$login]:-}" ] || die "login duplicado en el CSV: '$login'"
  SEEN_TENANT["$tenant"]=1
  SEEN_LOGIN["$login"]=1
  T_TENANT[N]="$tenant"; T_LOGIN[N]="$login"; T_ACCOUNT[N]="$account_id"
  T_SUB[N]="$subdomain"; T_PUBURL[N]="$pub_url"
  T_TOKENENV[N]="$token_env"; T_TOKENFILE[N]="$token_file"
  N=$((N + 1))
done < "$CONFIG"

[ "$N" -ge 1 ] || die "el CSV no trae ningún tenant válido"
log "Config validada: $N tenant(s): $(printf '%s ' "${T_TENANT[@]}")"
[ "$DRY_RUN" = 1 ] && log "DRY-RUN: no se toca Cloudflare ni se cambia ningún archivo"

# ---------------------------------------------------------------------------
# Resolución de tokens por tenant (en memoria; nunca se imprimen)
# ---------------------------------------------------------------------------
resolve_tenant_token() {
  # $1 = token_env, $2 = token_file. Imprime el token por stdout.
  local token_env="$1" token_file="$2" tok=""
  if [ -n "$token_env" ]; then
    tok="${!token_env:-}"
    [ -n "$tok" ] || { printf 'FALLO: la variable %s está vacía (tenant %s)\n' "$token_env" "$3" >&2; return 1; }
    printf '%s' "$tok"; return 0
  fi
  # token_file: una línea; avisar si es legible por otros.
  local perms
  perms="$(stat -c %a "$token_file" 2>/dev/null || echo "?")"
  case "$perms" in ?|600|400) : ;; *) printf 'AVISO: %s tiene permisos %s (se recomienda 600)\n' "$token_file" "$perms" >&2 ;; esac
  tok="$(tr -d '\r\n' < "$token_file")"
  [ -n "$tok" ] || { printf 'FALLO: el archivo %s está vacío\n' "$token_file" >&2; return 1; }
  printf '%s' "$tok"
}

# API de Cloudflare (sin mostrar el token; curl con -f para fallar en HTTP >=400)
cf_api() {
  # $1 = token, $2 = path (p. ej. /accounts/<id>/workers/scripts)
  curl -fsS -m 30 "https://api.cloudflare.com/client/v4$2" \
    -H "Authorization: Bearer $1" -H "Content-Type: application/json"
}

worker_exists_in_account() {
  # $1 = token, $2 = account_id, $3 = worker_name. 0 = existe.
  local body
  body="$(cf_api "$1" "/accounts/$2/workers/scripts/$3" 2>/dev/null)" || return 1
  printf '%s' "$body" | jq -e '.success == true' >/dev/null 2>&1
}

pages_project_exists_in_account() {
  # $1 = token, $2 = account_id. 0 = existe un proyecto Pages `avc` en ESA cuenta.
  local body
  body="$(cf_api "$1" "/accounts/$2/pages/projects/avc" 2>/dev/null)" || return 1
  printf '%s' "$body" | jq -e '.success == true' >/dev/null 2>&1
}

health_ok() {
  # $1 = worker_url, $2 = tenant. 0 = /api/health responde app=avc y tenant.
  "$HEALTH_CHECK" "$1" "$2" >/dev/null 2>&1
}

pages_has_entry() {
  # $1 = login, $2 = worker_url. 0 = el Pages COMPARTIDO sirve la entrada.
  curl -fsS -m 15 "$PAGES_URL/workers.json" 2>/dev/null \
    | jq -e --arg u "$1" --arg w "$2" '.[$u] == $w' >/dev/null 2>&1
}

# Nota: el bucket R2 ya NO se crea aquí. provision-tenant.sh lo crea en
# la cuenta del tenant si no existe (paso 1c, idempotente, Bug 1
# resuelto 2026-11-21). El checkout gratuito de R2 en el dashboard de
# esa cuenta sigue siendo previo necesario (si no está hecho, la
# creación del bucket falla con el motivo en el error de wrangler).

# ---------------------------------------------------------------------------
# Resumen final
# ---------------------------------------------------------------------------
declare -a SUM_TENANT SUM_STATUS SUM_URL SUM_NOTE
SUM_N=0
record_summary() {
  SUM_TENANT[SUM_N]="$1"; SUM_STATUS[SUM_N]="$2"; SUM_URL[SUM_N]="$3"; SUM_NOTE[SUM_N]="$4"
  SUM_N=$((SUM_N + 1))
}

# ---------------------------------------------------------------------------
# Bucle principal: SECUENCIA, un tenant cada vez
# ---------------------------------------------------------------------------
ANY_FAILED=0

for ((i = 0; i < N; i++)); do
  tenant="${T_TENANT[i]}"; login="${T_LOGIN[i]}"; account_id="${T_ACCOUNT[i]}"
  subdomain="${T_SUB[i]}"; pub_url="${T_PUBURL[i]}"
  token_env="${T_TOKENENV[i]}"; token_file="${T_TOKENFILE[i]}"
  worker_url="https://avc-${tenant}.${subdomain}.workers.dev"
  note=""
  log "===== Tenant $((i + 1))/$N: $tenant (login $login, cuenta $account_id) ====="

  # Token del tenant (en memoria).
  if [ "$DRY_RUN" = 1 ]; then
    if [ -n "$token_env" ]; then
      [ -n "${!token_env:-}" ] || { warn "DRY-RUN: la variable $token_env está vacía (en una ejecución real fallaría aquí)"; }
    fi
    token="DRY-RUN-TOKEN"
  else
    token="$(resolve_tenant_token "$token_env" "$token_file" "$tenant")" \
      || { record_summary "$tenant" "FALLÓ" "$worker_url" "no se pudo resolver su token (variable vacía o archivo vacío)"; ANY_FAILED=1; continue; }
  fi

  # --- Reanudación: ¿el Worker ya existe en su cuenta? ---
  if [ "$DRY_RUN" = 0 ]; then
    if worker_exists_in_account "$token" "$account_id" "avc-$tenant"; then
      log "El Worker avc-$tenant ya existe en su cuenta: comprobando si está completo"
      if health_ok "$worker_url" "$tenant" && pages_has_entry "$login" "$worker_url"; then
        record_summary "$tenant" "SALTADO" "$worker_url" "ya aprovisionado y verificado (salud OK + entrada en Pages)"
        token=""; continue
      fi
      if health_ok "$worker_url" "$tenant"; then
        note="Worker en vivo pero sin entrada en Pages: solo se repara el routing (abajo)"
        log "$note"
        # Cae al bloque de reparación de routing al final del bucle.
        :
      else
        record_summary "$tenant" "FALLÓ" "$worker_url" "el Worker existe pero /api/health no responde. provision-tenant.sh no se puede re-ejecutar (su paso 1 muere si el Worker existe): revisar a mano D1/secretos/despliegue."
        ANY_FAILED=1; token=""; continue
      fi
      # Routing-only: saltar provision, ir directo a la reparación.
      ROUTING_ONLY=1
    else
      ROUTING_ONLY=0
    fi
  else
    ROUTING_ONLY=0
    log "DRY-RUN: se comprobaría si el Worker avc-$tenant ya existe en su cuenta (salud + Pages) para saltarlo o reparar solo el routing"
  fi

  if [ "${ROUTING_ONLY:-0}" = 0 ]; then
    # --- Paso A: provision-tenant.sh con SU token y SU cuenta ---
    # Bug 1 y 2 resueltos (2026-11-21): provision crea el bucket R2 en
    # su cuenta (paso 1c), escribe --r2-public-url en el toml generado
    # y NO redespliega Pages/router con el token del tenant
    # (--skip-pages-deploy). Ya no se sustituye nada en la plantilla.
    if [ "$DRY_RUN" = 1 ]; then
      log "DRY-RUN: CLOUDFLARE_API_TOKEN=[REDACTADO] CLOUDFLARE_ACCOUNT_ID=$account_id scripts/provision-tenant.sh $tenant --user $login --account-id $account_id --workers-subdomain $subdomain --r2-public-url $pub_url --skip-pages-deploy --admin-token-env CLOUDFLARE_API_TOKEN_ADMIN --generate-password --dry-run"
      ( export CLOUDFLARE_API_TOKEN="$token" CLOUDFLARE_ACCOUNT_ID="$account_id"
        "$PROVISION" "$tenant" --user "$login" --account-id "$account_id" --workers-subdomain "$subdomain" --r2-public-url "$pub_url" --skip-pages-deploy --admin-token-env CLOUDFLARE_API_TOKEN_ADMIN --generate-password --dry-run ) \
        || { record_summary "$tenant" "FALLÓ" "$worker_url" "provision-tenant.sh --dry-run falló (ver salida arriba)"; ANY_FAILED=1; token=""; continue; }
    else
      PROV_RC=0
      ( export CLOUDFLARE_API_TOKEN="$token" CLOUDFLARE_ACCOUNT_ID="$account_id"
        "$PROVISION" "$tenant" --user "$login" --account-id "$account_id" --workers-subdomain "$subdomain" --r2-public-url "$pub_url" --skip-pages-deploy --admin-token-env CLOUDFLARE_API_TOKEN_ADMIN --generate-password ) || PROV_RC=$?
      if [ "$PROV_RC" -ne 0 ]; then
        # provision puede haber fallado tras crear D1/Worker/secretos/
        # usuario (p. ej. en un paso de red). No se descarta todavía:
        # se verifica la salud y se intenta la reparación de routing;
        # si la salud falla, es un FALLO real.
        warn "provision-tenant.sh salió con código $PROV_RC para '$tenant' (se verifica la salud antes de darlo por fallido)"
        if ! health_ok "$worker_url" "$tenant"; then
          record_summary "$tenant" "FALLÓ" "$worker_url" "provision-tenant.sh falló (código $PROV_RC) y /api/health no responde. Revisar la salida de provision; el Worker/D1 pueden estar a medias."
          ANY_FAILED=1; token=""; continue
        fi
        note="provision falló (código $PROV_RC) pero el Worker responde; se repara el routing con el token admin. "
      fi
      # Verificación: el toml generado trae la URL de ESTE tenant.
      if [ -f "$ROOT/wrangler.$tenant.toml" ]; then
        grep -q "^CLIP_PUBLIC_BASE_URL = \"$pub_url\"\$" "$ROOT/wrangler.$tenant.toml" \
          || { record_summary "$tenant" "FALLÓ" "$worker_url" "wrangler.$tenant.toml no trae CLIP_PUBLIC_BASE_URL=$pub_url (sigue la de la plantilla): el cerebro descargaría del bucket equivocado"; ANY_FAILED=1; token=""; continue; }
      else
        record_summary "$tenant" "FALLÓ" "$worker_url" "provision no generó wrangler.$tenant.toml"
        ANY_FAILED=1; token=""; continue
      fi
    fi
  fi

  # --- Paso B: salud del Worker (verificación independiente) ---
  if [ "$DRY_RUN" = 1 ]; then
    log "DRY-RUN: scripts/health-check.sh $worker_url $tenant (con reintentos)"
  else
    HEALTH_OK=0
    for attempt in 1 2 3; do
      if health_ok "$worker_url" "$tenant"; then HEALTH_OK=1; break; fi
      warn "health-check intento $attempt/3 falló para $tenant; reintentando en 15 s"
      sleep 15
    done
    [ "$HEALTH_OK" = 1 ] || { record_summary "$tenant" "FALLÓ" "$worker_url" "/api/health no responde con app=avc y tenant=$tenant tras 3 intentos"; ANY_FAILED=1; token=""; continue; }
    log "Salud OK: $worker_url responde app=avc, tenant=$tenant"
  fi

  # --- Paso C: ¿el Pages COMPARTIDO sirve la entrada? Si no, reparar
  #     el routing con el token ADMIN vía el modo --repair-pages-deploy
  #     de provision (Bug 2 resuelto 2026-11-21: ese modo verifica que
  #     la cuenta sea la dueña del Pages antes de desplegar nada; los
  #     despliegues crudos de Pages/router ya no se hacen aquí). ---
  if [ "$DRY_RUN" = 1 ]; then
    log "DRY-RUN: curl $PAGES_URL/workers.json y comprobar [$login] == $worker_url; si falta, provision-tenant.sh $tenant --repair-pages-deploy con el token admin"
  else
    if pages_has_entry "$login" "$worker_url"; then
      log "Pages compartido ya sirve workers.json con $login -> $worker_url"
    else
      warn "Pages compartido aún no trae $login -> $worker_url: reparando el routing con el token admin (modo --repair-pages-deploy)"
      ( export CLOUDFLARE_API_TOKEN="$ADMIN_TOKEN" CLOUDFLARE_ACCOUNT_ID="27afc37225a02fd92557f901c05dabee"
        "$PROVISION" "$tenant" --user "$login" --account-id "27afc37225a02fd92557f901c05dabee" --workers-subdomain "$subdomain" --repair-pages-deploy --admin-token-env CLOUDFLARE_API_TOKEN ) \
        || { record_summary "$tenant" "FALLÓ" "$worker_url" "provision-tenant.sh --repair-pages-deploy con token admin falló (ver su salida: archivos locales o cuenta no dueña del Pages)"; ANY_FAILED=1; token=""; continue; }
      PAGES_OK=0
      for attempt in 1 2 3; do
        if pages_has_entry "$login" "$worker_url"; then PAGES_OK=1; break; fi
        warn "workers.json en Pages aún no trae la entrada (intento $attempt/3); reintentando en 20 s"
        sleep 20
      done
      [ "$PAGES_OK" = 1 ] || { record_summary "$tenant" "FALLÓ" "$worker_url" "tras reparar el routing, Pages sigue sin servir workers.json con $login -> $worker_url (propagación o error de despliegue)"; ANY_FAILED=1; token=""; continue; }
      log "Routing reparado: Pages ya sirve $login -> $worker_url"
      note="${note}Routing reparado con token admin. "
    fi
  fi

  # --- Paso D: aviso de duplicados (segunda comprobación independiente;
  #     provision ya los reporta en su paso 7). No bloquea el OK. ---
  if [ "$DRY_RUN" = 0 ]; then
    if pages_project_exists_in_account "$token" "$account_id"; then
      note="${note}AVISO: existe un proyecto Pages 'avc' en la cuenta del tenant (duplicado creado por el paso 7 de provision con su token); borrarlo a mano en su dashboard (Pages → avc → Delete). No afecta al panel compartido. "
    fi
    if worker_exists_in_account "$token" "$account_id" "avc"; then
      note="${note}AVISO: existe un Worker 'avc' (router) en la cuenta del tenant (duplicado del paso 7); borrarlo a mano en su dashboard (Workers → avc → Delete). No afecta al panel compartido. "
    fi
  fi

  record_summary "$tenant" "OK" "$worker_url" "${note:-Listo. Pendientes manuales: OAuth Google/Meta, conectar y verificar cuentas, pulsar Iniciar, cron apagado hasta --enable-cron.}"
  token=""
done

# ---------------------------------------------------------------------------
# Resumen final por tenant
# ---------------------------------------------------------------------------
echo
echo "================ RESUMEN DEL EQUIPO ================"
printf '%-14s %-9s %-58s %s\n' "TENANT" "ESTADO" "WORKER URL" "NOTA / PENDIENTES"
for ((i = 0; i < SUM_N; i++)); do
  printf '%-14s %-9s %-58s %s\n' "${SUM_TENANT[i]}" "${SUM_STATUS[i]}" "${SUM_URL[i]}" "${SUM_NOTE[i]}"
done
echo "===================================================="
if [ "$DRY_RUN" = 1 ]; then
  echo "(dry-run: no se creó nada en Cloudflare ni se cambió ningún archivo)"
fi
echo
echo "Pendientes manuales por tenant OK (ver cabecera del script):"
echo "  1. OAuth Google/Meta en sus consolas + secretos en SU Worker."
echo "  2. Conectar y verificar YouTube, TikTok (Zernio), Meta, Twitch, Gemini."
echo "  3. Pulsar Iniciar en la pestaña Worker (el Worker nace parado)."
echo "  4. Encender el cron tras verificar: scripts/provision-tenant.sh <tenant> --enable-cron"
echo "  5. La contraseña del panel la mostró provision-tenant.sh UNA vez en pantalla:"
echo "     si no se anotó, no se puede recuperar (solo regenerar el hash)."

if [ "$ANY_FAILED" = 1 ]; then
  echo
  echo "FALLO: al menos un tenant quedó en FALLÓ. Re-ejecutar el mismo comando"
  echo "es seguro (los OK se saltan); los FALLÓ traen el motivo en el resumen."
  exit 1
fi
echo
echo "OK: todos los tenants del CSV quedaron OK o SALTADO."
exit 0
