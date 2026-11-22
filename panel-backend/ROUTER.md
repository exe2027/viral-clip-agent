# Router sin estado `avc` — proxy del cerebro a los Workers por usuario

Fecha: 2026-11-19. Decisión "un Worker por usuario".

## Qué es

El Worker viejo `avc` (`https://avc.vikingosfoodspa.workers.dev`) deja de
ser el panel y pasa a ser SOLO el proxy del cerebro: el cerebro lleva
`PANEL_URL` hardcodeado a esa URL en
`.github/workflows/cerebro-live.yml` (3 lugares) y el repo del cerebro
no se puede tocar ahora. El código vive en `worker/src/router.ts` y se
despliega con `wrangler.router.toml` (mismo `name = "avc"`: desplegarlo
REEMPLAZA el Worker viejo; su cron muere con el reemplazo).

## Qué NO hace (sin estado)

- Sin bindings D1 ni R2, sin cron, sin secretos.
- NO verifica firmas: decodifica el `body` b64url del token de sesión
  (`body.sig`, `{sub, tenant, exp}`) y del `state` OAuth solo para LEER
  el `tenant`. La verificación HMAC la hace el Worker destino, que
  tiene el mismo `SESSION_SECRET`. Un token falsificado se reenvía
  igual y el destino lo rechaza con 401.
- NO toca D1: ni lee `panel_users` (el mapa usuario→tenant es la var
  `LOGIN_USER_MAP`), ni marca tokens, ni registra nada.
- NO autentica: un 401 del destino pasa tal cual al cerebro.
- NO cubre el frontend ni `/api/auth/me`, `/api/auth/logout`,
  `/api/public-video`, onboarding ni `/api/jobs/next`: esas rutas dan
  404 aquí. El frontend apunta directo a cada Worker por usuario
  (`frontend/dist/workers.json`).

## Tabla de rutas

| Ruta | Método | Destino |
| --- | --- | --- |
| `/api/health` | GET | JSON propio del router (`{ok, mode:"router", ...}`), sin reenviar |
| `/api/brain/session` | POST | Probing: sebastian y luego kaez con el MISMO body; primera 2xx tal cual; si ambas fallan, el error del primer intento |
| `/api/bridge/*`, `/api/clips*`, `/api/tracking/*`, `/api/worker/*`, `/api/influencers`, `/api/jobs`, `/api/accounts` | * | Tenant del Bearer (o cookie `panel-session`); sin sesión o sin tenant → 401. En `/api/bridge/*`, sin sesión se acepta `?tenant=` (como el panel con `BRIDGE_TOKEN`); la auth la hace el destino |
| `/api/oauth/<google\|meta>/callback` | GET | Tenant del `state` b64url-JSON `{provider, tenant, exp}` |
| `/api/auth/login` | POST | Usuario del body según `LOGIN_USER_MAP`; desconocido → 401 genérico (mismo mensaje que el destino) |
| resto | * | 404 `{ok:false, error:"Ruta no cubierta por el router avc."}` |

El reenvío conserva método, path, query, headers y body. El PUT del
MP4 (`/api/clips/:id/video`, hasta 128 MB) se reenvía en streaming, sin
cargarlo en memoria. Las respuestas pasan con su status, body y headers
(incluido `Set-Cookie` del login) más la cabecera de diagnóstico
`x-avc-router: avc-router`.

## Por qué el probing de `/api/brain/session` no consume el token

Verificado en `worker/src/brain.ts`, `redeemBrainToken` (líneas ~85-104):

1. `SELECT ... FROM brain_tokens WHERE token_hash = ?` — si no hay
   fila, lanza `"Token del cerebro inválido."` ANTES de cualquier
   escritura.
2. Solo si la fila existe, el tenant es válido, no caducó y
   `used_at IS NULL`, ejecuta
   `UPDATE brain_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL`
   (atómico: dos canjes simultáneos solo dejan pasar uno).

Cada tenant guarda sus tokens solo en SU D1: probar en sebastian un
token de kaez falla sin escribir nada; el intento en kaez canjea con
éxito. El handler HTTP devuelve 401 en ese fallo
(`worker/src/index.ts`, POST `/api/brain/session`), que es lo que el
router usa para seguir probando. Test que lo cubre:
`worker/test/router.test.ts` ("token de kaez: falla en sebastian (401)
y canjea en kaez (200)").

## Configuración

`wrangler.router.toml` (vars, NO hardcodeadas en el código):

- `WORKER_SEBASTIAN_URL = "https://avc-sebastian.vikingosfoodspa.workers.dev"`
- `WORKER_KAEZ_URL = "https://avc-kaez.vikingosfoodspa.workers.dev"`
- `LOGIN_USER_MAP = "{\"sebastian\":\"sebastian\",\"kaez\":\"kaez\"}"`
  (usuario == tenant; verificado en `worker/src/auth.ts` — "tenant
  kaez, usuario kaez" — y `scripts/seed-users.sql` para sebastian.
  Mantener igual que `frontend/dist/workers.json` cuando exista.)

## Despliegue

SOLO cuando `avc-sebastian` y `avc-kaez` estén en vivo:

```sh
cd ~/workspace/gta6-clips/independencia/panel-backend
wrangler deploy --config wrangler.router.toml
```

## Pruebas tras el deploy (curl)

```sh
BASE=https://avc.vikingosfoodspa.workers.dev

# 1. Salud propia del router (no reenvía nada)
curl -s $BASE/api/health
# -> {"ok":true,"app":"avc-router","mode":"router","tenants":["sebastian","kaez"],...}

# 2. Sin sesión -> 401 del router, sin reenviar
curl -s -o /dev/null -w "%{http_code}\n" $BASE/api/bridge/credentials
# -> 401

# 3. Canje real: pedir al panel de sebastian que dispare el cerebro
# (o un dispatch manual) y comprobar en los logs de GitHub que el
# POST /api/brain/session devuelve 200 con tenant sebastian, y que
# las llamadas siguientes (/api/bridge/credentials, /api/worker/phase)
# llegan al Worker de sebastian (cabecera x-avc-router presente y
# respuesta del tenant correcto).

# 4. Login por usuario (fallback del cerebro)
curl -s -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"kaez","password":"<su contraseña>"}'
# -> {"ok":true,"tenant":"kaez","token":"..."} con Set-Cookie

# 5. Ruta no cubierta -> 404
curl -s -o /dev/null -w "%{http_code}\n" $BASE/api/auth/me
# -> 404
```

## Tests locales

```sh
cd worker
npx tsc --noEmit -p tsconfig.json   # en verde
bun test test/router.test.ts        # 15 tests
bun test test/                      # suite completa: 488 pass (2026-11-19)
```
