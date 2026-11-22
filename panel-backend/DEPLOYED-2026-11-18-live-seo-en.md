# Despliegue 2026-11-18 — cron en vivo + influencers EN + puerta SEO

## Tarea 1 — Worker cron */5 dispara el monitoreo en vivo (URGENTE)

- **Migración 0020** (`migrations/0020_worker_live_dispatch.sql`):
  `worker_state.last_live_dispatch_at INTEGER` (ms epoch, NULL = nunca).
  Verificado en vivo con `PRAGMA table_info(worker_state)`.
- **`worker/src/index.ts`**:
  - `dispatchCerebro` ahora devuelve `Promise<boolean>` (true solo si se
    intentó el POST, es decir, había `GITHUB_TOKEN`; false sin token).
  - Nuevo `maybeDispatchLive(env, tenant)` + `LIVE_DISPATCH_MIN_INTERVAL_MS`
    (10 min): dispara `panel-live-<tenant>` solo si pasaron >= 10 min desde
    el último dispatch; actualiza `last_live_dispatch_at` solo tras un
    intento real; sin token no actualiza (el siguiente ciclo reintenta).
    Un fallo de D1 no rompe el ciclo: queda en `worker_events` y se
    dispara igual (sin throttle) antes que perder el barrido.
  - `scheduled()`: tras `runWorkerCycle` por tenant, si `r.ran` (Worker en
    marcha) llama a `maybeDispatchLive`. Un tenant parado no dispara.
  - `POST /api/worker/run-once` hace lo mismo que el cron (ciclo + dispatch
    en vivo si toca), para forzar la verificación sin esperar al cron.
- **`GITHUB_TOKEN`**: existe como secreto del Worker (verificado con
  `wrangler secret list`; están `GITHUB_TOKEN`, `SESSION_SECRET`,
  `CREDENTIAL_ENCRYPTION_KEY`, `BRIDGE_TOKEN`). Sin bloqueador.
  `GITHUB_REPO` = `exe2027/viral-clip-agent` (var en `wrangler.toml`).
- **Despliegue Worker**: versión `128cff22-51b9-4ba2-87ae-30d372d06657`,
  cron `*/5 * * * *` activo en https://avc.vikingosfoodspa.workers.dev.
- **Verificación en vivo** (2026-11-18 19:40:51Z, tick del cron tras el
  despliegue): `worker_state.sebastian` `last_live_dispatch_at` =
  1795030848363; `worker_events` registra "Dispatch panel-live-sebastian
  enviado a GitHub (exe2027/viral-clip-agent)."; `gh run list --workflow
  cerebro-live.yml` muestra run **44382779254** "Cerebro — Live Cutter",
  evento **repository_dispatch**, título **panel-live-sebastian**,
  estado queued. El chequeo de las 19:40:45 llegó 3 s antes de que el
  dispatch aterrizara (19:40:48–51): el tick de las 19:40 disparó.
- **Throttle de 10 min verificado** (19:46:30Z): `last_live_dispatch_at`
  seguía en 1795030848363, sin nuevo `github_dispatch` — el tick de
  las 19:45 no volvió a disparar.

## Corrección 2026-11-18 19:46 — throttle 15 min + no disparar con corrida en curso

- Causa medida: la corrida 44381513042 se CANCELÓ a los ~9 min por el
  siguiente trigger (el workflow tenía `concurrency: cancel-in-progress:
  true` y una corrida dura ~11-13 min con triggers cada 10 min: con
  cancelación NINGUNA terminaba nunca). Además hay DOS fuentes de
  triggers (cron del workflow + dispatch del panel); a las 19:46:40Z
  apareció incluso un tercer trigger `workflow_dispatch` (run
  44383823349, pending detrás de 44382779254).
- **`worker/src/index.ts`**:
  - `LIVE_DISPATCH_MIN_INTERVAL_MS`: 10 → **15 min** por tenant (el
    workflow pasa a */15 con sesiones de 10 min; el parent lo cambia
    en el cerebro en paralelo; el workflow ya no cancela corridas).
  - Nuevo `liveRunStatus(env, tenant)`: GET
    `/repos/{repo}/actions/workflows/cerebro-live.yml/runs?status=
    in_progress&per_page=1` y lo mismo con `status=queued`; devuelve
    "busy" / "clear" / "unknown" (fallo de consulta = no disparar este
    ciclo; el motivo queda en `worker_events`). Sin token devuelve
    "unknown" y `maybeDispatchLive` aplica solo el throttle (ese caso
    es bloqueador pendiente — NO es el caso actual: `GITHUB_TOKEN`
    existe).
  - `maybeDispatchLive`: throttle 15 min → si "busy", registra evento
    `live_dispatch` ("Hay una corrida de cerebro-live en curso o en
    cola: no se dispara panel-live-<tenant> este ciclo") y salta;
    si "unknown" con token, tampoco dispara este ciclo.
  - Comentario de la migración 0020 actualizado a 15 min + chequeo
    (solo comentario; la migración ya estaba aplicada, no se re-ejecuta).
- **Despliegue Worker (corrección)**: versión
  `2debe4cf-dac0-42e0-96dd-f3d13938af07`; `bun test` 193 pass / 0 fail;
  `tsc --noEmit` OK.
- **Verificación en vivo de la corrección** (2026-11-18 20:01:25Z):
  el tick de las 20:00 (throttle de 15 min vencido a las 19:55:48)
  llegó al chequeo y SALTÓ el dispatch: `worker_events` registra a
  las 20:00:49Z `live_dispatch` "Hay una corrida de cerebro-live en
  curso o en cola: no se dispara panel-live-sebastian este ciclo
  (reintento en el siguiente)."; `last_live_dispatch_at` sigue en
  1795030848363; `gh run list` no muestra ningún `repository_dispatch`
  nuevo. La corrida 44382779254 terminó `completed/failure` a las
  ~20:00 (19m52s; el fallo es del cerebro, no del panel) y la
  44383823349 (`workflow_dispatch` 19:46:40Z) seguía en cola a las
  20:01 — el próximo tick volverá a saltar mientras haya cola.
  GITHUB_TOKEN/GITHUB_REPO presentes: sin bloqueador en el check.

## Tarea 2 — Influencers EN en D1 (tenant sebastian)

- 8 insertados en vivo (ids 11–18), `game` "GTA VI", `language` "en",
  `permission` 1, `active` 1, `permission_date` 2026-11-18,
  `publish_platforms` las 4:
  Kai Cenat (twitch, kaicenat), IShowSpeed (youtube, @ishowspeed),
  N3ON (kick, n3on), xQc (kick, xqc), TheBurntPeanut (twitch),
  Burn (twitch), Kaysan (twitch), Will Neff (twitch).
- Sin cambios de código: la tabla ya tenía `language` (0002) y `game`
  (0018); `GET /api/influencers` ya devuelve platform, game, language,
  active (`influencerOut`); la tarjeta del front ya muestra plataforma
  con etiqueta/icono + idioma + pill GTA VI + Permiso/Activo
  (`frontend/dist/app.js` `influencerCardHtml`).
- Verificado en D1: 10 ES + 8 EN. Verificación por API pendiente: el
  `PANEL_SESSION_TOKEN` del entorno caducó (401 "Inicia sesión primero")
  y la contraseña de `muse-servicio2` no estaba en el contexto del
  subagente (no está en archivos: solo "en el informe final al agente
  principal").

## Tarea 3 — POST /api/clips rechaza SEO bajo

- **`worker/src/index.ts` `parseClipInput`**: si el POST trae
  `seo_scores` (sueltos o dentro de `viral`) y el MÁXIMO es <
  `min_seo` de Ajustes (default 90; en vivo sebastian: 90), 400 salvo
  override explícito (`seo_override: true`). Sin `seo_scores` no se
  rechaza por SEO (decide la puerta de viralidad).
- **Tests** (`worker/test/clips-scoring.test.ts`, bloque nuevo
  "POST /api/clips: puerta SEO en la creación"): 6 tests — máx 88 → 400,
  máx 94 → 201, máx exactamente 90 → 201, sin seo_scores → 201,
  bajos + `seo_override: true` → 201, bajos dentro de `viral` → 400.
- Suite: `bun test` 193 pass / 0 fail; `tsc --noEmit` OK.
- Verificación en vivo con clips de prueba + borrado: pendiente de login
  de servicio (mismo bloqueador que tarea 2).

## Despliegue

- `python3 scripts/deploy_with_token.py migrate`: 0020 ✅. El paso `all`
  sigue fallando por el `schema.sql` obsoleto preexistente ("table
  settings already exists", fallo ya conocido, documentado en
  DEPLOYED-2026-11-18-dos-colas.md); Worker y Pages se desplegaron por
  separado con `deploy-worker` + `deploy-pages` como entonces.
- Pages: 0 archivos nuevos (front sin cambios), despliegue completo en
  https://avc-em9.pages.dev (alias https://9b2d4aa0.avc-em9.pages.dev).
- `GET /api/health` en vivo: ok, `worker_running.sebastian=true`.
