#!/usr/bin/env bash
# provision-tenant.sh — Aprovisionamiento AUTOMÁTICO de un tenant nuevo del panel AVC.
#
# Camino de registro: dado un tenant nuevo (p. ej. `luis`) crea TODO lo que
# ese usuario necesita para entrar al panel, sin pasos manuales en Cloudflare:
#   D1 propia -> migraciones -> wrangler.<tenant>.toml -> secretos -> Worker
#   propio -> usuario del panel + settings base -> workers.json + router
#   -> verificación de salud.
#
# USO:
#   scripts/provision-tenant.sh <tenant> [opciones]
#
#   <tenant>            Nombre corto del tenant: solo [a-z0-9-], p. ej. `luis`.
#                       Worker: avc-<tenant> · D1: clips-avc-<tenant>.
#
#   --user <login>      Usuario de login del panel (defecto: el tenant).
#                       Puede diferir del tenant (p. ej. tenant `luis`,
#                       login `luisito`); workers.json y LOGIN_USER_MAP usan
#                       el login, el Worker y la D1 usan el tenant.
#   --role <rol>        Rol en panel_users (defecto: `usuario`; el código no
#                       lo comprueba hoy; seed-users.sql usa `dueno` para
#                       Sebastián — pasa --role dueno para paridad de dueño).
#   --generate-password Genera una contraseña aleatoria (20 caracteres) y la
#                       muestra UNA vez al final. Es el defecto si no se da
#                       --password ni --prompt-password.
#   --prompt-password   Pide la contraseña dos veces por TTY (sin eco).
#   --password <pw>     Usa esta contraseña. DESACONSEJADO: queda en el
#                       historial del shell y en `ps`; prefiere --generate-
#                       password o --prompt-password.
#   --workers-subdomain <sub>
#                       Subdominio workers.dev de la cuenta (defecto: se
#                       deriva de frontend/dist/workers.json, hoy
#                       `vikingosfoodspa`). La URL del Worker nuevo es
#                       https://avc-<tenant>.<sub>.workers.dev
#   --account-id <id>   Cuenta de Cloudflare (defecto: la de
#                       scripts/deploy_with_token.py).
#   --r2-public-url <url> | AVC_R2_PUBLIC_URL
#                       URL pública r2.dev del bucket R2 DE LA CUENTA
#                       DESTINO, p. ej. https://pub-<hash>.r2.dev. Es la
#                       que queda como CLIP_PUBLIC_BASE_URL en el
#                       wrangler.<tenant>.toml generado. OBLIGATORIA
#                       cuando --account-id es DISTINTO de la cuenta de
#                       Sebastián: si falta, el script FALLA antes de
#                       crear nada (nunca copia silenciosamente la URL
#                       de Sebastián a otra cuenta). En la cuenta de
#                       Sebastián (defecto) se usa la de la plantilla
#                       si no se da. Se rechaza si es la URL de
#                       Sebastián con otra cuenta (copia-pega erróneo).
#   --r2-bucket <nombre> | AVC_R2_BUCKET
#                       Bucket R2 del tenant (defecto: clips-avc). Se
#                       crea en la cuenta destino si no existe (paso
#                       1c, idempotente). Si crear `clips-avc` falla
#                       por conflicto de nombre, re-ejecuta con
#                       --r2-bucket clips-avc-<tenant> y su URL r2.dev.
#   --skip-pages-deploy No redesplegar Pages `avc` ni el router `avc`
#                       en el paso 7. El routing local (workers.json +
#                       router.toml) se parchea y verifica igual, pero
#                       el despliegue NO se hace con el token que se
#                       pasó (que puede ser el del tenant: no
#                       actualizaría el panel compartido y podría
#                       crear un Pages/Worker `avc` duplicado en su
#                       cuenta). Al final se indica el comando exacto
#                       para repararlo con el token admin
#                       (--repair-pages-deploy). Imprescindible en
#                       alta multi-cuenta (onboard-team.sh lo pasa).
#   --admin-token-env <VAR>
#                       Nombre de la variable de entorno donde vive el
#                       token ADMIN (cuenta dueña de Pages/router).
#                       Defecto: CLOUDFLARE_API_TOKEN_ADMIN. Solo se
#                       usa en los mensajes y en --repair-pages-deploy.
#   --repair-pages-deploy
#                       Modo aparte: SOLO redespliega Pages `avc` y el
#                       router `avc` con el token admin (nunca crea
#                       D1/Worker/secretos/usuario). Antes verifica
#                       que workers.json y router.toml ya traigan la
#                       entrada del tenant y que ESTA cuenta sea la
#                       dueña del Pages `avc`; si algo falla, no
#                       despliega nada. Uso: $0 <tenant>
#                       --repair-pages-deploy [--admin-token-env VAR]
#
#   Secretos compartidos (mismo valor en todos los tenants; ver MATRIZ abajo):
#   se resuelven en este orden: flag > variable de entorno > pregunta por
#   TTY (sin eco) > FALLO con mensaje claro. NUNCA se inventa uno.
#     --github-token <v>          | AVC_GITHUB_TOKEN
#     --telegram-bot-token <v>    | AVC_TELEGRAM_BOT_TOKEN
#     --telegram-webhook-secret <v> | AVC_TELEGRAM_WEBHOOK_SECRET
#     --bridge-token <v>          | AVC_BRIDGE_TOKEN
#   --encryption-key <v> | AVC_CREDENTIAL_ENCRYPTION_KEY
#                       SOLO si se migran credenciales cifradas desde la
#                       instalación vieja: fija CREDENTIAL_ENCRYPTION_KEY al
#                       valor maestro existente. Si no se da, se genera uno
#                       fresco (ver MATRIZ).
#   --force             Sobrescribe wrangler.<tenant>.toml si ya existe con
#                       valores distintos.
#   --force-secrets     Vuelve a poner TODOS los secretos aunque ya existan
#                       (rotación). Sin esto, un re-ejecución salta los que
#                       ya están puestos.
#   --skip-tests        No corre `bun test test/router.test.ts` tras el
#                       parche de código (el typecheck `tsc` sigue siendo
#                       obligatorio).
#   --skip-typecheck    No corre `tsc --noEmit` (SOLO para probar el script
#                       en una copia sin node_modules; nunca en real).
#   --no-code-patch     No parchea worker/src/auth.ts ni router.ts: si el
#                       tenant no está ya en TENANTS, el script falla con
#                       las instrucciones manuales. Útil si el parche lo
#                       hace otra persona.
#   --skip-cloud        Ejecuta SOLO los pasos locales (parche de código,
#                       wrangler.<tenant>.toml, workers.json, router.toml,
#                       generación del SQL de siembra en un temporal que se
#                       conserva) sin tocar Cloudflare. Para revisar el
#                       diff local antes de crear recursos.
#   --dry-run           Imprime los comandos que ejecutaría y sale sin
#                       escribir archivos ni tocar Cloudflare. No genera
#                       contraseña ni secretos reales.
#   --enable-cron       Enciende el cron de un Worker YA aprovisionado
#                       (modo aparte: no crea nada). Solo tras verificar
#                       salud (/api/health), worker_running=false, los
#                       6 secretos y el presupuesto de las migraciones
#                       pendientes (A5, 2026-11-20): si algo falla, no
#                       enciende. El Worker nuevo SIEMPRE se despliega
#                       con el cron apagado; este es el único camino
#                       para encenderlo. Uso: $0 <tenant> --enable-cron
#   --help              Esta ayuda.
#
# REQUISITOS:
#   - bash, python3, jq, curl, openssl, node, bun (bun ya lo usa deploy.sh
#     para los tests; aquí ejecuta el código TS del proyecto como fuente
#     única de verdad para el hash y los settings).
#   - worker/node_modules instalado (wrangler 4.x en
#     worker/node_modules/.bin/wrangler; si falta: (cd worker && npm ci)).
#     Solo para ejecuciones reales: en --dry-run no se exige (wrangler
#     nunca se ejecuta en ese modo).
#   - Token Cloudflare, MISMO PATRÓN que scripts/deploy_with_token.py:
#     si CLOUDFLARE_API_TOKEN no está en el entorno, el script lo obtiene
#     con dynamic_credential_entry("custom.cloudflare", "access_token") y
#     lo exporta solo en memoria (nunca se imprime ni se guarda en archivo).
#     CLOUDFLARE_ACCOUNT_ID por defecto es el de deploy_with_token.py.
#
# MATRIZ DE SECRETOS (los 8 del Worker; GOOGLE_* quedan FUERA, ver abajo):
#   GENERADOS FRESCOS por tenant (32 bytes base64, openssl rand):
#     SESSION_SECRET               Sesiones HMAC de ESTE Worker. Fresco por
#                                  tenant: la sesión la firma y verifica el
#                                  mismo Worker; el router no verifica firmas.
#     CREDENTIAL_ENCRYPTION_KEY    Clave maestra de cifrado de credenciales
#                                  de ESTE tenant. Fresco por defecto: la D1
#                                  nueva está vacía, no hay nada que descifrar.
#                                  CONFLICTO CONOCIDO: wrangler.sebastian.toml
#                                  y wrangler.kaez.toml dicen que DEBE ser el
#                                  MISMO valor en todos los tenants porque de
#                                  él se deriva una clave por tenant (HKDF) y
#                                  las credenciales MIGRADAS siguen
#                                  descifrando. Eso solo importa si se migran
#                                  credenciales cifradas desde otra D1: en ese
#                                  caso pasa --encryption-key con el valor
#                                  maestro existente (hay que pedirlo al
#                                  operador; NO se puede leer de Cloudflare).
#   COMPARTIDOS (mismo valor que en los Workers existentes):
#     GITHUB_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, BRIDGE_TOKEN
#       Mismo bot de Telegram, mismo repo del cerebro y mismo puente para
#       todos los tenants.
#       LIMITACIÓN DURA (Cloudflare): los valores de los secretos NO se
#       pueden leer por API ni con `wrangler secret list` (solo devuelve
#       NOMBRES). No existe forma de "copiarlos" del Worker existente.
#       El operador DEBE aportarlos (flag/env/pregunta por TTY); si falta
#       uno, el script falla ANTES de crear nada, con el nombre exacto.
#   NO SE PONEN (los crea el usuario en sus consolas; el panel lo guía):
#     GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET — OAuth de Google. Sin ellos el
#       panel funciona con login por contraseña; el onboarding avisa qué
#       falta y dónde crearlo. Tampoco se ponen secretos de Meta/Twitch/
#       Gemini: son credenciales DEL USUARIO y se conectan en el panel
#       (Cuentas / onboarding), nunca como secretos del Worker.
#
# QUÉ QUEDA FUERA (lo hace el usuario en el panel, guiado por el onboarding):
#   - Registro en Google Cloud / Meta developers (OAuth) y pegado de claves
#     de Twitch/Gemini; conectar YouTube/TikTok/Meta/Twitch/Gemini y
#     verificar cada cuenta (botón Verificar del panel).
#   - Pulsar Iniciar en la pestaña Worker (el Worker nace parado).
#     El cron TAMBIÉN nace apagado (A5, 2026-11-20): se enciende solo
#     con --enable-cron, tras verificar salud, D1 y secretos.
#   - Elegir influencers y permisos de clipping (el cerebro solo corta de
#     quien tiene permiso).
#   - El repo del cerebro NO se toca: el dispatch ya lleva tenant y
#     brain_token; el cerebro no necesita nada por tenant nuevo.
#
# IDEMPOTENCIA: re-ejecutar es seguro. Cada paso detecta qué ya existe y lo
# salta: bucket R2 existente se reutiliza (paso 1c), D1 existente se
# reutiliza, migraciones ya aplicadas no se repiten,
# secretos existentes no se reponen (salvo --force-secrets), el usuario y
# los settings existentes NO se sobrescriben (INSERT OR IGNORE /
# ON CONFLICT DO NOTHING), workers.json y router.toml solo cambian si el
# valor difiere. El parche de código es exacto: si ya está aplicado, se
# detecta y no se duplica.
#
# SEGURIDAD: `set -euo pipefail`, umask 077, temporales con mktemp que se
# borran con trap. NINGÚN secreto se imprime en logs: los valores solo
# viajan por stdin (pipe) a `wrangler secret put` y a `bun` (hash), nunca
# en argv ni en archivos. La contraseña generada se muestra UNA vez en el
# resumen final y no se guarda en ningún archivo. El hash PBKDF2 se calcula
# con la propia función hashPassword del proyecto (worker/src/auth.ts) y
# los settings base con extractBaseSettings(effectiveSettings({})) de
# worker/src/db.ts — la MISMA base que /api/onboarding/copy-settings —, no
# con valores copiados a mano. OJO: esos defaults del código son
# min_virality=50 / min_seo=90; las reglas vivas de sebastian (45/80) viven
# en SU fila de settings, no en el código: el tenant nuevo nace con los
# defaults del código y los ajusta en Ajustes.
#
# FUTURO ENDPOINT /register (qué le faltaría para llamar a este script):
#   Parámetros: { tenant, login_user, password | generate: true, role?,
#     shared_secrets: { github_token, telegram_bot_token,
#     telegram_webhook_secret, bridge_token } } — los compartidos NO pueden
#     leerse de Cloudflare, así que el endpoint los recibiría del operador
#     (o de un almacén de secretos) en la llamada, nunca los inventaría.
#   El endpoint ejecutaría este script (o su lógica) como trabajo en
#   segundo plano con un job_id, y devolvería al usuario: { ok, tenant,
#     worker_url, panel_url, login_user, password_mostrada_una_vez (solo si
#     se generó), estado_por_paso, pendientes: ["oauth google/meta",
#     "conectar cuentas", "pulsar Iniciar"] }. La contraseña solo se
#     devuelve UNA vez en esa respuesta; jamás se guarda ni se re-devuelve.
#   NO existe hoy: no hay endpoint de signup ni aprovisionamiento en el
#   Worker (el onboarding actual es solo OAuth + copy-settings).
#
set -euo pipefail
umask 077

# ---------------------------------------------------------------------------
# Rutas y constantes
# ---------------------------------------------------------------------------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRANGLER="$ROOT/worker/node_modules/.bin/wrangler"
WORKER_DIR="$ROOT/worker"
PAGES_PROJECT="avc"
PAGES_URL="https://avc-em9.pages.dev"
ACCOUNT_ID_DEFAULT="27afc37225a02fd92557f901c05dabee"  # mismo que deploy_with_token.py
R2_BUCKET="${AVC_R2_BUCKET:-clips-avc}"
R2_PUBLIC_URL="${AVC_R2_PUBLIC_URL:-}"
SKIP_PAGES_DEPLOY=0
ADMIN_TOKEN_ENV="CLOUDFLARE_API_TOKEN_ADMIN"
REPAIR_PAGES_DEPLOY=0
PAGES_DEPLOY_SKIPPED=0
GITHUB_REPO_DEFAULT="exe2027/viral-clip-agent"  # se verifica contra la plantilla
TEMPLATE_TOML="$ROOT/wrangler.sebastian.toml"
ROUTER_TOML="$ROOT/wrangler.router.toml"
WORKERS_JSON="$ROOT/frontend/dist/workers.json"

TENANT=""
LOGIN_USER=""
ROLE="usuario"
PASSWORD=""
PASSWORD_MODE="generate"  # generate | prompt | given
WORKERS_SUBDOMAIN=""
ACCOUNT_ID="$ACCOUNT_ID_DEFAULT"
ENCRYPTION_KEY_OVERRIDE=""
GITHUB_TOKEN="${AVC_GITHUB_TOKEN:-}"
TELEGRAM_BOT_TOKEN="${AVC_TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_WEBHOOK_SECRET="${AVC_TELEGRAM_WEBHOOK_SECRET:-}"
BRIDGE_TOKEN="${AVC_BRIDGE_TOKEN:-}"
FORCE=0
FORCE_SECRETS=0
SKIP_TESTS=0
SKIP_TYPECHECK=0
NO_CODE_PATCH=0
SKIP_CLOUD=0
DRY_RUN=0
ENABLE_CRON=0

TMPDIR_WORK="$(mktemp -d "${TMPDIR:-/tmp}/provision-tenant.XXXXXX")"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

# ---------------------------------------------------------------------------
# Ayuda y argumentos
# ---------------------------------------------------------------------------
usage() {
  sed -n '2,/^set -euo pipefail/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//; /^$/d' | head -n -1
  echo "Uso: $0 <tenant> [opciones]"
  echo "Ejemplo: $0 luis --generate-password"
  echo "Ejemplo: $0 luis --user luisito --prompt-password"
  echo "Dry-run: $0 luis --dry-run"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --user) LOGIN_USER="$2"; shift 2 ;;
    --role) ROLE="$2"; shift 2 ;;
    --password) PASSWORD="$2"; PASSWORD_MODE="given"; shift 2 ;;
    --prompt-password) PASSWORD_MODE="prompt"; shift ;;
    --generate-password) PASSWORD_MODE="generate"; shift ;;
    --workers-subdomain) WORKERS_SUBDOMAIN="$2"; shift 2 ;;
    --account-id) ACCOUNT_ID="$2"; shift 2 ;;
    --r2-public-url) R2_PUBLIC_URL="$2"; shift 2 ;;
    --r2-bucket) R2_BUCKET="$2"; shift 2 ;;
    --skip-pages-deploy) SKIP_PAGES_DEPLOY=1; shift ;;
    --admin-token-env) ADMIN_TOKEN_ENV="$2"; shift 2 ;;
    --repair-pages-deploy) REPAIR_PAGES_DEPLOY=1; shift ;;
    --github-token) GITHUB_TOKEN="$2"; shift 2 ;;
    --telegram-bot-token) TELEGRAM_BOT_TOKEN="$2"; shift 2 ;;
    --telegram-webhook-secret) TELEGRAM_WEBHOOK_SECRET="$2"; shift 2 ;;
    --bridge-token) BRIDGE_TOKEN="$2"; shift 2 ;;
    --encryption-key) ENCRYPTION_KEY_OVERRIDE="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --force-secrets) FORCE_SECRETS=1; shift ;;
    --skip-tests) SKIP_TESTS=1; shift ;;
    --skip-typecheck) SKIP_TYPECHECK=1; shift ;;
    --no-code-patch) NO_CODE_PATCH=1; shift ;;
    --skip-cloud) SKIP_CLOUD=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --enable-cron) ENABLE_CRON=1; shift ;;
    --) shift; break ;;
    -*) echo "FALLO: opción desconocida: $1 (usa --help)" >&2; exit 2 ;;
    *) if [ -n "$TENANT" ]; then echo "FALLO: tenant ya dado ($TENANT); sobra: $1" >&2; exit 2; fi
       TENANT="$1"; shift ;;
  esac
done
[ -n "$TENANT" ] || { echo "FALLO: falta el tenant. Uso: $0 <tenant> [opciones]" >&2; exit 2; }
[ -z "$LOGIN_USER" ] && LOGIN_USER="$TENANT"

DB_NAME="clips-avc-$TENANT"
WORKER_NAME="avc-$TENANT"
TENANT_ENV="$(printf '%s' "$TENANT" | tr 'a-z-' 'A-Z_')"
ROUTER_URL_VAR="WORKER_${TENANT_ENV}_URL"

# ---------------------------------------------------------------------------
# Salida y ejecución (sin secretos en logs: ningún valor secreto viaja en argv)
# ---------------------------------------------------------------------------
log()  { printf '== %s\n' "$*"; }
warn() { printf 'AVISO: %s\n' "$*" >&2; }
die()  { printf 'FALLO: %s\n' "$*" >&2; exit 1; }

# run: ejecuta (o imprime en dry-run). NUNCA se le pasa un secreto en argv.
run() {
  if [ "$DRY_RUN" = 1 ]; then printf 'DRY-RUN: %s\n' "$*"; return 0; fi
  printf '$ %s\n' "$*"
  "$@"
}

# run_in: como run, con cwd.
run_in() {
  local dir="$1"; shift
  if [ "$DRY_RUN" = 1 ]; then printf 'DRY-RUN: (cd %s) %s\n' "$dir" "$*"; return 0; fi
  printf '$ (cd %s) %s\n' "$dir" "$*"
  (cd "$dir" && "$@")
}

# cf_api_get <path>: GET a la API de Cloudflare sin mostrar el token.
cf_api_get() {
  local path="$1"
  if [ "$DRY_RUN" = 1 ]; then
    printf 'DRY-RUN: curl -fsS https://api.cloudflare.com/client/v4%s (Authorization: Bearer [REDACTADO])\n' "$path"
    return 0
  fi
  curl -fsS -m 30 "https://api.cloudflare.com/client/v4${path}" \
    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    -H "Content-Type: application/json"
}

# report_tenant_account_duplicates (Bug 2, 2026-11-21): si la cuenta con la
# que se está aprovisionando NO es la dueña del panel compartido y en ella
# existe un proyecto Pages `avc` o un Worker `avc`, son DUPLICADOS creados
# por un despliegue anterior con el token del tenant (el panel compartido
# vive en la cuenta de Sebastián). Solo se REPORTAN, con el comando exacto
# para borrarlos: este script NUNCA borra nada solo. En la cuenta dueña
# (ACCOUNT_ID_DEFAULT) no hay nada que reportar: ahí son los reales.
report_tenant_account_duplicates() {
  [ "${CLOUDFLARE_ACCOUNT_ID:-$ACCOUNT_ID}" = "$ACCOUNT_ID_DEFAULT" ] && return 0
  local acct="${CLOUDFLARE_ACCOUNT_ID:-$ACCOUNT_ID}"
  if cf_api_get "/accounts/${acct}/pages/projects/${PAGES_PROJECT}" 2>/dev/null \
    | jq -e '.success == true' >/dev/null 2>&1; then
    warn "AVISO: la cuenta $acct tiene un proyecto Pages '$PAGES_PROJECT' duplicado (creado por un despliegue con el token del tenant; el panel compartido vive en la cuenta de Sebastián). NO se borra solo. Para borrarlo: dashboard de esa cuenta → Pages → $PAGES_PROJECT → Delete; o: curl -X DELETE https://api.cloudflare.com/client/v4/accounts/${acct}/pages/projects/${PAGES_PROJECT} -H \"Authorization: Bearer <token del tenant>\""
  fi
  if cf_api_get "/accounts/${acct}/workers/scripts/avc" 2>/dev/null \
    | jq -e '.success == true' >/dev/null 2>&1; then
    warn "AVISO: la cuenta $acct tiene un Worker 'avc' (router) duplicado (creado por un despliegue con el token del tenant; el router compartido vive en la cuenta de Sebastián). NO se borra solo. Para borrarlo: dashboard de esa cuenta → Workers → avc → Delete; o: curl -X DELETE https://api.cloudflare.com/client/v4/accounts/${acct}/workers/scripts/avc -H \"Authorization: Bearer <token del tenant>\""
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "falta el comando '$1' en el PATH"
}

# Todo lo demás corre desde la raíz del proyecto (los heredocs python y
# los `bun -e` usan rutas relativas a ella).
cd "$ROOT"

# ---------------------------------------------------------------------------
# Modo --enable-cron (A5): enciende el cron de un Worker YA aprovisionado.
# No crea nada. Solo se enciende tras verificar, en este orden:
#   salud (/api/health) -> worker_running=false -> 6 secretos presentes
#   -> presupuesto de migraciones pendientes -> parche del toml ->
#   redespliegue -> verificación por API de que el cron existe.
# Si cualquier verificación falla, NO se enciende (fail closed).
# El Worker sigue parado (worker_running=false) hasta que el usuario
# pulse Iniciar en el panel: encender el cron no pone nada en marcha.
# ---------------------------------------------------------------------------
if [ "$ENABLE_CRON" = 1 ]; then
  log "Modo --enable-cron para el tenant '$TENANT'"
  printf '%s' "$TENANT" | grep -Eq '^[a-z0-9][a-z0-9-]{0,30}$' \
    || die "tenant inválido '$TENANT': solo [a-z0-9-], máx 31 caracteres"
  TENANT_TOML="$ROOT/wrangler.$TENANT.toml"
  if [ "$DRY_RUN" = 0 ]; then
    [ -f "$TENANT_TOML" ] || die "no existe $TENANT_TOML: el tenant '$TENANT' no está aprovisionado (primero provision-tenant.sh $TENANT)"
    D1_ID_EC="$(grep '^database_id' "$TENANT_TOML" | sed 's/.*"\([^"]*\)".*/\1/' | head -n 1)"
    [ -n "$D1_ID_EC" ] || die "no se pudo leer database_id de $TENANT_TOML"
  fi
  if [ -z "$WORKERS_SUBDOMAIN" ]; then
    WORKERS_SUBDOMAIN="$(python3 - "$WORKERS_JSON" <<'PY'
import json, re, sys
data = json.load(open(sys.argv[1]))
for v in data.values():
    m = re.match(r"https://avc-[a-z0-9-]+\.([a-z0-9-]+)\.workers\.dev/?$", str(v).strip())
    if m:
        print(m.group(1)); break
PY
)" || die "no se pudo derivar el subdominio workers.dev de $WORKERS_JSON (pasa --workers-subdomain)"
    [ -n "$WORKERS_SUBDOMAIN" ] || die "workers.json no contiene ninguna URL avc-*.workers.dev; pasa --workers-subdomain"
  fi
  WORKER_URL_EC="https://avc-${TENANT}.${WORKERS_SUBDOMAIN}.workers.dev"
  if [ "$DRY_RUN" = 1 ]; then
    log "DRY-RUN: se verificaría (en orden, sin encender si algo falla):"
    log "DRY-RUN:   1. scripts/health-check.sh $WORKER_URL_EC $TENANT"
    log "DRY-RUN:   2. worker_running=false en $WORKER_URL_EC/api/health"
    log "DRY-RUN:   3. los 6 secretos presentes (wrangler secret list)"
    log "DRY-RUN:   4. presupuesto de migraciones pendientes (check-migration-budget.py --migrations-dir migrations --d1 <id del toml>) y aplicarlas si las hay"
    log "DRY-RUN:   5. añadir [triggers] crons = [\"*/5 15-18 * * *\", \"*/5 21-23,0-3 * * *\"] a $TENANT_TOML y redesplegar el Worker"
    log "DRY-RUN:   6. verificar por API que el cron existe (GET .../workers/scripts/avc-$TENANT/schedules)"
    echo "OK (dry-run): enable-cron no haría nada más; el Worker seguiría parado hasta pulsar Iniciar."
    exit 0
  fi
  for c in python3 jq curl; do require_cmd "$c"; done
  [ -x "$WRANGLER" ] || die "falta wrangler en $WRANGLER"
  if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
    CLOUDFLARE_API_TOKEN="$(python3 - <<'PY'
import sys
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import dynamic_credential_entry
print(dynamic_credential_entry("custom.cloudflare", "access_token")["surrogate"])
PY
)" || die "no se pudo obtener el token Cloudflare"
    [ -n "$CLOUDFLARE_API_TOKEN" ] || die "el token Cloudflare llegó vacío"
  fi
  export CLOUDFLARE_API_TOKEN
  export CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-$ACCOUNT_ID}"
  log "1/6 salud: $WORKER_URL_EC"
  "$ROOT/scripts/health-check.sh" "$WORKER_URL_EC" "$TENANT" \
    || die "/api/health no responde con app=avc y tenant=$TENANT; no se enciende el cron"
  log "2/6 worker_running=false (el Worker nace parado; el cron no pone nada en marcha solo)"
  HEALTH_EC="$(curl -fsS -m 15 "$WORKER_URL_EC/api/health")" \
    || die "no se pudo leer $WORKER_URL_EC/api/health"
  printf '%s' "$HEALTH_EC" | grep -q '"worker_running":false' \
    || die "/api/health dice worker_running=true; no se enciende el cron hasta aclararlo"
  log "3/6 secretos presentes (nombres; Cloudflare no devuelve valores)"
  SECRETS_EC="$( (cd "$WORKER_DIR" && "$WRANGLER" secret list --config "../wrangler.$TENANT.toml" --format json) \
    | jq -r '.[].name' 2>/dev/null || true)"
  for s in SESSION_SECRET CREDENTIAL_ENCRYPTION_KEY GITHUB_TOKEN TELEGRAM_BOT_TOKEN TELEGRAM_WEBHOOK_SECRET BRIDGE_TOKEN; do
    printf '%s\n' "$SECRETS_EC" | grep -qx "$s" \
      || die "falta el secreto $s en $WORKER_NAME; no se enciende el cron"
  done
  log "4/6 presupuesto de migraciones pendientes (A5)"
  python3 "$ROOT/scripts/check-migration-budget.py" \
    --migrations-dir "$ROOT/migrations" --d1 "$D1_ID_EC" \
    || die "el chequeo de presupuesto rechazó o no pudo decidir; no se enciende el cron"
  run_in "$WORKER_DIR" "$WRANGLER" d1 migrations apply "$DB_NAME" \
    --config "../wrangler.$TENANT.toml" --remote
  log "5/6 parche del toml: añadir [triggers] y redesplegar"
  python3 - "$TENANT_TOML" <<'PY'
import re, sys
path = sys.argv[1]
s = open(path).read()
if re.search(r"^\[triggers\]\ncrons = \[\"\*/5 15-18", s, re.M):
    print("  el toml ya trae el cron: no se parchea")
    sys.exit(0)
anchor = s.find("[[d1_databases]]")
if anchor == -1:
    print(f"FALLO: no hay [[d1_databases]] en {path} para anclar [triggers]", file=sys.stderr)
    sys.exit(1)
block = ('# Cron (A5): APAGADO en el primer despliegue; encendido con\n'
         '# --enable-cron tras verificar salud, D1 y secretos.\n'
         '[triggers]\ncrons = ["*/5 15-18 * * *", "*/5 21-23,0-3 * * *"]\n\n')
open(path, "w").write(s[:anchor] + block + s[anchor:])
print("  [triggers] añadido al toml")
PY
  grep -q '^crons = \["\*/5 15-18' "$TENANT_TOML" \
    || die "el parche del toml no dejó el cron puesto"
  run_in "$WORKER_DIR" "$WRANGLER" deploy --config "../wrangler.$TENANT.toml"
  log "6/6 verificación por API: el cron existe"
  CRON_EC="$(cf_api_get "/accounts/${CLOUDFLARE_ACCOUNT_ID}/workers/scripts/${WORKER_NAME}/schedules" \
    | jq -r '.result.schedules[].cron' 2>/dev/null || true)"
  echo "$CRON_EC" | grep -q "^\*/5 15-18 " && echo "$CRON_EC" | grep -q "^\*/5 21-23,0-3 " \
    || die "tras el despliegue la API no lista el cron '* * * * *' en $WORKER_NAME (llegó: '$CRON_EC'); revisa a mano"
  echo
  echo "OK: cron encendido en $WORKER_NAME (* * * * *)"
  echo "  El Worker sigue PARADO (worker_running=false): nada corre hasta"
  echo "  que el usuario pulse Iniciar en el panel."
  exit 0
fi

# ---------------------------------------------------------------------------
# Modo --repair-pages-deploy (Bug 2, 2026-11-21): SOLO redespliega Pages
# `avc` y el router `avc` con el token ADMIN. No crea D1/Worker/secretos/
# usuario ni toca workers.json/router.toml: esos archivos ya tienen que
# traer la entrada del tenant (los parchea el paso 7 de provision).
# Fail closed, en orden:
#   1. workers.json local trae login -> Worker URL del tenant.
#   2. wrangler.router.toml local trae WORKER_<TENANT>_URL y la entrada
#      en LOGIN_USER_MAP.
#   3. ESTA cuenta es la dueña del Pages `avc` (GET pages/projects/avc
#      con éxito). Si el token es el del tenant, la cuenta NO lo tiene
#      y se muere aquí: así nunca se crea un Pages duplicado.
#   4. Despliegue de Pages + router y verificación de que el Pages
#      compartido ya sirve la entrada (3 intentos).
# El token admin se resuelve: --admin-token-env VAR > CLOUDFLARE_API_TOKEN
# en el entorno > dynamic_credential_entry("custom.cloudflare").
# ---------------------------------------------------------------------------
if [ "$REPAIR_PAGES_DEPLOY" = 1 ]; then
  log "Modo --repair-pages-deploy para el tenant '$TENANT'"
  printf '%s' "$TENANT" | grep -Eq '^[a-z0-9][a-z0-9-]{0,30}$' \
    || die "tenant inválido '$TENANT': solo [a-z0-9-], máx 31 caracteres"
  [ -f "$WORKERS_JSON" ] || die "falta $WORKERS_JSON"
  [ -f "$ROUTER_TOML" ] || die "falta $ROUTER_TOML"
  if [ -z "$WORKERS_SUBDOMAIN" ]; then
    WORKERS_SUBDOMAIN="$(python3 - "$WORKERS_JSON" <<'PY'
import json, re, sys
data = json.load(open(sys.argv[1]))
for v in data.values():
    m = re.match(r"https://avc-[a-z0-9-]+\.([a-z0-9-]+)\.workers\.dev/?$", str(v).strip())
    if m:
        print(m.group(1)); break
PY
)" || die "no se pudo derivar el subdominio workers.dev de $WORKERS_JSON (pasa --workers-subdomain)"
    [ -n "$WORKERS_SUBDOMAIN" ] || die "workers.json no contiene ninguna URL avc-*.workers.dev; pasa --workers-subdomain"
  fi
  WORKER_URL_RP="https://avc-${TENANT}.${WORKERS_SUBDOMAIN}.workers.dev"
  log "1/4 archivos locales: workers.json[$LOGIN_USER] y router.toml ($ROUTER_URL_VAR)"
  python3 -c "import json; d=json.load(open('$WORKERS_JSON')); assert d['$LOGIN_USER']=='$WORKER_URL_RP', 'workers.json sin la entrada'" \
    || die "workers.json no trae $LOGIN_USER -> $WORKER_URL_RP (primero provision-tenant.sh $TENANT, que parchea el routing en su paso 7)"
  grep -q "^$ROUTER_URL_VAR = \"$WORKER_URL_RP\"\$" "$ROUTER_TOML" \
    || die "router.toml no trae $ROUTER_URL_VAR = $WORKER_URL_RP"
  python3 - "$LOGIN_USER" "$TENANT" <<'PY'
import json, sys, tomllib
user, tenant = sys.argv[1], sys.argv[2]
with open("wrangler.router.toml", "rb") as f:
    m = tomllib.load(f)["vars"]["LOGIN_USER_MAP"]
assert json.loads(m)[user] == tenant, "LOGIN_USER_MAP sin la entrada"
PY
  if [ "$DRY_RUN" = 1 ]; then
    log "DRY-RUN: se verificaría que esta cuenta es la dueña del Pages $PAGES_PROJECT (GET /accounts/<id>/pages/projects/$PAGES_PROJECT)"
    log "DRY-RUN: wrangler pages deploy frontend/dist --project-name $PAGES_PROJECT"
    log "DRY-RUN: (cd worker) wrangler deploy --config ../wrangler.router.toml"
    log "DRY-RUN: curl $PAGES_URL/workers.json y comprobar [$LOGIN_USER] == $WORKER_URL_RP"
    echo "OK (dry-run): repair-pages-deploy no desplegaría nada más."
    exit 0
  fi
  for c in python3 jq curl; do require_cmd "$c"; done
  [ -x "$WRANGLER" ] || die "falta wrangler en $WRANGLER"
  # Token admin (nunca se imprime): --admin-token-env > CLOUDFLARE_API_TOKEN > surrogate.
  if [ -n "${!ADMIN_TOKEN_ENV:-}" ]; then
    CLOUDFLARE_API_TOKEN="${!ADMIN_TOKEN_ENV}"
    log "Token admin tomado de \$$ADMIN_TOKEN_ENV"
  elif [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
    log "Token admin tomado de CLOUDFLARE_API_TOKEN"
  else
    CLOUDFLARE_API_TOKEN="$(python3 - <<'PY'
import sys
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import dynamic_credential_entry
print(dynamic_credential_entry("custom.cloudflare", "access_token")["surrogate"])
PY
)" || die "no se pudo obtener el token admin (pasa --admin-token-env <VAR> o CLOUDFLARE_API_TOKEN)"
    [ -n "$CLOUDFLARE_API_TOKEN" ] || die "el token admin llegó vacío"
  fi
  export CLOUDFLARE_API_TOKEN
  export CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-$ACCOUNT_ID}"
  log "2/4 esta cuenta es la dueña del Pages $PAGES_PROJECT"
  cf_api_get "/accounts/${CLOUDFLARE_ACCOUNT_ID}/pages/projects/${PAGES_PROJECT}" \
    | jq -e '.success == true' >/dev/null 2>&1 \
    || die "la cuenta ${CLOUDFLARE_ACCOUNT_ID} NO es la dueña del proyecto Pages '$PAGES_PROJECT' (¿token del tenant?). Ejecuta este modo con el token ADMIN de la cuenta de Sebastián."
  log "3/4 redesplegando Pages ($PAGES_PROJECT) y el router"
  run_in "$ROOT" "$WRANGLER" pages deploy frontend/dist --project-name "$PAGES_PROJECT"
  run_in "$WORKER_DIR" "$WRANGLER" deploy --config ../wrangler.router.toml
  log "4/4 verificación: Pages sirve workers.json con $LOGIN_USER -> $WORKER_URL_RP"
  PAGES_OK=0
  for i in 1 2 3; do
    if curl -fsS -m 15 "$PAGES_URL/workers.json" \
      | jq -e --arg u "$LOGIN_USER" --arg w "$WORKER_URL_RP" '.[$u] == $w' >/dev/null; then
      PAGES_OK=1; break
    fi
    warn "workers.json en Pages aún no trae la entrada (intento $i/3); reintentando en 20 s"
    sleep 20
  done
  [ "$PAGES_OK" = 1 ] \
    || die "tras el redespliegue, Pages sigue sin servir workers.json con $LOGIN_USER -> $WORKER_URL_RP (propagación o error de despliegue)"
  echo
  echo "OK: routing reparado para '$TENANT' ($LOGIN_USER -> $WORKER_URL_RP)"
  exit 0
fi

# ---------------------------------------------------------------------------
# Paso 0 — Validaciones (nada se crea si algo de esto falla)
# ---------------------------------------------------------------------------
log "Paso 0 — validando tenant '$TENANT', usuario '$LOGIN_USER' y requisitos"

[ "$TENANT" != "sebastian" ] && [ "$TENANT" != "kaez" ] \
  || die "el tenant '$TENANT' ya existe (sebastian/kaez son los actuales)"
printf '%s' "$TENANT" | grep -Eq '^[a-z0-9][a-z0-9-]{0,30}$' \
  || die "tenant inválido '$TENANT': solo [a-z0-9-], empieza por letra o dígito, máx 31 caracteres"
printf '%s' "$LOGIN_USER" | grep -Eq '^[a-z0-9][a-z0-9_-]{0,39}$' \
  || die "usuario de login inválido '$LOGIN_USER': solo [a-z0-9_-], máx 40 caracteres"
printf '%s' "$ROLE" | grep -Eq '^[a-z][a-z0-9_]{0,19}$' \
  || die "rol inválido '$ROLE': solo [a-z0-9_], máx 20 caracteres"
[ "$PASSWORD_MODE" = "given" ] && [ -z "$PASSWORD" ] && die "--password vacío"
[ "$PASSWORD_MODE" = "given" ] && [ "${#PASSWORD}" -lt 8 ] && die "la contraseña debe tener al menos 8 caracteres"
[ "$PASSWORD_MODE" = "prompt" ] && [ ! -t 0 ] && [ ! -r /dev/tty ] \
  && die "--prompt-password necesita una TTY (usa --generate-password en no interactivo)"

for c in python3 jq curl openssl node bun; do require_cmd "$c"; done
# En dry-run wrangler nunca se ejecuta (run/run_in solo imprimen y
# los pasos con llamadas reales van tras [ "$DRY_RUN" = 0 ]), así que
# el binario no se exige aquí: un checkout fresco o CI (sin
# worker/node_modules) debe poder validar el dry-run. En una
# ejecución real sigue siendo obligatorio.
[ "$DRY_RUN" = 1 ] || [ -x "$WRANGLER" ] \
  || die "falta wrangler en $WRANGLER (ejecuta: (cd worker && npm ci --no-audit --no-fund))"
[ -f "$TEMPLATE_TOML" ] || die "falta la plantilla $TEMPLATE_TOML"
[ -f "$ROUTER_TOML" ] || die "falta $ROUTER_TOML"
[ -f "$WORKERS_JSON" ] || die "falta $WORKERS_JSON"
[ -f "$ROOT/worker/src/auth.ts" ] || die "falta worker/src/auth.ts"
[ -f "$ROOT/worker/src/router.ts" ] || die "falta worker/src/router.ts"
[ -f "$ROOT/worker/src/db.ts" ] || die "falta worker/src/db.ts"
[ -d "$ROOT/migrations" ] || die "falta el directorio migrations/"
[ "$(ls "$ROOT"/migrations/0*.sql 2>/dev/null | wc -l)" -ge 32 ] \
  || die "se esperaban al menos las migraciones 0001-0032 en migrations/"
[ "$DRY_RUN" = 1 ] && log "dry-run: no se crea nada, no se escriben archivos"

# URL pública de R2 (Bug 1, 2026-11-21): la CLIP_PUBLIC_BASE_URL que queda
# en wrangler.<tenant>.toml debe ser la DEL BUCKET DE LA CUENTA DESTINO.
# Nunca se copia silenciosamente la URL de Sebastián a otra cuenta:
#   - Sin --r2-public-url (ni AVC_R2_PUBLIC_URL) y cuenta DISTINTA de la
#     de Sebastián: FALLO aquí, antes de crear nada.
#   - Sin --r2-public-url y cuenta de Sebastián (defecto): se usa la de
#     la plantilla (que ES la de su bucket).
#   - Con otra cuenta y la URL de Sebastián: FALLO (copia-pega erróneo).
# La URL se normaliza sin barra final.
TEMPLATE_PUB_URL="$(grep '^CLIP_PUBLIC_BASE_URL' "$TEMPLATE_TOML" | sed 's/^CLIP_PUBLIC_BASE_URL = "\(.*\)"$/\1/' | head -n 1)"
[ -n "$TEMPLATE_PUB_URL" ] || die "la plantilla $TEMPLATE_TOML no trae CLIP_PUBLIC_BASE_URL (no se puede resolver la URL de R2)"
if [ -z "$R2_PUBLIC_URL" ]; then
  if [ "$ACCOUNT_ID" = "$ACCOUNT_ID_DEFAULT" ]; then
    R2_PUBLIC_URL="$TEMPLATE_PUB_URL"
    log "CLIP_PUBLIC_BASE_URL: sin --r2-public-url, se usa la de la plantilla (cuenta de Sebastián)"
  else
    die "falta --r2-public-url <https://pub-<hash>.r2.dev> (o AVC_R2_PUBLIC_URL): la cuenta destino $ACCOUNT_ID no es la de Sebastián y no se puede copiar su URL de R2. La URL es la del bucket $R2_BUCKET de ESA cuenta (R2 → bucket → Settings → r2.dev subdomain)."
  fi
fi
R2_PUBLIC_URL="${R2_PUBLIC_URL%/}"
printf '%s' "$R2_PUBLIC_URL" | grep -Eq '^https://pub-[A-Za-z0-9]+\.r2\.dev$' \
  || die "--r2-public-url inválida: '$R2_PUBLIC_URL' (se espera https://pub-<hash>.r2.dev)"
if [ "$ACCOUNT_ID" != "$ACCOUNT_ID_DEFAULT" ] && [ "$R2_PUBLIC_URL" = "${TEMPLATE_PUB_URL%/}" ]; then
  die "--r2-public-url es la URL de Sebastián con otra cuenta destino ($ACCOUNT_ID): copiarla apuntaría al bucket de otro. Cada cuenta tiene su propia URL pub-*.r2.dev."
fi
printf '%s' "$R2_BUCKET" | grep -Eq '^[a-z0-9][a-z0-9-]{0,62}$' \
  || die "--r2-bucket inválido: '$R2_BUCKET' (solo [a-z0-9-], 3-63 caracteres, empieza por letra o dígito)"
log "R2: bucket $R2_BUCKET en la cuenta $ACCOUNT_ID, CLIP_PUBLIC_BASE_URL=$R2_PUBLIC_URL"

# Subdominio workers.dev: se deriva de workers.json (primera URL avc-*.workers.dev).
if [ -z "$WORKERS_SUBDOMAIN" ]; then
  WORKERS_SUBDOMAIN="$(python3 - "$WORKERS_JSON" <<'PY'
import json, re, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception as e:
    print(f"no se pudo leer workers.json: {e}", file=sys.stderr); sys.exit(1)
for v in data.values():
    m = re.match(r"https://avc-[a-z0-9-]+\.([a-z0-9-]+)\.workers\.dev/?$", str(v).strip())
    if m:
        print(m.group(1)); break
PY
)" || die "no se pudo derivar el subdominio workers.dev de $WORKERS_JSON (pasa --workers-subdomain)"
  [ -n "$WORKERS_SUBDOMAIN" ] || die "workers.json no contiene ninguna URL avc-*.workers.dev; pasa --workers-subdomain"
fi
WORKER_URL="https://avc-${TENANT}.${WORKERS_SUBDOMAIN}.workers.dev"
log "Worker nuevo: $WORKER_URL  ·  D1: $DB_NAME  ·  panel: $PAGES_URL"

# ---------------------------------------------------------------------------
# Token Cloudflare (patrón deploy_with_token.py; el token nunca se imprime)
# ---------------------------------------------------------------------------
if [ "$DRY_RUN" = 0 ] && [ "$SKIP_CLOUD" = 0 ]; then
  if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
    log "Token Cloudflare: obteniendo surrogate vía dynamic_credential_entry (custom.cloudflare)"
    CLOUDFLARE_API_TOKEN="$(python3 - <<'PY'
import sys
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import dynamic_credential_entry
print(dynamic_credential_entry("custom.cloudflare", "access_token")["surrogate"])
PY
)" || die "no se pudo obtener el token Cloudflare (revisa la conexión custom.cloudflare)"
    [ -n "$CLOUDFLARE_API_TOKEN" ] || die "el token Cloudflare llegó vacío"
  fi
  export CLOUDFLARE_API_TOKEN
  export CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-$ACCOUNT_ID}"
  log "Token Cloudflare cargado en memoria (no se muestra ni se guarda)"
else
  export CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-$ACCOUNT_ID}"
fi

# ---------------------------------------------------------------------------
# Paso 1 — ¿Existe ya el Worker / la D1? (fallo cerrado si la API no responde)
# ---------------------------------------------------------------------------
log "Paso 1 — comprobando que no existan ya el Worker $WORKER_NAME ni la D1 $DB_NAME"
WORKER_EXISTS=0
D1_EXISTS=0
D1_ID=""
if [ "$DRY_RUN" = 0 ] && [ "$SKIP_CLOUD" = 0 ]; then
  WORKER_EXISTS="$(cf_api_get "/accounts/${CLOUDFLARE_ACCOUNT_ID}/workers/scripts" \
    | jq -r --arg n "$WORKER_NAME" '[.result[]? | select(.id == $n)] | length')"
  [ "$WORKER_EXISTS" = "1" ] && die "ya existe el Worker $WORKER_NAME en la cuenta; elige otro tenant o borra el Worker a mano"
  [ "$WORKER_EXISTS" = "0" ] || die "respuesta inesperada al listar Workers (se esperaba 0 o 1)"
  D1_JSON="$("$WRANGLER" d1 list --json)" || die "wrangler d1 list falló (revisa token y cuenta)"
  D1_ID="$(printf '%s' "$D1_JSON" | jq -r --arg n "$DB_NAME" \
    '.[] | select(.name == $n) | (.uuid // .database_id // .id) // empty' | head -n 1)"
  [ -n "$D1_ID" ] && D1_EXISTS=1
  if [ "$D1_EXISTS" = 1 ]; then
    log "La D1 $DB_NAME ya existe (id $D1_ID): se reutiliza (migraciones y siembra son idempotentes)"
  else
    log "Ni el Worker ni la D1 existen: vía libre"
  fi
else
  log "DRY-RUN/SKIP-CLOUD: se comprobaría con: wrangler d1 list --json y GET /workers/scripts"
fi

# ---------------------------------------------------------------------------
# Paso 1b — Parche de código (obligatorio: TENANTS está hardcodeado)
# ---------------------------------------------------------------------------
# Sin esto el Worker nuevo NO sirve: requireTenant (auth.ts) rechaza
# cualquier TENANT fuera de TENANTS, y /api/health daría 500; el login
# daría 200 pero /api/auth/me 401 (ver el comentario en auth.ts:29).
# El router (router.ts) además solo conoce sebastian/kaez en upstreamUrl.
# El parche es mínimo, exacto e idempotente; si el texto esperado no
# aparece tal cual, se falla en voz alta en vez de parchear a ciegas.
# Con --no-code-patch se verifica y se falla con instrucciones manuales.
log "Paso 1b — parche de código para el tenant '$TENANT' (auth.ts + router.ts + test)"
if [ "$NO_CODE_PATCH" = 1 ]; then
  grep -q "\"$TENANT\"" "$ROOT/worker/src/auth.ts" \
    || die "TENANTS en worker/src/auth.ts no incluye '$TENANT' y se pasó --no-code-patch. Añádelo a mano: export const TENANTS = [..., \"$TENANT\"] as const; y haz genérico upstreamUrl() en worker/src/router.ts (WORKER_<TENANT>_URL), luego re-ejecuta sin --no-code-patch."
  log "--no-code-patch: el tenant ya está en TENANTS; sigo"
elif [ "$DRY_RUN" = 1 ]; then
  log "DRY-RUN: se parchearían (idempotente, con verificación por grep):"
  log "DRY-RUN:   worker/src/auth.ts: TENANTS += \"$TENANT\" y mensaje de error genérico"
  log "DRY-RUN:   worker/src/router.ts: upstreamUrl() genérico por WORKER_<TENANT>_URL + /api/health con Object.fromEntries(TENANTS) + DEFAULT_LOGIN_USER_MAP"
  log "DRY-RUN:   worker/test/router.test.ts: expectativa de tenants += \"$TENANT\""
  log "DRY-RUN:   luego: npx tsc --noEmit y bun test test/router.test.ts"
else
  python3 - "$TENANT" "$LOGIN_USER" <<'PY'
import re, sys
tenant, user = sys.argv[1], sys.argv[2]

def patch(path, old, new, what):
    s = open(path).read()
    if new in s:
        print(f"  ya aplicado: {what} ({path})")
        return
    if s.count(old) != 1:
        print(f"FALLO: en {path} se esperaba 1 ocurrencia de {what}, hay {s.count(old)}", file=sys.stderr)
        sys.exit(1)
    open(path, "w").write(s.replace(old, new, 1))
    print(f"  aplicado: {what} ({path})")

AUTH = "worker/src/auth.ts"
ROUTER = "worker/src/router.ts"
TEST = "worker/test/router.test.ts"

# 1) TENANTS: el Worker nuevo solo sirve si su tenant está en la lista.
patch(AUTH,
      'export const TENANTS = ["sebastian", "kaez"] as const;',
      f'export const TENANTS = ["sebastian", "kaez", "{tenant}"] as const;',
      "TENANTS += tenant")
# 2) Mensaje de error de requireTenant (si no, diría "sebastian o kaez" para el nuevo).
patch(AUTH,
      'debe ser "sebastian" o "kaez".',
      'debe ser uno de los tenants conocidos.',
      "mensaje requireTenant genérico")
patch(AUTH,
      'Falla en voz alta si falta o no es "sebastian"|"kaez": un Worker',
      'Falla en voz alta si falta o no es uno de los tenants conocidos: un Worker',
      "comentario requireTenant genérico")

# 3) Router: upstream genérico WORKER_<TENANT>_URL, con fallback legacy sebastian/kaez.
old_up = '  const raw = tenant === "sebastian" ? env.WORKER_SEBASTIAN_URL : env.WORKER_KAEZ_URL;'
new_up = ('  // Genérico por tenant (provision-tenant.sh): WORKER_<TENANT>_URL\n'
          '  // (p. ej. WORKER_LUIS_URL; "-" del tenant es "_" en la var).\n'
          '  // Los dos campos legacy se mantienen como fallback.\n'
          '  const upper = tenant.toUpperCase().replace(/-/g, "_");\n'
          '  const generic = (env as unknown as Record<string, string | undefined>)[`WORKER_${upper}_URL`];\n'
          '  const legacy = tenant === "sebastian" ? env.WORKER_SEBASTIAN_URL : tenant === "kaez" ? env.WORKER_KAEZ_URL : undefined;\n'
          '  const raw = generic ?? legacy;')
patch(ROUTER, old_up, new_up, "upstreamUrl genérico")
# 4) /api/health del router: upstream construido desde TENANTS, no hardcodeado.
old_health = ('      upstream: {\n'
              '        sebastian: upstreamUrl(env, "sebastian"),\n'
              '        kaez: upstreamUrl(env, "kaez"),\n'
              '      },')
new_health = '      upstream: Object.fromEntries(TENANTS.map((t) => [t, upstreamUrl(env, t)])),'
patch(ROUTER, old_health, new_health, "/api/health upstream desde TENANTS")
# 5) Mapa por defecto usuario->tenant (fallback si LOGIN_USER_MAP está vacío).
#    Solo tiene sentido cuando usuario == tenant; si difieren, manda la var
#    LOGIN_USER_MAP del router.toml (paso 7), no este default.
if user == tenant:
    patch(ROUTER,
          '  kaez: "kaez",\n};',
          f'  kaez: "kaez",\n  {tenant}: "{tenant}",\n}};',
          "DEFAULT_LOGIN_USER_MAP += tenant")
# 6) Test del router: la expectativa hardcodeada de tenants.
patch(TEST,
      'expect(r.json?.tenants).toEqual(["sebastian", "kaez"]);',
      f'expect(r.json?.tenants).toEqual(["sebastian", "kaez", "{tenant}"]);',
      "router.test.ts expectativa de tenants")
PY
  # Verificación del parche (fail loudly si algo no quedó).
  grep -q "\"$TENANT\"" "$ROOT/worker/src/auth.ts" || die "el parche de auth.ts no quedó aplicado"
  grep -q 'WORKER_${upper}_URL' "$ROOT/worker/src/router.ts" || die "el parche de router.ts (upstreamUrl) no quedó aplicado"
  grep -q 'Object.fromEntries(TENANTS.map' "$ROOT/worker/src/router.ts" || die "el parche de router.ts (/api/health) no quedó aplicado"
  log "Parche de código aplicado y verificado por grep"
  if [ "$SKIP_TYPECHECK" = 0 ]; then
    log "Typecheck del worker (tsc --noEmit)"
    run_in "$WORKER_DIR" npx tsc --noEmit -p tsconfig.json
  else
    warn "--skip-typecheck: se omite tsc (solo para pruebas del script)"
  fi
  if [ "$SKIP_TESTS" = 0 ]; then
    log "Tests del router (bun test test/router.test.ts)"
    run_in "$WORKER_DIR" bun test test/router.test.ts
  else
    warn "--skip-tests: no se corren los tests del router"
  fi
fi

# ---------------------------------------------------------------------------
# Paso 1c — Bucket R2 $R2_BUCKET en la cuenta destino (Bug 1, 2026-11-21)
# ---------------------------------------------------------------------------
# Antes NO se creaba: se asumía que existía. Un Worker no puede usar un
# bucket de OTRA cuenta, así que el bucket tiene que vivir en la cuenta
# destino. Idempotente: si ya existe se reutiliza; si no, se crea y se
# verifica volviendo a listar. La activación del acceso público r2.dev
# del bucket sigue siendo manual en el dashboard de esa cuenta (la URL
# ya se exigió con --r2-public-url en el paso 0).
log "Paso 1c — bucket R2 $R2_BUCKET en la cuenta ${CLOUDFLARE_ACCOUNT_ID:-$ACCOUNT_ID}"
if [ "$DRY_RUN" = 1 ]; then
  log "DRY-RUN: wrangler r2 bucket list (cuenta ${CLOUDFLARE_ACCOUNT_ID:-$ACCOUNT_ID}) y, si falta, wrangler r2 bucket create $R2_BUCKET"
elif [ "$SKIP_CLOUD" = 1 ]; then
  log "--skip-cloud: bucket R2 no verificado ni creado en este modo"
else
  R2_LISTING="$( (cd "$WORKER_DIR" && "$WRANGLER" r2 bucket list) 2>&1 )" \
    || die "wrangler r2 bucket list falló en la cuenta ${CLOUDFLARE_ACCOUNT_ID:-$ACCOUNT_ID} (¿token sin permiso R2 Edit? ¿checkout gratuito de R2 sin hacer en el dashboard de esa cuenta?)"
  if printf '%s' "$R2_LISTING" | grep -qw "$R2_BUCKET"; then
    log "Bucket R2 $R2_BUCKET ya existe en la cuenta: se reutiliza"
  else
    log "Creando bucket R2 $R2_BUCKET en la cuenta ${CLOUDFLARE_ACCOUNT_ID:-$ACCOUNT_ID}"
    (cd "$WORKER_DIR" && "$WRANGLER" r2 bucket create "$R2_BUCKET") \
      || die "wrangler r2 bucket create $R2_BUCKET falló en la cuenta ${CLOUDFLARE_ACCOUNT_ID:-$ACCOUNT_ID}. Si el error es de nombre en conflicto, los nombres de bucket R2 pueden ser únicos en todo Cloudflare: re-ejecuta con --r2-bucket $R2_BUCKET-$TENANT (y su URL r2.dev con --r2-public-url). Si el error menciona la suscripción de R2, hay que hacer el checkout gratuito en el dashboard de esa cuenta."
    R2_LISTING2="$( (cd "$WORKER_DIR" && "$WRANGLER" r2 bucket list) 2>&1 )" \
      || die "no se pudo verificar el bucket tras crearlo (wrangler r2 bucket list falló)"
    printf '%s' "$R2_LISTING2" | grep -qw "$R2_BUCKET" \
      || die "el bucket $R2_BUCKET no aparece en 'wrangler r2 bucket list' tras crearlo; revisa a mano en el dashboard"
    log "Bucket R2 $R2_BUCKET creado y verificado en la cuenta"
  fi
  warn "Recuerda: activa el acceso público r2.dev del bucket $R2_BUCKET en el dashboard de esa cuenta (R2 → bucket → Settings → r2.dev subdomain → Enable); la URL debe ser $R2_PUBLIC_URL"
fi

# ---------------------------------------------------------------------------
# Paso 2 — D1 clips-avc-<tenant> (crear o reutilizar) + migraciones 0001-0032
# ---------------------------------------------------------------------------
log "Paso 2 — D1 $DB_NAME"
TENANT_TOML="$ROOT/wrangler.$TENANT.toml"
if [ "$DRY_RUN" = 1 ]; then
  log "DRY-RUN: wrangler d1 create $DB_NAME (si no existe) y wrangler d1 list --json para el id"
  log "DRY-RUN: generar wrangler.$TENANT.toml desde $TEMPLATE_TOML con el id real de la D1"
  log "DRY-RUN: chequeo previo de presupuesto (check-migration-budget.py --migrations-dir migrations --d1 <id>; A5: rechaza si supera el 20% de la cuota restante)"
  log "DRY-RUN: (cd worker) wrangler d1 migrations apply $DB_NAME --config ../wrangler.$TENANT.toml --remote"
  D1_ID="DRY-RUN-DATABASE-ID"
elif [ "$SKIP_CLOUD" = 1 ]; then
  log "--skip-cloud: no se crea ni se consulta la D1; se genera el toml con un placeholder"
  D1_ID="__D1_ID_${TENANT_ENV}__"
else
  if [ "$D1_EXISTS" = 0 ]; then
    run "$WRANGLER" d1 create "$DB_NAME"
    D1_ID="$("$WRANGLER" d1 list --json \
      | jq -r --arg n "$DB_NAME" '.[] | select(.name == $n) | (.uuid // .database_id // .id) // empty' | head -n 1)"
    [ -n "$D1_ID" ] || die "la D1 $DB_NAME se creó pero no aparece en 'wrangler d1 list --json' (no se pudo obtener su id)"
    log "D1 creada: $DB_NAME (id $D1_ID)"
  else
    log "D1 reutilizada: $DB_NAME (id $D1_ID)"
  fi
fi

# ---------------------------------------------------------------------------
# Paso 3 — wrangler.<tenant>.toml desde la plantilla (wrangler.sebastian.toml)
# ---------------------------------------------------------------------------
log "Paso 3 — generando wrangler.$TENANT.toml"
if [ "$DRY_RUN" = 1 ]; then
  log "DRY-RUN: se generaría $TENANT_TOML (name, database_name, database_id, TENANT, bucket_name=$R2_BUCKET, CLIP_PUBLIC_BASE_URL=$R2_PUBLIC_URL; sin [triggers]: cron apagado, A5)"
elif [ -f "$TENANT_TOML" ] && [ "$FORCE" = 0 ]; then
  # Re-ejecución: el archivo ya existe; solo vale si coincide con este tenant.
  # A5: un toml reutilizado con cron activo es un Worker que nacería
  # encendido: no se reutiliza sin --force (que lo regenera sin cron).
  grep -q "^crons = " "$TENANT_TOML" && die "wrangler.$TENANT.toml ya existe CON cron activo (A5: un Worker nuevo se despliega apagado). Quita el bloque [triggers] a mano o re-ejecuta con --force para regenerarlo sin cron."
  if grep -q "^name = \"avc-$TENANT\"\$" "$TENANT_TOML" \
    && grep -q "^database_name = \"$DB_NAME\"\$" "$TENANT_TOML" \
    && grep -q "^database_id = \"$D1_ID\"\$" "$TENANT_TOML" \
    && grep -q "^TENANT = \"$TENANT\"\$" "$TENANT_TOML" \
    && grep -q "^bucket_name = \"$R2_BUCKET\"\$" "$TENANT_TOML" \
    && grep -q "^CLIP_PUBLIC_BASE_URL = \"$R2_PUBLIC_URL\"\$" "$TENANT_TOML"; then
    log "wrangler.$TENANT.toml ya existe con los valores correctos: se reutiliza"
  else
    die "wrangler.$TENANT.toml ya existe con valores distintos (¿otro tenant, otro bucket R2 u otra URL pública? Revisa el archivo o re-ejecuta con --force para regenerarlo desde la plantilla con --r2-bucket/--r2-public-url)."
  fi
else
  [ -f "$TENANT_TOML" ] && log "--force: regenerando wrangler.$TENANT.toml desde la plantilla"
  python3 - "$TENANT" "$DB_NAME" "$D1_ID" "$TEMPLATE_TOML" "$TENANT_TOML" "$R2_BUCKET" "$R2_PUBLIC_URL" <<'PY'
import re, sys
tenant, db_name, db_id, template, out, r2_bucket, r2_pub_url = sys.argv[1:8]
src = open(template).read()
# Cabecera: todo lo anterior a la primera línea `name =` se sustituye por
# una cabecera propia del tenant (la de sebastian menciona a sebastian).
header = f"""# wrangler.{tenant}.toml — Worker por usuario: UN SOLO TENANT ({tenant}).
# Generado por scripts/provision-tenant.sh desde wrangler.sebastian.toml.
# Decisión del usuario 2026-11-19: cada cuenta/tenant tiene su propio
# Worker desplegado (código igual para todos, solo cambia
# configuración/APIs/cuentas por usuario). Este Worker sirve
# EXACTAMENTE al tenant "{tenant}" (var TENANT).
#
# Recursos (cuenta de Cloudflare del tenant):
#   D1     {db_name} (una base SOLO con filas de {tenant})
#   R2     {r2_bucket} (bucket PROPIO en la cuenta del tenant, creado en
#          el paso 1c; keys clips/<tenant>/<clip_id>/...; URL pública
#          {r2_pub_url})
#   Worker avc-{tenant}         (este archivo; avc-{tenant}.<cuenta>.workers.dev)
#   Cron   APAGADO (A5, 2026-11-20): el Worker nuevo se despliega SIN
#          [triggers] y con worker_running=false. Se enciende solo con
#          scripts/provision-tenant.sh {tenant} --enable-cron, tras
#          verificar salud (/api/health), la D1 y los 6 secretos.
#
# SIN secretos aquí: los secretos van con `wrangler secret put
# --config wrangler.{tenant}.toml` (BRIDGE_TOKEN,
# CREDENTIAL_ENCRYPTION_KEY, GITHUB_TOKEN, GOOGLE_CLIENT_ID,
# GOOGLE_CLIENT_SECRET, SESSION_SECRET, TELEGRAM_BOT_TOKEN,
# TELEGRAM_WEBHOOK_SECRET). SESSION_SECRET y CREDENTIAL_ENCRYPTION_KEY
# son FRESCOS por tenant (los genera provision-tenant.sh); GOOGLE_* los
# crea el usuario en sus consolas y el panel lo guía.
#
# Despliegue: python3 scripts/deploy_with_token.py deploy-worker-{tenant}
# (o re-ejecutar provision-tenant.sh, que es idempotente).
#
"""
m = re.search(r"^name = ", src, re.M)
if not m:
    print(f"FALLO: la plantilla {template} no tiene línea 'name ='", file=sys.stderr); sys.exit(1)
body = src[m.start():]
def sub_once(pattern, repl, what):
    global body
    new, n = re.subn(pattern, repl, body, count=1, flags=re.M)
    if n != 1:
        print(f"FALLO: en la plantilla se esperaba 1 '{what}', hay {n}", file=sys.stderr); sys.exit(1)
    body = new
sub_once(r'^name = "avc-[^"]+"', f'name = "avc-{tenant}"', "name")
sub_once(r'^database_name = "clips-avc-[^"]+"', f'database_name = "{db_name}"', "database_name")
sub_once(r'^database_id = "[^"]+"', f'database_id = "{db_id}"', "database_id")
sub_once(r'^TENANT = "[^"]+"', f'TENANT = "{tenant}"', "TENANT")
# Bug 1 (2026-11-21): el bucket y la URL pública son LOS DEL TENANT, no
# los de la plantilla de Sebastián. Antes se copiaban tal cual.
sub_once(r'^bucket_name = "[^"]+"', f'bucket_name = "{r2_bucket}"', "bucket_name")
sub_once(r'^CLIP_PUBLIC_BASE_URL = "[^"]+"', f'CLIP_PUBLIC_BASE_URL = "{r2_pub_url}"', "CLIP_PUBLIC_BASE_URL")
# A5: el Worker nuevo se despliega con el cron APAGADO. La plantilla
# trae [triggers] crons cada minuto; se quita del toml generado y
# solo se repone con --enable-cron tras verificar salud, D1 y
# secretos. Si la plantilla cambia de forma, fallar en voz alta.
trig = re.search(r"^# Cron del Worker:.*?\n\[triggers\]\ncrons = \[\"[^\"]+\"\]\n", body, re.M | re.S)
if not trig:
    print("FALLO: la plantilla no trae el bloque [triggers] esperado para quitarlo (A5)", file=sys.stderr); sys.exit(1)
body = body[:trig.start()] + ("# Cron APAGADO (A5, 2026-11-20): el Worker nuevo se despliega\n"
    "# sin cron y con worker_running=false. Se enciende solo con\n"
    "# scripts/provision-tenant.sh " + tenant + " --enable-cron, tras verificar\n"
    "# salud (/api/health), la D1 y los 6 secretos.\n\n") + body[trig.end():]
open(out, "w").write(header + body)
print(f"generado {out}")
PY
  # Verificación: los valores del tenant y que lo demás sea igual a la plantilla.
  grep -q "^name = \"avc-$TENANT\"\$" "$TENANT_TOML" || die "el toml generado no tiene name avc-$TENANT"
  grep -q "^database_name = \"$DB_NAME\"\$" "$TENANT_TOML" || die "el toml generado no tiene database_name $DB_NAME"
  grep -q "^database_id = \"$D1_ID\"\$" "$TENANT_TOML" || die "el toml generado no tiene el database_id real"
  grep -q "^TENANT = \"$TENANT\"\$" "$TENANT_TOML" || die "el toml generado no tiene TENANT $TENANT"
  # A5: el toml generado NO debe traer cron (un Worker nuevo se
  # despliega apagado; si esto falla, la plantilla cambió y hay que
  # revisar el quite del bloque [triggers] antes de desplegar nada).
  grep -q "^crons = " "$TENANT_TOML" && die "el toml generado trae cron activo (A5: un Worker nuevo se despliega apagado)"
  # GITHUB_REPO debe ser igual a la plantilla; el bucket y la URL pública
  # deben ser LOS DEL TENANT (resueltos en el paso 0), no los de la plantilla.
  a="$(grep '^GITHUB_REPO' "$TEMPLATE_TOML" || true)"; b="$(grep '^GITHUB_REPO' "$TENANT_TOML" || true)"
  [ "$a" = "$b" ] || die "el toml generado difiere de la plantilla en GITHUB_REPO (plantilla: $a)"
  grep -q "^bucket_name = \"$R2_BUCKET\"\$" "$TENANT_TOML" || die "el toml generado no tiene bucket_name $R2_BUCKET"
  grep -q "^CLIP_PUBLIC_BASE_URL = \"$R2_PUBLIC_URL\"\$" "$TENANT_TOML" || die "el toml generado no tiene CLIP_PUBLIC_BASE_URL $R2_PUBLIC_URL"
  log "wrangler.$TENANT.toml generado y verificado"
fi

if [ "$DRY_RUN" = 0 ] && [ "$SKIP_CLOUD" = 0 ]; then
  # A5: puerta de presupuesto ANTES de aplicar migraciones. La
  # migración a D1 por usuario escribió 23.531 filas de una vez el
  # 2026-11-19; el chequeo la habría rechazado (20% de la cuota
  # restante). Si rechaza o no puede decidir, no se aplica nada.
  log "Chequeo previo de presupuesto de las migraciones pendientes (A5)"
  python3 "$ROOT/scripts/check-migration-budget.py" \
    --migrations-dir "$ROOT/migrations" --d1 "$D1_ID" \
    || die "el chequeo de presupuesto rechazó o no pudo decidir; no se aplican migraciones"
  log "Aplicando migraciones pendientes a $DB_NAME"
  run_in "$WORKER_DIR" "$WRANGLER" d1 migrations apply "$DB_NAME" \
    --config "../wrangler.$TENANT.toml" --remote
else
  log "DRY-RUN/SKIP-CLOUD: migraciones no aplicadas en este modo"
fi

# ---------------------------------------------------------------------------
# Paso 4 — Primer despliegue del Worker (crea el Worker en la cuenta)
# ---------------------------------------------------------------------------
# ORDEN (difiere del encargo, a propósito): `wrangler secret put` exige
# que el Worker YA exista — la API de Cloudflare devuelve 404 si el script
# aún no está desplegado. Por eso el primer deploy va ANTES de los secretos
# (paso 5). Los secretos se leen en runtime, así que ponerlos después NO
# exige redesplegar: el Worker los ve en la siguiente petición.
log "Paso 4 — primer despliegue del Worker $WORKER_NAME (crea el Worker; sin secretos aún)"
if [ "$DRY_RUN" = 1 ]; then
  log "DRY-RUN: (cd worker) wrangler deploy --config ../wrangler.$TENANT.toml"
elif [ "$SKIP_CLOUD" = 1 ]; then
  log "--skip-cloud: despliegue no ejecutado en este modo"
else
  run_in "$WORKER_DIR" "$WRANGLER" deploy --config "../wrangler.$TENANT.toml"
  log "Worker $WORKER_NAME desplegado (primera vez; los secretos van en el paso 5)"
  # A5: el Worker nuevo nace SIN cron. Verificar por API que no hay
  # triggers: el toml generado no los trae, y esto caza un cambio de
  # plantilla o de wrangler que los re-pusiera.
  SCHED_N="$(cf_api_get "/accounts/${CLOUDFLARE_ACCOUNT_ID}/workers/scripts/${WORKER_NAME}/schedules" 2>/dev/null | jq -r '.result.schedules | length' 2>/dev/null || echo "?")"
  if [ "$SCHED_N" = "?" ]; then
    warn "no se pudo verificar por API que el Worker no tenga cron (revisa a mano: GET .../workers/scripts/$WORKER_NAME/schedules)"
  elif [ "$SCHED_N" != "0" ]; then
    die "el Worker $WORKER_NAME nació con $SCHED_N trigger(s) de cron (A5: debe nacer apagado); apágalo antes de seguir"
  else
    log "Verificado por API: el Worker nuevo no tiene triggers de cron (apagado)"
  fi
fi

# ---------------------------------------------------------------------------
# Paso 5 — Secretos del Worker nuevo
# ---------------------------------------------------------------------------
# Frescos por tenant: SESSION_SECRET y CREDENTIAL_ENCRYPTION_KEY.
# Compartidos (hay que aportarlos; NO se pueden leer del Worker existente):
# GITHUB_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, BRIDGE_TOKEN.
# GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET: NO se ponen (ver cabecera).
log "Paso 5 — secretos del Worker $WORKER_NAME"
resolve_shared_secret() {
  # $1 = nombre del secreto (p. ej. GITHUB_TOKEN); $2 = valor actual (flag/env)
  # Devuelve el valor por stdout. Si falta y hay TTY, pregunta sin eco;
  # si no, falla con el nombre exacto y cómo aportarlo.
  local name="$1" value="$2" env_name="$3"
  if [ -n "$value" ]; then printf '%s' "$value"; return 0; fi
  if [ "$DRY_RUN" = 1 ]; then printf 'DRY-RUN-VALOR'; return 0; fi
  if [ -t 0 ] || [ -r /dev/tty ]; then
    local v1 v2
    printf 'Secreto compartido %s (mismo valor que en avc-sebastian; no se muestra): ' "$name" >/dev/tty
    IFS= read -rs v1 </dev/tty; printf '\n' >/dev/tty
    [ -n "$v1" ] || die "falta $name: pásalo con --$(printf '%s' "$name" | tr 'A-Z_' 'a-z-') <valor> o con la variable $env_name"
    printf '%s' "$v1"; return 0
  fi
  die "falta el secreto compartido $name (NO se puede leer del Worker existente: Cloudflare no devuelve valores de secretos). Pásalo con --$(printf '%s' "$name" | tr 'A-Z_' 'a-z-') <valor> o con la variable de entorno $env_name."
}
if [ "$DRY_RUN" = 1 ]; then
  log "DRY-RUN: se generarían SESSION_SECRET y CREDENTIAL_ENCRYPTION_KEY (openssl rand -base64 32)"
  log "DRY-RUN: se pondrían con 'wrangler secret put <NOMBRE> --config ../wrangler.$TENANT.toml' (valor por stdin, nunca en argv ni en logs): SESSION_SECRET, CREDENTIAL_ENCRYPTION_KEY, GITHUB_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, BRIDGE_TOKEN"
  log "DRY-RUN: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET NO se ponen (los crea el usuario; el panel lo guía)"
elif [ "$SKIP_CLOUD" = 1 ]; then
  log "--skip-cloud: secretos no puestos en este modo"
else
  SESSION_SECRET="$(openssl rand -base64 32)"
  if [ -n "${ENCRYPTION_KEY_OVERRIDE:-}" ]; then
    CREDENTIAL_ENCRYPTION_KEY="$ENCRYPTION_KEY_OVERRIDE"
    warn "CREDENTIAL_ENCRYPTION_KEY fijado al valor aportado con --encryption-key (para migrar credenciales cifradas); NO es fresco"
  elif [ -n "${AVC_CREDENTIAL_ENCRYPTION_KEY:-}" ]; then
    CREDENTIAL_ENCRYPTION_KEY="$AVC_CREDENTIAL_ENCRYPTION_KEY"
    warn "CREDENTIAL_ENCRYPTION_KEY tomado de AVC_CREDENTIAL_ENCRYPTION_KEY (para migrar credenciales cifradas); NO es fresco"
  else
    CREDENTIAL_ENCRYPTION_KEY="$(openssl rand -base64 32)"
  fi
  [ -n "$SESSION_SECRET" ] && [ -n "$CREDENTIAL_ENCRYPTION_KEY" ] \
    || die "no se pudieron generar los secretos frescos"
  # Compartidos: fallar ANTES de poner nada si falta alguno.
  GITHUB_TOKEN="$(resolve_shared_secret GITHUB_TOKEN "$GITHUB_TOKEN" AVC_GITHUB_TOKEN)"
  TELEGRAM_BOT_TOKEN="$(resolve_shared_secret TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN" AVC_TELEGRAM_BOT_TOKEN)"
  TELEGRAM_WEBHOOK_SECRET="$(resolve_shared_secret TELEGRAM_WEBHOOK_SECRET "$TELEGRAM_WEBHOOK_SECRET" AVC_TELEGRAM_WEBHOOK_SECRET)"
  BRIDGE_TOKEN="$(resolve_shared_secret BRIDGE_TOKEN "$BRIDGE_TOKEN" AVC_BRIDGE_TOKEN)"
  # ¿Cuáles ya existen? (secret list devuelve solo NOMBRES, nunca valores.)
  # Se ejecuta desde worker/ porque --config es relativo a ese directorio.
  EXISTING_SECRETS="$( (cd "$WORKER_DIR" && "$WRANGLER" secret list --config "../wrangler.$TENANT.toml" --format json) \
    | jq -r '.[].name' 2>/dev/null || true)"
  put_secret() {
    local name="$1" value="$2"
    if [ "$FORCE_SECRETS" = 0 ] && printf '%s\n' "$EXISTING_SECRETS" | grep -qx "$name"; then
      log "Secreto $name ya existe en $WORKER_NAME: se salta (usa --force-secrets para rotarlo)"
      return 0
    fi
    printf '%s' "$value" | run_in "$WORKER_DIR" "$WRANGLER" secret put "$name" \
      --config "../wrangler.$TENANT.toml"
  }
  put_secret SESSION_SECRET "$SESSION_SECRET"
  put_secret CREDENTIAL_ENCRYPTION_KEY "$CREDENTIAL_ENCRYPTION_KEY"
  put_secret GITHUB_TOKEN "$GITHUB_TOKEN"
  put_secret TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
  put_secret TELEGRAM_WEBHOOK_SECRET "$TELEGRAM_WEBHOOK_SECRET"
  put_secret BRIDGE_TOKEN "$BRIDGE_TOKEN"
  # Limpieza: los valores solo viven en estas variables; se vacían al salir
  # del bloque (las variables se sobreescriben con vacío, no quedan en disco).
  SESSION_SECRET=""; CREDENTIAL_ENCRYPTION_KEY=""
  GITHUB_TOKEN=""; TELEGRAM_BOT_TOKEN=""; TELEGRAM_WEBHOOK_SECRET=""; BRIDGE_TOKEN=""
  log "Secretos puestos (6 de 8; GOOGLE_CLIENT_ID/SECRET quedan sin poner, ver cabecera)"
fi

# ---------------------------------------------------------------------------
# Paso 6 — Usuario del panel + settings base en la D1 nueva
# ---------------------------------------------------------------------------
# El usuario se crea por SQL directo: NO existe endpoint sin sesión para
# sembrar un tenant (POST /api/onboarding/copy-settings exige sesión de
# ese tenant, y aún no hay usuario con el que iniciar sesión).
# El hash lo calcula la propia función hashPassword del proyecto
# (worker/src/auth.ts, PBKDF2 100000/SHA-256/sal 16 B — el mismo formato
# que scripts/gen-password-hash.mjs, que es interactivo por TTY y no se
# puede manejar desde un script). Los settings base salen de
# extractBaseSettings(effectiveSettings({})) de worker/src/db.ts, la
# MISMA base que /api/onboarding/copy-settings: nunca se copia la fila
# de otro tenant ni se inventan valores a mano.
log "Paso 6 — usuario '$LOGIN_USER' y settings base en la D1 $DB_NAME"
SEED_SQL="$TMPDIR_WORK/seed-$TENANT.sql"
if [ "$DRY_RUN" = 1 ]; then
  log "DRY-RUN: se resolvería la contraseña (generada por defecto, mostrada UNA vez al final)"
  log "DRY-RUN: hash con bun (hashPassword de worker/src/auth.ts, contraseña por stdin, nunca en argv)"
  log "DRY-RUN: settings base con bun (extractBaseSettings(effectiveSettings({})) de worker/src/db.ts)"
  log "DRY-RUN: INSERT OR IGNORE en panel_users + INSERT ... ON CONFLICT DO NOTHING en settings, vía wrangler d1 execute $DB_NAME --remote --file <seed.sql>"
  log "DRY-RUN: verificación con SELECT de panel_users y settings"
elif [ "$SKIP_CLOUD" = 1 ]; then
  log "--skip-cloud: se genera el SQL de siembra (hash PLACEHOLDER, no válido) y se muestra para revisión; la contraseña NO se genera en este modo"
  PASSWORD="SKIP-CLOUD-SIN-CONTRASEÑA"
  HASH="pbkdf2\$100000\$AAAAAAAAAAAAAAAAAAAAAA==\$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
  SETTINGS_JSON="$(bun -e "import {effectiveSettings, extractBaseSettings} from './worker/src/db.ts'; console.log(JSON.stringify(extractBaseSettings(effectiveSettings({}))));")"
  NOW_MS="$(python3 -c 'import time; print(int(time.time()*1000))')"
  python3 - "$TENANT" "$LOGIN_USER" "$ROLE" "$HASH" "$SETTINGS_JSON" "$NOW_MS" "$SEED_SQL" <<'PY'
import sys
tenant, user, role, h, settings, now_ms, out = sys.argv[1:8]
def q(s): return "'" + str(s).replace("'", "''") + "'"
sql = f"""-- Siembra de tenant {tenant} (provision-tenant.sh, --skip-cloud: hash PLACEHOLDER, no válido).
-- En una ejecución real el hash lo calcula hashPassword de worker/src/auth.ts.
INSERT OR IGNORE INTO panel_users (name, role, status, note, created_at, username, password_hash, tenant)
VALUES ({q(user)}, {q(role)}, 'activo', {q('Propietario del tenant ' + tenant)}, {now_ms}, {q(user)}, {q(h)}, {q(tenant)});
INSERT INTO settings (tenant_id, data, updated_at) VALUES ({q(tenant)}, {q(settings)}, {now_ms})
ON CONFLICT(tenant_id) DO NOTHING;
"""
open(out, "w").write(sql)
print(f"SQL de siembra (revisar) en {out}")
PY
  log "SQL de siembra generado (hash PLACEHOLDER, no válido; el temporal se borra al salir):"
  cat "$SEED_SQL"
else
  # Contraseña: generar (defecto), pedir por TTY o usar la dada con --password.
  case "$PASSWORD_MODE" in
    generate)
      PASSWORD="$(python3 -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20)))")"
      GENERATED_PASSWORD=1
      ;;
    prompt)
      printf 'Contraseña del panel para %s (no se guarda, no se ve): ' "$LOGIN_USER" >/dev/tty
      IFS= read -rs PASSWORD </dev/tty; printf '\n' >/dev/tty
      printf 'Repetir: ' >/dev/tty
      IFS= read -rs PASSWORD2 </dev/tty; printf '\n' >/dev/tty
      [ -n "$PASSWORD" ] && [ "$PASSWORD" = "$PASSWORD2" ] \
        || die "la contraseña está vacía o no coincide"
      PASSWORD2=""
      GENERATED_PASSWORD=0
      ;;
    given) GENERATED_PASSWORD=0 ;;
  esac
  [ -n "$PASSWORD" ] && [ "${#PASSWORD}" -ge 8 ] \
    || die "la contraseña debe tener al menos 8 caracteres"
  # Hash con el código del proyecto (la contraseña viaja por stdin, nunca en argv).
  HASH="$(printf '%s' "$PASSWORD" | bun -e "import {hashPassword} from './worker/src/auth.ts'; const pw = await new Response(Bun.stdin).text(); console.log(await hashPassword(pw));")"
  printf '%s' "$HASH" | grep -Eq '^pbkdf2\$100000\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+$' \
    || die "el hash generado no tiene el formato pbkdf2\$100000\$... esperado"
  # Settings base desde el código (misma base que /api/onboarding/copy-settings).
  SETTINGS_JSON="$(bun -e "import {effectiveSettings, extractBaseSettings} from './worker/src/db.ts'; console.log(JSON.stringify(extractBaseSettings(effectiveSettings({}))));")"
  [ -n "$SETTINGS_JSON" ] || die "no se pudieron extraer los settings base de worker/src/db.ts"
  printf '%s' "$SETTINGS_JSON" | jq -e '.layout and .min_duration_s' >/dev/null \
    || die "los settings base extraídos no traen las claves esperadas (layout, min_duration_s)"
  NOW_MS="$(python3 -c 'import time; print(int(time.time()*1000))')"
  python3 - "$TENANT" "$LOGIN_USER" "$ROLE" "$HASH" "$SETTINGS_JSON" "$NOW_MS" "$SEED_SQL" <<'PY'
import sys
tenant, user, role, h, settings, now_ms, out = sys.argv[1:8]
def q(s): return "'" + str(s).replace("'", "''") + "'"
sql = f"""-- Siembra de tenant {tenant} (provision-tenant.sh). El hash lo calculó
-- hashPassword de worker/src/auth.ts; los settings son
-- extractBaseSettings(effectiveSettings({{}})) de worker/src/db.ts.
INSERT OR IGNORE INTO panel_users (name, role, status, note, created_at, username, password_hash, tenant)
VALUES ({q(user)}, {q(role)}, 'activo', {q('Propietario del tenant ' + tenant)}, {now_ms}, {q(user)}, {q(h)}, {q(tenant)});
INSERT INTO settings (tenant_id, data, updated_at) VALUES ({q(tenant)}, {q(settings)}, {now_ms})
ON CONFLICT(tenant_id) DO NOTHING;
"""
open(out, "w").write(sql)
PY
  log "Sembrando usuario y settings en $DB_NAME"
  run_in "$WORKER_DIR" "$WRANGLER" d1 execute "$DB_NAME" --remote \
    --file "$SEED_SQL" --config "../wrangler.$TENANT.toml"
  # Verificación: la fila del usuario y la de settings existen y son de este tenant.
  # (Llamada directa, sin run_in: run_in imprime el comando en stdout y
  # corrompería el JSON que se parsea con jq.)
  GOT_USER="$( (cd "$WORKER_DIR" && "$WRANGLER" d1 execute "$DB_NAME" --remote --json \
    --command "SELECT username, tenant FROM panel_users WHERE username = '$LOGIN_USER';" \
    --config "../wrangler.$TENANT.toml") | jq -r '.[0].results[0].username // empty')"
  [ "$GOT_USER" = "$LOGIN_USER" ] || die "la siembra no dejó la fila de panel_users para '$LOGIN_USER' (revisa $DB_NAME)"
  GOT_SETTINGS="$( (cd "$WORKER_DIR" && "$WRANGLER" d1 execute "$DB_NAME" --remote --json \
    --command "SELECT tenant_id FROM settings WHERE tenant_id = '$TENANT';" \
    --config "../wrangler.$TENANT.toml") | jq -r '.[0].results[0].tenant_id // empty')"
  [ "$GOT_SETTINGS" = "$TENANT" ] || die "la siembra no dejó la fila de settings para '$TENANT'"
  log "Usuario '$LOGIN_USER' y settings base sembrados y verificados en $DB_NAME"
fi

# ---------------------------------------------------------------------------
# Paso 7 — Routing: workers.json + LOGIN_USER_MAP + WORKER_<TENANT>_URL,
#           redespliegue de Pages (avc) y del router
# ---------------------------------------------------------------------------
# workers.json lo lee el front (frontend/dist/app.js) para saber a qué
# Worker va cada usuario de login. LOGIN_USER_MAP es el mismo mapa en el
# router (wrangler.router.toml), que reenvía /api/auth/login y el tráfico
# del cerebro al Worker del tenant. WORKER_<TENANT>_URL es la URL destino
# que usa upstreamUrl() (genérico desde el parche del paso 1b).
log "Paso 7 — routing: workers.json + router.toml, redespliegue de Pages y del router"
REPAIR_CMD="scripts/provision-tenant.sh $TENANT --user $LOGIN_USER --workers-subdomain $WORKERS_SUBDOMAIN --repair-pages-deploy --admin-token-env $ADMIN_TOKEN_ENV"
if [ "$DRY_RUN" = 1 ]; then
  log "DRY-RUN: workers.json['$LOGIN_USER'] = $WORKER_URL"
  log "DRY-RUN: wrangler.router.toml: $ROUTER_URL_VAR = \"$WORKER_URL\" y LOGIN_USER_MAP['$LOGIN_USER'] = \"$TENANT\""
  if [ "$SKIP_PAGES_DEPLOY" = 1 ]; then
    log "DRY-RUN: --skip-pages-deploy: NO se redesplegarían Pages ni el router con este token; la reparación se haría con el token admin:"
    log "DRY-RUN:   $REPAIR_CMD"
    log "DRY-RUN: se comprobaría si la cuenta tiene un Pages/Worker 'avc' duplicado (solo se reporta, nunca se borra solo)"
  else
    log "DRY-RUN: wrangler pages deploy frontend/dist --project-name $PAGES_PROJECT"
    log "DRY-RUN: (cd worker) wrangler deploy --config ../wrangler.router.toml"
  fi
else
  python3 - "$LOGIN_USER" "$WORKER_URL" "$TENANT" "$ROUTER_URL_VAR" <<'PY'
import json, re, sys, tomllib
user, worker_url, tenant, url_var = sys.argv[1:5]

# --- workers.json (usuario de login -> URL del Worker) ---
p = "frontend/dist/workers.json"
data = json.load(open(p))
if data.get(user) == worker_url:
    print(f"  workers.json: {user} ya apunta a {worker_url}")
else:
    data[user] = worker_url
    open(p, "w").write(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"  workers.json: {user} -> {worker_url}")

# --- wrangler.router.toml: WORKER_<TENANT>_URL + LOGIN_USER_MAP ---
p = "wrangler.router.toml"
lines = open(p).read().splitlines(keepends=True)
with open(p, "rb") as f:
    login_map = tomllib.load(f)["vars"].get("LOGIN_USER_MAP", "{}")
try:
    m = json.loads(login_map)
except Exception:
    print(f"FALLO: LOGIN_USER_MAP en {p} no es JSON válido: {login_map}", file=sys.stderr); sys.exit(1)
# WORKER_<TENANT>_URL: reemplazar si existe, si no insertar tras WORKER_KAEZ_URL.
url_line = f'{url_var} = "{worker_url}"\n'
found = [i for i, ln in enumerate(lines) if ln.startswith(url_var + " =")]
if found:
    lines[found[0]] = url_line
    print(f"  router.toml: {url_var} actualizado")
else:
    anchor = [i for i, ln in enumerate(lines) if ln.startswith("WORKER_KAEZ_URL =")]
    if not anchor:
        print(f"FALLO: no hay línea WORKER_KAEZ_URL en {p} para anclar {url_var}", file=sys.stderr); sys.exit(1)
    lines.insert(anchor[0] + 1, url_line)
    print(f"  router.toml: {url_var} añadido")
# LOGIN_USER_MAP: añadir/actualizar la entrada usuario -> tenant.
m[user] = tenant
new_map = json.dumps(m, sort_keys=True, separators=(",", ":"))
# En TOML es una cadena básica con comillas escapadas: {"a":"b"} -> "{\"a\":\"b\"}".
toml_escaped = new_map.replace("\\", "\\\\").replace('"', '\\"')
pat = re.compile(r'^LOGIN_USER_MAP = ".*"$')
hit = [i for i, ln in enumerate(lines) if pat.match(ln.rstrip("\n"))]
if len(hit) != 1:
    print(f"FALLO: se esperaba 1 línea LOGIN_USER_MAP en {p}, hay {len(hit)}", file=sys.stderr); sys.exit(1)
lines[hit[0]] = f'LOGIN_USER_MAP = "{toml_escaped}"\n'
open(p, "w").write("".join(lines))
print(f"  router.toml: LOGIN_USER_MAP[{user}] = {tenant}")
PY
  # Verificación local de los dos archivos.
  python3 -c "import json; d=json.load(open('$WORKERS_JSON')); assert d['$LOGIN_USER']=='$WORKER_URL', 'workers.json sin la entrada'" \
    || die "workers.json no quedó con $LOGIN_USER -> $WORKER_URL"
  grep -q "^$ROUTER_URL_VAR = \"$WORKER_URL\"\$" "$ROUTER_TOML" \
    || die "router.toml no quedó con $ROUTER_URL_VAR = $WORKER_URL"
  python3 - "$LOGIN_USER" "$TENANT" <<'PY'
import sys, tomllib
user, tenant = sys.argv[1], sys.argv[2]
with open("wrangler.router.toml", "rb") as f:
    m = tomllib.load(f)["vars"]["LOGIN_USER_MAP"]
import json
assert json.loads(m)[user] == tenant, "LOGIN_USER_MAP sin la entrada"
PY
  log "workers.json y router.toml actualizados y verificados"
  if [ "$SKIP_CLOUD" = 1 ]; then
    log "--skip-cloud: Pages y router no redesplegados en este modo"
  elif [ "$SKIP_PAGES_DEPLOY" = 1 ]; then
    # Bug 2 (2026-11-21): el token que se pasó puede ser el del tenant.
    # Desplegar Pages/router con él NO actualizaría el panel compartido
    # y podría crear un Pages/Worker `avc` duplicado en su cuenta.
    # Se salta el despliegue; el routing se repara con el token admin.
    PAGES_DEPLOY_SKIPPED=1
    log "--skip-pages-deploy: Pages y router NO se redespliegan con este token"
    echo "  Para reparar el routing con el token admin (variable $ADMIN_TOKEN_ENV cargada en el entorno):"
    echo "    $REPAIR_CMD"
    report_tenant_account_duplicates
  else
    if [ "${CLOUDFLARE_ACCOUNT_ID:-$ACCOUNT_ID}" != "$ACCOUNT_ID_DEFAULT" ]; then
      warn "La cuenta ${CLOUDFLARE_ACCOUNT_ID:-$ACCOUNT_ID} no es la dueña del panel compartido: este despliegue de Pages/router NO actualizará el Pages $PAGES_URL y puede crear un Pages/Worker 'avc' duplicado en esa cuenta. La próxima vez pasa --skip-pages-deploy y repara el routing con el token admin: $REPAIR_CMD"
    fi
    log "Redesplegando Pages ($PAGES_PROJECT) con el workers.json nuevo"
    run_in "$ROOT" "$WRANGLER" pages deploy frontend/dist --project-name "$PAGES_PROJECT"
    log "Redesplegando el router (Worker avc) con el mapa nuevo"
    run_in "$WORKER_DIR" "$WRANGLER" deploy --config ../wrangler.router.toml
    report_tenant_account_duplicates
  fi
fi

# ---------------------------------------------------------------------------
# Paso 8 — Verificación final (sin probar el login con la contraseña real)
# ---------------------------------------------------------------------------
# El login NO se prueba: la contraseña solo existe en memoria y en el
# resumen final (si se generó); probarlo exigiría enviarla y guardarla.
# Lo que se verifica: /api/health del Worker nuevo responde app=avc y
# tenant=<tenant> (scripts/health-check.sh, con reintentos por la
# propagación del deploy), y Pages sirve el workers.json nuevo.
log "Paso 8 — verificación: /api/health del Worker nuevo y workers.json en Pages"
if [ "$DRY_RUN" = 1 ]; then
  log "DRY-RUN: scripts/health-check.sh $WORKER_URL $TENANT (hasta 5 intentos, 15 s)"
  log "DRY-RUN: curl $PAGES_URL/workers.json y comprobar [$LOGIN_USER] == $WORKER_URL"
elif [ "$SKIP_CLOUD" = 1 ]; then
  log "--skip-cloud: verificación en vivo omitida en este modo"
else
  OK=0
  for i in 1 2 3 4 5; do
    if "$ROOT/scripts/health-check.sh" "$WORKER_URL" "$TENANT"; then OK=1; break; fi
    warn "health-check intento $i/5 falló; reintentando en 15 s (propagación del deploy)"
    sleep 15
  done
  [ "$OK" = 1 ] || die "/api/health de $WORKER_URL no responde con tenant=$TENANT tras 5 intentos"
  # A5: el Worker nuevo debe estar PARADO (worker_running=false). Sin
  # fila en worker_state, /api/health devuelve false. Si es true,
  # algo sembró worker_state en marcha: no encender el cron hasta
  # aclararlo.
  HEALTH_JSON="$(curl -fsS -m 15 "$WORKER_URL/api/health")" \
    || die "no se pudo leer /api/health de $WORKER_URL"
  printf '%s' "$HEALTH_JSON" | grep -q '"worker_running":false' \
    || die "/api/health de $WORKER_URL dice worker_running=true (A5: un Worker nuevo nace parado); no seguir hasta aclararlo"
  log "Verificado: worker_running=false (el Worker nace parado)"
  PAGES_OK=0
  for i in 1 2 3; do
    if curl -fsS -m 15 "$PAGES_URL/workers.json" \
      | jq -e --arg u "$LOGIN_USER" --arg w "$WORKER_URL" '.[$u] == $w' >/dev/null; then
      PAGES_OK=1; break
    fi
    warn "workers.json en Pages aún no trae la entrada (intento $i/3); reintentando en 20 s"
    sleep 20
  done
  [ "$PAGES_OK" = 1 ] \
    || warn "Pages aún no sirve workers.json con $LOGIN_USER -> $WORKER_URL (propagación de Pages; verifica a mano en $PAGES_URL/workers.json)"
  log "Verificación OK: /api/health responde app=avc, tenant=$TENANT"
fi

# ---------------------------------------------------------------------------
# Resumen final (la contraseña generada se muestra UNA vez y no se guarda)
# ---------------------------------------------------------------------------
echo
echo "OK: tenant '$TENANT' aprovisionado"
echo "  Worker:  $WORKER_URL"
echo "  Panel:   $PAGES_URL  (usuario de login: $LOGIN_USER)"
echo "  D1:      $DB_NAME"
echo "  Config:  wrangler.$TENANT.toml"
echo "  Router:  $ROUTER_URL_VAR en wrangler.router.toml + LOGIN_USER_MAP[$LOGIN_USER]=$TENANT"
if [ "$DRY_RUN" = 1 ]; then
  echo "  (dry-run: no se creó nada)"
  echo "  Cron: APAGADO en el despliegue (A5); se enciende con: scripts/provision-tenant.sh $TENANT --enable-cron"
elif [ "$SKIP_CLOUD" = 1 ]; then
  echo "  (--skip-cloud: solo pasos locales; el SQL de siembra se mostró arriba)"
  echo "  Falta: re-ejecutar SIN --skip-cloud para crear D1, Worker, secretos y verificar."
else
  echo "  Secretos: SESSION_SECRET y CREDENTIAL_ENCRYPTION_KEY frescos; GITHUB_TOKEN,"
  echo "            TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET y BRIDGE_TOKEN compartidos;"
  echo "            GOOGLE_CLIENT_ID/SECRET sin poner (los crea el usuario; el panel lo guía)."
  echo "  Cron:     APAGADO (A5). Encenderlo tras verificar, con:"
  echo "            scripts/provision-tenant.sh $TENANT --enable-cron"
  if [ "${GENERATED_PASSWORD:-0}" = 1 ]; then
    echo
    echo "  *** CONTRASEÑA GENERADA (se muestra UNA vez, no está en ningún archivo) ***"
    echo "  Usuario: $LOGIN_USER"
    echo "  Contraseña: $PASSWORD"
    echo "  Guárdala ahora: no hay forma de recuperarla (solo se puede regenerar el hash)."
    echo "  *** Fin de la contraseña ***"
  fi
  echo
  if [ "$PAGES_DEPLOY_SKIPPED" = 1 ]; then
    echo "  ROUTING PENDIENTE (--skip-pages-deploy): Pages y router NO se"
    echo "            redesplegaron con este token. Para dejar el panel"
    echo "            sirviendo a $LOGIN_USER, ejecuta con el token admin:"
    echo "            $REPAIR_CMD"
  fi
  echo "Pendientes (operador y usuario):"
  echo "  1. Encender el cron (operador, tras verificar): scripts/provision-tenant.sh $TENANT --enable-cron"
  echo "  2. Entrar en $PAGES_URL con $LOGIN_USER y completar el onboarding:"
  echo "     OAuth Google/Meta (crear en sus consolas), claves Twitch/Gemini,"
  echo "     conectar y verificar YouTube, TikTok, Meta, Twitch y Gemini."
  echo "  3. Pulsar Iniciar en la pestaña Worker (el Worker nace parado)."
  echo "  4. Elegir influencers con permiso de clipping."
fi
