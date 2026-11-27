# SPEC — Piso mínimo 3 publicaciones/día por plataforma (borrador v1)

Fecha: 2026-11-26. Orden del usuario 20:23: **"Mínimo 3 por día!"**.
Interpretación que se somete a aprobación (no corregida por él tras
comunicársela 20:23): **mínimo 3 publicaciones al día por plataforma
(YouTube, TikTok, Instagram, Facebook), en cada cuenta (seba y kaez),
por canal plataforma+idioma (ES y EN por separado)**. Si el usuario
quiso decir otra cosa (por cuenta, por clip), esta spec es inválida
y se reescribe antes de implementar.

## §0. Evidencia verificada en vivo (2026-11-26 20:23)

- Día: seba 10 (YT 4, TT 2, IG 2, FB 2), kaez 9 (YT 3, TT 2, IG 2,
  FB 2). Total 19/38.
- Lo que falta para el piso hoy: seba TT 3º a las 22:37 (job 168),
  seba FB 3º a las 23:01 (job 162, rolling 3/3 hasta esa hora),
  kaez TT 3º a las 22:31 (lote 42–65), kaez IG 3º a las 22:14
  (job 70). Esos cuatro llegan solos con lo programado.
- Lo que NO puede llegar hoy: **seba IG y kaez FB, tope 2/2 por
  calentamiento**. Mensaje del gate (gate.ts:289): `calentamiento:
  2/2 hoy en instagram_reels (cuenta nueva: ni a mano se supera el
  tope de su etapa)`. El gate lo impide hasta a mano.
- **Causa raíz del 2/día (no es solo la etapa)**: la fila de
  `api_accounts` de Meta tiene `platform='meta'` y `maturityOut`
  (db.ts:1450-1459) calcula `plan = platformPlan(eff, row.platform)`
  → `platformPlan` (db.ts:1039) no conoce `'meta'` → default →
  `resto_plan`, que en D1 en vivo vale **2**
  (`instagram_plan: 3`, `facebook_plan: 3` existen pero nunca se
  consultan para la fila meta). `warmupPlan` semana 1/2 =
  `min(new_account_daily=3, plan=2, max=resto_max=3)` = **2**.
  El gate toma ese 2 como `warmupDailyLimit` (gate.ts:741) y el
  plan diario lo toma como `target` (daily-plan.ts: `target =
  ... ? acc.max : acc.plan`, con `plan = mat.effective_daily_plan`).
  Sin corregir esto, el piso de 3 es inalcanzable en IG/FB por
  construcción, también después de graduarse (plan seguiría en 2).
- Ajustes en vivo relevantes: `new_account_daily: 3`,
  `new_account_daily_youtube: 6`, `youtube_plan/max: 6`,
  `tiktok/instagram/facebook_plan/max: 3` (settings D1 seba, id 1).
  `rolling_24h_max: 3`, `rolling_24h_max_youtube: 6`.
- El plan diario es solo una VISTA (`daily-plan.ts`: "el plan es
  una vista, no el programador — el auto-enqueue es quien pone el
  next_run_at real"). Su `capacity = max(0, target -
  publishedToday)` recortada por rolling. Hoy el plan ya asigna
  hasta `capacity` en orden de Cola (campaña primero hasta la
  reserva, luego resto); si no hay clips, avisa "sin clips en Cola
  para su idioma/plataforma hoy".
- El auto-enqueue (`autoEnqueueTopClips`, jobs.ts:1192) es por
  clip, no por piso: recorre la Cola (topes por ciclo:
  `AUTO_ENQUEUE_MAX_CLIPS_PER_CYCLE = 5`,
  `AUTO_ENQUEUE_MAX_EVALUATED_PER_CYCLE = 10`,
  `AUTO_ENQUEUE_MAX_SCANNED_PER_CYCLE = 60`) y encola las
  plataformas que "necesitan trabajo" (`platformsNeedingWork`,
  jobs.ts: sin job vigente scheduled/processing/posted). No existe
  hoy ningún objetivo de cantidad por plataforma/día.

## §1. El piso (definición)

- Constante de código `DAILY_FLOOR_PER_PLATFORM = 3` (en `db.ts`,
  junto a los topes). **No es un ajuste del panel**: el usuario
  ordenó no más configuraciones; el número vive en el código.
- Piso efectivo por canal (cuenta plataforma+idioma):
  `effectiveFloor = min(3, effective_daily_plan)`. Para cuentas
  establecidas (plan 3, YT 6) el piso es 3. Para cuentas en
  calentamiento con plan 1–2, el piso es su plan: el piso **nunca**
  supera el tope de la etapa.
- El piso se mide por **publicados del día** (`posted_day`, Chile)
  por canal. Un job `scheduled`/`processing` para hoy cuenta como
  "en camino" a efectos del pase del piso (§2), pero el piso solo
  se da por cumplido con `posted`.
- El piso rige igual para seba y kaez y para ES y EN. En EN, con
  poco stock, el piso normalmente no se alcanzará: eso se reporta
  (§3), no se fuerza.

## §2. Dónde se cumple: pase del piso en el auto-enqueue

- Después del bucle principal y del pase de sellados en
  `autoEnqueueTopClips` (jobs.ts:1192), y **solo si queda
  presupuesto de ciclo** (`considered < MAX_CLIPS_PER_CYCLE` y
  `evaluated < MAX_EVALUATED_PER_CYCLE`), un pase del piso:
  para cada canal con `postedToday + scheduledToday <
  effectiveFloor`, recorre la Cola en orden y encola jobs para
  ese canal (vía el mismo `attemptClip`, con el mismo freno A4 y
  las mismas puertas) hasta llegar al piso o agotar la Cola.
- El pase del piso **no relaja ninguna puerta**: calidad 45/80,
  piso de 90 min, rolling 24 h (3, YT 6), 15 min entre plataformas
  del mismo clip, un job vigente por (clip, plataforma), tope
  diario de la cuenta, pausa por spam, día libre. Si una puerta
  frena, el job no se crea y el canal queda corto (§3).
- El pase del piso no crea jobs fuera del horario del ciclo ni
  cambia `next_run_at`: usa el cálculo ya vigente (última +
  azar 90–180 min, SPEC quitar-franjas v2).
- Si el pase no puede cubrir un canal (sin clips, o todas las
  puertas frenan), escribe un evento `auto_publish` con el canal,
  el piso, lo publicado hoy y el motivo dominante (sin clips /
  rolling / calentamiento / calidad), para que la revisión diaria
  lo vea. No reintenta fuera del ciclo.

## §3. Qué se ve: el plan diario reporta el déficit

- En `buildDailyPlan` (daily-plan.ts), por cada cuenta con
  `effectiveFloor > publicaciones planificadas para hoy`:
  alerta exacta `Cuenta <label>: piso 3/día no cubierto
  (X/3 planificadas hoy): <motivo>`, con motivo = "sin clips en
  Cola para su idioma/plataforma" | "tope de etapa en
  calentamiento (máx Y/día)" | "tope rolling 24 h" (el que
  corresponda según lo calculado, no un texto genérico).
- La alerta genérica actual "sin clips en Cola para su
  idioma/plataforma hoy" se mantiene solo cuando el canal no tiene
  ningún clip en Cola; con clips insuficientes para el piso, la
  alerta es la del déficit.
- El plan sigue sin publicar nada: solo calcula y avisa.

## §4. Corrección previa obligatoria: la fila meta resuelve su plan

- Sin esto el piso es inalcanzable en IG/FB (§0). `maturityOut`
  debe resolver plan/máx **por canal de panel** (instagram_reels
  → `instagram_plan`/`instagram_max`, facebook_reels →
  `facebook_plan`/`facebook_max`), no por `row.platform='meta'`
  (que cae en `resto_plan=2`).
- Forma: `maturityOut` acepta la plataforma de panel (o se
  calcula la madurez por canal en los llamadores) y `gate.ts:687,
  827`, `jobs.ts:806` y `daily-plan.ts` pasan el canal que están
  evaluando. No se toca `resto_plan` (sigue siendo el plan de
  plataformas resto) ni los valores 3/3 de IG/FB en D1.
- Alternativa rechazada: subir `resto_plan` de 2 a 3 en ajustes.
  Cambiaría silenciosamente el plan de cualquier plataforma resto
  futura y no arregla la resolución errónea; además es un ajuste
  que el usuario tendría que tocar.
- Tras esta corrección, el plan de calentamiento de IG/FB pasa a
  `min(3, 3, 3) = 3` (con los ajustes en vivo actuales) y el piso
  deja de estar bloqueado por construcción. El gate seguirá
  impidiendo superar el tope de la etapa que corresponda.

## §5. Lo que NO cambia

- Topes diarios (YT 6, resto 3) y rolling 24 h (3, YT 6).
- Duración de las etapas de calentamiento ni `WARMUP_MIN_DAYS` /
  `WARMUP_MIN_PUBLISHED`.
- Piso de 90 min entre publicaciones ni azar 90–180 min (SPEC
  quitar-franjas v2, commit 26267d5).
- Umbrales de calidad 45/80 ni la puerta B4.
- El total de 38/día ni la regla 1:30–3 h.
- Panel (sin ajustes nuevos), recopilación, watchdog kaez 401.
- El pase del piso no publica nada por sí solo fuera del ciclo
  ni toca jobs existentes.

## §6. Tests

- `test/daily-plan.test.ts` (o el archivo vigente del plan):
  cuenta establecida con plan 3 y stock ≥3 → el plan trae ≥3
  publicaciones para esa cuenta; stock 1 → 1 publicación +
  alerta de déficit con la cuenta y el motivo; cuenta en
  calentamiento con plan 2 → piso efectivo 2, sin alerta de
  déficit por la 3ª (sí alerta de tope de etapa).
- `test/floor-3-per-day.test.ts` (nuevo): `maturityOut` con fila
  meta + canal instagram_reels → plan 3 (no 2); canal
  facebook_reels → plan 3; fila youtube → 6; fila tiktok → 3.
- `test/auto-enqueue.test.ts`: el pase del piso encola los jobs
  que faltan hasta 3 cuando hay stock y puertas libres; no encola
  cuando el rolling frena (y deja evento); respeta los topes
  `MAX_*_PER_CYCLE` (el pase comparte presupuesto, no lo amplía).
- `tsc --noEmit` en verde; suite completa sin fallos nuevos
  (la base 2026-11-26 tiene 5 fallos preexistentes en Python/CI,
  ajenos a este cambio: test_compilation, test_fanout x3,
  test_hook_formulas — no se tocan).

## §7. Verificación en vivo (tras aprobar e implementar)

- Desplegar ambos Workers con `wr_v2.py` (kaez con
  `wrangler.kaez.toml` sin [triggers]); verificar `/api/health`
  y schedules `["*/5 * * * *"]` en ambos.
- Evidencia D1 al día siguiente: por canal, `posted` del día ≥
  piso efectivo, o alerta de déficit con el motivo real en el
  plan (`GET /api/daily-plan`) y evento `auto_publish` del pase
  del piso. El ciclo nocturno 03:00 sigue pendiente de verificar
  hasta las 03:00 (heredado de SPEC quitar-franjas v2).
