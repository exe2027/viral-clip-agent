# Vigilante de tenant + cerebro propio de kaez

2026-11-21. Auto-reparación del cerebro por tenant, independiente
de sebastian. Kaez se ENCIENDE AHORA (no a las 21:00): la
restricción de las 21:00 es SOLO por la cuota D1 agotada de
sebastian (100.306 escrituras); la D1 de kaez está fresca y su
cuenta de Cloudflare es propia.

## El problema que resuelve

Kaez: Worker en marcha (Iniciar pulsado 12:11 y 13:01), 5 cuentas
conectadas y verificadas, 20 influencers, pero el panel mostraba
ATASCADO "Sin señal del cerebro todavía" y `last_live_dispatch_at`
era NULL. El cerebro nunca llegó a su tenant.

Causa raíz (verificada en vivo, 3 corridas fallidas de
`cerebro.yml`: 44917889824, 44922296080, 44922316075):

1. El cerebro genérico (`cerebro.yml` / `cerebro-live.yml`)
   resolvía siempre la URL del Worker de sebastian: PanelBridge
   solo lee `PANEL_BASE_URL` y esos workflows fijaban `PANEL_URL`
   (que el puente ignora). El canje del brain_token de kaez daba
   401 "Token del cerebro inválido" porque el token vive en la D1
   de kaez, no en la de sebastian.
2. Nadie volvía a intentarlo: el cron del Worker está apagado y
   el dispatch de Iniciar es de un solo intento.

## La solución (todo independiente de sebastian)

| Pieza | Qué es | Estado |
|---|---|---|
| `clipagent/tenant_watchdog.py` | Detecta "Worker en marcha + dispatch NULL/obsoleto + Cola vacía" y dispara el cerebro propio de kaez | Nuevo, 20/20 tests |
| `.github/workflows/cerebro-tenant-watchdog.yml` | Vigila a kaez cada 30 min (+ dispatch manual). `TENANT_WATCHDOG_ENABLED: "true"` (encendido ahora) | Nuevo |
| `.github/workflows/cerebro-kaez.yml` | Cerebro diario SOLO de kaez (watch VODs + worker + publish + audit). Auth por sesión del entorno `kaez`, `PANEL_BASE_URL` al Worker de kaez | Nuevo |
| GitHub Environment `kaez` | Secretos del vigilante y del cerebro de kaez | Creado (vacío; falta `PANEL_PASSWORD`) |
| `config.yaml` → `tenant_watchdog:` | `cooldown_min: 60`, `stale_live_dispatch_min: 60` (single-source, sin defaults silenciosos) | Añadido |

Los workflows compartidos (`cerebro.yml`, `cerebro-live.yml`,
`cerebro-sebastian.yml`) NO se tocaron: el cerebro de sebastian
sigue apagado hasta las 21:00 y su D1 no se escribe. La
condición de atascado es estricta (solo "nunca arrancó"): un
tenant con clips/jobs no se toca aquí.

## Cómo funciona el auto-fix

1. El vigilante lee el estado del panel de kaez por sesión
   (`GET /api/worker/state` → running, `lastSweepAt`) y la Cola
   (`GET /api/clips`, `GET /api/jobs`).
2. Atascado = running + (`lastSweepAt` NULL o más viejo que 60
   min) + 0 clips + 0 jobs.
3. Si está atascado y no hay una corrida de `cerebro-kaez.yml`
   iniciada hace menos de 60 min (idempotencia por la API de
   GitHub), dispara `repository_dispatch` `panel-start-kaez`.
4. `cerebro-kaez.yml` corre la cadena (VODs → worker → publish
   → audit → status) contra el Worker/D1/R2 de kaez. El cerebro
   publica respetando franjas, topes y calentamiento (el gate
   del Worker); el vigilante no fuerza nada.

Guardas: ante un 7500 se aborta todo sin dispatch ni reintentos
(circuito de `clipagent/retry.py`); el dispatch no escribe en D1;
un solo tenant por corrida (`require_tenant`, lista blanca);
$0 (sin ffmpeg/Whisper en el vigilante).

## El único paso pendiente (1 secreto)

El entorno `kaez` está creado pero VACÍO. Faltan 2 secretos:

| Secreto | Valor | Quién lo pone |
|---|---|---|
| `PANEL_USERNAME` | `kaez` | cualquiera (no es secreto, es el usuario del panel) |
| `PANEL_PASSWORD` | la contraseña del panel de kaez (la que el usuario fijó) | kaez o el usuario, por entrada segura (nunca en chat) |

Comandos (sin exponer el valor en logs):

```bash
gh secret set PANEL_USERNAME --env kaez --body "kaez"
gh secret set PANEL_PASSWORD --env kaez   # pide el valor sin eco
```

En cuanto estén puestos, kaez queda ACTIVO de inmediato (no hay
que esperar a las 21:00):

1. Pushear estos archivos (sin commitear nada de sebastian).
2. Probar el vigilante en dry-run: Actions → "Cerebro —
   Vigilante de tenant" → Run workflow (live=false). Debe
   informar "atascado, would-dispatch".
3. Probar en vivo: mismo workflow con live=true. Debe disparar
   `panel-start-kaez` y `cerebro-kaez.yml` debe correr y encolar
   los primeros clips de kaez.
4. A partir de ahí el vigilante corre solo cada 30 min.

## Por qué no valió el BRIDGE_TOKEN

Verificado en el código del Worker (`worker/src/index.ts`,
`mcp.ts`): el bridge token solo autentica
`GET /api/bridge/settings` y `/mcp` (lecturas + `retry_job`).
El cerebro necesita sesión para `GET /api/bridge/credentials`
(bootstrap de Twitch/Gemini/YouTube/Zernio/Meta),
`POST /api/clips`, `PUT /api/clips/:id/video`,
`POST /api/worker/run-once`, `/api/worker/phase` y
`/api/worker/state`. Por eso la vía es la sesión del panel
(entorno `kaez`), no el bridge token. El Worker de kaez ni
siquiera tiene secreto `BRIDGE_TOKEN`.

## Lo que NO cubre (aún)

- El cortador en vivo (`cerebro-live.yml`, `panel-live` cada 15
  min) sigue apagado (workflow compartido, `disabled_manually`).
  El cerebro diario de kaez (`cerebro-kaez.yml`) ya corta de
  VODs, así que kaez deja de estar ATASCADO sin él. El vivo se
  enciende a las 21:00 con la reactivación general (o se le hace
  su workflow propio `cerebro-live-kaez.yml` si se quiere antes).
- Los 7 influencers EN de kaez (Kai Cenat, N3ON, xQc,
  TheBurntPeanut, Burn, Kaysan, Will Neff) quedaron con
  `active=0, permission=0` (los apagó kaez o el panel tras la
  migración). ES está completo (12/12), así que el cerebro
  arranca igual; si kaez quiere EN, hay que reactivarlos en el
  panel.
