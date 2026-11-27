# SPEC — Piso mínimo 3 publicaciones/día por plataforma (v2)

Fecha: 2026-11-26. Orden del usuario 20:23: **"Mínimo 3 por día!"**.
v2 = v1 + correcciones de la revisión Goldfish (2026-11-26 20:37,
veredicto "lista con correcciones"). v1 queda como borrador revisado.

Interpretación que se somete a aprobación (no corregida por él tras
comunicársela 20:23): **mínimo 3 publicaciones al día por plataforma
(YouTube, TikTok, Instagram, Facebook), en cada cuenta (seba y kaez),
por canal plataforma+idioma (ES y EN por separado)**. Si el usuario
quiso decir otra cosa (por cuenta, por clip), esta spec es inválida
y se reescribe antes de implementar.

Contexto (Goldfish H9): la orden "mínimo 3 POR PLATAFORMA al día"
ya existe en el código (db.ts:596-607, 751-760, 794-799) y los
valores plan 3/3 ya la codifican. Lo NUEVO aquí no son los valores
del plan, sino tres cosas: (1) un piso exigible que el sistema
persigue solo, (2) el pase del piso en el auto-enqueue (§2) y (3)
la corrección de la fila meta (§4), sin la cual el piso es
inalcanzable en IG/FB.

## §0. Evidencia verificada en vivo (2026-11-26 20:23–20:35)

- Día: seba 10 (YT 4, TT 2, IG 2, FB 2), kaez 9 (YT 3, TT 2, IG 2,
  FB 2). Total 19/38 (confirmado en D1 por plataforma, ambos
  tenants; la revisión Goldfish lo re-confirmó 20:35).
- Lo que falta para el piso hoy: seba TT 3º a las 22:37 (job 168),
  seba FB 3º a las 23:01 (job 162, rolling 3/3 hasta esa hora),
  kaez TT 3º a las 22:31 (lote 42–65), kaez IG 3º a las 22:14
  (job 70). Matiz (Goldfish): el 168 depende del job 160 (mismo
  clip 249 en YT) por la regla de 15 min entre plataformas — si
  160 publica después de las ~22:22, el 168 rebota a ~22:45+.
  "Llega solo" es probable, no garantizado.
- Lo que NO puede llegar hoy: **seba IG y kaez FB, tope 2/2 por
  calentamiento**. Mensaje del gate (gate.ts:288-289):
  `calentamiento: 2/2 hoy en instagram_reels (cuenta nueva: ni a
  mano se supera el tope de su etapa)`. El gate lo impide hasta a
  mano.
- **Causa raíz del 2/día (verificada en código y D1)**: la fila de
  `api_accounts` de Meta tiene `platform='meta'` (seba id 1, ES,
  `maturity='nueva'`, 21 pubs, primera 2026-11-18; kaez id 4, 17
  pubs, primera 2026-11-22; no existe ninguna fila
  `instagram`/`facebook` separada) y `maturityOut` (db.ts:1442)
  calcula `plan = platformPlan(eff, row.platform)` (db.ts:1452) →
  `platformPlan` (db.ts:1039-1047) no conoce `'meta'` → default →
  `resto_plan`, que en D1 en vivo vale **2** (seba y kaez,
  settings id 1; `resto_max: 3`). `warmupPlan` semana 1/2 =
  `min(new_account_daily=3, plan=2, max=3)` = **2** (seba Meta:
  8 días → semana2; kaez Meta: 4 días → semana1).
  El gate toma ese 2 como `warmupDailyLimit` (gate.ts:741) y el
  plan diario lo toma como `target` (daily-plan.ts:154-159 y
  291-296: `buildDailyPlanForTenant` calcula `maturityOut` **una
  vez por fila** y reutiliza ese `plan: 2` para los dos canales IG
  y FB, aunque ya distingue `apiPlatform: "instagram"/
  "facebook"` — el bug está también ahí, no solo en el gate).
  Tras graduarse el plan seguiría en 2 (db.ts:1459:
  `effective_daily_plan = stored === "establecida" ? plan :
  warmupPlan`, con `plan = resto_plan = 2`).
- Ajustes en vivo (settings id 1, seba y kaez coinciden):
  `new_account_daily: 3`, `new_account_daily_youtube: 6`,
  `youtube_plan/max: 6`, `tiktok_plan/max: 3`,
  `instagram_plan/max: 3`, `facebook_plan/max: 3`,
  `rolling_24h_max: 3`, `rolling_24h_max_youtube: 6`,
  `min_virality: 45`, `min_seo: 80`.
- El plan diario es solo una VISTA (`daily-plan.ts`: "el plan es
  una vista, no el programador — el auto-enqueue es quien pone el
  next_run_at real"). Su `capacity = max(0, target -
  publishedToday)` recortada por rolling. Hoy el plan ya asigna
  hasta `capacity` en orden de Cola (campaña primero hasta la
  reserva, luego resto); si no hay clips, avisa "sin clips en Cola
  para su idioma/plataforma hoy" (`assigned === 0`,
  daily-plan.ts:263-265, por cualquier motivo: 15 min, cursor
  fuera del día, sin clips).
- El auto-enqueue (`autoEnqueueTopClips`, jobs.ts:1192-1288) es
  por clip, no por piso: recorre la Cola (topes por ciclo:
  `AUTO_ENQUEUE_MAX_CLIPS_PER_CYCLE = 5` (jobs.ts:1037),
  `AUTO_ENQUEUE_MAX_EVALUATED_PER_CYCLE = 10` (jobs.ts:1050),
  `AUTO_ENQUEUE_MAX_SCANNED_PER_CYCLE = 60` (jobs.ts:1043)) y
  encola las plataformas que "necesitan trabajo"
  (`platformsNeedingWork`, jobs.ts:1299: sin job vigente
  scheduled/processing/posted). No existe hoy ningún objetivo de
  cantidad por plataforma/día.
- Nota: los comentarios de db.ts:1366 y gate.ts:319 dicen "1/día
  en semana 1/2" cuando el código hace `min(3, plan, max)` =
  2–3–6. Son comentarios obsoletos; este cambio los corrige para
  no confundir al implementador.

## §1. El piso (definición)

- Constante de código `DAILY_FLOOR_PER_PLATFORM = 3` en `db.ts`,
  junto a los topes (`rolling_24h_max` y familia). **No es un
  ajuste del panel**: el usuario ordenó no más configuraciones;
  el número vive en el código.
- Piso efectivo por canal (cuenta plataforma+idioma):
  `effectiveFloor = min(3, effective_daily_plan)`. Para cuentas
  establecidas (plan 3, YT 6) el piso es 3. Para cuentas en
  calentamiento con plan 1–2, el piso es su plan: el piso **nunca**
  supera el tope de la etapa. Si los ajustes bajaran el plan a 0,
  el piso es 0: el canal se salta sin alertar.
- El piso se mide por **publicados del día** (`posted_day`, Chile)
  por canal. Un job `scheduled`/`processing` para hoy cuenta como
  "en camino" a efectos del pase (§2), pero el piso solo se da por
  cumplido con `posted`.
- El piso rige igual para seba y kaez y para ES y EN. En EN, con
  poco stock, el piso normalmente no se alcanzará: eso se reporta
  (§3), no se fuerza.

## §2. Dónde se cumple: pase del piso en el auto-enqueue

- Después del bucle principal y del pase de sellados en
  `autoEnqueueTopClips` (jobs.ts:1192), y **solo si queda
  presupuesto de ciclo en los tres contadores**
  (`considered < 5 && evaluated < 10 && scanned < 60`), un pase
  del piso: para cada canal con `postedToday + scheduledToday <
  effectiveFloor`, recorre la Cola en orden y encola jobs para
  ese canal hasta llegar al piso o agotar la Cola. El pase
  incrementa los mismos tres contadores al recorrer (comparte
  presupuesto, no lo amplía).
- `scheduledToday` = jobs con `state='scheduled'` **o**
  `'processing'` cuyo `next_run_at` cae dentro de hoy (clave de
  día Chile). Un job con `next_run_at` mañana NO cuenta para el
  piso de hoy. Si el `next_run_at` calculado para el siguiente job
  del canal cae fuera de hoy, el pase no lo crea (sin derrame: el
  ciclo de mañana lo cubre).
- El pase encola **para un solo canal**: nueva clausura
  `attemptClipForChannel(clip, canal)` que llama a
  `tryEnqueueClipPublish` con `platforms=[canal]` (ese override
  existe: "si se pasan, son las únicas que se encolan",
  jobs.ts:705-709), replicando la siembra A4 y la contabilidad de
  presupuesto de `attemptClip` (jobs.ts:1215). NO se usa
  `attemptClip` tal cual: ese encola todas las plataformas que
  necesitan trabajo del clip, no un canal.
- Enumeración y orden de canales: las filas `api_accounts` →
  plataformas de panel, igual que `buildDailyPlanForTenant`
  (hoy 10 canales vivos: seba 6 + kaez 4). Se saltan los canales
  sin cuenta, sin credencial verificada, en pausa por spam o en
  día libre (el plan ya los alerta; el pase no escribe evento por
  ellos). Con presupuesto corto, se atienden por mayor déficit
  primero (`floor − (posted+scheduled)`); empate: YT → TT → IG →
  FB, ES antes que EN.
- El pase del piso **no relaja ninguna puerta**: calidad 45/80,
  piso de 90 min, rolling 24 h (3, YT 6), 15 min entre plataformas
  del mismo clip, un job vigente por (clip, plataforma), tope
  diario de la cuenta, pausa por spam, día libre. Si una puerta
  frena, el job no se crea y el canal queda corto (§3).
- El pase no crea jobs fuera del horario del ciclo ni cambia
  `next_run_at`: usa el cálculo ya vigente (última + azar 90–180
  min, SPEC quitar-franjas v2). Para YT el pase para en 3 (el
  piso) aunque el bucle principal pueda llegar a 6.
- Si el pase no puede cubrir un canal, escribe un evento
  `auto_publish` con formato fijo:
  `Piso {F}/día no cubierto en {canal}: {posted}/{F} publicados
  hoy, motivo: {motivo}`.
  `{motivo}` = el más frecuente entre los `blocked` del canal en
  este ciclo (desempate por primer visto), de la lista: sin clips
  / rolling / calentamiento / calidad / pausa / día libre / sin
  cuenta. Sin reintentos fuera del ciclo.

## §3. Qué se ve: el plan diario reporta el déficit

- `buildDailyPlanForTenant` calcula el piso por cuenta como
  `floor = min(3, mat.effective_daily_plan)` y lo pasa en
  `DailyPlanAccountInput` (campo nuevo `floor`); `buildDailyPlan`
  lo usa para la alerta.
- Por cada cuenta con `publishedToday + asignadas < floor`:
  alerta exacta `Cuenta <label>: piso {F}/día no cubierto
  ({X}/{F} planificadas hoy): <motivo>`, con `X =
  publishedToday + asignadas` y `{F}` el piso efectivo (una
  cuenta en calentamiento con plan 2 dice `(X/2…)` y "piso
  2/día", no 3). Motivo = "sin clips en Cola para su
  idioma/plataforma" | "tope de etapa en calentamiento (máx
  Y/día)" | "tope rolling 24 h" (el que corresponda según lo
  calculado, no un texto genérico).
- La alerta genérica actual ("sin clips en Cola para su
  idioma/plataforma hoy") se mantiene solo cuando el canal no
  tiene ningún clip en Cola que case idioma+plataforma
  (pre-chequeo nuevo); con clips insuficientes para el piso, la
  alerta es la del déficit.
- Divergencia reconocida: el plan asigna un clip COMO MUCHO a una
  cuenta al día (regla 7, `plannedIds`, daily-plan.ts:133/206),
  mientras el auto-enqueue permite el mismo clip en varias
  plataformas el mismo día con 15 min. El plan puede reportar un
  déficit que el pase del piso no tendría: la alerta del plan usa
  la asignación del plan (conservadora) y el evento del pase usa
  los enqueues reales. No se unifica en este cambio.
- El plan sigue sin publicar nada: solo calcula y avisa.

## §4. Corrección previa obligatoria: la fila meta resuelve su plan

- Sin esto el piso es inalcanzable en IG/FB (§0). Decisión
  (Goldfish H1): parámetro opcional `platformOverride` en
  `maturityOut` (afecta solo a `plan`/`max`; `newDaily` no cambia
  para meta→instagram/facebook porque ambas usan
  `new_account_daily`). La alternativa (recalcular en cada
  llamador) duplicaría el `min()` en 7 sitios.
- Llamadores (todos, Goldfish E3):
  - `gate.ts:687` y `gate.ts:827`: pasan el canal de panel que
    evalúan (`"instagram"`/`"facebook"` para los jobs de
    instagram_reels/facebook_reels; `"youtube"`/`"tiktok"` igual
    que hoy).
  - `jobs.ts:806` (`maturityByPlatform`): por canal de panel.
  - `daily-plan.ts` (`buildDailyPlanForTenant`, ~291-296): por
    canal (ya distingue `apiPlatform`; ahora también el plan).
  - `index.ts:3300` (endpoint de canales/cola): usa el override
    por canal; su fallback (`platformMax(eff, p)` /
    `platformPlan(eff, p)` con `p = "instagram"`/`"facebook"`) ya
    es correcto y queda como está.
  - `index.ts:3633` (GET /api/accounts) y `index.ts:3719` (PATCH
    /api/accounts): devuelven la madurez **por fila**, sin canal,
    como hoy (la vista por canal vive en el endpoint 3300 y en
    el plan). Se documenta, no se cambia.
- No se toca `resto_plan` (sigue siendo el plan de plataformas
  resto) ni los valores 3/3 de IG/FB en D1.
- Alternativa rechazada: subir `resto_plan` de 2 a 3 en ajustes.
  No arregla la resolución errónea y subiría silenciosamente el
  plan de cualquier plataforma resto futura. (Se retira el
  sub-argumento de v1 sobre "lo tendría que tocar el usuario":
  podría hacerse por migración; el argumento válido es el otro.)
- Tras esta corrección, el plan de calentamiento de IG/FB pasa a
  `min(3, 3, 3) = 3` (con los ajustes en vivo actuales) y el piso
  deja de estar bloqueado por construcción. El gate seguirá
  impidiendo superar el tope de la etapa que corresponda.
- Se corrigen los comentarios obsoletos de db.ts:1366 y
  gate.ts:319 ("1/día en semana 1/2") para que digan lo que hace
  el código (`min(new_account_daily, plan, max)`).

## §5. Lo que NO cambia

- Topes diarios (YT 6, resto 3) y rolling 24 h (3, YT 6).
- Duración de las etapas de calentamiento ni `WARMUP_MIN_DAYS` /
  `WARMUP_MIN_PUBLISHED`.
- Piso de 90 min entre publicaciones ni azar 90–180 min (SPEC
  quitar-franjas v2, commit 26267d5).
- Umbrales de calidad 45/80 ni la puerta B4.
- La regla 1:30–3 h.
- Panel (sin ajustes nuevos), recopilación, watchdog kaez 401.
- El pase del piso no publica nada por sí solo fuera del ciclo
  ni toca jobs existentes.
- El total de 38/día no cambia. Aritmética: hoy hay 10 canales
  vivos (seba 6 + kaez 4) → el piso, cumplido en todos, sumaría
  30 ≤ 38, sin conflicto. Si aparecen cuentas EN de kaez y Meta
  EN de seba, serían 16 canales × 3 = 48 > 38 y el objetivo
  habría que revisarlo; eso queda fuera de esta spec.

## §6. Tests

- Base aplicable: suite TS del Worker (1042 tests, 1034 pass,
  8 flaky preexistentes — Facebook offset ×2, B1 logout,
  clip-language, channels, retry-a4 ×2, según la verificación del
  deploy de franjas). Se exige `tsc --noEmit` en verde y "suite
  TS sin fallos nuevos". (Los 5 fallos Python de CI — test_
  compilation, test_fanout x3, test_hook_formulas — son reales
  pero de otro árbol; no son la base de este cambio.)
- `test/settings-maturity.test.ts` (ya prueba
  `computeMaturity`/`maturityOut`): fila meta + canal
  instagram_reels → plan 3 (no 2); canal facebook_reels → plan
  3; fila youtube → 6; fila tiktok → 3; fila meta ya establecida
  + canal instagram → plan 3 (regresión de §0).
- `test/campaign-priority.test.ts` /
  `test/publish-rules-2026-11-19.test.ts` (donde hoy se prueba
  `buildDailyPlan`): cuenta establecida con plan 3 y stock ≥3 →
  el plan trae ≥3 publicaciones para esa cuenta; stock 1 → 1
  publicación + alerta de déficit con la cuenta y el motivo;
  cuenta en calentamiento con plan 2 → piso efectivo 2, sin
  alerta de déficit por la 3ª (sí alerta de tope de etapa).
- `test/auto-enqueue.test.ts`: el pase del piso encola los jobs
  que faltan hasta 3 cuando hay stock y puertas libres; con stock
  1 encola 1 + escribe el evento con motivo "sin clips"; no
  encola cuando el rolling frena (y deja evento); respeta los
  topes `MAX_*_PER_CYCLE` (comparte presupuesto, no lo amplía);
  salta canal en pausa / día libre / sin credencial (sin jobs +
  evento con ese motivo); YT con 2 publicados → encola
  exactamente 1 (no hasta 6); el `next_run_at` respeta el piso de
  90 min (última+azar, no inmediato).
- No se crea `test/daily-plan.test.ts` ni
  `test/floor-3-per-day.test.ts`: cada caso va en el archivo
  vigente de su área (arriba).

## §7. Verificación en vivo (tras aprobar e implementar)

- Desplegar ambos Workers con `wr_v2.py` (kaez con
  `wrangler.kaez.toml` sin [triggers]); verificar `/api/health`
  y schedules `["*/5 * * * *"]` en ambos.
- Evidencia D1 al día siguiente: por canal, `posted` del día ≥
  piso efectivo, o alerta de déficit con el motivo real en el
  plan (`GET /api/daily-plan`) y evento `auto_publish` del pase
  del piso. El ciclo nocturno 03:00 sigue pendiente de verificar
  hasta las 03:00 (heredado de SPEC quitar-franjas v2).
