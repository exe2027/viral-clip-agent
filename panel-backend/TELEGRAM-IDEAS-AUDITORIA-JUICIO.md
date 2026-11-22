# Bot Telegram AVC — Lluvia de ideas + Auditoría + Juicio (v3)

Fecha: 2026-11-18. Autor: lluvia de ideas pedida por el usuario (20:44 -03).
Alcance: solo informe. No se escribió código, no se desplegó nada, no se tocó el Worker ni el panel.

## 0. Punto de partida verificado (no opinado)

- Bot: @bitsoner_bot. v2 multi-tenant **desplegada** (Worker `0e6d795b`, migración 0023).
  Vinculación por código de 6 dígitos generado en el panel con sesión
  (`POST /api/telegram/link-code`, 10 min, un solo uso, solo hash SHA-256 en D1),
  webhook `POST /api/telegram/webhook` con secreto (`X-Telegram-Bot-Api-Secret-Token`,
  fail-closed: sin secreto configurado → 403 siempre), tabla `telegram_links`
  (`chat_id` → `tenant`, `last_sent_day`).
- Comandos hoy: `/start`, `/link <código>`, `/unlink`. Nada más.
  El webhook (`worker/src/index.ts:1260`) solo entiende esos tres: cualquier otro
  texto recibe instrucciones o la confirmación de vínculo. **Nunca datos.**
- Resumen diario: `maybeSendDailyTelegram` (`index.ts:1191`), 09:00 America/Santiago,
  solo si el Worker de ese tenant está en marcha (`r.ran`), deduplicado por
  tenant+día en `telegram_links.last_sent_day`. Usa `computeAnalytics`
  (`index.ts:1126`): medianas SOLO con mediciones reales de `clip_stats`.
- Reglas duras del usuario: $0, REGLA Nº1 no ser baneados, español primero,
  todo automático desde la web, nunca inventar métricas, aprobar cada clip
  después de ver el preview (el gate lo exige: `gate.ts:113`).
- Límite Bot API: 4096 caracteres por mensaje (el código usa 4000, `telegram.ts`).
  Bot API gratis, sin plantillas. Cron del Worker: cada minuto (`* * * * *`).

## 1. LLUVIA DE IDEAS (22 ideas)

### A. Comandos bajo demanda

#### A1. `/resumen` — analíticas cuando él quiera
Qué hace: envía en el momento el mismo resumen del diario (por plataforma:
publicados, medidos, mediana de vistas/retención, top clip).
Datos: `computeAnalytics()` + `buildAnalyticsMessage()` — ya existen, se reutilizan tal cual.
Ejemplo:
```
📊 Analíticas GTA 6 — 2026-11-18
Canales ES · @elclipdegta6
YouTube
  Publicados: 2 · Medidos: 2
  Mediana vistas: 400 · Retención: 50 %
  Top: «Clip uno» — 500 vistas
...
```

#### A2. `/cola` — cuántos vídeos hay en cola y cuáles son
Qué hace: cuenta la Cola y lista los 5 primeros por prioridad, con segmento
LIVE/VOD, idioma, score y freno visible (sin subtítulos / falta preview).
Datos: `listQueueClips` (`db.ts:762`) — statuses `en_carpeta/aprobado/programado`,
orden LIVE primero y luego `score DESC`. `ClipRow` trae `score`, `viral.priority`,
`seo_scores`, `preview_seen`, `publish_blocked`, `origin`, `language`.
Ejemplo:
```
🎬 Cola: 7 clips (LIVE 2 · VOD 5)
1. #42 «Cromos coleccionables» — Rubius · LIVE · 52,5 · SEO 94
2. #41 «…» — IlloJuan · VOD · 48,1 · ⚠️ falta preview
…
```

#### A3. `/top [n]` — a cuáles les ha ido mejor
Qué hace: ranking de clips medidos por vistas (por defecto top 5, todas las
plataformas juntas o con `/top youtube`).
Datos: el `ranking` que ya calcula `computeAnalytics()` por plataforma.
Ejemplo:
```
🏆 Top por vistas (medido)
1. «Cromos coleccionables» — YouTube — 1.240 vistas
2. «…» — TikTok — 860 vistas
Sin medición todavía en Instagram y Facebook.
```

#### A4. `/hoy` — qué pasó hoy
Qué hace: publicados hoy, usados/restantes de cuota por canal, jobs fallidos hoy.
Datos: publicados hoy se cuentan de `publish_states.publishedAt`
(`GET /api/channels`, `index.ts:1957`) y de `jobs.posted_day`. Vistas de hoy NO
existen (ver auditoría).
Ejemplo:
```
📅 Hoy 18 nov
Publicados: 3 (YT 2 · TikTok 1 · IG 0 · FB 0)
Cuota: YT 2/5 · TikTok 1/3 · IG 0/3 · FB 0/3
Fallidos hoy: 1 — #7 Facebook: límite de frecuencia
```

#### A5. `/canales` — salud de cada canal
Qué hace: por cada uno de los 8 canales (4 plataformas × ES/EN): estado de la
cuenta (`sin_crear/sin_credencial/conectada/no_conectada`), usados hoy, restantes,
tope y madurez.
Datos: `GET /api/channels` ya lo calcula todo (`index.ts:1957`).
Ejemplo:
```
📺 Canales ES
YouTube — Conectada · 2/5 hoy · quedan 3
TikTok — Conectada · 1/3 hoy · quedan 2
Instagram — No conectada · 0/3
Facebook — Conectada · 0/3
```

#### A6. `/estado` — ¿está todo andando?
Qué hace: Worker en marcha o parado, último ciclo (procesados/atascados),
fallidos pendientes, último evento, vínculo Telegram.
Datos: `worker_state` (`isWorkerRunning`, `jobs.ts:385`), `worker_events`
(`GET /api/worker/state`, `index.ts:1523`), `listJobs(state=failed)`.
Ejemplo:
```
🟢 Worker en marcha desde 08:12
Último ciclo: 2 procesados · 0 atascados
Fallidos pendientes: 1 · Avisos sin leer: 2
```

#### A7. `/clip <id>` — ficha de un clip
Qué hace: título, influencer, score/SEO por plataforma, estado por plataforma
(publicado/en cola/fallido/borrador + motivo), última medición, link de evidencia.
Datos: `getClip` + `listClipStatsForClip` + `parsePublishStates` — existen.
Ejemplo: `#42 «Cromos…» — viral 52,5 · YT publicado (link) · TikTok en cola · última medición: 500 vistas (24h)`.

#### A8. `/avisos` — últimos avisos del panel
Qué hace: las 5 notificaciones no leídas (mismo contenido que la campana del panel).
Datos: tabla `notifications`, `GET /api/notifications` (`index.ts:1704`).
Ejemplo: `⚠️ 19:57 Facebook: límite de frecuencia (clip #7) · 19:12 TikTok: captcha`.

#### A9. `/semana` — comparativa 7 días
Qué hace: publicados últimos 7 días vs 7 anteriores, mediana de vistas por plataforma.
Datos: publicados 7 días existen (`published_last_7d` en `/api/channels`).
Mediana por semana: calculable de `clip_stats.recorded_at`, pero con poquísimas
mediciones la comparación es ruido (ver auditoría).

#### A10. `/ayuda` — lista de comandos
Qué hace: lista corta de comandos disponibles y qué hace cada uno.
Datos: ninguno, texto fijo. Fácil.

### B. Avisos automáticos (el bot habla sin que le pregunten)

#### B1. Clip publicado ✅
Qué hace: un mensaje por cada publicación exitosa (plataforma + link de evidencia).
Datos: el `applyPublishResult` modo `posted` (`jobs.ts`) ya sabe cuándo ocurre;
hoy nadie avisa.
Riesgo: hasta 14 mensajes/día en el máximo teórico (YT 5 + resto 3×3, solo ES).

#### B2. Job fallido final ❌ (el usuario lo pidió explícito)
Qué hace: cuando un job llega a `failed` definitivo, avisar con clip, plataforma
y `last_error` recortado.
Datos: `markFailed` (`jobs.ts:325`) + `notify(kind="failed")`. Existe el punto
de enganche; el envío por Telegram hoy está roto (ver §2, hallazgo H1).

#### B3. Job atascado 🕐
Qué hace: cuando un job lleva >30 min en `processing`, avisar.
Datos: `detectStalledJobs` (`jobs.ts:271`) + `notify(kind="stall")`. El enganche
existe; el envío, no.

#### B4. Tope diario alcanzado / plan cubierto
Qué hace: avisar cuando un canal llega a su tope o plan del día.
Datos: el gate lo calcula (`gate.ts`, `tope diario alcanzado: X/Y`), pero hoy
solo frena en silencio (queda en `clip_events` como "frenada").

#### B5. Facebook (o cualquier plataforma) con límite de frecuencia
Qué hace: detectar `last_error` de rate-limit y avisar con el enfriamiento
recomendado ("no reintentar hasta las HH:MM").
Datos: el texto vive en `last_error` (caso real 2026-11-18 19:57: Facebook
"Limitamos la frecuencia…", 3 h de enfriamiento). Detección por subcadena = frágil.
Mejor: es un caso especial de B2, con texto distinto si `last_error` contiene
"frecuencia" / "rate limit" / "429".

#### B6. Hito viral 🎉
Qué hace: avisar cuando un clip medido supera 1.000 / 10.000 vistas.
Datos: NO hay medición automática (ver §2). Solo se sabría cuando el usuario
pegue la medición a mano, y habría que recordar qué hitos ya se avisaron.

#### B7. Cuenta desconectada / credencial fallida
Qué hace: avisar si `verify_ok=0` o la credencial falla al publicar.
Datos: `api_accounts.verify_ok/verify_message` existen; `notify(kind="credential")`
existe como tipo pero casi no se usa.

#### B8. Nuevo clip en cola con score alto
Qué hace: avisar cuando entra un clip con viralidad ≥50 (pasa la puerta).
Datos: `clips.score` / `viral.viral_score` al crear (`POST /api/clips`). Habría
que enganchar el aviso en la creación.

### C. Control desde Telegram

#### C1. `/iniciar` / `/parar` el Worker
Qué hace: mismo botón Iniciar/Detener del panel, desde el chat.
Datos: `setWorkerRunning` (`jobs.ts:390`) + `POST /api/worker/start|stop`
(`index.ts:1536`). El start además dispara el workflow del cerebro en GitHub.
Riesgo: `/parar` detiene TODA la publicación. Necesita confirmación en dos pasos.

#### C2. `/reintentar <job o clip>` — reintentar un fallo
Qué hace: volver a poner en `scheduled` un job en `failed`.
Datos: NO hay endpoint de reintento; habría que crearlo (reset de `state`,
`attempts`, `next_run_at`). El reintento automático transitorio ya existe
(máx 3, backoff 5/15/45 min, `jobs.ts`).

#### C3. `/aprobar <id>` / publicar desde Telegram
Qué hace: aprobar y/o encolar un clip desde el chat.
Datos: `POST /api/clips/:id/publish` existe (`index.ts:2044`), pero el gate exige
`preview_seen=1`. Por Telegram no se puede ver el preview. Ver §2 (ROJO).

#### C4. `/silencio` / preferencias de alertas
Qué hace: activar o apagar cada tipo de aviso (fallos sí, publicados no, etc.).
Datos: no existe; haría falta una clave en `settings` por tenant
(`telegram_alerts_*`) o una tabla `telegram_prefs`. Fácil, pero solo vale
cuando haya varios tipos de aviso que apagar.

### D. Analíticas avanzadas

#### D1. `/influencer` — a qué streamer le va mejor
Qué hace: ranking de influencers por vistas medidas de sus clips.
Datos: unible (`clip_stats` → `clips.influencer_id` → `influencers.name`),
pero con 2-3 clips medidos el ranking es azar, no señal.

#### D2. `/mejorhora` — a qué hora conviene publicar
Qué hace: cruzar hora de publicación con vistas.
Datos: NO existen. Haría falta una serie temporal de vistas por clip
(hoy solo hay UNA medición por clip+plataforma, la última) y decenas de
clips medidos. Imposible con los datos actuales.

#### D3. `/comparar` — esta semana vs la anterior
Qué hace: deltas de publicados y mediana de vistas.
Datos: publicados sí; mediana por semana, con el mismo problema de muestra
que A9. Versión honesta: solo publicados + top clip de cada semana.

---

## 2. AUDITORÍA (con evidencia del código)

### Hallazgos previos (condicionan todo)

- **H1 — El aviso automático por Telegram hoy está MUERTO.** `notify()` en
  `jobs.ts:147` lee `settings.telegram_bot_token` y `settings.telegram_chat_id`.
  Pero `effectiveSettings` (`db.ts:511-512`) dice explícito: esas claves
  "quedan fuera. NUNCA secretos". El front (`frontend/dist/app.js:2106`) lo
  confirma: "lo viejo queda ignorado por el Worker". Resultado: `notify()`
  hace `return` silencioso siempre. Los avisos de atasco, captcha, credencial
  y fallo hoy solo viven en el panel (`notifications` + `clip_events` +
  `worker_events`). **Cualquier aviso automático (B2/B3/B7) exige primero
  re-cablear `notify()` a la v2** (`telegram_links` + `TELEGRAM_BOT_TOKEN` del
  entorno). Sin ese arreglo, B2/B3 no funcionan aunque se "activen".
- **H2 — No hay medición automática de vistas.** Ningún código del Worker
  consulta YouTube, TikTok, Instagram ni Facebook para traer vistas
  (búsqueda de `metrics`/fetch de vistas en `worker/src`: solo el `JobKind`
  `"metrics"`, sin implementación de lectura). Todo lo que el bot dice de
  vistas sale de `clip_stats`, pegado a mano (`POST /api/clips/:id/stats`,
  `index.ts:1932`). Consecuencia: `/top`, `/semana`, hitos y comparativas
  solo saben lo que el usuario pegó; si no pegó nada, la respuesta honesta
  es "sin medición", no un número.
- **H3 — El webhook no tiene router de comandos.** Hoy solo hay un `if` para
  `/unlink`, uno para `/link|/start <código>` y el resto cae en instrucciones
  (`index.ts:1260`). Cada comando nuevo es una rama más en ese switch, con
  resolución de tenant SOLO por `getTelegramLinkByChat`. Eso es lo que hay
  que construir en v3, una vez.
- **H4 — El incidente Facebook 2026-11-18 sigue vigente como lección.**
  Un reintento automático sin control marcó spam y bloqueó 3 h.
  Cualquier reintento (manual o automático) que toque Facebook con
  `last_error` de límite es peligroso. El reintento transitorio automático
  ya existe y ya distingue (`retryable`, máx 3, backoff).

### Tabla de auditoría

Leyenda: Fácil = reutiliza funciones que ya existen; Media = endpoint o
tabla nueva pequeña; Difícil = datos que no existen o integración externa.
"Datos" = ¿ya existen en el Worker? "Filtra" = ¿puede ver datos de otro tenant
si se hace mal? "Spam" = ¿puede saturar el chat? "Reglas" = ¿rompe una regla?

| Idea | Factib. | Datos | Filtra | Spam/Ban | Costo | Reglas |
|---|---|---|---|---|---|---|
| A1 /resumen | Fácil | Sí, todo | No, tenant por link | Bajo (bajo demanda) | $0 | OK |
| A2 /cola | Fácil | Sí, todo | No | Bajo | $0 | OK |
| A3 /top | Fácil | Sí, escasos | No | Bajo | $0 | OK, decir "sin medición" |
| A4 /hoy | Media | Parcial (vistas hoy: NO) | No | Bajo | $0 | OK si no inventa vistas |
| A5 /canales | Media | Sí, todo | No | Bajo | $0 | OK |
| A6 /estado | Fácil | Sí, todo | No | Bajo | $0 | OK |
| A7 /clip id | Media | Sí, todo | No, si valida tenant | Bajo | $0 | OK (solo lectura) |
| A8 /avisos | Fácil | Sí, todo | No | Bajo | $0 | OK |
| A9 /semana | Media | Parcial, muestra mínima | No | Bajo | $0 | OK, sin inventar tendencia |
| A10 /ayuda | Fácil | Texto fijo | No | Nulo | $0 | OK |
| B1 publicado | Media | Sí, enganche en posted | No | Alto (hasta 14/día) | $0 | OK, pero ruidoso |
| B2 fallo final | Media | Sí, enganche existe | No, si se re-cablea bien | Bajo (pocos fallos) | $0 | OK — **exige H1** |
| B3 atascado | Media | Sí, enganche existe | No, si se re-cablea bien | Medio (sin dedup, 1 por ciclo) | $0 | OK — **exige H1 + dedup** |
| B4 tope diario | Media | Sí, gate lo sabe | No | Medio (1 por canal/día máx) | $0 | OK |
| B5 límite FB | Media | Parcial (subcadena frágil) | No | Bajo | $0 | OK como variante de B2 |
| B6 hito viral | Difícil | No (sin auto-medición) | No | Bajo | $0 | OK, pero sin datos |
| B7 cuenta caída | Media | Sí, parcial | No | Bajo | $0 | OK |
| B8 clip score alto | Media | Sí, enganche en crear | No | Medio (varios/día) | $0 | OK |
| C1 iniciar/parar | Media | Sí, todo | **SÍ si falta confirmación** | Nulo | $0 | Peligroso sin 2 pasos |
| C2 reintentar | Media | No hay endpoint | No | Medio | $0 | **ROJO si toca FB limitado** |
| C3 aprobar/publicar | Media | Gate lo impide | No | Nulo | $0 | **ROJO: sin preview** |
| C4 silencio/prefs | Fácil | No, 1 clave settings | No | — (reduce spam) | $0 | OK |
| D1 influencer | Fácil* | Sí, muestra mínima | No | Bajo | $0 | *Engañoso con n=2-3 |
| D2 mejor hora | Difícil | **NO existen** | No | Bajo | $0 | **NO: inventaría** |
| D3 comparar | Media | Parcial | No | Bajo | $0 | OK solo publicados |

### ROJO — lo que NO se debe hacer

1. **Aprobar o publicar un clip desde Telegram sin ver el preview (C3).**
   El gate exige `preview_seen=1` (`gate.ts:113`) y la regla del usuario es
   aprobar cada clip después de verlo. Telegram no puede mostrar el MP4 del
   Editor ni garantizar que se vio. Publicar a ciegas rompe la regla y
   arriesga un clip mal cortado en 4 canales. Tampoco vale "aprobar por
   texto": la aprobación sin ver no es aprobación.
2. **Auto-reintentar (o reintentar a mano sin mirar) un fallo de Facebook
   con límite de frecuencia (C2 + B5).** Es exactamente lo que causó el
   bloqueo de spam del 18 nov (lease corto → re-subidas → límite → 3 h de
   enfriamiento). Un `/reintentar` que no lea `last_error` y no bloquee los
   fallos de rate-limit es un botón de auto-baneo.
3. **Pedir contraseña, token o código del panel por Telegram.**
   Ya establecido en v2: un bot falso podría robarlas. Solo el código de
   un solo uso generado dentro del panel con sesión.
4. **Mandar el MP4 o el preview por Telegram para "aprobarlo ahí".**
   El vídeo vive en R2 privado con URL firmada de 15 min
   (`/api/public-video`); reenviarlo por Bot API es pesado, caduca, y no
   deja constancia de que se vio el preview. El preview se ve en el panel.
5. **Alertas sin deduplicación.** El cron corre cada minuto y
   `detectStalledJobs` vuelve a encontrar el mismo atasco en cada ciclo:
   sin marca de "ya avisado", un solo atasco genera 60 mensajes/hora.
   Cada aviso automático necesita una marca (fila `notifications` ya
   existe: un aviso = una fila = un envío, nunca reenviar la misma fila).
6. **Inventar métricas o "estimar" vistas, mejor hora o tendencias.**
   Sin medición, la respuesta es "sin medición". Un ranking con 2 clips
   o una "mejor hora" con 3 datos es mentira con formato de dato.
7. **Dar datos a un chat no vinculado o resolver el tenant desde el texto.**
   El tenant sale SOLO de `telegram_links` por `chat_id`. Un comando con
   `tenant=colega` escrito a mano debe ignorarse.

### Notas de seguridad multi-tenant (para todas)

- El webhook valida el secreto (`index.ts:1260`, fail-closed). No tocar eso.
- Cada consulta a D1 debe llevar `tenant_id = ?` con el tenant del vínculo.
  `computeAnalytics`, `listQueueClips`, `listJobs` y `listClipStats` ya lo
  hacen; cualquier comando nuevo debe reutilizarlas, no escribir SQL nuevo
  sin filtro de tenant.
- Nunca devolver `chat_id` completo, tokens ni credenciales en un mensaje
  (el estado ya usa `maskChatId`, solo últimos 4 dígitos).

---

## 3. JUICIO — ranking top 8

Criterio: valor para el usuario (él pidió: pedir cuando quiera + avisos de
error + cola + mejores) dividido por esfuerzo y riesgo. Todo $0.

| # | Idea | Veredicto | Por qué |
|---|---|---|---|
| 1 | **A1 `/resumen`** | **HACER AHORA** | Es lo que pidió literal ("cuando yo quiera"). Reutiliza `computeAnalytics` + `buildAnalyticsMessage`. Esfuerzo mínimo, riesgo cero. |
| 2 | **B2 Alerta job fallido final** | **HACER AHORA** | Es lo que pidió literal ("si hay un error"). El enganche existe; solo hay que re-cablear `notify()` (H1). Un aviso por fallo, poco ruido, valor máximo. |
| 3 | **B3 Alerta job atascado** | **HACER AHORA** | Mismo re-cableado que #2 (mismo `notify()`). Sin ese arreglo, un atasco solo se ve entrando al panel. Exige dedup por fila de `notifications`. |
| 4 | **A2 `/cola`** | **HACER AHORA** | Es lo que pidió literal ("cuántos vídeos están en cola"). `listQueueClips` ya existe. Respuesta corta y útil a diario. |
| 5 | **A6 `/estado`** | **HACER AHORA** | "¿Está todo andando?" en un mensaje. Todo existe (`worker_state`, `worker_events`, `listJobs`). |
| 6 | **A3 `/top`** | **HACER AHORA** | Es lo que pidió literal ("a cuáles les ha ido mejor"). Reutiliza el ranking de `computeAnalytics`. Honesto: dice "sin medición" donde no hay. |
| 7 | **A4 `/hoy`** | **HACER DESPUÉS** | Útil, pero las "vistas de hoy" no existen y habría que dejarlo claro. Publicados + cuotas sí existen. Segunda tanda. |
| 8 | **A5 `/canales`** | **HACER DESPUÉS** | Útil para diagnosticar, pero largo (8 canales) y el usuario lo ve en el panel. Segunda tanda, con formato compacto. |

### HACER DESPUÉS (no en el top 8)

- **A7 `/clip <id>`, A8 `/avisos`, A10 `/ayuda`** — fáciles, poco urgentes; `/ayuda`
  conviene meterla en la v3 porque sin ella nadie descubre los comandos.
- **B1 Clip publicado** — solo como opt-in (`/silencio` inverso): por defecto
  apagado, porque hasta 14 mensajes/día es ruido. Primero fallos y atascos.
- **B4 Tope diario, B7 Cuenta caída, B8 Clip con score alto** — útiles, pero
  cada uno es un enganche nuevo; después de que B2/B3 funcionen.
- **C1 `/iniciar` / `/parar`** — solo con confirmación en dos pasos
  (`/parar` → "¿Seguro? Responde SÍ" → ejecuta). Nunca sin confirmar.
- **C4 Preferencias de alertas** — cuando haya más de 2 tipos de aviso.
- **A9 `/semana`, D3 `/comparar`** — solo publicados + top; sin tendencias
  hasta tener ≥30 clips medidos.
- **C2 `/reintentar`** — solo con lista blanca: únicamente jobs `failed` con
  `retryable=1` y `last_error` SIN "frecuencia"/"rate limit"/"429", una sola
  vez por job, y nunca en Facebook limitado. Si no se puede garantizar eso,
  no se hace.

### NO HACER (ahora, y probablemente nunca en esta forma)

- **C3 Aprobar/publicar desde Telegram** — rompe la regla del preview (ROJO 1).
- **D2 `/mejorhora`** — los datos no existen; cualquier respuesta sería inventada.
- **D1 `/influencer` como ranking** — con 2-3 clips medidos es azar; revisitar
  con ≥10 clips medidos por influencer.
- **B6 Hito viral** — sin medición automática no hay hito que detectar; cuando
  haya auto-medición (otro proyecto, con APIs por plataforma), se revisita.
- **Auto-fetch de vistas desde YouTube/TikTok/Meta** — no es un comando, es
  otro proyecto: credenciales por plataforma, cuotas, App Review de Meta y
  auditoría de TikTok pendientes. No entra en la v3.

---

## 4. Diseño mínimo del top 3

### 4.1 `/resumen` — comando bajo demanda

- **Comando exacto:** `/resumen` (también vale `/resumen` sin argumentos; sin
  argumentos extra).
- **Respuesta exacta:** idéntica a `buildAnalyticsMessage(platforms, day)` del
  diario (mismo texto, mismo día Santiago). Si no hay vínculo: el mensaje de
  instrucciones actual. Si no hay datos: "Sin medición todavía…".
- **Endpoint / handler nuevo:** ninguno de panel. Solo una rama nueva en el
  webhook (`index.ts:1260`): si `text === "/resumen"`, resolver tenant por
  `getTelegramLinkByChat(chatId)` → si no hay vínculo, instrucciones;
  si hay, `computeAnalytics(env, tenant)` → `buildAnalyticsMessage(...)` →
  `sendTelegram(env, chatId, text)`. Límite 4000 caracteres ya aplicado.
- **Tabla D1:** ninguna.
- **Tests:** chat vinculado recibe exactamente el mismo texto que el diario;
  chat no vinculado recibe instrucciones y ningún dato; mensaje truncado a
  ≤4000; `/resumen` con texto extra no se ejecuta (o se ignora).

### 4.2 Alerta job fallido final (+ atascado, mismo cableado)

- **Comando:** ninguno (automático). El usuario solo recibe el aviso.
- **Respuesta exacta:**
  ```
  ❌ Fallo al publicar — clip #7 «Título» · Facebook
  Motivo: Limitamos la frecuencia con la que puedes publicar…
  No se reintentará solo. Revisa el panel o espera al enfriamiento.
  ```
  Para atasco:
  ```
  🕐 Atascado — clip #12 «Título» · YouTube, en processing más de 30 min.
  Revisa el panel: Tracking.
  ```
- **Endpoint / handler nuevo:** ninguno de panel. Cambio en `jobs.ts:147`
  `notify()`: borrar la lectura de `settings.telegram_bot_token/chat_id`
  (muerta, H1) y sustituirla por: `getTelegramLinkByTenant(db, tenant)` +
  `sendTelegram(env, link.chat_id, "🎬 Clips GTA 6: " + message)` con el
  `TELEGRAM_BOT_TOKEN` del entorno. Importar `sendTelegram` desde
  `./telegram` (sin ciclo: `telegram.ts` solo importa el tipo `Env`).
  **Regla de dedup:** `notify()` inserta primero la fila en `notifications`
  y envía una sola vez por fila; nunca reenviar. Solo kinds
  `failed`/`stall`/`captcha`/`credential` (no `info`). Si el envío falla,
  `logWorkerEvent` y no reintentar en bucle.
  Variante B5: si `last_error` contiene "frecuencia", "rate limit" o "429",
  el texto añade "No reintentes hasta que pase el enfriamiento".
- **Tabla D1:** ninguna (reutiliza `notifications` + `telegram_links`).
- **Tests:** `notify()` con tenant vinculado envía solo a SU chat;
  tenant sin vínculo no envía nada y no falla; `notify()` no lee ningún
  secreto de `settings`; un fallo de Facebook con "frecuencia" lleva el
  aviso de no reintentar; doble llamada a `notify()` con la misma fila no
  duplica el envío (o, mínimo: dos fallos distintos generan dos avisos,
  un mismo fallo uno solo).

### 4.3 `/cola` — cuántos vídeos hay en cola

- **Comando exacto:** `/cola` (sin argumentos).
- **Respuesta exacta:**
  ```
  🎬 Cola: 7 clips (LIVE 2 · VOD 5)
  1. #42 «Cromos coleccionables» — LIVE · 52,5 · SEO 94
  2. #41 «…» — VOD · 48,1 · ⚠️ falta preview
  3. #39 «…» — VOD · 45,0 · ⚠️ sin subtítulos
  4. #38 «…» — LIVE · 44,2 · SEO 91
  5. #35 «…» — VOD · 41,8 · SEO 90
  Y 2 más en cola. Ver en el panel: https://avc-em9.pages.dev
  ```
  Sin clips: `🎬 Cola vacía. No hay clips pendientes de publicar.`
- **Endpoint / handler nuevo:** ninguno de panel. Rama nueva en el webhook:
  resolver tenant por chat → `listQueueClips(db, tenant)` (máx 5 en el
  mensaje) → formatear con `score`, `viral.priority`, `seo_scores`,
  `preview_seen`, `publish_blocked`, `origin`, `language`. Contar LIVE/VOD
  por `origin === "live"`. Títulos recortados a ~40 caracteres.
- **Tabla D1:** ninguna.
- **Tests:** cola vacía responde "Cola vacía"; cola con 7 muestra 5 + "Y 2 más";
  clip con `publish_blocked=1` muestra "sin subtítulos"; clip con
  `preview_seen=0` muestra "falta preview"; un tenant nunca ve la cola de
  otro (dos tenants, dos colas distintas).

---

## 5. Recomendación — qué implementar primero (v3 pequeña)

**Hacer una v3 con solo 3 cosas, en este orden:**

1. **Router de comandos en el webhook + `/resumen` + `/cola` + `/estado` + `/top` + `/ayuda`.**
   Es un solo cambio (una función de dispatch en el webhook) que desbloquea
   todos los comandos de lectura. Solo lectura, sin riesgo de publicar nada
   ni de filtrar datos: cada comando reutiliza una función que ya filtra por
   tenant. Es lo que el usuario pidió y se prueba con los tests del
   `telegram-link.test.ts` ampliado.
2. **Re-cablear `notify()` a la v2 (H1) y con ello activar B2 + B3.**
   Es el único cambio que convierte los avisos del panel en avisos reales.
   Sin esto, todo lo demás de "avisos" es humo. Incluir el dedup por fila
   de `notifications` y el texto especial de rate-limit.
3. **Nada de control en v3.** Ni aprobar, ni publicar, ni reintentar, ni
   parar el Worker desde Telegram. Eso va después, con confirmación en dos
   pasos y lista blanca, cuando los comandos de lectura y los avisos ya
   estén probados en uso real una semana.

Con eso, el bot pasa de "resumen a las 9" a "pregunta cuando quieras y te
avisa si algo se rompe", sin tocar la publicación, sin inventar una sola
métrica y sin gastar un peso.

---

## 6. Segunda vuelta salvaje (sin filtro)

Fecha: 2026-11-18. Pedida por el usuario (20:53 -03) porque la primera vuelta
solo recogió lo que él pidió literal. Regla de esta vuelta: SIN filtro de
factibilidad, SIN preguntar si los datos existen, SIN limitarse a lo que el
Worker ya hace. Si una idea necesita datos nuevos, APIs nuevas o un proyecto
aparte, se pone igual y se anota qué haría falta. Nada de esto está auditado
ni aprobado: es materia prima divergente. No se escribió código.

40 ideas nuevas, ninguna repetida de la §1 (no están /resumen, /cola, /top,
/hoy, /canales, /estado, /clip, /avisos, /semana, /ayuda, ni los avisos
B1–B8, ni el control C1–C4, ni D1–D3).

### 6.1 Buscar y descubrir clips

1. **`/buscar <texto>`** — busca clips por palabra del título o de la transcripción. Le da: encontrar "ese clip del coche" sin entrar al panel. Falta: índice de texto sobre transcripciones. Realista.
2. **`/frase "<frase>"`** — busca el momento exacto donde un streamer dice esa frase. Le da: cazar reacciones citables. Falta: transcripciones con timestamps ya existen en parte; hace falta búsqueda por frase. Realista.
3. **`/momento <influencer> <tema>`** — ej. `/momento rubius helicóptero`: clips de ese streamer sobre ese tema. Le da: armar una serie temática. Falta: etiquetas de tema por clip (hoy solo hay score/SEO). Realista.
4. **`/de <influencer>`** — lista los clips pendientes de ese streamer, sin ranking. Le da: revisar solo a IlloJuan o solo a Rubius. Falta: nada, filtrar la cola por `influencer_id`. Realista.
5. **`/azar`** — te muestra un clip aleatorio de la cola para revisar. Le da: romper la rutina de revisar siempre en el mismo orden. Falta: nada, un `ORDER BY RANDOM()` con filtro de tenant. Realista.
6. **`/sorprendeme`** — el clip con el score más raro o inesperado (alto viral pero bajo SEO, o al revés). Le da: descubrir joyas que el orden normal esconde. Falta: nada, una consulta sobre los scores que ya existen. Realista.
7. **`/envivo`** — qué streamers de la lista están en directo ahora mismo. Le da: saber si vale la pena mirar el directo para cazar un clip. Falta: consultar Twitch/YouTube en vivo (API o scraping). Loca.
8. **`/sinpublicar`** — clips que llevan más de X días en cola sin salir. Le da: detectar los olvidados antes de que caduquen. Falta: nada, filtrar por `created_at` y estado. Realista.

### 6.2 El bot como copiloto que sugiere

9. **`/quepublico`** — sugiere los 3 mejores clips para publicar ahora mismo. Le da: no tener que decidir, solo elegir entre 3. Falta: una regla de recomendación (score + frescura + variedad de influencer). Realista.
10. **`/titulo <id>`** — propone 3 títulos alternativos para ese clip. Le da: títulos con gancho sin pensarlos. Falta: un modelo de lenguaje (Gemini gratis ya existe en el proyecto). Realista.
11. **`/descripcion <id>`** — genera descripción + hashtags para el clip. Le da: publicar más rápido sin escribir. Falta: modelo de lenguaje + plantillas por plataforma. Realista.
12. **`/miniatura <id>`** — sugiere qué frame usar y qué texto ponerle. Le da: miniaturas que se pinchan. Falta: extraer frames del vídeo y un modelo que elija. Loca.
13. **`/mejororden`** — orden ideal de publicación del día (qué clip a qué hora y en qué plataforma). Le da: un plan del día en un mensaje. Falta: planificador que cruce scores, cuotas y frescura. Realista.
14. **`/porque <id>`** — explica en palabras simples por qué ese clip tiene ese score. Le da: entender al algoritmo en vez de obedecerlo a ciegas. Falta: desglose legible de los 7 componentes del score. Realista.
15. **`/reescribir <id>`** — reescribe un título aburrido en 3 versiones más virales. Le da: rescatar clips con buen momento y mal título. Falta: modelo de lenguaje. Realista.

### 6.3 Interacción con la audiencia / comunidad

16. **`/encuesta`** — el bot te propone una encuesta entre 2 clips y tú eliges cuál publicar primero. Le da: decidir en 10 segundos sin abrir el panel. Falta: nada, dos clips + botones inline de Telegram. Realista.
17. **`/duelo`** — duelo entre 2 clips: votas el ganador y el perdedor baja de prioridad. Le da: ordenar la cola jugando. Falta: escribir el voto como ajuste de prioridad. Loca.
18. **`/tendencia`** — qué está buscando la gente sobre GTA 6 hoy. Le da: publicar lo que la gente ya está buscando. Falta: Google Trends / búsqueda de tendencias (API externa). Loca.
19. **`/comentarios <id>`** — resume los comentarios del clip ya publicado (qué gusta, qué critican). Le da: saber si un clip pegó sin leer 200 comentarios. Falta: leer comentarios por API de cada plataforma + resumirlos. Loca.
20. **`/comunidad`** — qué clip piden los seguidores o qué tema se repite en los comentarios. Le da: publicar lo que la audiencia quiere, no lo que toca. Falta: lo mismo que 19, agregado por tema. Loca.

### 6.4 Control y automatización avanzada

21. **`/programar <id> <hora>`** — programa ese clip para esa hora. Le da: dejar el día listo de una sentada. Falta: campo de hora programada por clip + que el Worker lo respete. Realista.
22. **`/pausar <plataforma>`** — pausa solo TikTok (o solo Facebook) por hoy. Le da: frenar un canal sin parar todo el sistema. Falta: pausa por plataforma en `worker_state` (hoy es global). Realista.
23. **`/vacaciones <días>`** — modo vacaciones: pausa todo X días y avisa al volver. Le da: desconectar sin que se publique nada raro. Falta: pausa con fecha de fin + mensaje al reactivar. Realista.
24. **`/regla <texto>`** — crea una regla propia, ej. "nunca más de 2 clips de Rubius al día". Le da: que el bot publique como tú publicarías. Falta: motor de reglas por tenant. Loca.
25. **`/prioridad <id> <alta|baja>`** — sube o baja a mano la prioridad de un clip. Le da: imponer tu criterio sobre el score. Falta: un campo de ajuste manual que pese en el orden. Realista.
26. **`/modo <auto|manual>`** — cambia entre publicar solo lo aprobado por ti o dejar que el sistema proponga. Le da: un interruptor de confianza. Falta: un flag por tenant que el gate lea. Realista.

### 6.5 Analíticas creativas

27. **`/racha`** — racha de días publicando sin fallar (ej. "12 días seguidos"). Le da: motivación y constancia visible. Falta: contar días con al menos una publicación desde `publish_states`. Realista.
28. **`/resumenvoz`** — resumen semanal en audio, narrado. Le da: escuchar las analíticas en el coche. Falta: texto a voz (TTS) + envío de audio por Bot API. Loca.
29. **`/grafico`** — gráfico de vistas por día como imagen. Le da: ver la tendencia de un vistazo. Falta: generar un PNG/SVG en el Worker + enviarlo como foto. Realista.
30. **`/prediccion <id>`** — predice cuántas vistas podría tener ese clip. Le da: apostar por los que más prometen. Falta: un modelo simple entrenado con clips medidos (hoy hay poquísimos datos). Loca.
31. **`/premios`** — premios de la semana: mejor reacción, mejor momento, más compartido. Le da: celebrar lo que funciona y repetirlo. Falta: reglas de premio sobre mediciones reales. Realista.
32. **`/versus`** — compara dos plataformas: dónde rinde mejor cada tipo de clip. Le da: dejar de publicar lo mismo en todas partes. Falta: agregar mediciones por plataforma y por tipo de clip. Realista.
33. **`/resumensemana`** — newsletter semanal automática con lo mejor, lo peor y qué probar la semana que viene. Le da: una retrospectiva sin hacerla a mano. Falta: plantilla semanal + agregación de 7 días. Realista.

### 6.6 Alertas inteligentes (oportunidades, no solo errores)

34. **Alerta "influencer en vivo ahora"** — "Rubius acaba de prender stream". Le da: no perderse el directo del que salen los mejores clips. Falta: vigilancia de directos (API Twitch/YouTube). Loca.
35. **Alerta "pico de vistas"** — un clip publicado está explotando y conviene aprovecharlo. Le da: reaccionar a tiempo (fijarlo, hacer otro parecido). Falta: medición automática de vistas, que hoy no existe. Loca.
36. **Alerta "oportunidad en cola"** — hay un clip con potencial viral que lleva 2 días sin publicarse. Le da: que no se te pudra un buen clip en la cola. Falta: nada, una revisión diaria de cola por score y antigüedad. Realista.
37. **Alerta "tendencia GTA 6"** — un tema de GTA 6 está en tendencia y tienes un clip de eso. Le da: publicar justo cuando la gente lo busca. Falta: tendencias externas + cruce con la cola. Loca.
38. **Alerta "silencio"** — llevas 24 h sin publicar nada. Le da: que el bot te empuje cuando el canal se para. Falta: nada, contar horas desde la última publicación. Realista.

### 6.7 Multi-usuario y equipo

39. **`/equipo`** — qué hizo hoy cada uno (sebastian, chacal): cuántos revisaron, aprobaron, publicaron. Le da: ver el trabajo del equipo sin preguntar. Falta: registro de acciones por usuario (hoy no se guarda quién hizo qué). Realista.
40. **Aprobar en equipo** — el bot pide el voto a sebastian y a chacal antes de publicar un clip polémico. Le da: que nadie publique solo un clip arriesgado. Falta: votación por botones inline + quorum por tenant. Loca.
41. **`/turno`** — reparte la revisión de la cola entre los usuarios del equipo. Le da: que la cola no dependa siempre de la misma persona. Falta: asignación de clips por usuario. Loca.
42. **Modo invitado solo-lectura** — un invitado puede preguntar /resumen o /cola pero no tocar nada. Le da: enseñar el canal sin dar el control. Falta: rol por vínculo (lectura vs control). Realista.

### 6.8 Cosas locas pero útiles

43. **El bot te manda el clip por Telegram para verlo** — te llega el vídeo y lo ves en el chat antes de aprobar. Le da: revisar sin abrir el panel. Falta: enviar el MP4 desde R2 por Bot API (pesado, URL firmada de 15 min). Loca.
44. **Hablar en lenguaje natural** — escribir "¿qué me recomiendas publicar hoy?" sin comando y que el bot entienda. Le da: usar el bot como se habla con una persona. Falta: modelo de lenguaje que interprete la intención y llame a la función correcta. Loca.
45. **El bot aprende tus gustos** — recuerda qué clips te gustan y cuáles saltas, y ordena la cola según eso. Le da: una cola cada vez más tuya. Falta: guardar tus decisiones y ajustar el orden con ellas. Loca.
46. **`/voz`** — que el bot responda con una nota de voz. Le da: respuestas sin leer. Falta: texto a voz + envío de audio. Loca.
47. **`/recordatorio <hora> <texto>`** — "avísame a las 18:00 si no revisé la cola". Le da: un empujón a la hora que tú eliges. Falta: recordatorios programados por tenant en D1 + revisión en el cron. Realista.
48. **Modo entrenador** — te felicita la racha y te reta si no publicas ("llevas 2 días sin publicar, ¿qué pasa?"). Le da: constancia con humor. Falta: nada nuevo, mensajes sobre la racha y el silencio. Realista.

Total segunda vuelta: 48 ideas nuevas. Ninguna de la §1 repetida. Sin auditar: la auditoría y el juicio de estas 48 quedan para el paso siguiente, si el usuario lo pide.

---

## 7. v3 IMPLEMENTADA (2026-11-18, código listo, SIN desplegar)

El usuario aprobó la v3 pequeña el 2026-11-18 21:03 («Sí, súmala
a la v3» para ordenar la Cola, idea 49) y añadió después: alertas
de cuenta desconectada (B7, 21:07) y felicitación por hito viral
(B6, 21:08). Todo implementado en el Worker + front, sin
desplegar. Verificación: `bun test` 275 pass / 0 fail (eran 258;
17 nuevos), `tsc` limpio, migración 0024 aplicada en local.

### Lo que se hizo (archivos)

- `migrations/0024_clip_manual_rank.sql` (nueva): `clips.manual_rank INTEGER NULL` (NULL = automático).
- `worker/src/db.ts`: `listQueueClips` ordena ahora en JS
  (segmento LIVE → manual_rank → score → fecha); `queueClipCompare`
  exportado; `moveQueueClip` (`first`/`up`/`down`/`auto`) con
  materialización del orden del segmento al subir/bajar; nunca
  toca `score` ni `viral`; por tenant; un VOD manual nunca pasa
  por encima de un LIVE.
- `worker/src/telegram.ts`: `helpMessage`, `parseTelegramCommand`,
  `buildQueueMessage`, `buildTopMessage(platforms, limit)`,
  `buildStatusMessage`, `isRateLimitText`, `QueueClipView`.
- `worker/src/index.ts`: `handleTelegramCommand` (router v3) y su
  cableado en el webhook (chat vinculado → comandos; no vinculado
  → solo instrucciones); `maybeNotifyViralMilestone` + enganche
  en `POST /api/clips/:id/stats`; `notify` y `markFailed` del
  puente ahora pasan `env` (B2/B7).
- `worker/src/jobs.ts`: `notify(db, input, tenant, env?)`
  re-cableado a la v2 (H1 resuelto; el camino muerto por settings
  se borró); `detectStalledJobs`, `markFailed`, `markDraft` y
  `applyPublishResult` propagan `env`; kind nuevo `milestone`
  (hito = buena noticia en Tracking, no fallo).
- `frontend/dist/index.html` + `app.js`: Ajustes → Telegram
  menciona los comandos v3.
- Tests: `telegram-link.test.ts` (+ mock FakeD1 ampliado: `IN`,
  literales, `<=`, literales en `SET`) y `queue-order.test.ts`
  (SQLite real, columna `manual_rank`).

### Comandos v3

`/resumen`, `/cola`, `/estado`, `/top [n]`, `/ayuda`,
`/primero|/subir|/bajar|/auto <id>`.

### Avisos v3 (todos con dedup una fila `notifications` = un envío)

B2 fallo final (+ «No reintentes» si es rate-limit), B3 atascado
>30 min, B7 cuenta desconectada/credencial fallida (enganche:
`verifyApiAccount`, único punto donde `verify_ok` cambia),
B6 hito viral honesto (1.000 y 10.000 vistas, solo al pegar
medición manual, dedup por clip+plataforma+umbral). `info`
nunca se envía por Telegram.

### NO hecho (fuera de v3, como se acordó)

Aprobar/publicar/reintentar/parar el Worker desde Telegram,
`/mejorhora`, mandar MP4 por Telegram, pedir contraseñas por
Telegram.

### Para desplegar (lo hace el agente principal, no esta v3)

`wrangler d1 migrations apply clips-avc` (remoto, aplica la
0024), desplegar Worker y Pages. Sin secretos nuevos, sin
re-vincular. Verificación en vivo: ver
`TELEGRAM-VINCULACION.md` §v3 (pasos 1–7).
