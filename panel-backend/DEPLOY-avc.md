# Despliegue de avc.pages.dev — runbook único

Decisión del usuario 17 nov 2026, 15:05 y 15:33: **UNA sola página,
https://avc.pages.dev, con login al entrar**. Cada persona entra con
su login y ve solo su panel, sus cuentas, su cola y sus credenciales.
NO un avc-colega.pages.dev separado. AVC = Agent Viral Clip.
Primero desplegar avc.pages.dev, para probar lo demás (IG/FB por API,
etc.).

## Qué es la instalación única

| Pieza | Nombre | Notas |
|---|---|---|
| Pages (front) | `avc` | https://avc.pages.dev |
| Worker (API) | `avc` | `avc.<cuenta>.workers.dev` (lo usa el front) |
| D1 | `clips-avc` | Una base; `tenant_id` por fila (migración 0012) |
| R2 | `clips-avc` | Keys `clips/<tenant>/<clip_id>/<archivo>.mp4` |
| Cron | cada 5 min | Un ciclo por tenant (el parado no frena al otro) |
| Cuenta Cloudflare | la de Sebastián | UNA sola cuenta y UNA sola cuota gratis para los dos |

## Estado al escribir esto (17 nov 2026, verificado)

- ✅ Código adaptado a multi-tenant: migración 0012, login por
  usuario, `tenant_id` en todas las consultas, cifrado HKDF por
  tenant, keys R2 con prefijo, puente con `X-Bridge-Tenant`.
- ✅ `tsc --noEmit` limpio. ✅ `bun test` **17/17** (12 anteriores +
  5 nuevas: cifrado por tenant, sesión con tenant).
- ✅ Migraciones 0001–0012 aplicadas en SQLite real + `seed-users.sql`
  (con hashes falsos) + upserts de settings por tenant.
- ✅ `wrangler` 4.164.0 instalado localmente (`worker/node_modules`).
  Corregido un conflicto preexistente: `wrangler@4.164.0` exigía
  `@cloudflare/workers-types@^5`; se subió de `^4` a `^5` en
  `worker/package.json` (con `package-lock.json` generado).
- ❌ **NO desplegado**: `wrangler whoami` dice *"You are not
  authenticated. Please run `wrangler login`."* No hay credenciales
  de Cloudflare en este entorno y el login es OAuth en navegador:
  solo lo puede hacer Sebastián. No se pidió ni se guardó ningún
  token o contraseña en chat.
- ❌ Nombre `avc.pages.dev` sin verificar: el intento de abrirlo
  desde aquí falló sin HTTP (no resuelve: coherente con que el
  proyecto aún no existe, pero NO es prueba). La comprobación real
  es el paso 1 (`wrangler pages project create avc` falla si el
  nombre está ocupado).
- ❌ Front de Pages NO está en este repo: hay que exportar el build
  del panel (artifact) y desplegarlo (§5).
- ❌ Push 403 a GitHub sigue pendiente (ver §7): NO bloquea el
  despliegue de Pages/Worker (Wrangler despliega desde archivos
  locales), pero SÍ bloquea los workflows del cerebro en GitHub
  Actions (viven en `exe2027/viral-clip-agent`, sin push).

## Paso 0 — Login en Cloudflare (solo Sebastián, en su navegador)

```bash
cd ~/workspace/gta6-clips/independencia/panel-backend
./worker/node_modules/.bin/wrangler login
# Abre el navegador, entra con su cuenta Cloudflare (gratis) y autoriza.
# Alternativa sin navegador: crear un API token en dash.cloudflare.com
# (My Profile → API Tokens → Create Token → Edit Cloudflare Workers)
# y exportar CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID.
```

## Paso 1 — Crear los recursos (una vez)

```bash
./worker/node_modules/.bin/wrangler d1 create clips-avc
# → copiar el database_id que devuelve
./worker/node_modules/.bin/wrangler r2 bucket create clips-avc
./worker/node_modules/.bin/wrangler pages project create avc
# R2 → bucket clips-avc → Settings → Public access: activar el
# dominio público (https://pub-xxxx.r2.dev). Sin dominio público,
# Instagram queda en draft (Meta no puede descargar el video).
```

## Paso 2 — Rellenar `wrangler.toml` (raíz de panel-backend)

```toml
database_id = "<el database_id del paso 1>"   # era RELLENAR-CON-...
PUBLIC_BASE_URL = "https://pub-xxxx.r2.dev"   # era RELLENAR-DOMINIO-PUBLICO-R2
```

## Paso 3 — Migraciones

```bash
cd worker && npm run d1:migrate
# Aplica 0001–0012 a clips-avc (remoto). Verificar que termina en 0012.
```

## Paso 4 — Usuarios de login (contraseñas, nunca por chat)

```bash
node ../scripts/gen-password-hash.mjs   # contraseña de Sebastián (la pide 2 veces, sin eco)
node ../scripts/gen-password-hash.mjs   # contraseña de cada usuario nuevo (p. ej. kaez)
# Pegar cada hash en scripts/seed-users.sql (RELLENAR_HASH_SEBASTIAN /
# RELLENAR_HASH_<USUARIO>) y ejecutar:
./worker/node_modules/.bin/wrangler d1 execute clips-avc --file scripts/seed-users.sql --remote
# Comprobar: wrangler d1 execute clips-avc --remote --command "SELECT username, tenant FROM panel_users;"
# Debe listar sebastian/sebastian y kaez/kaez (el tenant "colega" se
# eliminó el 2026-11-19: nunca fue un usuario). Los hashes de
# seed-users.sql NO se commitean con valores reales.
```

## Paso 5 — Front de Pages (el build no está en este repo)

```bash
# 1. Exportar el build del panel (artifact panel-de-clips-gta-6) a una
#    carpeta, p. ej. panel-backend/frontend/dist (index.html + assets).
#    El front debe llamar a la API del Worker avc con credentials:include
#    (ver frontend-notes.md: /login con {username, password}).
# 2. Desplegarlo:
./worker/node_modules/.bin/wrangler pages deploy frontend/dist --project-name avc
# 3. Abrir https://avc.pages.dev → debe mostrar el login.
```

## Paso 6 — Secretos (cada uno con `wrangler secret put`)

Desde `panel-backend/` (sin `--config`: el `wrangler.toml` de la raíz
es el de avc):

| Secreto | Cómo se genera / de dónde sale |
|---|---|
| `SESSION_SECRET` | `openssl rand -base64 32` (firma cookies; cambiarlo invalida todas las sesiones) |
| `CREDENTIAL_ENCRYPTION_KEY` | `openssl rand -base64 32` (maestro AES; de él se deriva una clave por tenant con HKDF; cambiarlo exige volver a pegar TODAS las credenciales) |
| `BRIDGE_TOKEN` | `openssl rand -base64 32` (solo lo usa el puente alternativo) |
| `GITHUB_TOKEN` | Token de GitHub con `actions:write` sobre `exe2027/viral-clip-agent`: el botón Iniciar dispara el workflow del cerebro con `repository_dispatch` (`panel-start-<tenant>`). Sin él, el panel sigue con el flag D1 y el cron de GitHub queda en espera (queda registrado en `worker_events`). |

```bash
./worker/node_modules/.bin/wrangler secret put SESSION_SECRET
./worker/node_modules/.bin/wrangler secret put CREDENTIAL_ENCRYPTION_KEY
./worker/node_modules/.bin/wrangler secret put BRIDGE_TOKEN
./worker/node_modules/.bin/wrangler secret put GITHUB_TOKEN
```

## Paso 7 — Migrar datos del panel v17 (opcional)

```bash
python3 scripts/migrate-appdb-to-d1.py --db /ruta/a/app.db --tenant sebastian --out /tmp/migracion-sebastian.sql
# Revisar /tmp/migracion-sebastian.sql: las credenciales salen VACÍAS (REDACTED).
./worker/node_modules/.bin/wrangler d1 execute clips-avc --file /tmp/migracion-sebastian.sql --remote
# Repetir con --tenant colega si el colega tiene su propio app.db.
# Los usuarios de login NO se migran: ya están en seed-users.sql.
```

## Paso 8 — Desplegar el Worker y comprobar

```bash
./scripts/deploy.sh
# Hace: npm ci + tsc + bun test + wrangler deploy + health-check.
./scripts/health-check.sh https://avc.pages.dev
# Debe responder {"ok":true,"app":"avc","tenants":["sebastian","colega"],
# "worker_running":{"sebastian":false,"colega":false},...}
```

## Paso 9 — Primer login y credenciales de plataformas

1. Abrir https://avc.pages.dev → login con `sebastian` + su contraseña.
2. Cuentas → volver a pegar cada credencial (la migración las redactó
   a propósito):
   - **YouTube**: API key (verificación) + en `extra`, JSON
     `{"client_id":"…","client_secret":"…","refresh_token":"…"}` (OAuth).
   - **TikTok**: access token con scope `video.publish` (sin auditoría,
     solo privado → draft).
   - **Meta**: token LARGO de la Página + en `extra`, JSON
     `{"ig_user_id":"17841457001703774","page_id":"1346242648582758"}`.
     App ID 29102700319392159 es solo de Sebastián.
3. Botón Verificar en cada cuenta (llama a la API de verdad).
4. Repetir con el login `colega` (sus propias cuentas; nunca ve las
   de Sebastián).

## Paso 10 — Probar 1 clip por tenant

Worker → Iniciar. Subir un MP4 9:16 de prueba y publicar en YouTube
(1 clip, verificar la URL en Tracking). Después, Instagram y
Facebook por API (con el dominio público R2 ya puesto).

## §7 — Push 403 a GitHub (no bloquea Pages, sí bloquea el cerebro)

- El despliegue de Pages/Worker con Wrangler NO necesita GitHub:
  usa los archivos locales de `panel-backend/` (que además no está
  en ningún repo git).
- Lo que SÍ necesita el push: los workflows del cerebro
  (`.github/workflows/cerebro-sebastian.yml` /
  `cerebro-colega.yml`, en `exe2027/viral-clip-agent`). Sin push,
  el botón Iniciar solo pone el flag D1 y GitHub no corre nada.
- Estado: `gh` no logueado en la terminal (`gh auth status`: "You
  are not logged into any GitHub hosts"); el push por HTTPS falla
  con `fatal: could not read Username`. El 403 original venía de la
  integración GitHub App de Muse, no de esta terminal. El usuario
  ya revisó la instalación de la app en GitHub (Settings →
  Applications → Muse): hay que reintentar el push desde la app
  (o `gh auth login` en una terminal con su cuenta) y verificar que
  `git push origin main` sube los commits locales `656d6f6` y
  `84aaef7` + el trabajo sin commitear.
