# Inventario — Panel AVC: v17 vs front nuevo vs Worker real

- **Fecha:** 2026-11-17
- **Alcance:** solo inventario. No se escribió código de implementación, no se rediseñó nada visual, no se tocó lógica de negocio, API, fórmulas de viralidad/SEO ni reglas de publicación.
- **Fuentes leídas (verificadas en disco):**
  - Front nuevo: `frontend/dist/index.html` (221 líneas), `app.js` (979 líneas), `styles.css`.
  - Worker: `worker/src/index.ts` (476 líneas, router completo), `worker/src/db.ts`, `worker/src/jobs.ts`, `worker/src/verify.ts`, `worker/src/auth.ts`, `worker/src/publish/` (adaptadores por plataforma, sin ruta HTTP propia).
  - Migraciones `migrations/0001–0012` + `migrations/schema.sql`.
  - `frontend-notes.md`, `README.md`, `COMPUTE.md`, `wrangler.toml`.
  - `redesign/` (auditoria-ux.md, auditoria-visual.md, diseno-cola.md, diseno-cuentas-avisos-ajustes.md, diseno-login.md, diseno-worker.md, ideas.md, implementacion.md, qa.md).
  - `design-system/avc-panel/` (MASTER.md, tokens.css, tokens.json, OVERRIDES.md — OVERRIDES manda sobre MASTER).
- **Advertencia de método (importante):** la lista de las 10 pestañas y 51 acciones del panel v17 **no pudo verificarse contra el artifact** en esta sesión: el namespace `artifact` disponible aquí solo expone `restore` / `share` / `unshare`; no existe `artifact.list_actions`. La reconstrucción de v17 (§1) se hizo a partir del encargo, de `frontend-notes.md` §6 ("10 pestañas, Cola con prioridad, Tracking de 11 pasos, Analíticas, Ajustes…") y de las tablas D1 de las migraciones 0002/0006/0007 (que son el esquema del panel v17 adaptado a D1). Donde la reconstrucción es inferencia y no hecho verificado, se marca con ⚠️.
- **Nombres:** en este informe solo aparecen los usernames `seba` y `chacal`. Los valores de tenant del código se citan solo como literales de código entre comillas invertidas donde el desarrollador debe copiarlos exactos.

## Resumen ejecutivo (números)

| Magnitud | Valor |
|---|---|
| Endpoints reales del Worker (combinaciones método+ruta) | **24** (22 rutas distintas) + preflight `OPTIONS` + cron cada 5 min (no HTTP) |
| Endpoints que usa el front nuevo (`app.js`) | **20** combinaciones (19 rutas distintas) |
| Endpoints reales que el front NO usa | **4** combinaciones |
| Funcionalidades mapeadas (§2) | 10 |
| Funcionalidades **sin ningún endpoint** | **6** |
| Funcionalidades **parciales** (endpoint existe pero incompleto o sin efecto) | **3** (Cuentas API, Canales/Salud, Ajustes) + Cola parcial |
| Funcionalidades **cubiertas** por endpoint real | Worker, Avisos, Tracking (lectura), publicar/subir MP4 |

### Las 3 brechas más grandes

1. **Editor ausente y publicación bloqueada por `preview_seen`.** El gate de publicación (`jobs.ts`, `publishGate`) frena todo clip con `preview_seen = 0` ("falta ver el preview en el Editor"), pero **ningún endpoint del Worker escribe `preview_seen`** (ni `clip_previews`, ni `captions`, ni ningún `PATCH /api/clips/:id`). No existe forma, desde el panel nuevo ni desde la API, de marcar un preview como visto. Un clip migrado con `preview_seen = 0` no puede publicarse nunca: el botón Publicar siempre devolverá ese freno. Es la brecha nº 1 porque no es solo una pestaña que falta: bloquea el flujo principal.
2. **Seis funcionalidades tienen tabla D1 pero cero ruta HTTP.** Cuentas sociales (`accounts`), Influencers (`influencers`), Fuentes/VODs (`sources`, `vod_checks`), Analíticas (`clip_stats`), Usuarios del panel (`panel_users` salvo login) y Editor (`clip_previews`, `captions`) existen como esquema migrado del v17 y no tienen ni un `GET` de lectura en `worker/src/index.ts`. El panel nuevo no puede mostrar ni crear nada de eso; la Cola solo lista clips que ya existen en D1 (migrados o creados fuera del panel).
3. **Ajustes que se guardan pero no se aplican + Cola sin prioridad real.** `GET/PUT /api/settings` funciona, pero el Worker **no lee** `subtitles_enabled`, `subtitle_style`, `daily_limit_*` ni `publish_window_*`: `publishGate` lleva hardcodeados franja 18:00–24:00, tope YouTube 5/día y resto 3/día, plan 3/2. Solo Telegram (`telegram_bot_token` + `telegram_chat_id`) sí lo lee el Worker (`notify()` en `jobs.ts`). Además `GET /api/clips` ordena por `created_at DESC`, no por prioridad, y no hay segmentos LIVE/VOD: la prioridad y los segmentos existen solo como diseño, no como API.

---

## 1. Tabla pestaña-por-pestaña: v17 vs front actual

Estado: **Existe** = pestaña presente y alimentada por endpoint real · **Parcial** = presente pero incompleta o con datos que el Worker no aplica · **Falta** = no existe en el front nuevo.

| # | Pestaña / funcionalidad v17 ⚠️ | Front nuevo (5 pestañas + login) | Estado | Qué hay / qué falta |
|---|---|---|---|---|
| 1 | Worker | Worker (`#tab-worker`) | **Existe** | Estado, actividad (últimos 50 `worker_events`), Iniciar / Parar / Ejecutar un ciclo / Actualizar. Semáforo en header (`#workerLight`). Completa. |
| 2 | Cola | Cola (`#tab-cola`) | **Parcial** | Lista + detalle + tracking + publicar + subir MP4. Falta: orden por prioridad (la API ordena por fecha), segmentos LIVE/VOD, editor 9:16, desglose viralidad/SEO (ver §2.5), archivar/borrar por plataforma, crear clip, editar clip. |
| 3 | Cuentas (sociales + API) | Cuentas (`#tab-cuentas`) | **Parcial** | Solo cuentas API (`api_accounts`): listar, pegar credencial (cifrada), verificar real. Falta: cuentas sociales (`accounts`, sin endpoint), crear cuenta nueva (no hay `POST /api/accounts`), eliminar cuenta. |
| 4 | Influencers | — | **Falta** | Sin pestaña y sin endpoint. Tabla `influencers` existe en D1, sin uso. |
| 5 | Fuentes / VODs | — | **Falta** | Sin pestaña y sin endpoint. Tablas `sources` y `vod_checks` existen en D1, sin uso. La detección de VODs nuevos vive en GitHub Actions (cerebro), no en el panel. |
| 6 | Editor de clip 9:16 | — (solo subida MP4 dentro del detalle de Cola) | **Falta** | Sin pestaña de editor, sin preview, sin subtítulos quemados, sin `preview_seen`. La subida MP4 existe, pero no es el editor. Bloquea publicar (brecha nº 1). |
| 7 | Analíticas | — | **Falta** | Sin pestaña y sin endpoint. Tabla `clip_stats` existe en D1, sin uso. No hay lectura de métricas por clip/plataforma. |
| 8 | Canales / Salud | — (solo `#healthLine` en el header) | **Parcial** | `GET /api/health` (sin auth) + estado del Worker. Falta: salud por canal/plataforma, estado de cada cuenta API como "canal", cualquier vista de canales. |
| 9 | Tracking (11 pasos) | Dentro del detalle de Cola (`#clipTracking`) | **Existe** (como sub-vista, no como pestaña) | `GET /api/clips/:id/tracking` devuelve los eventos reales de `clip_events`. ⚠️ Los "11 pasos" son el diseño de `trazabilidad-live-prioridad.md`; la API devuelve los eventos que existan, sin garantizar 11. No inventar pasos que no estén en la respuesta. |
| 10 | Usuarios del panel | — (solo login, `#loginBox`) | **Falta** | Login por username+contraseña existe (`panel_users`, migración 0012). Falta: listar/crear/editar/desactivar usuarios. La creación hoy es por SQL (`scripts/seed-users.sql`), fuera del panel. |
| — | Ajustes | Ajustes (`#tab-ajustes`) | **Parcial** | Formulario real (subtítulos, límites, franja, Telegram) + JSON avanzado, con `GET/PUT /api/settings`. Pero el Worker solo aplica Telegram; subtítulos/límites/franja se guardan sin efecto (ver §2.10). |
| — | Avisos (campana) | Avisos (`#tab-avisos`, badge `#unreadBadge`) | **Existe** | Lista + contador de no leídas + "Marcar leída". Alimentada por `GET /api/notifications`. En v17 la campana existía como avisos/notificaciones (`frontend-notes.md` §4); en el front nuevo es pestaña propia. |

⚠️ **Nota sobre el conteo v17:** el encargo lista 11 ítems (cuentas sociales, cuentas API, influencers, fuentes/VODs, editor, analíticas, canales/salud, tracking, usuarios, ajustes, worker) y a la vez "10 pestañas". La reconciliación adoptada aquí —y solo esa— es: cuentas sociales + API eran **una** pestaña "Cuentas" en v17, y Tracking era una **vista dentro de Cola/Editor**, no una pestaña. Si el artifact v17 dice otra cosa, esta tabla hay que corregirla; no se pudo verificar (ver Advertencia de método).

---

## 2. Mapa endpoint-por-endpoint

Base del Worker (única en todo `app.js`): `https://avc.vikingosfoodspa.workers.dev`.
Front: `https://avc-em9.pages.dev` (único origen permitido por CORS en el Worker; `credentials: "include"` obligatorio). Auth: todas las rutas `/api/*` exigen sesión por cookie firmada, salvo `GET /api/health` y `POST /api/auth/login`. Las rutas `/api/jobs/*` exigen `X-Bridge-Token` + `X-Bridge-Tenant`, no sesión.

### 2.1 Todos los endpoints reales del Worker (24 combinaciones, 22 rutas)

| # | Método + ruta | Auth | Qué devuelve / hace | Lo usa el front |
|---|---|---|---|---|
| 1 | `GET /api/health` | No | `{ok, app:"avc", tenants, worker_running:{…}, time}` | Sí (`checkHealth`) |
| 2 | `POST /api/auth/login` | No | `{ok, tenant}` + cookie de sesión; body `{username, password}` | Sí (`doLogin`) |
| 3 | `POST /api/auth/logout` | Sesión* | `{ok}` + limpia cookie | Sí (`logoutBtn`) |
| 4 | `GET /api/auth/me` | Sesión | `{ok, tenant, sub}` | Sí (init + tras login) |
| 5 | `GET /api/worker/state` | Sesión | `{ok, running, startedAt, lastNote, activity:[{at,kind,message}]×50}` | Sí (`loadWorker`) |
| 6 | `POST /api/worker/start` | Sesión | `{ok, running:true}` + dispatch GitHub `panel-start-<tenant>` | Sí (`btnStart`) |
| 7 | `POST /api/worker/stop` | Sesión | `{ok, running:false}` + dispatch GitHub `panel-stop-<tenant>` | Sí (`btnStop`) |
| 8 | `POST /api/worker/run-once` | Sesión | `{ok, ran, processed, stalled}` (ciclo manual) | Sí (`btnRunOnce`) |
| 9 | `GET /api/notifications` | Sesión | `{ok, notifications:[…]×50}` con `read_at` | Sí (`loadNotifications`) |
| 10 | `POST /api/notifications/:id/read` | Sesión | `{ok}`; marca `read_at` | Sí (`data-read`) |
| 11 | `GET /api/clips` | Sesión | `{ok, clips:[…]}` — solo `archived=0` y `status IN ('en_carpeta','aprobado','programado')`, orden `created_at DESC` | Sí (`loadClips`) |
| 12 | `GET /api/clips/:id` | Sesión | `{ok, clip}` (fila completa de `clips`, incl. `viral` y `seo_overrides` como JSON string) | Sí (`selectClip`) |
| 13 | `GET /api/clips/:id/tracking` | Sesión | `{ok, events:[…]}` de `clip_events`, orden `at ASC` | Sí (`selectClip`) |
| 14 | `POST /api/clips/:id/publish` | Sesión | `{ok, jobs:[{id,platform,state}], blocked:[…]}` o error 400 con los frenos; encola 1 job por plataforma que pasa los gates | Sí (`publishSelected`) |
| 15 | `PUT /api/clips/:id/video?filename=…` | Sesión | `{ok, r2_key, size, public_url}`; body `video/*`, máx. 128 MB (límite comprobado **después** de subir) | Sí, por XHR (`uploadSelected`) |
| 16 | `GET /api/clips/:id/video` | Sesión | Bytes del MP4 desde R2 (o 404 si el clip no tiene MP4) | **No** |
| 17 | `GET /api/accounts` | Sesión | `{ok, accounts:[{id,platform,language,label,status,note,verified_at,verify_ok,verify_message,has_credential,credential_masked}]}` — nunca la credencial | Sí (`loadAccounts`) |
| 18 | `POST /api/accounts/:id/credential` | Sesión | `{ok, masked}`; body `{credential, extra}`; cifra (AES-GCM por tenant) y borra el texto plano legacy | Sí (`data-save`) |
| 19 | `POST /api/accounts/:id/verify` | Sesión | `{ok, status, message, verifiedAt}`; verificación real contra YouTube/TikTok/Meta (`verify.ts`) | Sí (`data-verify`) |
| 20 | `GET /api/settings` | Sesión | `{ok, settings:{…}}` (JSON libre por tenant) | Sí (`loadSettings`) |
| 21 | `PUT /api/settings` | Sesión | `{ok}`; guarda el JSON tal cual (merge lo hace el front, no el Worker) | Sí (`saveSettings`) |
| 22 | `GET /api/jobs/next` | Puente (`X-Bridge-Token` + `X-Bridge-Tenant`) | `{ok, job}` (siguiente job `scheduled` vencido) o `{ok, job:null}` | **No** (solo `publisher-bridge`) |
| 23 | `GET /api/jobs/:id/video` | Puente | Bytes del MP4 del job desde R2 | **No** (solo `publisher-bridge`) |
| 24 | `POST /api/jobs/:id/complete` | Puente | `{ok}`; body `{state: posted\|failed\|draft\|processing, evidence_url?, error?, retryable?, resume_data?}`; `posted` exige `evidence_url` https | **No** (solo `publisher-bridge`) |
| — | `OPTIONS *` | — | Preflight CORS (204), solo origen del front | Automático (navegador) |
| — | Cron `*/5 * * * *` | — | `runWorkerCycle` por tenant (solo si su Worker está iniciado); no es HTTP | — |

\* `POST /api/auth/logout` no verifica la sesión en el código (devuelve `{ok}` y limpia la cookie siempre).

**Endpoints que el front NO usa (4):** `GET /api/clips/:id/video`, `GET /api/jobs/next`, `GET /api/jobs/:id/video`, `POST /api/jobs/:id/complete`. No borrarlos ni "reutilizarlos" para otra cosa: los 3 de `/api/jobs/*` son el contrato del puente y el `GET` de vídeo es descarga directa del MP4.

### 2.2 Cuentas sociales — SIN ENDPOINT

- Endpoint real: **ninguno**. No hay `GET /api/social-accounts`, ni lectura de la tabla `accounts` (migración 0002: `platform, label, handle, status, note, sort`) en ninguna ruta de `index.ts` ni en `db.ts`.
- Regla dura: la pestaña Cuentas del front nuevo muestra **solo** cuentas API (`api_accounts`). Si se añade una sección de cuentas sociales, debe mostrar estado vacío real ("Sin cuentas sociales conectadas — aún no hay forma de conectarlas desde este panel") con su acción pendiente (crear el endpoint en el Worker), **nunca** inventar cuentas, handles ni estados.
- Acción pendiente: crear endpoint de lectura (y luego creación) para `accounts` en el Worker. Trabajo de backend, fuera de `frontend/dist/`.

### 2.3 Cuentas API (por plataforma + idioma) — PARCIAL (existe, incompleto)

- Endpoints reales: `GET /api/accounts`, `POST /api/accounts/:id/credential`, `POST /api/accounts/:id/verify` (ver §2.1, nº 17–19). Verificación real por plataforma en `verify.ts` (YouTube API key u OAuth, TikTok `open.tiktokapis.com`, Meta Graph `/me`).
- Lo que falta (SIN ENDPOINT): **crear** una cuenta (`POST /api/accounts` no existe), **eliminar** una cuenta, **editar** `label`/`language`/`platform`. Las cuentas solo aparecen si ya existen en D1 (migración desde el panel anterior). El estado "Sin crear" no se puede resolver desde el front: no hay forma de crear la fila.
- Regla dura: si `GET /api/accounts` devuelve lista vacía, el front muestra su empty state real ("Sin cuentas todavía…") — ya lo hace. No mostrar cuentas de ejemplo ni plataformas con estado inventado. Cada cuenta muestra solo lo que devuelve la API (`verify_ok`, `verify_message`, `has_credential`, `credential_masked`).

### 2.4 Influencers — SIN ENDPOINT

- Endpoint real: **ninguno**. Tabla `influencers` (migración 0002: `name, platform, profile_url, language, permission, active, notes`) sin ninguna ruta. `db.ts` no tiene ninguna función de influencers.
- Regla dura: no existe pestaña de Influencers en el front nuevo y no debe aparecer con datos. Si se crea, estado vacío real ("Sin influencers — la lista vive fuera del panel por ahora") hasta que exista endpoint. No inventar permiso de ningún streamer: el permiso real hoy solo lo verifica el puente contra su propio archivo, no el panel.

### 2.5 Fuentes / VODs — SIN ENDPOINT

- Endpoint real: **ninguno**. Tablas `sources` y `vod_checks` (migraciones 0002/0006) sin ninguna ruta. La detección de VODs nuevos es trabajo de GitHub Actions (cerebro, cron 09:00), no del Worker ni del front.
- Regla dura: sin pestaña de Fuentes/VODs y sin lista de VODs en el front. Si se añade, estado vacío real hasta que exista endpoint. No mostrar VODs "detectados" a partir de la Cola: un clip en Cola no es un VOD.

### 2.6 Editor de clip 9:16 — SIN ENDPOINT (y bloquea publicar)

- Endpoint real: **ninguno**. No hay ruta de editor, ni de preview (`clip_previews`: `status, error, progress, step`), ni de subtítulos (`clips.captions`, `clips.subtitle_style`, `clips.layout_mode`), ni de edición del clip (no hay `PATCH/PUT /api/clips/:id` de ningún tipo). `grep` en `worker/src/`: `preview_seen` solo se **lee** en `jobs.ts:148` (gate); ningún código lo escribe.
- Consecuencia verificada: `publishGate` devuelve "falta ver el preview en el Editor" para todo clip con `preview_seen = 0`, y `POST /api/clips/:id/publish` lo registra como freno en Tracking. Sin editor no hay forma de levantar ese freno desde el panel ni desde la API.
- Regla dura: el detalle de Cola **no** debe fingir un editor ni un preview. Si se muestra algo de preview/subtítulos, solo lo que venga en la fila del clip (`captions`, `subtitle_style`, `layout_mode` dentro de `GET /api/clips/:id`); si esos campos están vacíos, estado vacío real ("Sin preview — el editor aún no existe"). Nunca marcar `preview_seen` desde el front (no hay endpoint y el front no debe escribir en D1).
- Acción pendiente (backend): endpoint que registre preview visto (y, después, editor real). Es la brecha nº 1.

### 2.7 Analíticas — SIN ENDPOINT

- Endpoint real: **ninguno**. Tabla `clip_stats` (migración 0006: `clip_id, platform, views, retention_pct, shares, saves, comments, new_subs, window_label, recorded_at`) sin ninguna ruta. Los jobs `kind='metrics'` existen como tipo en `jobs.ts`, pero no hay endpoint que los liste ni que devuelva métricas.
- Regla dura: no existe pestaña de Analíticas y no debe aparecer con cifras. Si se crea, estado vacío real ("Sin datos de analíticas todavía") hasta que exista endpoint. Nunca mostrar vistas/retención inventadas ni derivarlas del score de la Cola.

### 2.8 Canales / Salud — PARCIAL (existe, genérico)

- Endpoints reales: `GET /api/health` (nº 1: salud del Worker + `worker_running` por tenant, sin auth) y `GET /api/worker/state` (nº 5: estado + actividad). Nada más.
- Lo que falta (SIN ENDPOINT): salud por canal/plataforma, estado de publicación por plataforma, latencia/errores por adaptador (`worker/src/publish/` no expone ruta de salud).
- Regla dura: el header (`#healthLine`, `#workerLight`) solo muestra lo que devuelven esos dos endpoints ("API conectada / no responde", "En marcha / Parado"). Si se añade una vista de Canales/Salud, cada canal sin endpoint muestra estado vacío real ("Sin datos de salud para este canal"), nunca un "OK" inventado.

### 2.9 Tracking (11 pasos) — EXISTE (lectura)

- Endpoint real: `GET /api/clips/:id/tracking` (nº 13), eventos reales de `clip_events` ordenados por `at ASC`, con `step, actor, message, failed`. El front lo muestra como timeline con pill "Fallo" y "Último paso".
- Matiz: los eventos solo existen si algo los escribió (`logClipEvent` en publish, verify, notify). Un clip sin eventos muestra "Sin seguimiento todavía" — ese es su estado vacío real y correcto.
- Regla dura: mostrar **exactamente** los eventos de la API, en su orden, sin completar pasos que falten. Los "11 pasos" son el diseño; si la API devuelve 3 eventos, se muestran 3. Nunca rellenar pasos con texto inventado ni numerar pasos que no existen.

### 2.10 Usuarios del panel — SIN ENDPOINT (salvo login)

- Endpoints reales: solo `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout` (nº 2–4). Leen `panel_users` (`username, tenant, password_hash`, migración 0012) pero no hay listar/crear/editar/desactivar usuarios.
- Regla dura: no existe gestión de usuarios en el front y no debe fingirse. La creación de usuarios es por SQL (`scripts/seed-users.sql` + `gen-password-hash.mjs`), fuera del panel. Si se añade la pestaña, estado vacío real con la acción pendiente (endpoint en el Worker), nunca una lista inventada de usuarios.

### 2.11 Ajustes — PARCIAL (endpoint existe; 3 de 4 secciones sin efecto en el Worker)

- Endpoints reales: `GET /api/settings`, `PUT /api/settings` (nº 20–21). El Worker guarda y devuelve el JSON tal cual; el front hace el merge con lo último recibido.
- Qué aplica de verdad el Worker (verificado en `jobs.ts`):
  - **Telegram: sí.** `notify()` lee `telegram_bot_token` + `telegram_chat_id` de settings y envía a `api.telegram.org`. Sin configurar, no se envía nada.
  - **Subtítulos: no.** `subtitles_enabled` / `subtitle_style` no se leen en ningún archivo de `worker/src/`. El estilo vive por clip (`clips.subtitle_style`).
  - **Límites diarios: no.** `daily_limit_*` no se leen. `publishGate` usa valores fijos: YouTube 5/día, resto 3/día; plan YouTube 3, resto 2.
  - **Franja horaria: no.** `publish_window_*` no se leen. `publishGate` usa franja fija 18:00–24:00 (America/Santiago).
- Regla dura: el front ya lo dice con las notas "Aún no lo aplica el Worker" en Subtítulos, Límites y Franja — **esas notas no se quitan** hasta que el Worker las aplique de verdad. Guardar un ajuste no es aplicarlo: nunca mostrar "límite aplicado" ni "franja activa" a partir del formulario.

### 2.12 Avisos — bug del punto rojo — EXISTE (con matiz)

- Endpoints reales: `GET /api/notifications` y `POST /api/notifications/:id/read` (nº 9–10). No hay endpoint de "contador de no leídas": el badge (`#unreadBadge`, tope "9+", oculto en 0) lo calcula el front contando `read_at == null` en la respuesta de `GET /api/notifications`.
- Regla dura: el punto rojo / badge solo refleja ese conteo. Si el badge no se actualiza, la causa está en no re-llamar `loadNotifications()` tras marcar leída (el front ya lo hace) o en `read_at` no persistido — no se "arregla" poniendo un número a mano. Nunca mostrar un conteo distinto del que sale de la API.

### 2.13 Cola — detalle del mapa (lo que sí alimenta cada pieza)

| Pieza del front | Endpoint real | Dato real / límite |
|---|---|---|
| Lista de clips | `GET /api/clips` | Orden `created_at DESC` (no prioridad). Solo estados `en_carpeta/aprobado/programado`, no archivados. |
| Puntos / barra de prioridad | `GET /api/clips` (`c.score`) | Solo `score`. El desglose (viralidad/SEO/chat) **no** se muestra: `app.js` busca `viral_score/seo_score/chat_norm` como campos sueltos y la API devuelve `viral` como JSON string sin parsear. Sin botón ni ceros (correcto: no inventar). |
| Matriz por plataforma | `GET /api/clips` (`platforms` + `publish_states`) | Estados reales por plataforma desde `publish_states`. |
| Detalle del clip | `GET /api/clips/:id` | Fila completa: título, SEO, tags, plataformas, score. |
| Seguimiento | `GET /api/clips/:id/tracking` | Ver §2.9. |
| Frenos | `POST /api/clips/:id/publish` (`blocked[]`) | Transitorios, solo tras intentar publicar. No hay endpoint de "frenos" por sí solo. |
| Subir MP4 | `PUT /api/clips/:id/video` | R2, máx. 128 MB (el Worker lo comprueba tras recibirlo). Devuelve `r2_key` + `public_url`. |
| Publicar ahora | `POST /api/clips/:id/publish` | Encola jobs; el Worker/puente los ejecuta. Gates reales en `jobs.ts` (preview, duración 15–90 s, SEO ≥ 90 por plataforma salvo override, tope diario, espaciamiento 2 h, franja 18:00–24:00, permiso del streamer verificado en el puente). |
| Descargar MP4 | `GET /api/clips/:id/video` | Existe pero el front no lo usa. |
| Archivar / borrar clip, crear clip, editar clip, reordenar Cola, segmentos LIVE/VOD | — | **SIN ENDPOINT.** No existe ninguna de esas acciones. |

---

## 3. Qué ids de `app.js` y endpoints NO se deben romper al implementar

### 3.1 Base y contrato HTTP (no tocar)

- `const API = "https://avc.vikingosfoodspa.workers.dev"` (única base en `app.js`, línea 3). No cambiarla, no añadir otra base, no quitar `credentials: "include"` en `api()` / `apiRaw()` ni en el XHR de subida.
- CORS del Worker solo permite `https://avc-em9.pages.dev`. El front debe seguir sirviéndose desde ahí.
- No cambiar método, ruta ni cuerpo de ninguno de los 20 endpoints que usa el front (§2.1). En particular: `POST /api/clips/:id/publish` **sin cuerpo**; `PUT /api/clips/:id/video?filename=…` con `Content-Type: video/*`; `POST /api/accounts/:id/credential` con `{credential, extra}`; `PUT /api/settings` con el JSON completo (merge en front).
- No tocar `TAB_NAMES = ["worker","cola","cuentas","avisos","ajustes"]`, el hash `#worker/#cola/#cuentas/#avisos/#ajustes` ni `switchTab`/`loadTab`.
- No tocar la lógica de `publishGate` / `seoForPlatform` / `scoreClass` (tramos <50 / 50–89 / ≥90) ni los mapas `STATUS_MAP` / `PLAT_STATE_MAP`. Son reglas de negocio, fuera de este inventario y del rediseño visual.

### 3.2 Ids estáticos de `index.html` que `app.js` lee con `$("…")` (69 ids en total según `redesign/qa.md`: 62 estáticos + 7 dinámicos)

Estáticos (no renombrar ni eliminar ninguno):

- Login/header: `loginBox`, `loginMsg`, `loginForm`, `username`, `username-error`, `password`, `password-error`, `passwordWrap`, `passwordToggle`, `loginBtn`, `loginHint`, `whoLine`, `healthLine`, `logoutBtn`, `workerLight`, `workerLightText`, `msg`, `app`, `main`, `tabs`.
- Pestañas: `tab-worker`, `tab-cola`, `tab-cuentas`, `tab-avisos`, `tab-ajustes`, `tabbtn-worker`, `tabbtn-cola`, `tabbtn-cuentas`, `tabbtn-avisos`, `tabbtn-ajustes`, `unreadBadge`.
- Worker: `wDot`, `wRunning`, `wStarted`, `wNote`, `wActivity`, `btnStart`, `btnStop`, `btnRunOnce`, `btnRefreshW`.
- Cola: `clipList`, `clipDetailCard`, `clipTitle`, `clipScoreRing`, `clipDetail`, `clipBrakes`, `brakeList`, `brakeNote`, `clipTracking`, `videoFile`, `uploadHelp`, `uploadError`, `uploadInfo`, `uploadProgress`, `uploadProgressFill`, `uploadStatus`, `uploadResult`, `btnUpload`, `btnCancelUpload`, `btnPublish`, `btnCloseDetail`, `publishLabel`.
- Cuentas: `accountList`.
- Avisos: `notifHeading`, `notifCount`, `notifList`.
- Ajustes: `settingsForm`, `settingsDirty`, `settingsJson`, `settingsJson-error`, `settingsAdvanced`, `btnCopyJson`, `btnSaveSettings`, `settingsSavedAt`, `set-subtitles-enabled`, `set-subtitle-style`, `set-limit-youtube`, `set-limit-tiktok`, `set-limit-instagram`, `set-limit-facebook`, `set-limit-error`, `set-window-start`, `set-window-end`, `set-window-error`, `set-telegram-bot-token`, `set-telegram-chat-id`, `set-telegram-error`.

Dinámicos (se crean por `innerHTML` en la misma función antes de leerse; no existen en `index.html`, no buscarlos ahí): `msgClose` (`showMsg`), `retryClips` / `emptyGoWorker` / `emptyRefreshClips` (`loadClips`), `retryAccounts` (`loadAccounts`), `retryNotifs` (`loadNotifications`), `btnCopyUrl` (`uploadSelected`). Además, por cuenta: `cred-<id>`, `extra-<id>`, `cred-<id>-error`, `extra-<id>-error`; por desglose: `bd-<id>`.

### 3.3 Datos que el front NO debe inventar (resumen de la regla dura)

Por cada funcionalidad sin endpoint, el estado es **vacío real + acción pendiente**, nunca datos de ejemplo:

| Funcionalidad | Vacío real que debe mostrar | Acción pendiente (no es trabajo de front) |
|---|---|---|
| Cuentas sociales | Sin cuentas sociales | Endpoint de lectura/creación en el Worker |
| Influencers | Sin influencers | Endpoint en el Worker |
| Fuentes/VODs | Sin fuentes/VODs | Endpoint en el Worker (o seguir en GitHub Actions) |
| Editor / preview | Sin preview; sin editor | Endpoint que marque `preview_seen` + editor real |
| Analíticas | Sin datos de analíticas | Endpoint de lectura de `clip_stats` |
| Usuarios del panel | Sin gestión de usuarios | Endpoint CRUD en el Worker |
| Crear cuenta API | No hay forma de crearla desde el panel | `POST /api/accounts` en el Worker |
| Desglose viralidad/SEO | Sin desglose (sin botón) | Que la API devuelva los componentes parseados, o parsear `viral` en el front sin inventar valores |
| Ajustes (subtítulos/límites/franja) | Nota "Aún no lo aplica el Worker" (ya existe, no quitar) | Que el Worker lea esos settings en `publishGate` |

---

## 4. Lista de verificación para los desarrolladores

**Archivos que se pueden tocar: solo `frontend/dist/`** — `index.html`, `app.js`, `styles.css`. Nada fuera de ahí (no `worker/`, no `migrations/`, no `wrangler.toml`, no `design-system/`, no `redesign/` salvo este inventario).

- [ ] Antes de tocar nada: `node --check frontend/dist/app.js` OK; servir `frontend/dist/` en local y comprobar HTTP 200 en los 3 archivos.
- [ ] No cambiar la base `API`, ni método/ruta/cuerpo de los 20 endpoints usados (§2.1, nº 1–21 salvo `GET /api/clips/:id/video`).
- [ ] No renombrar ni eliminar ninguno de los 62 ids estáticos del §3.2; los 7 dinámicos se crean en su función, no en `index.html`.
- [ ] No quitar las notas "Aún no lo aplica el Worker" de Ajustes (Subtítulos, Límites, Franja) ni la nota de franja 18:00–24:00.
- [ ] No añadir pestañas nuevas (Influencers, Fuentes, Editor, Analíticas, Usuarios, Canales) con datos: sin endpoint, solo vacío real + acción pendiente (§3.3). Si se añade la pestaña, que nazca vacía.
- [ ] No mostrar desglose de prioridad con ceros ni valores por defecto: solo si la API devuelve componentes numéricos reales.
- [ ] No mostrar estados de cuenta distintos de los 4 reales (Conectada / Sin credencial / No conectada / Sin crear); "Sin verificar" no existe.
- [ ] No mostrar nombres personales: solo usernames `seba` / `chacal` (el front ya usa solo `me.sub` en `#whoLine`).
- [ ] No tocar fórmulas ni gates: `scoreClass`, `STATUS_MAP`, `PLAT_STATE_MAP`, `publishGate`, `seoForPlatform`, orden de Cola (lo da la API).
- [ ] No inventar `preview_seen`: sin endpoint, el freno "falta ver el preview en el Editor" seguirá apareciendo al publicar; no ocultarlo en el front.
- [ ] Tokens visuales solo desde `design-system/avc-panel/tokens.css` / `OVERRIDES.md` (manda sobre `MASTER.md`, que es una plantilla genérica clara no aplicable tal cual). Cero hex fuera de `:root` en `styles.css`; cero hex en `app.js`; único hex permitido en `index.html`: `theme-color #0b0d12`.
- [ ] Tras cada cambio: `node --check app.js`; `grep` de ids usados vs ids existentes; `grep -riE` de nombres personales en `dist/` = 0; comprobar que cada `fetch` sigue apuntando a una ruta del §2.1.
- [ ] Verificación en vivo pendiente (fuera de este inventario, la hace el coordinador): abrir el panel, entrar, recorrer las 5 pestañas a 360 px y en escritorio, y probar un publicar real para ver el freno de `preview_seen` en vivo.

## 5. Fuentes y trazabilidad

- Router y endpoints: `worker/src/index.ts` (líneas 122–433: todas las rutas; 155–219 puente; 224–433 sesión).
- Gates y límites fijos: `worker/src/jobs.ts` (`publishGate`, `seoForPlatform`, `notify`, `runWorkerCycle`).
- Verificación real de cuentas: `worker/src/verify.ts`.
- Esquema D1: `migrations/0001–0012` y `migrations/schema.sql` (tablas sin endpoint: `accounts`, `influencers`, `sources`, `vod_checks`, `clip_previews`, `clip_stats`, `panel_users` salvo login).
- Front: `frontend/dist/index.html`, `frontend/dist/app.js` (base API línea 3; `TAB_NAMES`; 20 combinaciones usadas), `frontend/dist/styles.css`.
- QA previo: `redesign/qa.md` §2 (19 rutas / 20 combinaciones usadas; 69 ids) e `redesign/implementacion.md` (qué se implementó y qué no se pudo: logos SVG, franja 24:00, "Sin crear" como tarjeta, agrupar avisos / "Marcar todas" / "Probar Telegram" / borrado de token — todos sin endpoint).
- Diseño: `design-system/avc-panel/OVERRIDES.md` (autoritativo) + `MASTER.md` (plantilla genérica).
