# Campaign Submissions (migración 0041) — diseño

Fecha: 2026-11-23. Estado: **código en el repo, SIN desplegar** (el
coordinador despliega con `wr_v2.py`; ver "Despliegue" abajo). No se
envió nada real, no se unió a ninguna campaña, no se gastó nada.

## 1. El problema

Un clip publicado por el panel/Worker tiene que llegar a Content
Rewards **dentro de 30 minutos** de su publicación, **una sola vez**,
a **una sola campaña** que paga. La extensión de Chrome es el único
camino que puede llamar al MCP oficial (la API key de Content Rewards
vive solo en el Chrome del usuario), pero Chrome puede estar cerrado.
Hace falta un registro en la nube que sobreviva sin Chrome y que diga,
con el instante REAL de publicación, qué hay que enviar y qué ya se
envió.

## 2. El disparador: `markPosted` (no el panel, no el endpoint publish)

El punto real de publicación es **`markPosted`** en
`panel-backend/worker/src/jobs.ts:1229` — la función que pasa un job a
`posted` con su `evidence_url`. Es el único embudo por el que un job
se convierte en publicado (la llaman el bridge `index.ts:2132` y el
ciclo `jobs.ts:1658`).

**Por qué no los otros puntos:**
- `POST /api/clips/:id/publish` (`frontend/dist/app.js:1049,2050` →
  Worker `index.ts:3080-3126`) **solo encola**: la publicación real
  ocurre después, en el ciclo. Disparar ahí enviaría un link que aún
  no existe.
- El panel nunca ve el `evidence_url` real: solo el Worker lo tiene
  cuando el job pasa a `posted`.
- No hay push del Worker a la extensión (ningún canal push): el
  disparador es **una fila en D1** que la extensión lee por GET.

**Qué hace `markPosted` (v1.3.0):** calcula `postedAtMs = nowMs()`
**una sola vez** y lo reutiliza para `jobs.updated_at` y para
`stampCampaignSubmissionPosted(db, tenant, clip_id, platform,
evidenceUrl, postedAtMs)` (`jobs.ts:1288`, best-effort con
`.catch(() => 0)` para no romper el ciclo 39/día si la tabla aún no
existe). El `publishedAtMs` que ve la extensión es ese instante real:
**nunca `Date.now()` por defecto**.

**Hueco conocido:** el flush de la WAL (`wal.ts:250`) marca jobs como
`posted` con un UPDATE directo y **no pasa por `markPosted`**. Se cubre
con `backfillCampaignSubmissionsPosted` (`db.ts`): cada lectura de
pendientes (`listPendingCampaignSubmissions`) primero rellena las filas
`pending` sin `published_at_ms` desde los jobs `posted` de D1 (mismo
patrón defensivo con try/catch que la retención 0039). Si el backfill
no encuentra el job, la fila queda sin `published_at_ms` y la extensión
la bloquea (`missing_published_at`): nunca se inventa el instante.

## 3. La tabla `campaign_submissions` (migración 0041)

`panel-backend/migrations/0041_campaign_submissions.sql`. Una fila =
**un envío de un clip en una plataforma a una campaña** (no por clip
solo: el mismo clip en TikTok y en YouTube son dos filas).

| Columna | Contenido |
|---|---|
| `id` | PK autoincremental |
| `tenant_id` | `sebastian` / `kaez` (la sesión es tenant-bound) |
| `clip_id` | n.º de clip |
| `platform` | `youtube` / `tiktok` / `instagram` / `facebook` / `x` |
| `marketplace` | `content_rewards` (default; el diseño admite otros) |
| `campaign_id` | UUID de la campaña en Content Rewards |
| `campaign_name` | nombre legible |
| `streamer` | streamer del clip (debe coincidir con el de la campaña) |
| `gross_rate_per_1k` / `net_rate_per_1k` | tarifa bruta y neta (neta = bruta × 0,9; piso $1/1k neto) |
| `min_views` / `cap_per_clip` | mínimo para cobrar y tope por clip |
| `post_url` | link público real (de `markPosted`, nunca inventado) |
| `published_at_ms` | instante REAL de publicación (de `markPosted`) |
| `status` | `pending` → `submitted` / `failed` / `expired` (CHECK) |
| `submission_id` | id que devuelve el MCP al enviar |
| `views` / `amount_usd` | vistas e importe (se rellenan después, por analíticas) |
| `error` | motivo del último fallo |
| `created_at` / `updated_at` | ms epoch |

- `UNIQUE (tenant_id, clip_id, platform, campaign_id)`: un post, una
  campaña, una vez — el duplicado es imposible por diseño, no solo por
  código. La escritura con duplicado devuelve la fila existente con
  `duplicate: true` (no error).
- Índices en `(tenant_id, clip_id, platform)` (chequeo de duplicado) y
  `(tenant_id, status, published_at_ms)` (cola de pendientes).
- `submitted` es **terminal**: un PATCH que intente cambiarlo da 409.
- Ventana: `CAMPAIGN_SUBMIT_WINDOW_MS = 30 min` (`db.ts:2632`).
  `expireStaleSubmissions` marca `expired` todo `pending` con más de
  30 min: **nada se envía tarde**.

**Helpers en `db.ts`** (todos degradan a vacío/0 si la tabla 0041 aún
no está migrada, para no romper el Worker antes del despliegue):
`createCampaignSubmission`, `find/get/listCampaignSubmission`,
`updateCampaignSubmission`, `stampCampaignSubmissionPosted` (solo
`pending` + `published_at_ms IS NULL`),
`backfillCampaignSubmissionsPosted`, `expireStaleSubmissions`,
`listPendingCampaignSubmissions` (backfill + expiración primero,
ordenadas por `published_at_ms ASC`: lo más urgente primero).

## 4. Las rutas (Worker, con sesión; nunca D1 directo desde el navegador)

En `panel-backend/worker/src/index.ts:3662-3780` (después de las rutas
de engagement, antes de Ajustes):

- `GET /api/campaign-submissions?clip_id=&platform=&campaign_id=&status=`
  — lectura. `?pending=1` devuelve la **cola en nube** (solo
  `pending` con `published_at_ms` real, ordenadas por antigüedad).
- `POST /api/campaign-submissions` — registra el envío **antes** de
  llamar al MCP (201, o 409 con la fila existente si ya estaba). Valida:
  el clip existe (404 si no), plataforma válida, `campaign_id`
  obligatorio, `published_at_ms` real y no futuro, `post_url` y
  `published_at_ms` juntos o ninguno.
- `PATCH /api/campaign-submissions/:id` — marca `submitted` +
  `submission_id` (o `failed`/`views`/`amount_usd`/`error`). `submitted`
  terminal → 409. Fila inexistente → 404.

## 5. La cola en nube (Chrome cerrado) — diseño

```
panel publica → ciclo → markPosted (posted + evidence_url + postedAtMs)
   → stampCampaignSubmissionPosted: la fila pending ya existe
     (registrada al asignar la campaña) y queda sellada con el
     post_url + published_at_ms REALES
   → la extensión (alarma cada 5 min, o al reabrir Chrome)
     GET /api/campaign-submissions?pending=1
   → por cada fila pending en ventana: puerta (worker corriendo,
     clip posted, sin duplicado) → POST MCP → PATCH submitted
   → pasados 30 min sin enviar: expired (nunca se envía tarde)
```

- La fila nace `pending` **al registrar la asignación** (antes de
  publicar) o al sellarla `markPosted`; vive en D1 y sobrevive a
  Chrome cerrado, al PC apagado y al reinicio de la extensión.
- **Lo que el Worker NO puede hacer:** enviar al marketplace. No tiene
  la API key de Content Rewards de ningún tenant (vive solo en el
  Chrome del usuario) ni llama al MCP. La extensión sigue siendo la
  única que envía; la D1 es la cola que sobrevive sin ella.
- La extensión registra la fila `pending` en D1 **antes** del MCP y la
  marca `submitted` después; si el PATCH falla, queda `syncWarning` y
  **no se reenvía por eso** (el envío ya se hizo; reenviarlo sería un
  duplicado).
- Si la ruta aún no está desplegada, la extensión usa el registro local
  y lo anota como `local_fallback` (nunca finge un chequeo de D1).

## 6. Reglas que el diseño hace cumplir

- **Un post = una campaña, una vez:** UNIQUE en D1 + `submitted`
  terminal + la extensión decide el duplicado solo por la fila en D1
  (la puerta corregida v1.3.0: `posted` es el requisito, no un duplicado).
- **30 minutos:** `published_at_ms` real de `markPosted` + expiración
  a los 30 min + la extensión bloquea fuera de ventana.
- **Tarifa neta + fama:** la prioridad de cola y el ranking exigen
  neta ≥ $1/1k **y** streamer famoso (≥ 50.000 seguidores, regla
  2026-11-23 18:50). Fuente única TS: `CAMPAIGN_STREAMERS` en `db.ts`
  (`isCampaignStreamer`, `isFamousCampaignStreamer`,
  `FAME_MIN_FOLLOWERS = 50_000`); la extensión la replica en `lib.js`.
  - Bobby Van Riper: $1,35 neto pero **734 seguidores** → fuera del
    ranking, fuera de la prioridad, fuera de la activación (su campaña
    sigue en el mapa como referencia, no como vía de envío).
  - BenDaDonnn: famoso (645.690) pero neta $0,90 → sin prioridad ni
    auto-asignación.
- **Material (pregunta del usuario 2026-11-23 18:49):** ninguna campaña
  entrega clips listos para subir. Todas dan **brief + stream/VOD
  permitido** y hay que **cortar tu propio edit**; un clip ya hecho y
  subido tal cual lo rechazan por **duplicado / no original**. El
  cerebro ya corta solo de los streams en vivo: los 4 streamers
  famosos de campaña van al **live scan** (`clipagent/live_scan.py`,
  twitch/kick; Dylan Page es youtube-only y solo entra por
  VOD-fallback), no se busca clip por clip a mano. Las fichas de brief
  viven en la extensión (`CAMPAIGN_BRIEFS`, `material: "brief_only"`).

## 7. Prioridad en cola (regla 2026-11-23 18:37)

Los clips de streamers con campaña (neta ≥ $1/1k **y** famosos)
ocupan primero **`campaign_priority_share_pct` %** de los slots de
cada cuenta en el plan diario (`daily-plan.ts:167-176`, dos pasadas:
reserva de campaña primero, resto después en orden normal de Cola).
- **% propuesto: 60%** (default en `DEFAULT_PANEL_SETTINGS`,
  `db.ts:602`; `effectiveSettings` lo limita a 0–100 y
  `validateSettingsInput` lo valida). Con capacidad 3/día, 2 slots van
  primero a campaña y 1 queda siempre para el resto: los demás nunca
  quedan a cero (no matar el crecimiento de las cuentas). La reserva es
  un **mínimo, no un techo**: los clips de campaña que sobran compiten
  en la pasada 2 en orden normal.
- Es **por tenant** (los settings viven en la D1 de cada tenant, en
  Ajustes, sin tocar código para cambiarlo) y respeta topes por
  plataforma (3/24h, YT 6, 2h entre posts) y virality ≥ 45 / SEO ≥ 80
  (el plan no toca esos filtros; la Cola sigue ordenándose igual).
- Implementación: `buildDailyPlan` puro (`daily-plan.ts`), `campaign`
  lo pone `buildDailyPlanForTenant` con `isCampaignStreamer(c.streamer)`.

## 8. Puerta de influencers (levantada 2026-11-23)

`isActivatableInfluencer` (`db.ts`): los 10 del lanzamiento + los **4
virales de campaña FAMOSOS** (Dasgasd3m, ArthurTV, Dylan Page,
BenDaDonnn). Bobby Van Riper queda fuera por fama (regla 18:50).
Cualquier otro handle se puede crear desactivado; activarlo sigue
siendo 400. Alta en D1 por tenant (sin tocar credenciales de kaez):
`panel-backend/scripts/seed_campaign_influencers.py` — dry-run por
defecto; `--apply` con `PANEL_SESSION_TOKEN` (sesión del panel de ESE
tenant) y `PANEL_BASE_URL` (o `frontend/dist/workers.json`).

## 9. Despliegue (para el coordinador — NO desplegado en esta tarea)

1. Aplicar la migración 0041 en las D1 de **seba y kaez** (y
   avc-sebastian si corresponde; la migración es la misma para los 3).
2. Desplegar los 3 Workers con `wr_v2.py` (kaez SIEMPRE con
   `wrangler.kaez.toml` SIN `[triggers]` para preservar sus crons;
   **nunca desplegar avc sin `TENANT=sebastian`** en el config).
3. Verificar `/api/health` en los 3 (tenant correcto, `worker_running`
   true) y que los crons de franja sigan intactos (GET schedules; si un
   deploy nocron los borró, reponerlos con PUT array JSON y verificar
   con GET).
4. Verificar la ruta: `GET /api/campaign-submissions?pending=1` con
   sesión → 200 con `{ ok, submissions: [] }`.
5. Alta de influencers por tenant:
   `python3 panel-backend/scripts/seed_campaign_influencers.py --tenant seba --apply`
   (con la sesión de seba) y lo mismo para kaez (con la sesión de kaez).
   Bobby Van Riper se salta solo, con el motivo impreso.
6. No unir a nadie a campañas, no enviar clips reales, no gastar nada.

## 10. Tests

- Bun (`panel-backend/worker/test/`): `campaign-submissions.test.ts`
  (13 tests: UNIQUE, duplicado 409, `markPosted` sella `post_url` +
  `published_at_ms` reales, backfill WAL, expiración 30 min, orden de
  la cola de pendientes, rutas incl. 401 sin sesión, rechazo de futuro
  / URL huérfana, PATCH terminal), `campaign-priority.test.ts` (reserva
  60%, pct 0/100, reserva-mínimo, BenDaDonnn y Bobby excluidos, topes),
  `influencers.test.ts` (4 famosos → 201, Bobby activo → 400 con motivo
  de fama, Bobby desactivado → 201, desconocido → 400).
- Python: `test_campaign_submissions.py` (migración SQL en sqlite3),
  `test_streamers.py` (13 EN, 4 famosos + Bobby excluido/desactivado,
  material brief_only en notas), `test_live_scan.py` (checked 24:
  Bobby no se vigila; los 4 famosos sí entran al barrido).
- Extensión: `extension/validate.sh` (puerta corregida, ranking
  neto+fama, brief, MCP, asignación por plataforma, reintentos).
