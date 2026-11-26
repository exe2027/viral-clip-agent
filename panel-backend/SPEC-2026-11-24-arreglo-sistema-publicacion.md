# SPEC — Arreglo del sistema de publicación para llegar a 39/día

- **Fecha:** 2026-11-24 (v2: 2026-11-25, con la revisión goldfish aplicada — ver nota al final)
- **Estado:** borrador para aprobación del usuario (no implementado)
- **Alcance:** Worker (`panel-backend/worker/src/`) + cerebro (`clipagent/revalidate_pending.py`). Solo el camino de publicación (encolar → ejecutar → reintentar). No toca el cortador, ni el validador, ni el panel, ni las cuentas.
- **Meta:** 39 vídeos/día todos los días (24 seba + 15 kaez), y 48/día cuando kaez tenga inglés. El sistema lo hace solo; nada a mano.

> **Nota v2 (2026-11-25).** La v1 pasó por revisión goldfish (subagente con solo esta spec + los archivos citados). El goldfish devolvió 20 hallazgos (5 bloqueadores, 12 importantes, 3 menores). Esta v2 los resuelve todos, con verificación en el código y en D1 donde el hallazgo era factual. Cambios principales frente a v1: nuevo **F0** (el hueco YT 00:00 es inalcanzable y el techo automático real es 36/día, no 39); **F2/F3 rediseñados** (el gate sigue devolviendo `string | null`; la clasificación transitorio/duro vive en una tabla única `GATE_BLOCKS` en `gate.ts`, no en un cambio de firma); **F1 incluye el chequeo posted-ever** (el duplicado del 102 se explica por fin: el gate solo mira posted_day=hoy); nuevo **F1b** (clips sellados como `publicado` con plataformas que les faltan: seba 15 y 173, kaez 4); **F2b** (el 15 min también ve hermanos en `processing`); **F4 ya no sube evaluados a 30** (la cuenta de lecturas/escrituras estaba mal por ~5×); §5 corregido (los dobles citados no existían donde se decía); §1 con la aritmética corregida (13 clips distintos mínimo, no 18). Dos premisas del goldfish resultaron **falsas** al verificarlas y no se aplican: `auto_publish_top` está en 1 en ambas D1 (verificado 2026-11-25), y el rolling de YT es 6 (db.ts:628), no 5.

## §0. Precondiciones verificadas en vivo (2026-11-25)

Antes de implementar, estas ya están verificadas; el plan de despliegue (§6) las vuelve a comprobar.

| Dato | Valor verificado |
|---|---|
| `auto_publish_top` seba (D1 `clips-avc`, settings id 1) | **1** |
| `auto_publish_top` kaez (D1 `clips-avc-kaez`, settings id 1) | **1** |
| Crons vivos en ambos Workers | `*/5 15-18` y `*/5 21-23,0-3` UTC (GET schedules) |
| `rolling_24h_max_youtube` (default, db.ts:628) | **6** — el rolling no frena el 6.º YT del día |
| Comentario obsoleto en gate.ts:187 ("techo YT 5 / resto 3") | No es funcional; se corrige el comentario al tocar el archivo, sin cambio de conducta |

## §1. Problema

Hoy 2026-11-24, verificado en D1 por el agente (no estimado):

| Dato | Valor |
|---|---|
| Publicados hoy (21:09 Chile) | **5 de 39** (seba 2, kaez 3) |
| Ayer 2026-11-23 (cierre) | 18 de 39 |
| Drafts atascados hoy (21:00) | **10** (seba 1, kaez 9), todos por motivos transitorios |
| Clip validado sin jobs en todo el día | seba **185** (score 55, aprobado, con MP4) |
| Clips con SEO tras el loop de hoy | seba 61, kaez 31 |
| Duplicado del 102 | TikTok: vídeo nuevo 7711771156801424661 (el original 7710454410865937685 sigue vivo); YT: el job 132 apunta al mismo Shorts que el 102 (u7AdVWyiNR4) — ahí no hay segundo vídeo |
| Clips sellados sin completar | seba 15 (solo IG) y 173 (solo YT); kaez 4 (sin TT) — `status='publicado'` con plataformas vinculadas que les faltan |

Los 5 publicados hoy: seba 102 YT 12:01 + TT 13:15; kaez 10 IG 12:06, 9 YT 13:11, 10 FB 13:11. Los 10 drafts: seba job 131 (176 TT, espaciamiento <2 h); kaez jobs 10 (10 YT, mismo clip 4 min), 11 (10 TT, mismo clip 5 min), 15 (9 TT, espaciamiento <3 h), 16 (9 IG, espaciamiento <3 h), 17 (9 FB, espaciamiento <3 h), 18 (5 YT, espaciamiento <2 h), 19 (5 TT, espaciamiento <3 h), 20 (5 IG, 1 por franja), 21 (5 FB, 1 por franja). Todos los motivos son transitorios: horas después ya no aplicaban, pero los jobs siguieron en draft y el ciclo nunca los retomó.

**Techo automático real hoy: 36/día, no 39.** La cuenta 39/día exige YT 6/día, pero el hueco YT 00:00 es inalcanzable por el auto-enqueue (D0): el techo automático es seba ES 14 (YT 5 + TT 3 + IG 3 + FB 3) + seba EN 8 (YT 5 + TT 3) + kaez ES 14 = **36**. Con F0 el techo vuelve a 39 (y a 48 con el inglés de kaez). Sin F0, el 39/día exigiría una publicación manual diaria a las 00:00, contra la regla "el sistema lo hace solo".

**Clips distintos necesarios:** 39/día = seba ES 15 + seba EN 9 + kaez ES 15. Cada clip rinde como mucho 4 posts en ES (YT/TT/IG/FB) y 2 en EN (YT/TT). Mínimo teórico: **13 clips distintos validados al día** (4 ES + 5 EN seba, 4 ES kaez) si cada clip usa todas sus plataformas; en la práctica más, porque no todos los clips salen por las 4. (La v1 decía 18; esa cuenta no cuadraba con los topes por plataforma.)

El sistema de publicación, no el cortador, es lo que hoy impide llegar: los clips validados existen pero no salen.

## §2. Diagnóstico verificado en el código

Las 7 causas, cada una leída en el código (no inferida). Todas son del camino encolar → ejecutar → reintentar.

### D0. El hueco YT 00:00 es inalcanzable por el auto-enqueue (nuevo; lo encontró el goldfish)

- **Dónde:** `panel-backend/worker/src/db.ts:1084-1095` (`PUBLISH_SLOT_WINDOWS`: mañana 12:00-14:00, tarde 18:00-20:00, noche 21:00-23:00 — no hay ventana 00:00), `db.ts:1230` (`PLATFORM_SCHEDULE`: YT incluye 00:00), `db.ts:1249-1262` (`nextPlatformHourMs`), `panel-backend/worker/src/gate.ts:175` (`publishGate`, `checkSlot`).
- **Código:** `nextPlatformHourMs` solo devuelve mañana 00:00 cuando ya pasaron las 23:00 (último hueco del día agotado). Pero a esa hora `isWithinPublishSlot` es falso (la noche termina a las 23:00) y el gate del auto-enqueue (`checkSlot: !opts.forceNow`, con `refMs = now`) frena con "fuera de franja". Ningún ciclo del auto-enqueue asigna jamás `next_run_at = 00:00`. El comentario de `PLATFORM_SCHEDULE` ("para que nextPlatformHourMs resuelva bien el hueco 00:00") describe una intención que el chequeo de franja impide.
- **Efecto:** YT automático = 5/día (12:00, 14:00, 18:00, 20:00, 22:00), no 6. Los huecos 14:00 y 20:00 sí funcionan: el job se encola dentro de franja (13:xx / 19:xx) con `next_run_at` en el hueco, y el re-chequeo en ejecución **no** vuelve a comprobar la franja (gate.ts, comentario de la puerta de franja: "el re-chequeo en ejecución no la comprueba").
- **Agravante estacional:** el cron nocturno (`*/5 21-23,0-3` UTC) cubre 00:00-00:59 Chile solo en verano (UTC-3). En invierno (UTC-4) cubre 17:00-23:59 Chile: ni siquiera el cron corre a las 00:00. F0 arregla ambas cosas.
- **Prueba:** ningún job YT tiene `next_run_at` en 00:00; el hueco existe solo en `PLATFORM_SCHEDULE`, no en ninguna ventana de publicación.

### D1. `hasVigente` es por clip, no por plataforma — y el gate no recuerda lo publicado otros días (la causa raíz)

- **Dónde:** `panel-backend/worker/src/jobs.ts:1084-1092`, en `autoEnqueueTopClips` (jobs.ts:1060-1145); `panel-backend/worker/src/jobs.ts:677-960` (`tryEnqueueClipPublish`, contadores por plataforma); `panel-backend/worker/src/gate.ts:175-360` (`publishGate`).
- **Código (dos agujeros, no uno):**
  1. El auto-enqueue consulta los jobs publish del clip (`SELECT id, state, posted_day FROM jobs ...` — **sin la columna `platform`**) y salta el clip entero si alguno está `scheduled`, `processing`, o `posted` con `posted_day === today`. Un clip que subió en YouTube a las 12:00 no recibe sus jobs de TikTok/Instagram/Facebook ese día; los recibe mañana. El comentario del propio código dice la intención contraria: "(Un posted de otro día no bloquea: el clip puede salir por las plataformas que le falten, con el gate de cada una.)" — pero un posted de HOY sí bloquea todas las plataformas.
  2. El gate solo mira lo publicado **hoy** en esa plataforma (`clipToday`: `posted_day = refDayKey`, jobs.ts:~840; `clipPublishedTodayOnPlatform` en el re-chequeo, gate.ts:404-530). No hay ningún chequeo de "publicado alguna vez en esta plataforma". Un clip que publicó en TikTok hace 3 días puede recibir un job nuevo de TikTok hoy.
- **Efecto (1):** el clip 10 de kaez subió en IG y FB al mediodía y sus jobs de YT/TT quedaron en draft sin reintento (el auto-enqueue no lo volvió a mirar).
- **Efecto (2) — el duplicado del 102, por fin explicado:** los jobs 102/103 (YT/TT) publicaron el 21-nov (posted_day 2026-11-21). El 24-nov a las 12:00:42-44 Chile se crearon los jobs 132 (YT) y 133 (TT) — 2 segundos aparte, en la misma llamada — porque `hasVigente` era falso (nada posted hoy todavía) y `clipToday` era 0 para ambas plataformas. El job 133 publicó en TikTok a las 13:15 un vídeo NUEVO (7711771156801424661) mientras el original (7710454410865937685) sigue vivo. En YT el 132 apunta al mismo Shorts que el 102 (u7AdVWyiNR4): ahí no se duplicó el vídeo, pero el job se re-creó igual. Los jobs 132/133 **no** vinieron del auto-enqueue: no hay ningún evento `auto_publish` ni `clip_events` para el 102 ese día, y el auto-enqueue nunca pasa override de plataformas. Vinieron de una publicación con override YT+TT (la vía manual, panel/API). El agujero es el mismo en ambos caminos porque comparten `tryEnqueueClipPublish` y `publishGate`: el dedup mismo-plataforma es solo mismo-día.
- **Prueba:** jobs 102/103 (posted_day 2026-11-21) y 132/133 (creados 2026-11-24 12:00:42/44 Chile, posted_day 2026-11-24); `evidence_url` de 132 = la misma que la de 102; la de 133 es un vídeo distinto de la de 103.

### D1b. Clips sellados como `publicado` con plataformas que les faltan (nuevo; lo encontró el goldfish)

- **Dónde:** `panel-backend/worker/src/db.ts:4097-4168` (`mergePublishStates`), `db.ts:1675` (`listQueueClips`), `jobs.ts:725` (`tryEnqueueClipPublish`, retorno temprano `clip.status === "publicado"`).
- **Código:** el `status` pasa a `'publicado'` cuando **todas las plataformas de `clip.platforms`** están en estado terminal (publicado/fallido/expirado/cancelado) y al menos una publicó. `listQueueClips` solo trae `status IN ('en_carpeta','aprobado','programado')`, y `tryEnqueueClipPublish` devuelve "clip ya publicado" si `status === "publicado"`. Un clip con `platforms = ["youtube_shorts"]` queda sellado tras su único YT: el auto-enqueue no lo vuelve a ver nunca, aunque la unión con sus cuentas vinculadas (regla 14:10) le daría TT/IG/FB.
- **Efecto:** 3 clips sellados con huecos hoy (verificado en D1): seba 15 (solo IG; le faltan YT/TT/FB), seba 173 (solo YT; le faltan TT/IG/FB), kaez 4 (YT/IG/FB; le falta TT). No es mucho volumen, pero el mecanismo sellará cualquier clip futuro que nazca con una lista de plataformas estrecha (p. ej. un enqueue manual de solo YT).
- **Matiz (verificado):** el sello NO muerde a los clips parcialmente publicados: `markDraft` deja la plataforma en estado `"pendiente"` (jobs.ts:1358-1372, no terminal), así que un clip con YT publicado + TT en draft sigue en `'en_carpeta'` y el auto-enqueue lo ve. El sello solo muerde cuando la lista `platforms` es estrecha.

### D2. `tryEnqueueClipPublish` no crea job para la plataforma bloqueada

- **Dónde:** `panel-backend/worker/src/jobs.ts:677-960` (`tryEnqueueClipPublish`), en especial el bucle por plataforma (~867-960).
- **Código:** por cada plataforma destino se evalúa `publishGate`; si frena, la plataforma va a `blocked[]` con el motivo (`blocked.push(\`${platform}: ${gate}\`)`, jobs.ts:~910) y **no se crea job**. Solo se encola "ESTA plataforma (la que pasó el gate)".
- **Efecto:** una plataforma bloqueada por un motivo transitorio (espaciamiento, 15 min, 1-por-franja, fuera de franja) no deja ningún job pendiente. El clip queda "vigente" por los jobs que sí se crearon (D1), así que el auto-enqueue no lo vuelve a mirar ese día. La plataforma perdida se recupera en la siguiente evaluación dentro de franja — **no "mañana"** (la v1 exageraba): lo que la estira hasta mañana es D1 + D4, no D2 sola.
- **Prueba (mecanismo, verificado en código):** el `continue` tras el `blocked.push` no crea job ni programa reintento. Síntoma observable: clips que publicaron en una plataforma y no tienen ningún job (ni draft) en otra — p. ej. el 102 no tiene jobs de IG/FB (en su caso la publicación manual eligió solo YT+TT, así que IG/FB ni se evaluaron) y el 173 no tiene jobs de TT/IG/FB (sellado por D1b).

### D3. El job que falla en ejecución vuelve a draft terminal y el ciclo nunca toma drafts

- **Dónde:** `panel-backend/worker/src/publish/index.ts:202` (`dispatchPublishStep` → `recheckPublishGate`), `panel-backend/worker/src/jobs.ts:1673-1760` (`applyPublishResult`, caso `"draft"` → `markDraft`), `jobs.ts:1358-1372` (`markDraft`), `jobs.ts:1150` (`dueJobs`).
- **Código:** en ejecución, `dispatchPublishStep` vuelve a aplicar todo el gate (`recheckPublishGate`, gate.ts:404-530). Si frena (espaciamiento, 15 min, 1-por-franja, rolling, tope), el resultado es modo `"draft"` → `applyPublishResult` suelta el cupo (`releasePublishSlot`) y `markDraft` pone `state='draft', retryable=0` ("Draft terminal") y limpia `run_token`/`slot_day`. `dueJobs` solo toma `state IN ('scheduled','processing')`.
- **Efecto:** un freno transitorio de minutos (15 min entre plataformas, espaciamiento de 2-3 h) convierte el job en draft terminal hasta mañana. El ciclo no lo retoma nunca. Es lo que les pasó a los 10 drafts de hoy: todos frenaron en ejecución por motivos que horas después ya no aplicaban.
- **Prueba:** job 131 (176 TT): frenó 13:01 por espaciamiento; a las 21:00 el espaciamiento llevaba 8 h cumplido y el job seguía en draft con `attempts=0`. Los 10 drafts de hoy prueban **D3**, no D2: un draft solo existe si el job fue creado; D2 produce jobs ausentes, no drafts (corrección del goldfish a la v1).

### D4. El freno A4 aparca el clip tras 3 intentos fallidos, y los intentos fallidos los causan los frenos transitorios

- **Dónde:** `panel-backend/worker/src/jobs.ts:982-989` (topes), `jobs.ts:1060-1145` (bucle del auto-enqueue), `panel-backend/worker/src/retry.ts:319-334` (`autoEnqueueAllowed`), `retry.ts:46-62` (backoff 1/5/15 min, `MAX_RETRIES_PER_DAY = 3` en retry.ts:59), `panel-backend/worker/src/cycle-gate.ts:55,60` (`CYCLE_REVIEW_INTERVAL_MS = 12 min`, `CYCLE_DUE_WINDOW_MS = 60 s`).
- **Código:** un "intento fallido" es un ciclo en el que el clip encoló 0 jobs (`recordAutoEnqueueAttempt`, jobs.ts:~1130). Tras 3 intentos fallidos en el día, `autoEnqueueAllowed` devuelve `allowed=false` hasta mañana. El estado vive en **memoria del Worker** (con siembra por lectura de `clip_events`, paso `'publicado'`, actor `'worker'`). El ciclo completo corre como mucho cada 12 min por revisión periódica (o antes si un job vence en los próximos 60 s): el cron dispara cada 5 min, pero `cycleGate` es quien decide si el ciclo trabaja.
- **Efecto:** un clip cuyas plataformas están todas transitoriamente bloqueadas quema 1 intento A4 por ciclo evaluado. Con backoff 1 min tras el 1.er intento y 5 min tras el 2.º, 3 intentos fallidos tardan **~6 min (si hay jobs debidos que despiertan el ciclo) a ~24 min (solo por revisión cada 12 min)** en aparcar el clip hasta mañana (la v1 decía "~15 min", impreciso en ambos sentidos). Peor: fuera de franja (14:00-15:59, cuando el cron corre pero no hay ventana) **todas** las plataformas frenan con "fuera de franja" y cada evaluación quema un intento A4 — los clips se aparcan a diario sin haber tenido ocasión de publicar.
- **Prueba:** kaez clip 5: 2 intentos fallidos hoy (según el chequeo de las 14:30), le quedaba 1 por A4; sus 4 jobs quedaron en draft.

### D5. El auto-enqueue gasta su presupuesto en las cabezas bloqueadas y no llega a los validados

- **Dónde:** `panel-backend/worker/src/jobs.ts:982` (`AUTO_ENQUEUE_MAX_CLIPS_PER_CYCLE = 3`), `jobs.ts:989` (`AUTO_ENQUEUE_MAX_EVALUATED_PER_CYCLE = 10`), `jobs.ts:1078` (condición de corte), `panel-backend/worker/src/db.ts:1675` (`listQueueClips`: LIVE primero, luego `manual_rank`, luego `score`, luego fecha; con boost de `clip_stats`).
- **Código:** el bucle hace `evaluated++` **antes** de los saltos por `hasVigente` y por A4, y corta cuando `considered >= 3` o `evaluated >= 10`. Cada clip visitado, aunque se salte, consume presupuesto.
- **Efecto:** las cabezas de la Cola bloqueadas por motivos duros (seba 47/14, kaez 28: sin subtítulos completos, sin veredicto) se evalúan cada ciclo y consumen el presupuesto. Los clips validados con score menor (seba 185, score 55, aprobado, con MP4) no llegan a evaluarse en todo el día y quedan sin jobs. El 185 no tenía ningún job a las 21:00 pese a estar aprobado.
- **Prueba:** 185 sin jobs (verificado en D1 21:10); cabezas 47/14/28 frenadas por el auto-enqueue a las 21:01/21:16/21:45 UTC según el chequeo de las 20:30.

### D6 (contexto, no causa del camino de publicación). El loop de revalidación va por los más viejos primero

- **Dónde:** `clipagent/revalidate_pending.py:58` (`MAX_CLIPS_PER_RUN = 10`), `:222-236` (candidatos ordenados por `created_at` ascendente, tope 10), `panel-backend/worker/src/cycle-gate.ts:225` (`REVALIDATE_INTERVAL_MS = 6 h`), `panel-backend/worker/src/index.ts:1739-1746` (`maybeDispatchRevalidate`).
- **Código:** cada corrida toma los 10 candidatos más viejos que necesitan SEO o veredicto; se dispara cada 6 h.
- **Efecto:** los clips nuevos (seba 177-185, 198-210) esperan detrás de un backlog viejo (hoy el loop iba por seba 1-34 y kaez 1-23). El suministro de clips validados nuevos llega con días de retraso, y el auto-enqueue no tiene qué encolar.
- **Prueba:** seba 177/178/180/181/182/194 sin SEO a las 14:40 pese a llevar un día cortados; el loop los alcanzará cuando agote los viejos.

## §3. Qué NO cambia

Nada de esto se toca ni se relaja; los fixes solo cambian **cuándo** se reintenta, no **si** se puede publicar:

- Topes anti-ban por plataforma+idioma: diario (YT 6, resto 3), rolling 24 h (YT 6, resto 3), warmup (14 días / 20 publicados, etapa nueva 1/día).
- Slots por plataforma (`PLATFORM_SCHEDULE`, db.ts:1230): YT 00:00/12:00/14:00/18:00/20:00/22:00; IG 12:00/18:00/21:00; TT/FB 13:00/19:00/22:00 (Chile). F0 añade la ventana 00:00-01:00 para que el hueco 00:00 exista de verdad; no cambia ninguna hora.
- 1 publicación por plataforma y franja; espaciamiento por plataforma (YT 2 h, TT 3 h, resto según Ajustes); 15 min entre plataformas del mismo clip.
- Puertas de publicación: SEO ≥80, viralidad ≥45, veredicto de calidad (B4), subtítulos verificables o excepción "igual si no habla" con `preview_seen` humano.
- Cuentas por plataforma+idioma (regla 14:10); nunca se publica en el canal del otro idioma ni se auto-crea cuenta.
- La republicación sigue siendo manual y explícita (`republish=true`, key `|repubN`, P0 #5): el auto-enqueue nunca republica, y F1 no crea jobs de republicación.
- Guardián de presupuesto D1 y circuito 7500; retención R2 24 h; `auto_publish_top` sigue detrás de su ajuste (hoy en 1 en ambos tenants, §0); el panel no cambia; no se pide nada al usuario (cero configuraciones).

## §4. Cambios

Ocho fixes (F0-F6 + F1b + F2b), en orden de impacto. Cada uno cita archivos y funciones exactas. Ninguno cambia los números del §3.

### F0. El hueco YT 00:00 existe de verdad (ventana + cron)

- **Causa:** D0. Sin este fix el techo automático es 36/día y el 39/día es imposible sin una publicación manual diaria.
- **Archivos:** `panel-backend/worker/src/db.ts:1084-1095` (`PUBLISH_SLOT_WINDOWS`), schedules de Cloudflare (vía API, §6).
- **Cambio:**
  1. Añadir la ventana `{ label: "madrugada", startMin: 0, endMin: 60 }` a `PUBLISH_SLOT_WINDOWS`. A las 00:00-00:59 `isWithinPublishSlot` pasa a ser verdadero y `nextPlatformHourMs(youtube_shorts, now)` devuelve `nowMs` (dentro de la hora 00:00), así que el job YT nace debido y se ejecuta en esa ventana.
  2. Extender el cron nocturno de `*/5 21-23,0-3` a `*/5 21-23,0-4` UTC en ambos Workers (vía PUT a `/accounts/{id}/workers/scripts/{name}/schedules`, body ARRAY JSON — el body como objeto da error 10026). En verano 0-3 ya cubría 00:00-00:59 Chile; en invierno (UTC-4) hace falta 0-4. La hora extra (01:00-01:59 en verano) no tiene ventana de publicación, así que no publica nada fuera de franja: el gate sigue mandando.
- **Por qué es seguro:** solo añade una ventana que el cron ya cubría en verano; el resto de puertas (tope diario, rolling, 1-por-franja, espaciamiento) se aplican igual a las 00:00 que a las 12:00.
- **Si falla:** YT sigue en 5/día automáticos (como hoy); se detecta porque ningún job YT tiene `next_run_at` entre 00:00 y 01:00.

### F1. `hasVigente` por plataforma + el gate recuerda lo publicado otros días

- **Causa:** D1 (los dos agujeros) y D2.
- **Archivos:** `panel-backend/worker/src/jobs.ts` (`autoEnqueueTopClips`, jobs.ts:1060-1145; `tryEnqueueClipPublish`, jobs.ts:677-960; `enqueuePublishJobs`, jobs.ts:580-640), `panel-backend/worker/src/gate.ts` (`publishGate`, gate.ts:175-360; `recheckPublishGate`, gate.ts:404-530).
- **Cambio:**
  1. **Posted-ever en el gate (cubre auto-enqueue Y manual, porque comparten la función).** En el bucle por plataforma de `tryEnqueueClipPublish` y en `recheckPublishGate` se añade el contador que hoy falta: `SELECT COUNT(*) ... state='posted'` por clip+plataforma **sin filtro de día** (y el `posted_day` del más reciente). Nuevo campo en `GateContext`: `clipPublishedEverOnPlatform` (+ `clipLastPublishedOnPlatformDay`). `publishGate` devuelve, si es verdadero y `!republish`: `este clip ya se publicó en ${platform} el ${day}: no se repite en la misma cuenta (usa republicar si la plataforma borró el vídeo)`. Clasificación: **duro** (tabla §F2). Con `republish=true` se salta, como el "ya se publicó hoy" actual. Esto cierra el duplicado del 102 en los dos caminos: el auto-enqueue no re-crea el job de TikTok y la publicación manual tampoco (salvo republicación explícita).
  2. **`hasVigente` por plataforma en el auto-enqueue.** La consulta pasa a traer también `platform` (`SELECT id, platform, state, posted_day FROM jobs ...`, jobs.ts:1084-1092). El auto-enqueue calcula el conjunto de plataformas que aún necesitan trabajo (sin job `scheduled`/`processing`, sin `posted` hoy y sin `posted` alguna vez) sobre la unión de plataformas del clip y sus cuentas vinculadas (`linkedPlatforms`, la misma que usa `tryEnqueueClipPublish`). Si el conjunto está vacío, se salta el clip (sin consumir presupuesto, ver F4). Si no, llama a `tryEnqueueClipPublish` con ese conjunto como override explícito (`opts.platforms`, parámetro que ya existe, jobs.ts:~688). El mecanismo real de revivido de un draft existente sigue siendo `enqueuePublishJobs` (jobs.ts:580-600, draft/failed → scheduled).
  3. **Escalonado de 15 min al crear.** Cuando `tryEnqueueClipPublish` crea varios jobs del mismo clip en una sola llamada, sus `next_run_at` se escalonan 15 min en orden de plataforma (sobre la base de `nextPlatformHourMs` por plataforma): el primero a su hora de hueco, los siguientes +15/+30/+45 min. YT e IG comparten hueco (12:00 y 18:00, `PLATFORM_SCHEDULE` db.ts:1230) y `nextPlatformHourMs` devuelve `nowMs` dentro de la ventana de 60 min — la afirmación de la v1 ("≥1 h entre huecos") era falsa, y sin escalonado dos plataformas del mismo clip pueden vencer en el mismo ciclo.
  4. **El revive de `enqueuePublishJobs` preserva `attempts`.** Hoy revive un draft/failed con `attempts = 0` (jobs.ts:~590), lo que borra el conteo de reintentos del día y burla el tope de F2.5. Cambio: el UPDATE de revive no toca `attempts` (conserva el valor guardado); y si `retriesToday(job, today) >= MAX_RETRIES_PER_DAY` (retry.ts:164-166), no se revive: el job queda en draft y `tryEnqueueClipPublish` lo cuenta como bloqueado con el motivo de tope. Peor caso residual: un job revivido con 2 intentos usados se ejecuta una vez más (la re-encolada no es un reintento); si vuelve a frenar en ejecución, F2.5 lo manda a draft por tope.
- **Por qué no rompe el 15 min:** el escalonado (3) separa los vencimientos; el re-chequeo de ejecución sigue aplicando el 15 min; y F2b lo hace ver también a los hermanos en vuelo.
- **Si falla:** si el override se pasa mal, el comportamiento es el de hoy (se salta el clip); el fix es observable en D1 (jobs nuevos por plataforma en clips con un posted hoy; ningún job nuevo en una plataforma con posted de otro día).

### F1b. Los clips sellados como `publicado` vuelven a evaluarse para las plataformas que les faltan

- **Causa:** D1b.
- **Archivos:** `panel-backend/worker/src/jobs.ts` (`tryEnqueueClipPublish`, jobs.ts:725; `autoEnqueueTopClips`, jobs.ts:1060-1145), `panel-backend/worker/src/db.ts` (`listPublishedClips`, db.ts:~1745).
- **Cambio:**
  1. El retorno temprano `clip.status === "publicado"` de `tryEnqueueClipPublish` se relaja: se calcula primero la unión de plataformas vinculadas; si al clip le faltan plataformas vinculadas respecto a su lista (`linked − listed`), se sigue adelante **solo** con esas (override `opts.platforms = faltantes`). Si no le falta ninguna, devuelve "clip ya publicado" como hoy.
  2. `autoEnqueueTopClips`, después del bucle principal de la Cola y si `considered` no llegó al tope, escanea `listPublishedClips` (límite 5 por ciclo, más recientes primero) con la misma lógica por plataforma de F1. Acotado a 2 clips sellados por ciclo.
- **Efecto inmediato (sin tocar D1 a mano):** seba 15, seba 173 y kaez 4 reciben sus jobs de las plataformas que les faltan en los ciclos siguientes al despliegue.
- **Si falla:** los sellados siguen sellados (como hoy); se detecta porque 15/173/4 siguen sin jobs en las plataformas que les faltan.

### F2. El freno transitorio en ejecución reprograma el job, no lo manda a draft

- **Causa:** D3 (la más grave: es la que deja los 10 drafts de hoy).
- **Archivos:** `panel-backend/worker/src/gate.ts` (`publishGate`, `recheckPublishGate` gate.ts:404-530; nuevo: `GATE_BLOCKS`, `classifyGateBlock`, `retryAtForGateBlock`), `panel-backend/worker/src/publish/index.ts:168-212` (`dispatchPublishStep`), `panel-backend/worker/src/jobs.ts:1673-1760` (`applyPublishResult`), `panel-backend/worker/src/retry.ts` (`MAX_RETRIES_PER_DAY` retry.ts:59, `retriesToday` retry.ts:164-166, `nextRetryDelayMs` retry.ts:~170).
- **Cambio (rediseñado frente a la v1 — el gate NO cambia de firma):**
  1. `publishGate` y `recheckPublishGate` **siguen devolviendo `string | null`** (la v1 proponía un retorno estructurado; eso rompía el único otro call-site, `blocked.push(\`${platform}: ${gate}\`)` en jobs.ts:~910, el filtro `b.includes(": calidad:")` del auto-enqueue y decenas de tests que asertan `.toBeNull()` / `.toContain("espaciamiento")`). La clasificación vive en **un solo sitio nuevo, `gate.ts`: una tabla `GATE_BLOCKS`** que asocia cada motivo exacto del gate con `{ transient: boolean; retry: 'spacing' | 'cross15' | 'slotEnd' | 'nextSlot' | 'rolling' | 'tomorrow' | 'backoff' | null }`. `classifyGateBlock(reason)` la consulta. Un motivo no reconocido se trata como **duro** (fail-closed).
  2. La tabla (motivos exactos leídos de `publishGate` + `rateLimitGate` + `recheckPublishGate` + `qualityGateForClip` + `reservePublishSlot`):
     - **Transitorios** (el tiempo los resuelve solo, sin acción humana — hoy o mañana):
       - `espaciamiento: menos de {h} h desde la última subida en esta plataforma` → `spacing` (retryAt = última subida posted en plataforma+idioma + `spacing_hours`).
       - `espaciamiento: menos de {min} min desde la última subida en esta plataforma` → `spacing` (retryAt = última subida + `min_spacing_minutes`). Las dos razones se distinguen por el texto completo ("h" vs "min"); no por prefijo.
       - `mismo clip en otra plataforma hace {gap} min: mínimo {cross} min entre plataformas` → `cross15` (retryAt = última subida del clip en otra plataforma + `cross_platform_spacing_minutes`; con F2b, también hermanos en `processing`).
       - `ya se publicó 1 en {platform} en esta franja: como mucho 1 por plataforma y franja` → `slotEnd` (retryAt = fin de la franja actual, `slotWindowBoundsMs(now).endMs`; si no hay franja actual, próxima hora de hueco con `nextPlatformHourMs(now + 61 min)` — **no** `nextPlatformHourMs(now)`, que dentro de la ventana de 60 min devuelve `nowMs` y el job re-vencería de inmediato).
       - `fuera de franja: la publicación solo sale 12:00–14:00, 18:00–20:00 o 21:00–23:00 (hora de Chile)` → `nextSlot` (retryAt = inicio de la próxima ventana de `PUBLISH_SLOT_WINDOWS`; con F0 incluye 00:00-01:00).
       - `tope rolling 24 h alcanzado: {n}/{max} en {platform} (últimas 24 h)` → `rolling` (retryAt = la publicación más vieja dentro de la ventana + 24 h — se consulta `MIN(updated_at)`, no el COUNT; en el caso habitual cae **mañana**, y eso es coherente con la definición de transitorio: "pasa solo con el tiempo", no "pasa hoy").
       - `tope diario alcanzado: {n}/{max} en {platform}` → `tomorrow` (retryAt = primera hora de hueco de la plataforma mañana).
       - `plan del día cubierto: {n}/{plan} en {platform} (techo {max})` → `tomorrow`. (El plan es el tope diario operativo real: el gate lo comprueba después del máximo, así que dispara antes cuando `plan < max`.)
       - `calentamiento: {n}/{limit} hoy en {platform} (cuenta nueva: ni a mano se supera el tope de su etapa)` → `tomorrow` (el tope de etapa se resetea al cambiar el día).
       - `día libre de la cuenta en {platform}: hoy no publica (1 día libre por semana, rotativo)` → `tomorrow`.
       - `reserva de cupo perdida: el job cambió de manos antes de publicar` → `backoff` (retryAt = now + `nextRetryDelayMs(retriesUsedToday)`).
       - `tope diario alcanzado: cupo no reservado en {platform} tras 3 intentos` → `backoff`. (Ojo al orden de matching: esta cadena empieza como el tope diario; la tabla se consulta de más específica a menos específica, o se casa por la cadena completa.)
     - **Duros** (necesitan acción humana o cambio del sistema; van a draft como hoy):
       - `clip archivado` · `clip ya publicado` · `clip no encontrado` · `el clip no tiene MP4 en R2: súbelo desde el Editor antes de publicar.` · `el job apunta a una cuenta que ya no existe: vuelve a encolar el clip desde Publicar.`
       - `sin cuenta {label} en {lang}: crea la cuenta en Cuentas y pega su credencial` · `idioma: el clip es en {lang} pero la cuenta {label} es en {lang}: nunca se publica en el canal del otro idioma` · `idioma: el clip es en {lang} pero la cuenta {label} del job es en {lang}: nunca se publica en el canal del otro idioma` · `cuenta {label} en {lang} sin credencial verificada: pega la credencial en Cuentas y verifícala`
       - `rescatado por gritos (viralidad {v} < {min}): bloqueado hasta revisión humana — desbloquéalo en el Editor` · `sin subtítulos completos: necesita transcripción humana`
       - `calidad: {motivo}` · `calidad: sin veredicto válido ({motivo})` · `calidad: veredicto de calidad ilegible (JSON roto)` · `calidad: sin veredicto de calidad (clip sin validar: validar en el Editor o re-renderizar)`
       - `falta ver el preview en el Editor` · `duración {dur}s fuera de {min}–{max} s` · `categoría: solo GTA VI (este clip es {game})` · `categoría: sin categoría GTA VI declarada`
       - `el streamer no tiene permiso marcado` · `streamer desactivado en el panel` (son dos textos distintos para lo mismo: el primero del `publishGate`, el segundo del re-chequeo; la tabla lleva los dos — la v1 solo llevaba "streamer desactivado")
       - `SEO {score}: bajo la puerta de {min} en {platform}` · `cuenta en pausa por spam/límite hasta {iso}: no se publica ni a mano (pausa de 48 h)`
       - `este clip ya se publicó hoy en {platform}: no se repite el mismo clip en la misma cuenta el mismo día` · `este clip ya se publicó en {platform} el {day}: no se repite en la misma cuenta (usa republicar si la plataforma borró el vídeo)` (nuevo, F1.1)
       - Cualquier texto no reconocido → duro.
  3. `retryAtForGateBlock(env, job, reason, nowMs)`: calcula el `retryAtMs` re-consultando lo que necesita (la función recibe `env`+`job`, no depende de que el re-chequeo le pase los contadores — el re-chequeo los descarta tras llamar a `publishGate`): `MAX(updated_at)` de posted en plataforma+idioma (solo posted, **no** reservas de cupo: `lastPublishedAtMs` del re-chequeo está contaminado por `max(lastPosted, slot.updated_at)` y una reserva que nunca publicó no es una subida), `MIN(updated_at)` de posted en las últimas 24 h (para rolling), `MAX(updated_at)` del clip en otras plataformas (para cross15), `slotWindowBoundsMs` y `PLATFORM_SCHEDULE` (para franja/tope), y los Ajustes (spacing, cross min). Fallbacks si no se puede calcular: las reglas de hora (`nextSlot`, `slotEnd`, `tomorrow`) se calculan solo con el horario (sin lecturas D1); las de contador (`spacing`, `cross15`, `rolling`) caen a `now + nextRetryDelayMs(retriesUsedToday)`.
  4. `dispatchPublishStep` (`publish/index.ts:202`): si el re-chequeo o la reserva frenan con `transient=true` (clasificado por `classifyGateBlock` sobre el motivo, **tras quitar el prefijo `"gate de publicación: "`** que añade el propio dispatch), devuelve modo `"retry"` con `{ reason, retryAtMs }` en vez de `"draft"`. Si frena con `transient=false`, devuelve `"draft"` como hoy. Los retornos directos del dispatch que no pasan por el gate (sin MP4, cuenta del job inexistente o en otro idioma) siguen devolviendo `"draft"` sin cambio.
  5. `applyPublishResult` (jobs.ts:1673): nuevo caso `"retry"`: suelta el cupo (`releasePublishSlot`, como el caso `"draft"`, **antes** de la transición), y vía `fencedJobUpdate` pone `state='scheduled', next_run_at=retryAtMs, retryable=1, last_error='gate de publicación: {reason}', updated_at=now`, limpia `run_token`/`slot_day` (como `markDraft`), y escribe `attempts` con el valor absoluto correcto: **`retriesToday(job, todayKey) + 1`** (retry.ts:164-166) — no `job.attempts + 1`: `attempts` arrastra el conteo de ayer y solo `retriesToday` resetea por día. Si `retriesToday(job, todayKey) >= MAX_RETRIES_PER_DAY` (3/día, retry.ts:59) antes de incrementar, en vez de reprogramar va a `markDraft` (draft terminal, como hoy). El caso `"draft"` queda solo para frenos duros. Nota: `attempts` lo consumen también los reintentos de error de adaptador (`markFailed`) y `detectStalledJobs`: el tope es sobre reintentos totales del día, por cualquier causa mezclada — así está diseñado (`retriesToday` deriva de la fila, no de la causa).
- **Por qué es seguro:** el re-chequeo de ejecución vuelve a aplicar TODO el gate cuando llegue `next_run_at`; si la estimación era optimista, el job vuelve a frenar (y reprogramarse o ir a draft por tope de intentos). No se publica nada que el gate no permita en ese instante. El gate no cambia de firma: ningún call-site ni test existente se toca por este fix.
- **Si falla:** el comportamiento degrada al de hoy (draft); se detecta en D1 por jobs en draft con `last_error` transitorio.

### F2b. El 15 min también ve a los hermanos en `processing` (nuevo)

- **Causa:** D1/D3 (hueco de paralelismo; lo encontró el goldfish).
- **Archivos:** `panel-backend/worker/src/gate.ts` (`recheckPublishGate`, gate.ts:404-530, consulta `clipOther`), `panel-backend/worker/src/jobs.ts` (`tryEnqueueClipPublish`, consulta `clipOther`, jobs.ts:~845).
- **Código:** hoy `clipOther` es `SELECT updated_at FROM jobs WHERE clip_id=? AND platform<>? AND state='posted' ORDER BY updated_at DESC LIMIT 1`: solo ve publicados. Los jobs del ciclo se despachan agrupados por plataforma+idioma en paralelo (`Promise.all`, jobs.ts:~1570-1600) y cada grupo reclama su job (`claimJobForCycle`, jobs.ts:1424-1445, `state='processing'` + `run_token`) **antes** de llamar a `dispatchPublishStep` → `recheckPublishGate`. Dos plataformas del mismo clip con `next_run_at` en el mismo ciclo pueden publicarse a la vez violando los 15 min.
- **Cambio:** en `recheckPublishGate`, `clipOther` pasa a `state IN ('posted','processing')` (mismo `ORDER BY updated_at DESC LIMIT 1`): un hermano en `processing` cuenta como "última subida del clip en otra plataforma" a su `updated_at`. Es conservador: mientras el hermano siga en `processing`, su `updated_at` se mueve (el caso `processing` lo actualiza cada ~10 s), así que la segunda plataforma espera a que el hermano termine y luego 15 min desde su `posted`.
- **Coste honesto:** si un hermano se atasca en `processing`, retrasa a las demás plataformas del clip hasta que `detectStalledJobs` lo desatasque. Se acepta: retrasa, nunca viola los 15 min. Queda una carrera de milisegundos (dos grupos que reclaman y re-chequean a la vez pueden pasar los dos antes de verse); la mitiga el escalonado de F1.3, que ya separa los vencimientos 15 min.
- **Si falla:** el 15 min se sigue aplicando solo con posted (como hoy); se detecta por dos publicaciones del mismo clip con menos de 15 min entre ellas.

### F3. El freno A4 solo cuenta bloqueos duros

- **Causa:** D4.
- **Archivos:** `panel-backend/worker/src/jobs.ts` (`autoEnqueueTopClips`, ~1125-1135: el `else` que llama a `recordAutoEnqueueAttempt`), `panel-backend/worker/src/gate.ts` (`classifyGateBlock`, de F2).
- **Cambio:** un ciclo en el que `tryEnqueueClipPublish` devuelve 0 jobs cuenta como intento fallido A4 **solo si al menos un motivo en `r.blocked` es duro** (`transient=false` según `classifyGateBlock`, tras quitar el prefijo `"<platform>: "` que añade el `blocked.push`). Si todos los motivos son transitorios — incluido **"fuera de franja"** (que es el motivo que más dispara el auto-enqueue fuera de franja: el cron corre 14:00-15:59 sin ventana) — NO se llama a `recordAutoEnqueueAttempt`: el clip simplemente se salta este ciclo y se re-evalúa en el siguiente. `blocked` **sigue siendo `string[]`** (la v1 lo convertía en objetos y rompía el filtro `b.includes(": calidad:")` del propio auto-enqueue y a cualquier consumidor del endpoint que devuelva `blocked`); la clasificación se hace en el punto de uso con `classifyGateBlock`.
- **Interacción con F2:** F2 reprograma jobs ya creados; F3 evita que los clips sin jobs queden aparcados por frenos de minutos. Juntos cubren los dos caminos.
- **Si falla:** el clip se aparca como hoy (3 intentos); se detecta por `auto_publish` en `worker_events` con el motivo de tope A4.

### F4. El presupuesto de evaluación no lo consumen los saltos (sin subir a 30)

- **Causa:** D5.
- **Archivos:** `panel-backend/worker/src/jobs.ts` (`autoEnqueueTopClips`, jobs.ts:982-989 y 1078-1104).
- **Cambio:**
  1. `evaluated++` se mueve a **después** de los saltos por `hasVigente` y por A4: solo los clips que llegan a `tryEnqueueClipPublish` consumen presupuesto de evaluación.
  2. Nuevo tope de lectura por ciclo: `AUTO_ENQUEUE_MAX_SCANNED_PER_CYCLE = 60` (clips visitados, incluidos los saltados; acota las lecturas D1 por ciclo aunque los saltos sean gratis).
  3. Sube solo `AUTO_ENQUEUE_MAX_CLIPS_PER_CYCLE` 3 → 5. **`AUTO_ENQUEUE_MAX_EVALUATED_PER_CYCLE` se queda en 10** (la v1 lo subía a 30 con una cuenta de lecturas errónea — ver abajo).
- **Por qué no se sube a 30 (corrección del goldfish, verificada):** cada clip evaluado son ~30 lecturas, no ~6: `hasVigente` (1) + siembra A4 (1) + `getSettings` (1) + 3 resoluciones de cuenta en `linkedPlatforms` + ~6 consultas por plataforma (publicados hoy, última subida, rolling, clip hoy, clip otra plataforma, franja) × hasta 4 plataformas. 30 evaluados ≈ **900 lecturas/ciclo**, no 180. Y cada plataforma bloqueada escribe 1 `clip_event` + 1 `worker_event` (jobs.ts:~907-913, `logToWorkerEvents: true`): hasta 8 escrituras por clip totalmente bloqueado, ~240 por ciclo a 30 evaluados — justo la explosión de escrituras que el freno A4 se creó para parar (cabecera de retry.ts). Con evaluados en 10, el techo es ~300 lecturas y ~80 escrituras por ciclo. El guardián de presupuesto (`shouldWrite("bulk-job")`, jobs.ts:~1538) se consulta **una sola vez** antes del auto-enqueue, no por clip: los topes son la única cota por ciclo, y por eso no se suben. El guardián y el circuito 7500 siguen apagando el auto-enqueue entero si la cuota aprieta.
- **Si falla:** el ciclo evalúa menos clips (como hoy); se detecta por `considered`/`enqueued` en el resultado del ciclo.

### F5. El loop de revalidación prioriza los clips que desbloquean publicación

- **Causa:** D6.
- **Archivos:** `clipagent/revalidate_pending.py` (`run()`, :222-286; `_needs_work`, :86-92), `clipagent/panel_bridge.py` (`list_clips`, :360-364), `panel-backend/worker/src/index.ts:989-1002` (`clipOut`), `.github/workflows/cerebro-revalidate.yml` (sin cambio de cadencia).
- **Cambio:**
  1. Orden de candidatos: primero los clips **listos salvo SEO/veredicto** (tienen MP4 en R2 — `video_r2_key` no nulo — + `subtitles_complete=1` + `status != 'publicado'` + `score >= 45` + necesitan SEO o veredicto); después el resto. Dentro de cada grupo, el orden de la Cola **en la aproximación disponible en Python**: LIVE primero (`origin == 'live'`), luego `manual_rank` (NULL al final), luego `score` descendente, luego `created_at` ascendente como desempate. Es una aproximación de `listQueueClips` (db.ts:1675) **sin el boost de `clip_stats`**, que el endpoint no expone. Los campos existen: `list_clips()` devuelve `GET /api/clips` y `clipOut` (index.ts:989-1002) devuelve `...c` — la fila completa, incluidos `score`, `origin`, `subtitles_complete`, `video_r2_key`, `status`, `publish_blocked` y `viral`. (La v1 afirmaba el filtro sin citar de dónde salían los campos; `run()` y `_needs_work` hoy no los leen, pero el puente sí los devuelve.)
  2. Tope por corrida: `MAX_CLIPS_PER_RUN` 10 → 15. La cadencia se queda en 6 h (`REVALIDATE_INTERVAL_MS`, cycle-gate.ts:225) y el anti-atasco de 24 h (`_recently_attempted`, `STEP_REVALIDADO`) no cambia.
- **Por qué no deja tirados a los viejos:** los viejos siguen siendo candidatos y se procesan cuando no hay listos pendientes; el tope subido (15) y el orden por Cola hacen que el loop avance por los que el auto-enqueue quiere primero. Un viejo listo con más score o LIVE va antes que un nuevo listo: eso es el orden definido, no una contradicción (la v1 lo dejaba ambiguo).
- **Si falla:** el loop sigue yendo por antigüedad (como hoy); se detecta porque los clips nuevos no ganan SEO/veredicto.

### F6. Barrido de drafts transitorios en el ciclo (cura los drafts viejos y los que escapen a F2)

- **Causa:** D3 (residuo: los drafts creados antes del despliegue y cualquier draft transitorio futuro).
- **Archivos:** `panel-backend/worker/src/jobs.ts` (`runWorkerCycle`, jobs.ts:1483-1620: **después de `detectStalledJobs`, antes de `dueJobs`**), `panel-backend/worker/src/gate.ts` (`recheckPublishGate`, `classifyGateBlock`).
- **Cambio:** en cada ciclo en el que `cycleGate` deja correr el ciclo (no en cada tick de 5 min del cron), un barrido acotado: toma hasta 5 jobs en `draft` con `updated_at >= inicio del día de Santiago en ms` (definición de "hoy": `santiagoWallToUtcMs(santiagoDayKeyMs(now), "00:00")`, el mismo día que usa `retriesToday`) cuyo `last_error`, **tras quitar el prefijo `"gate de publicación: "`** que añade `dispatchPublishStep` (publish/index.ts:202), clasifique como transitorio según `classifyGateBlock`. A cada uno le aplica `recheckPublishGate` ahora; si pasa (null), el job vuelve a `scheduled` con `next_run_at=now`, `retryable=1`, `last_error=NULL` — **y solo si `retriesToday(job, today) < MAX_RETRIES_PER_DAY`** (si ya agotó sus 3 reintentos hoy, no se revive: el tope de F2.5 seguiría vigente y el barrido lo reviviría en bucle). Si sigue frenando, no se toca. Los jobs revividos del mismo clip en el mismo barrido se escalonan 15 min (no todos a `now`), como en F1.3. El barrido nunca toca drafts con motivo duro (calidad, sin cuenta, streamer desactivado, spam) ni jobs de días anteriores (esos los re-encola el auto-enqueue mañana si procede). No revive un job cuyo clip ya publicó hoy en esa plataforma: el propio re-chequeo lo frena con "ya se publicó hoy" (duro).
- **Coste real (corrección del goldfish):** cada `recheckPublishGate` son ~10 lecturas, más 1 escritura de `worker_event` por cada clip sin `influencer_id` (rama fail-open con log, gate.ts:404-530). 5 drafts ≈ 50 lecturas + hasta 5 escrituras de revive por ciclo, no "5 lecturas + 5 escrituras".
- **Por qué es seguro:** usa el mismo re-chequeo que la ejecución; solo revive lo que el gate ya permitiría publicar ahora. Acotado a 5/ciclo.
- **Si falla:** los drafts viejos se quedan (como hoy); se detecta en D1 por el conteo de drafts con `last_error` transitorio.

## §5. Tests (uno que falla antes y pasa después por fix)

Correcciones frente a la v1 (goldfish): `jobs.test.ts` no tiene ningún doble de D1 ni cubre `autoEnqueueTopClips`/`runWorkerCycle`; `_RecordingBridge` no existe (el doble Python es `FakeBridge`, `test_revalidate_pending.py:61`). Los dobles `FakeD1` sí existen en los tests del Worker (p. ej. `quality-gate.test.ts:123`, `slot-reservation.test.ts`, `cycle-gate.test.ts`) y `runWorkerCycle` ya se llama en tests (`slot-reservation.test.ts`, `cycle-gate.test.ts`, `security-fixes.test.ts`, `analytics-auto.test.ts`): esos archivos son el patrón de harness a reutilizar. Ningún test toca red ni D1 real.

- **F0** (nuevo; `test/publish-rules-2026-11-19.test.ts`, aserciones puras sin D1): `isWithinPublishSlot` a las 00:30 Chile = verdadero; `nextPlatformHourMs(youtube_shorts, 00:30)` devuelve `nowMs`; un job YT encolado a las 00:30 nace con `next_run_at` ≈ ahora. Antes: la franja 00:30 es falsa.
- **F1** (nuevo `panel-backend/worker/test/auto-enqueue.test.ts`, con `FakeD1` + tiempo congelado dentro de franja + `auto_publish_top=1` en settings + cuentas verificadas por plataforma+idioma): Cola con un clip con un job `posted` hoy solo en `youtube_shorts` y resto de plataformas libres, con gate que pasa. Antes: el auto-enqueue lo salta (`enqueued=0`). Después: encola TT/IG/FB (`enqueued=3`) y no re-encola YT. Segundo caso: clip con un `posted` en TikTok **de otro día** y sin jobs hoy → después: no se crea job de TikTok (posted-ever), sí de las demás. Tercer caso: clip con jobs `scheduled` en todas las plataformas → se salta (sin cambio).
- **F1b** (mismo archivo nuevo): clip con `status='publicado'` y `platforms=["youtube_shorts"]` pero TT/IG/FB vinculadas → el auto-enqueue encola las 3 faltantes. Clip con `status='publicado'` y las 4 plataformas → se salta.
- **F2** (por `runWorkerCycle` con `FakeD1` + adaptador simulado, patrón de `slot-reservation.test.ts`): `applyPublishResult` no está exportada (jobs.ts:1673), así que el test **no** la llama directamente. (a) job frenado por espaciamiento en ejecución → vuelve a `scheduled` con `next_run_at` = última subida **posted** + spacing (el doble debe distinguir una subida posted de una reserva de cupo: el valor no puede ser la marca de una reserva), `retryable=1`; (b) job frenado por calidad → `draft` con `retryable=0` (sin cambio); (c) job con 3 intentos hoy frenado por espaciamiento → `draft` (tope de intentos; el test fija `updated_at` explícitamente, porque `retriesToday` deriva de él). El test de (a) falla antes (el job cae a draft). El doble debe modelar la valla de `run_token` de `fencedJobUpdate`: sin ella, el test pasaría igual sin el fix.
- **F2b** (mismo archivo): dos jobs del mismo clip, uno en `processing` en YT y otro debido en IG 5 min después → el de IG frena por 15 min (transitorio) y se reprograma a +15 min. Antes: pasa (solo mira posted).
- **F3** (nuevo `test/auto-enqueue.test.ts`, `autoEnqueueTopClips` + `retry.ts`): clip con todas las plataformas frenadas solo por espaciamiento durante 3 ciclos seguidos → en el 4.º ciclo `autoEnqueueAllowed` sigue permitiendo (0 intentos A4 registrados). Mismo escenario con bloqueo por calidad → al 4.º ciclo está aparcado. **El test avanza el reloj entre ciclos** (1 min tras el 1.er intento, 5 min tras el 2.º, como exige el backoff): con el tiempo congelado solo se registraría 1 intento y el caso no se comportaría como se describe. El primer caso falla antes (al 4.º ciclo ya está aparcado).
- **F4** (nuevo `test/auto-enqueue.test.ts`): Cola con 10 clips saltados por `hasVigente` + 1 clip válido en la posición 11 → en el mismo ciclo el válido se encola. Antes: no se evalúa (corta en 10). Después: se encola. (El tope de escaneados 60 también se aserta: con 70 clips saltados, el ciclo visita 60.)
- **F5** (`test_revalidate_pending.py`, doble `FakeBridge`): candidatos mezclados con scores y orígenes fijados en el fixture (viejo sin MP4, nuevo listo salvo SEO con score 60 LIVE, viejo listo con score 70 VOD) → el orden es viejo-listo (score 70) primero, luego nuevo-listo, luego viejo-sin-MP4; el tope 15 se respeta; el anti-atasco 24 h sigue saltando los reintentados **sin consumir el tope** (`saltados` no cuentan como `revisados`); un clip que solo necesita SEO consume un hueco igual que uno que necesita veredicto. Antes: el viejo va primero por antigüedad.
- **F6** (nuevo `test/auto-enqueue.test.ts`, `runWorkerCycle`): job en `draft` de hoy con `last_error` = `"gate de publicación: espaciamiento: menos de 2 h..."` y gate que ya pasa → tras el ciclo está `scheduled`; job en `draft` con motivo duro (calidad) → sigue en `draft`; job en `draft` con 3 reintentos hoy → sigue en `draft`. El primer caso falla antes.
- **Regresión:** suite completa Python (`pytest`, con `TMPDIR=/home/hatch/tmp`) y Worker (`bun test test/` + `tsc --noEmit`) en verde salvo los 14 fallos pre-existentes ya verificados con `git stash` (contadores A2, topes 0019, republish P0 #5, cuota channels, offset Facebook, circuito 7500, A4 markFailed, freno auto-enqueue, Telegram, maybeDispatchDaily, logout B1). CI (GitHub Actions + CircleCI) en verde antes de desplegar.
- **Verificación de duración (F5, menor):** la subida de 10 a 15 por corrida no está verificada en tiempo: cada veredicto descarga MP4, transcribe y valida en serie. Medir la duración de las primeras corridas con tope 15 en el workflow; si supera el tiempo del workflow, bajar el tope.

## §6. Despliegue y verificación en vivo

- **Rama:** todo en `main` de `exe2027/viral-clip-agent`, un solo commit (o uno por fix si el revisor lo prefiere; el despliegue es el mismo).
- **Cerebro (Python, F5):** sale con el `push` a `main` (GitHub Actions lo toma solo). Sin despliegue.
- **Worker (TypeScript, F0-F4, F6, F1b, F2b):** desplegar con `wr_v2.py` (no con `wrangler` directo): `python3 wr_v2.py seba deploy --config wrangler.toml` y `python3 wr_v2.py kaez deploy --config wrangler.kaez.toml` (kaez SIEMPRE con `wrangler.kaez.toml` SIN `[triggers]`, que preserva los crons; nunca `wr_kaez.py`, que es nocron y los borra). `wr_v2.py` da veredicto DEPLOY OK/FALLIDO con health + crons (V6). **No tocar `avc-sebastian`.** Registrar los Version IDs anterior y nuevo de cada Worker antes y después (rollback = redesplegar el Version ID anterior). **Verificar `auto_publish_top=1` en ambas D1 antes del despliegue** (§0; si estuviera en 0, F1/F3/F4 serían código muerto y solo F2/F6 correrían).
- **Crons (F0):** tras el despliegue, actualizar los schedules de ambos Workers a `[{"cron":"*/5 15-18 * * *"},{"cron":"*/5 21-23,0-4 * * *"}]` vía PUT a `/accounts/{id}/workers/scripts/{name}/schedules` (body ARRAY JSON) y verificar con GET (la respuesta es `{"result": {"schedules": [...]}}`).
- **Sincronizar** los archivos cambiados del Worker a `~/workspace/gta6-clips/independencia/panel-backend/` (copia de despliegue), como se hizo con auto-seo.
- **Verificación en vivo (mismo día, sin esperar al día siguiente):**
  1. `/api/health` en ambos Workers: `tenant` correcto, `worker_running=true`; crons `*/5 15-18` y `*/5 21-23,0-4` presentes (GET schedules).
  2. Forzar una pasada: poner `last_revalidate_at_ms=0` en ambas D1 para el loop, y dejar que el ciclo corra; en la siguiente franja comprobar en D1:
     - (a) F1: un clip con un posted hoy en una plataforma recibe jobs en las demás. **Precondiciones para que la comprobación distinga "F1 no funciona" de "no tocaba":** el clip tiene que estar en cabeza de Cola dentro de los topes de F4, el gate tiene que pasar para las demás plataformas en ese instante (franja, espaciamiento, 1-por-franja, SEO por plataforma) y `auto_publish_top` tiene que estar en 1. Sin eso, no ver nada en D1 no prueba nada.
     - (b) F1-posted-ever: ningún job nuevo en una plataforma donde el clip ya publicó otro día (p. ej. el 102 no recibe ningún job nuevo de TikTok ni de YT).
     - (c) F1b: seba 15, seba 173 y kaez 4 reciben jobs de las plataformas que les faltan.
     - (d) F2: un job frenado por espaciamiento vuelve a `scheduled` con `next_run_at` futuro, no a `draft`.
     - (e) F0: un job YT con `next_run_at` entre 00:00 y 01:00 Chile (comprobar en la franja de madrugada siguiente).
     - (f) F5: `clip_events` con paso `revalidado` en clips nuevos (177-185, 198-210).
     - (g) F6: los drafts viejos con motivo transitorio se reprograman.
  3. Revisión tras la franja 18:00-20:00 y 21:00-23:00 del día del despliegue: contar publicados del día y drafts con motivo transitorio (deberían tender a 0). El objetivo del primer día es que no quede ningún draft transitorio al cierre.
- **Rollback:** si un fix se comporta mal en vivo (jobs rebotando, publicaciones fuera de franja, duplicados), redesplegar el Version ID anterior de ese Worker y reponer los crons si hiciera falta (con el F0, reponer `0-4`; si se revierte F0, volver a `0-3`); el cerebro se revierte con `git revert` del commit.

## §7. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| F2 estima mal `retryAtMs` y el job rebota cada 5 min todo el día | Tope 3 intentos/día por job (F2.5); el re-chequeo vuelve a frenar; cada rebote deja `last_error` con el motivo. Peor caso = comportamiento actual (draft). |
| F1 + F2 suben la velocidad de publicación y se roza un tope | Los topes no cambian y el gate los aplica en ejecución; el 1-por-franja y el espaciamiento siguen frenando. El primer día se vigila el conteo por plataforma+idioma en D1. |
| F4 sube las lecturas D1 por ciclo | Evaluados se quedan en 10 (~300 lecturas/ciclo); escaneados acotados a 60; el guardián de presupuesto y el circuito 7500 siguen apagando el auto-enqueue si la cuota aprieta. |
| F5 deja sin validar el backlog viejo | Orden por Cola, no por novedad pura: los viejos listos van antes que los nuevos no listos; el tope sube a 15. El backlog se vacía más despacio pero no se abandona. |
| F6 revive un draft que no debía (p. ej. motivo mal clasificado como transitorio) | Solo revive si `recheckPublishGate` pasa **ahora** y si no agotó sus 3 reintentos hoy; la clasificación es la tabla única de F2 y un motivo no reconocido es duro (fail-closed). |
| La clasificación transitorio/duro por texto es frágil | La tabla `GATE_BLOCKS` vive en un solo sitio (`gate.ts`) y se casa por cadena completa (o prefijo más específico primero, caso "cupo no reservado" vs "tope diario"); un test aserta que cada motivo que `publishGate` puede devolver está en la tabla. |
| Paralelismo entre grupos: dos plataformas del mismo clip en el mismo ciclo | Escalonado de 15 min al crear (F1.3) + F2b (el re-chequeo ve hermanos en `processing`). Queda una carrera de milisegundos entre claim y re-chequeo de dos grupos; el escalonado la hace improbable y el 1-por-franja + espaciamiento la acotan. |
| F2b retrasa plataformas si un hermano se atasca en `processing` | Es el coste aceptado (retrasa, nunca viola); `detectStalledJobs` desatasca el hermano en el mismo ciclo. |
| F0: la ventana 00:00-01:00 y el cron 0-4 cambian las horas en que corre el ciclo | Las puertas de publicación no cambian: fuera de ventana no se encola nada; la hora extra de cron en verano (01:00-01:59) no tiene ventana y no publica. |
| El estado A4 vive en memoria del Worker y se pierde al reiniciar | Sin cambio (limitación conocida, documentada en jobs.ts): la siembra por lectura de `clip_events` lo deja en 1 intento. F3 reduce cuánto importa (los transitorios ya no cuentan). |
| El revive de `enqueuePublishJobs` con intentos preservados deja un job con 2 intentos ejecutarse una vez más | Acotado a una ejecución extra por revive; si vuelve a frenar, F2.5 lo manda a draft por tope. Documentado en F1.4. |

## §8. Decisiones pendientes del usuario

1. **Aprobar la spec e implementarla** (o pedir cambios). Sin su "sí", no se toca código.
2. **F0 (hueco YT 00:00):** aprobar añadir la ventana 00:00-01:00 y extender el cron nocturno a `0-4` UTC. Sin F0 el techo automático es 36/día y el 39/día exige una publicación manual diaria a las 00:00.
3. **Tope de reintentos por job (F2.5):** 3/día (igual que el A4 actual) o otro número. Por defecto 3.
4. **Topes del auto-enqueue (F4):** evaluados 10 (sin cambio), escaneados 60, considerados 5 por ciclo. Por defecto los propuestos.
5. **Tope del loop de revalidación (F5):** 15/corrida (hoy 10). Por defecto 15.
6. **¿El barrido F6 también revive drafts de días anteriores?** Propuesta: no (solo los de hoy); los de días anteriores los re-encola el auto-enqueue mañana. Si quiere que barra todo, decirlo.
7. **F1b (sellados):** aprobar que el auto-enqueue escanee también los clips `publicado` con plataformas que les faltan (seba 15, 173; kaez 4). Si prefiere dejarlos como están, decirlo.
8. **Confirmar que el 39/día sigue siendo la meta** (24 seba + 15 kaez) y que el 48/día rige solo cuando kaez tenga inglés (regla 2026-11-23 17:39). Con F0, el mismo sistema debe dar 48 sin más cambios (los topes ya son por plataforma+idioma).

---

*Revisión goldfish: 2026-11-25, subagente 70ad280f (brief estricto: solo esta spec + jobs.ts, gate.ts, publish/index.ts, retry.ts, cycle-gate.ts, db.ts, revalidate_pending.py y los tests citados). 20 hallazgos: 5 bloqueadores, 12 importantes, 3 menores. Todos resueltos en esta v2 salvo dos premisas que la verificación refutó: (a) `auto_publish_top` está en 1 en ambas D1 (el goldfish pedía establecerlo; ahora está en §0 con el valor verificado); (b) el rolling de YT es 6, no 5 (db.ts:628; el comentario "techo YT 5" de gate.ts:187 es obsoleto).*
