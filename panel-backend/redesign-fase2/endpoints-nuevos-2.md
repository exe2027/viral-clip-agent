# Endpoints nuevos — Backend Dev 2 (fase 2)

- **Fecha:** 2026-11-17
- **Alcance:** solo backend TypeScript del Worker. Sin tocar el front, sin tocar `publishGate`, fórmulas de viralidad/SEO ni reglas de publicación, sin tocar secretos, sin tocar la migración 0013.
- **Archivos tocados:**
  - `worker/src/index.ts` — 6 rutas nuevas (Editor, Analíticas, Canales/Salud) + validación y serialización.
  - `worker/src/db.ts` — `updateClip`, acceso a `clip_stats` (`listClipStats`, `listClipStatsForClip`, `getClipStat`, `createClipStat`) y `listClipPublishStates`.
  - `worker/test/endpoints-nuevos-2.test.ts` (nuevo) — 30 tests con mock D1 en memoria (mismo patrón que el Dev 1).
- **Verificación:** `bun test` 68/68 (38 anteriores + 30 nuevos), `tsc --noEmit` OK.
- **Migraciones: NINGUNA nueva.** No hace falta 0014: todos los campos que usan estos endpoints ya existen en 0001–0013 (`clips.preview_seen`, `subtitle_style`, `seo_title`, `seo_description`, `seo_tags`, `thumb_text`, `platforms`, `scheduled_for`, `start_sec`, `end_sec`; `clip_stats` con `tenant_id` desde la 0012; `clip_events`; `api_accounts`). Nada que aplicar en D1 antes de desplegar.
- **Sin desplegar:** el despliegue lo hace el coordinador.

## Reglas comunes a todos los endpoints nuevos

- **Auth:** todos exigen sesión (`requireSession`: cookie firmada o `Authorization: Bearer`). Sin sesión: `401 {ok:false, error:"Inicia sesión primero."}`.
- **Tenant:** el tenant viene SIEMPRE de la sesión verificada, nunca del body. Cada consulta filtra por `tenant_id = <tenant de la sesión>`.
- **Sin datos inventados:** sin filas, lista vacía (`[]`) o `null` donde aplica. En Analíticas, sin medición la mediana es `null` ("sin medición"), **nunca 0**. En Canales, sin dato real la salud es `"sin_datos"`, nunca un "OK".
- **Errores:** `{ok:false, error:"<mensaje>"}` con el código HTTP que corresponde (400 validación, 401 sin sesión, 404 no existe o es de otro tenant).
- **Mapeos de nombres (la API acepta ambos, la BD guarda uno):** `thumbnail_text` → columna `thumb_text`; `target_platforms` → columna `platforms` (JSON); `schedule` → columna `scheduled_for`; `subscribers` → columna `new_subs`; `retention` → columna `retention_pct`. En las respuestas de mediciones salen tanto `new_subs` como `subscribers` con el mismo valor.

## 1. Editor — tabla `clips`

### `POST /api/clips/:id/preview-seen`

Levanta el freno de `publishGate` ("falta ver el preview en el Editor"): era la brecha nº 1 del inventario — ningún endpoint escribía `preview_seen` y ningún clip con `preview_seen = 0` podía publicarse.

- **Request:** sin body.
- **Response 200:** `{ok:true, clip, already_seen:false}` con la fila actualizada (`preview_seen = 1`).
- **Idempotente:** si el preview ya estaba visto, responde `{ok:true, clip, already_seen:true}` y NO duplica el evento de tracking.
- **Tracking:** al marcarlo (transición 0 → 1) registra un evento en `clip_events` con `step: "revision"`, `actor` = usuario de la sesión (`session.sub`, p. ej. `seba`) y mensaje `"Preview visto en el Editor."`. La hora es la del evento (`at`).
- **Errores:** 404 si el clip no existe o es de otro tenant; 401 sin sesión.

### `PATCH /api/clips/:id`

Edita SOLO estos campos; cualquier otro campo del body se ignora (un body sin ningún campo válido es 400, no un guardado silencioso).

| Campo del body | Columna | Validación |
|---|---|---|
| `subtitle_style` | `subtitle_style` | `tiktok` \| `highlight` \| `karaoke` |
| `seo_title` | `seo_title` | texto, máx. 200 caracteres |
| `seo_description` | `seo_description` | texto, máx. 5000 caracteres |
| `seo_tags` | `seo_tags` | texto o lista de textos (se guarda unida por ", "), máx. 2000 caracteres |
| `thumbnail_text` (o `thumb_text`) | `thumb_text` | texto, máx. 200 caracteres |
| `target_platforms` (o `platforms`) | `platforms` (JSON) | lista; cada elemento `youtube_shorts` \| `tiktok` \| `instagram_reels` \| `facebook_reels` |
| `schedule` (o `scheduled_for`) | `scheduled_for` | fecha ISO 8601 válida; `null` o `""` la quita |
| `duration` | `end_sec = start_sec + duration` | entero de segundos, **regla de oro: 36–60** (ver abajo) |

- **Regla de oro de duración (400 si se incumple, no se guarda nada):** 25–35 s (incluye exactamente 30 s) → `"Regla de oro: nunca 30 segundos. El rango 25–35 s está prohibido (mínimo 36 s, tope 60 s)."`; menos de 36 s (fuera de 25–35) → por debajo del mínimo; más de 60 s → por encima del tope. El `start_sec` no se toca: solo se mueve `end_sec`.
- **Preview invalidado:** si la duración cambia de verdad y el preview estaba visto, `preview_seen` vuelve a 0 y la respuesta lo dice: `preview_reset: true` + `preview_note: "La duración cambió: el preview guardado deja de valer; hay que ver el preview otra vez antes de publicar."` Si la duración nueva es igual a la actual, no hay reset (`preview_reset: false`).
- **Response 200:** `{ok:true, clip, preview_reset, preview_note?}` con la fila actualizada.
- **Errores:** 404 si no existe o es de otro tenant; 400 validación o sin campos válidos; 401 sin sesión.

## 2. Analíticas — tabla `clip_stats`

Tabla usada: **`clip_stats`** (migración 0006; `tenant_id` en 0012). Columnas: `id, tenant_id, clip_id, platform, views, retention_pct, shares, saves, comments, new_subs, window_label, recorded_at`. Los opcionales no pegados quedan `NULL` (sin medir), nunca 0.

### `POST /api/clips/:id/stats`

Guarda una medición real pegada por el usuario.

- **Request (JSON):** `{platform, views, retention?, shares?, saves?, comments?, subscribers?, window?, recorded_at?}`.
  - `platform` (obligatorio): `youtube` | `tiktok` | `instagram` | `facebook` (se aceptan también los nombres del panel: `youtube_shorts`, `instagram_reels`, `facebook_reels`; se normalizan a la clave API).
  - `views` (obligatorio): entero ≥ 0.
  - `retention` (opcional, alias `retention_pct`): número 0–100; ausente = `null` (sin retención medida).
  - `shares`, `saves`, `comments`, `subscribers` (opcionales, `subscribers` admite alias `new_subs`): enteros ≥ 0; ausentes = `null`.
  - `window` (opcional, por defecto `7d`): `24h` | `48h` | `7d`.
  - `recorded_at` (opcional, por defecto ahora): milisegundos o fecha ISO 8601.
- **Response 201:** `{ok:true, stat:{id, clip_id, platform, views, retention_pct, shares, saves, comments, new_subs, subscribers, window_label, recorded_at}}`.
- **Errores:** 404 si el clip no existe o es de otro tenant; 400 validación (vistas negativas o ausentes, retención fuera de 0–100, plataforma o ventana inválida); 401 sin sesión.

### `GET /api/clips/:id/stats`

- **Request:** sin body.
- **Response 200:** `{ok:true, stats:[...]}` con las mediciones del clip ordenadas por `recorded_at DESC, id DESC`. Sin mediciones: `{ok:true, stats:[]}`.
- **Errores:** 404 si el clip no existe o es de otro tenant; 401 sin sesión.

### `GET /api/analytics`

Resumen por plataforma a partir de mediciones reales y de `publish_states`.

- **Request:** sin body.
- **Response 200:** `{ok:true, platforms:[...], unmeasured:[...]}`.
  - Por plataforma (`youtube`, `tiktok`, `instagram`, `facebook`, siempre las 4):
    - `median_views`, `median_retention`: medianas de la **última medición por clip** (si un clip tiene varias mediciones en una plataforma, solo cuenta la más reciente por `recorded_at`). Sin medición: `null` = "sin medición", **nunca 0**. La mediana de retención solo usa mediciones con retención no nula; la de vistas, solo con vistas no nulas.
    - `measured_clips`: clips con al menos una medición en esa plataforma. `published_clips`: clips con `publish_states[plataforma].state === "publicado"`. `has_data`: `measured_clips > 0`.
    - `ranking`: última medición por clip, ordenada por vistas DESC, desempate por retención DESC. Cada fila: `{title, id, clip_id, platform, views, retention_pct, shares, saves, comments, new_subs, subscribers, window_label, recorded_at}`. Sin mediciones: `[]`.
  - `unmeasured`: clips publicados en alguna plataforma (`publish_states` con estado `publicado`) sin ninguna medición para esa plataforma: `[{clip_id, title, platform}]`. Sin casos: `[]`.
  - Las mediciones huérfanas (clip que ya no existe en el tenant) no entran en ningún cálculo.
- **Errores:** 401 sin sesión.

## 3. Canales/Salud

### `GET /api/channels`

Una entrada por plataforma (`youtube`, `tiktok`, `instagram`, `facebook`), siempre las 4, en ese orden. Todo sale de datos reales del tenant: cuentas API (`api_accounts`) y estados de publicación (`clips.publish_states`).

- **Request:** sin body.
- **Response 200:** `{ok:true, channels:[{...}]}`. Por canal:
  - `account_status`: `conectada` | `sin_credencial` | `no_conectada` | `sin_crear` — los mismos 4 estados de `GET /api/accounts` (`verify_ok === 1` → conectada; sin credencial cifrada ni legacy → sin credencial; resto → no conectada; sin fila de cuenta API → sin crear). **Meta unificada:** Instagram y Facebook comparten la misma cuenta API `platform: "meta"` (mismo `id` en ambas).
  - `account`: `{id, label, verified_at, verify_message, has_credential}` o `null` si no hay cuenta. Nunca la credencial ni su valor.
  - `daily_cap`, `daily_cap_fixed: true`: tope fijo del Worker (`publishGate`): YouTube 5/día, resto 3/día. `daily_plan`, `daily_plan_fixed: true`: plan del día fijo (YouTube 3, resto 2).
  - `used_today`: publicaciones reales de hoy (día America/Santiago) en `publish_states` con estado `publicado` y `publishedAt` de hoy. `remaining_today`: `max(0, daily_cap - used_today)`.
  - `published_total`: publicaciones reales totales en `publish_states` (estado `publicado`). `published_last_7d`: las de los últimos 7 días (por día America/Santiago de `publishedAt`).
  - `publish_window: "18:00–24:00"`, `publish_window_fixed: true`, `min_spacing_hours: 2`: constantes fijas de `publishGate`, etiquetadas como tales.
  - `health: "sin_datos"` + `health_note`: el Worker aún no expone salud por plataforma (latencia, errores del adaptador). Nunca un "OK" inventado.
- **Errores:** 401 sin sesión.

## Tests

`worker/test/endpoints-nuevos-2.test.ts` — 30 tests, mock D1 en memoria (mismo patrón que el Dev 1). Cubren:

- Preview-seen: 401 sin sesión; 404 (inexistente o de otro tenant); marca `preview_seen = 1` y devuelve el clip; evento en tracking con `step: "revision"`, `actor` = usuario de la sesión y hora; segunda llamada `already_seen: true` sin duplicar el evento; aislamiento (el otro tenant no lo marca ni ve el cambio).
- PATCH clip: 401; 404; edita solo los campos permitidos (los demás se ignoran, body sin campos válidos → 400); regla de oro (30, 28, 20 y 61 → 400 con "Regla de oro" en 25–35, sin guardar nada); duración 40 válida (`end_sec = start_sec + 40`); cambio de duración con preview visto → `preview_seen = 0`, `preview_reset: true` y nota; misma duración no resetea; `target_platforms`/`schedule`/estilo (válidos e inválidos, limpiar `schedule` con `null`).
- Stats: 401; 201 con medición completa; opcionales ausentes → `null` (nunca 0); normalización de plataforma del panel (`instagram_reels` → `instagram`); validación 400 (vistas negativas o ausentes, retención > 100, plataforma y ventana inválidas) sin guardar nada; 404 (inexistente o de otro tenant); `GET /api/clips/:id/stats` lista y vacía, sin datos del otro tenant.
- Analytics: 401; sin mediciones → medianas `null` (nunca 0), `ranking: []`, `unmeasured: []`; medianas con mediciones reales (manda la última medición por clip; mediana par e impar); ranking ordenado por vistas con desempate por retención; publicados sin medición en `unmeasured` (con medición, no); mediciones de otro tenant no cuentan.
- Channels: 401; sin cuentas ni clips → 4 canales `sin_crear`, cuota entera, salud `sin_datos`, sin credenciales en la respuesta; estados reales de cuenta API y Meta unificada (mismo `id` en Instagram y Facebook); cuota y publicados contados de `publish_states` reales (hoy, 3 días, 10 días); aislamiento por tenant.

## Qué quedó SIN endpoint real (con el motivo)

| Pendiente | Motivo |
|---|---|
| Salud por plataforma (latencia, errores del adaptador, última comprobación) | `worker/src/publish/` no expone ninguna ruta de salud. `GET /api/channels` devuelve `health: "sin_datos"` con nota, nunca un "OK" inventado. Hace falta exponer la salud en los adaptadores o registrarla en D1 — trabajo de backend aparte. |
| Editor visual 9:16 (preview con vídeo, subtítulos quemados, miniatura) | Estos endpoints cubren el guardado (`preview_seen`, edición del clip); el render del preview y los subtítulos quemados son trabajo del front / del cerebro, no del Worker. El front reproduce el MP4 real con `GET /api/clips/:id/video`. |
| Borrar o reintentar una medición (`DELETE /api/clips/:id/stats/:statId`) | Fuera del encargo. Una medición errónea se corrige pegando una medición nueva (la analítica usa la última por clip); no hay borrado desde el panel. |
| Aplicar los Ajustes en el Worker (subtítulos, límites, franja) | Fuera del encargo (y fuera de este dev). `publishGate` sigue con sus valores fijos; `GET /api/channels` los etiqueta como fijos. Las notas "Aún no lo aplica el Worker" de Ajustes no se quitan. |
| Crear cuenta API (`POST /api/accounts`) y eliminar cuenta API | Fuera de este encargo (pertenecen al Dev de Cuentas/API). Siguen sin endpoint; `GET /api/channels` muestra `sin_crear` cuando falta la cuenta. |

## Para el coordinador (despliegue)

1. **No hay migración nueva que aplicar.** Todo funciona con 0001–0013 ya aplicadas. (Nota preexistente, no introducida aquí: `migrations/schema.sql` no contiene la sección de la 0012 — salta de la 0011 a la 0013 — aunque el archivo `0012_multi_tenant_single_install.sql` sí existe y sí añade `tenant_id` a `clip_stats`, `jobs` y `clip_events`. Si `schema.sql` se usa como referencia para reconstruir la D1, le falta esa sección; el `wrangler d1 migrations apply` con los archivos 0001–0013 está completo.)
2. Desplegar el Worker como siempre; no hay secretos nuevos ni cambios en `wrangler.toml`.
3. El front puede empezar a llamar a estos 6 endpoints; con ellos se activan el botón "Marcar preview vista", la edición del clip, el formulario de pegar mediciones y la pestaña Canales/Salud (con salud por canal en "sin datos" hasta que exista el endpoint de salud).
