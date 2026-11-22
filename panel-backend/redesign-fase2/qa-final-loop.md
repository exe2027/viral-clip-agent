# QA final del loop — Panel AVC fase 2 + login + endpoints + cerebro

- **Fecha:** 2026-11-17 ~22:40 -03
- **Alcance:** `frontend/dist/` (index.html 571 l., app.js 2.085 l., styles.css 680 l., tokens-gta6.css 216 l.), `worker/src/index.ts` (1.119 l.), `worker/src/db.ts`, `worker/test/` (3 archivos), repo `exe2027/viral-clip-agent` (workflows, tests; solo lectura), API de Cloudflare (conector custom.cloudflare), D1 `clips-avc`, GitHub Actions, front desplegado en https://avc-em9.pages.dev (lectura de texto, sin navegador en vivo).
- **Documentos leídos antes:** `qa-fase2.md` (APROBADO, 13 menores), `inventario.md`, `endpoints-nuevos-1.md`, `endpoints-nuevos-2.md`, `OVERRIDES.md` §8, `DEPLOYED-2026-11-17-2.md`.
- **Método:** verificación estática de código + `node --check` + `bun test` + `tsc --noEmit` (ejecutados por mí) + `curl` a `/api/health` + lectura del front desplegado + `gh run view` + API Cloudflare/D1. **Sin navegador en vivo y sin contraseñas** (no están en contexto; el login 200 con token ya lo verificó el coordinador por curl — no se re-verifica ni se finge).

## Veredicto

**NO APROBADO SIN PENDIENTES — 3 bloqueantes** (lista exacta abajo, con evidencia de cada uno). El código del loop está bien: las 12 pestañas existen, todos los fetch apuntan a endpoints reales, el tema GTA 6 está aplicado, cero nombres personales, cero datos inventados, el bug del punto rojo está corregido en código, el login con token está cableado en Worker y front, los 15 endpoints nuevos existen y pasan 68/68 tests, el cron */5 está activo y D1 tiene las 13 migraciones con IlloJuan activo y con permiso. Lo que impide cerrar el loop es: (1) el CI en `main` está en rojo por 1 test que en local pasa; (2) ningún endpoint nuevo se ha probado en vivo con sesión contra el Worker desplegado; (3) el Worker está parado en vivo en este momento.

### Bloqueante 1 — CI en rojo en `main` (1 test fallido)

- **Evidencia:** `gh run view 44191013315` (workflow CI, push del commit `652db2a`, 2026-11-17 22:32): **failure** — job `tests` 5m56s, `1 failed, 594 passed, 4 skipped`. El fallo es `test_faces_1fps.py::TestDetectFaces1Fps::test_un_solo_ffmpeg_y_mismos_conteos` — `TypeError: 'NoneType' object is not iterable` en `test_faces_1fps.py:49` (`detect_faces_1fps` devolvió `None`).
- **No es causa del commit del smoke fix:** `git show --stat 652db2a` toca **solo** `test_smoke.py` (12 inserciones, 6 eliminaciones); `test_faces_1fps.py` no se toca desde `df4b6b0` (Fase 5.1). El mensaje del commit afirma "Suite local: 599 passed".
- **Contra-evidencia local:** en esta VM, con el `.venv` del repo y en `HEAD` (`652db2a`), `pytest test_faces_1fps.py -q` da **2 passed en 5,09 s**. El fallo no se reproduce en local: huele a entorno CI (ffmpeg en el runner) o a flaky, no a regresión del loop.
- **Por qué bloquea:** `main` no está en verde; el ciclo del cerebro no puede darse por cerrado con el CI en rojo. **Cierre:** re-ejecutar el CI y, si vuelve a fallar, depurar `detect_faces_1fps` en el runner (versión de ffmpeg / salida vacía del filtro).
- **No confundir:** el run del cerebro `44191029412` (dispatch panel-start) **sí** pasó su paso "Tests de humo" (ver §6). El CI y el cerebro no corren la misma suite de la misma forma.

### Bloqueante 2 — Endpoints nuevos sin verificación en vivo con sesión

- **Evidencia:** el cableado está verificado en código (ver tabla §1: los 35 combos que usa el front existen en `worker/src/index.ts`) y en tests (`bun test` 68/68, `tsc` limpio, ejecutados por mí). El despliegue está atestiguado en `DEPLOYED-2026-11-17-2.md` (Worker v10 `f0f764ee-1408-4cbd-814b-1ad237f3987b`, bundle con `preview-seen`, `/api/influencers`, `/api/analytics`, `social-accounts`; Pages `0a7b9adf`, 22:02). Pero **ninguna llamada autenticada** a un endpoint nuevo se ha ejecutado contra el Worker desplegado. La propia nota de despliegue lo registra: "`GET /api/social-accounts` con sesión no probado (requiere login)".
- **Yo verifiqué en vivo sin sesión:** `GET /api/health` → 200 (22:39), front desplegado servido con las 12 pestañas y vacíos reales (lectura de texto de https://avc-em9.pages.dev, 22:38). Eso prueba el despliegue del front, no el del Worker nuevo.
- **Cierre (coordinador, 1 minuto, con la contraseña):** login + `GET /api/influencers`, `GET /api/analytics`, `GET /api/channels`, `GET /api/social-accounts`, `GET /api/panel-users` contra `https://avc.vikingosfoodspa.workers.dev`. Si devuelven 200 con los datos reales de D1, este bloqueante se cierra.

### Bloqueante 3 — Worker parado en vivo (la cola no avanza)

- **Evidencia:** `GET /api/health` a las 22:39: `{"ok":true,"app":"avc","tenants":["sebastian","colega"],"worker_running":{"sebastian":false,"colega":false}}`. El run `panel-stop-sebastian` `44191608632` terminó en **success** (22:35–22:37, 1m52s). El cron `*/5` está activo (verificado por API), pero con el Worker parado no avanza nada.
- **No es un defecto de código:** el run `panel-start-sebastian` `44191029412` anterior terminó en success y el cerebro corrió. Es estado, no bug.
- **Cierre:** pulsar **Iniciar** en la pestaña Worker del panel (o dispatch `panel-start-sebastian`). Verificar después que `health` muestra `sebastian:true`.
- **Contexto:** dos runs anteriores fallaron (panel-start `44186993693` 22:08 y panel-stop `44187556844` 22:11), ambos **anteriores** a los fixes `a3e77c6` (paso 5, `--help || true`) y `652db2a` (smoke 40 s); quedaron superados por los runs en success de las 22:32 y 22:35. No son bloqueantes.

## Tabla de verificación punto por punto

### 1. Las 12 pestañas y el cableado de endpoints — APROBADO en código, PENDIENTE en vivo (Bloqueante 2)

- `TAB_NAMES` (app.js:189): `["worker","cola","cuentas","editor","canales","tracking","analiticas","usuarios","influencers","fuentes","avisos","ajustes"]` (12). Secciones `id="tab-*"` (12) y botones `tabbtn-*` (12 + 2 subtabs `tabbtn-social`/`tabbtn-apis` dentro de Cuentas). `loadTab()` tiene rama para las 12 (app.js:210-222); cada una llama a su loader (`loadSocialAccounts`+`loadAccounts`, `loadEditor`, `loadChannels`, `loadTracking`, `loadAnaliticas`, `loadPanelUsers`, `loadInfluencers`, `loadFuentes`).
- **Inventario completo de fetch del front (35 combos método+ruta), todos con ruta real en `worker/src/index.ts`:**
  - Auth/salud: `GET /api/health`, `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout`.
  - Worker: `GET /api/worker/state`, `POST /api/worker/start|stop|run-once` (vía `workerAction`, app.js:288-295).
  - Clips: `GET /api/clips`, `GET /api/clips/:id`, `GET /api/clips/:id/tracking`, `POST /api/clips/:id/publish`, `PUT /api/clips/:id/video?filename=…` (XHR), `GET /api/clips/:id/video` (fetch en el Editor, app.js:1129), `PATCH /api/clips/:id`, `POST /api/clips/:id/preview-seen`, `POST /api/clips/:id/stats`.
  - Nuevos fase 2 (Dev 1): `GET /api/social-accounts`, `POST /api/social-accounts/:id/verify`, `GET|POST /api/influencers`, `PATCH|DELETE /api/influencers/:id`, `GET /api/sources`, `GET /api/sources/:id`, `GET /api/panel-users`.
  - Nuevos fase 2 (Dev 2): `GET /api/analytics`, `GET /api/channels`.
  - Existentes: `GET /api/accounts`, `POST /api/accounts/:id/credential`, `POST /api/accounts/:id/verify`, `GET /api/notifications`, `POST /api/notifications/:id/read`, `GET|PUT /api/settings`.
  - **Cero llamadas a rutas inexistentes** (grep de `sources/check`, `POST /api/accounts`, `DELETE` de cuentas, `/api/users`, `/api/stats`, `/api/preview`, `/api/jobs` en app.js: 0).
- **Endpoints del Worker que el front NO usa (correcto, no es defecto):** `GET /api/jobs/next`, `GET /api/jobs/:id/video`, `POST /api/jobs/:id/complete` (contrato del puente, no del panel) y `GET /api/clips/:id/stats` (existe en el Worker; el front usa `GET /api/analytics` para el resumen y `POST …/stats` para pegar mediciones).
- **Worker tests ejecutados por mí:** `bun test` → **68 pass, 0 fail** (261 expects, 3 archivos); `tsc --noEmit` → limpio. `node --check frontend/dist/app.js` → OK.
- **Despliegue (atestiguado, no re-verificado por mí el bundle):** `DEPLOYED-2026-11-17-2.md` — migración 0013 aplicada, Worker v10, Pages `0a7b9adf` 22:02, `OPTIONS` preflight 204 con `Access-Control-Allow-Headers: Content-Type, Authorization`. Yo verifiqué por API que `d1_migrations` tiene 13 filas (0001–0013) y que el front desplegado sirve las 12 pestañas.

### 2. Tema GTA 6 — APROBADO (con menores abiertos M1, M3, M4, M5)

- `<html lang="es" data-theme="gta6">`, `<meta name="theme-color" content="#100a1a">`, `tokens-gta6.css` cargado después de `styles.css` y **idéntico byte a byte** al de `design-system/avc-panel/` (`diff` sin diferencias).
- **Táctil 44 px:** `--size-touch-target: 44px` (styles.css:58), `min-height: var(--size-touch-target)` global en botones/inputs/nav (styles.css:77, 144, 211, 254, 276, 426, 472). **Base 16 px:** `--font-size-base: 16px` (styles.css:40, tokens-gta6.css:93).
- **Cero hex fuera de tokens:** app.js **0** hex; styles.css 28 líneas con hex, **todas dentro de `:root`** (líneas 5–65, cierra en 65); index.html solo `#100a1a` en `theme-color`.
- **Contrastes:** los tokens no cambiaron (diff idéntico), así que los ratios AA verificados en qa-fase2 (§2, OVERRIDES §8.2) siguen vigentes; no se recalcularon de nuevo. Sin navegador en vivo no hay verificación visual.
- **Menores abiertos (de qa-fase2, sin cambio):** M1 (barra de prioridad sigue con `.priority-fill.ok/warn/bad` sólidos; las clases `fill-low/mid/high` con degradado de tokens-gta6.css:192-196 siguen sin usarse), M3 (`tokens.css` no se carga aparte), M4 (header no sticky — solo `nav#tabs` es sticky, styles.css:160; el header GTA 6 es `position: relative`, styles.css:527), M5 (tabla sin los rgba de §8.3). M10 **CORREGIDO**: `renderAnalyticsSummary` (app.js:1198-1213) reescribe las 4 tarjetas con `${measured_clips} clips medidos · ${published_clips} publicados` reales y medianas `null` → "—"; el texto estático "Sin clips medidos" de index.html:381-402 solo es el estado inicial antes de cargar.

### 3. Cero nombres personales visibles — APROBADO

- `grep -rniE 'sebasti|chacal'` en `frontend/dist/`: **0 coincidencias**. La única aparición de `vikingosfoodspa` es la base de la API (`https://avc.vikingosfoodspa.workers.dev`, app.js:2-3) — subdominio de infraestructura, no nombre personal. `#whoLine` pone solo `me.sub` (app.js:117). `IlloJuan` es handle público y regla de lanzamiento explícita (permitido).

### 4. Cero datos inventados — APROBADO (con 1 menor nuevo de copia, M14)

- **Estado real de D1 (consultado por API):** `clips=0`, `api_accounts=0`, `social_accounts=0`, `sources=0`, `panel_users=2`. Por eso el front desplegado muestra vacíos reales, verificados en la lectura de texto de https://avc-em9.pages.dev (22:38): "Sin fuentes todavía", "Sin revisiones todavía", "Sin mediciones todavía", medianas "—", "Sin clips publicados todavía", "Sin MP4 todavía", "Preview no vista", "Publicar está bloqueado: falta ver el preview en el Editor".
- **Por pestaña (código):** Influencers lee `GET /api/influencers` (IlloJuan real de D1) + nota de regla de lanzamiento (app.js:749, index.html:159); Fuentes lee `GET /api/sources` + `last_vod_check` (`null` = "Sin revisiones todavía", app.js:888-898) y el botón "Revisar VODs nuevos" sigue **deshabilitado** con su nota pendiente (index.html:201-202) — la revisión la hace el cerebro 09:00; Editor: `edMarkPreview` deshabilitado hasta ver preview y `edPublish` bloqueado sin preview (app.js:1028-1036, 1059-1079); Analíticas: medianas `null` → "—", nunca 0, ranking vacío con texto, formulario de pegar medición deshabilitado sin clips (app.js:1248-1290); Canales: salud `sin_datos` del Worker, nunca "OK", cuota y publicados contados de `publish_states` reales; Usuarios: lista real de `GET /api/panel-users` (solo username + estado + marca de sesión; sin tenant/rol/fechas ni nombres reales, app.js:1578-1611); Cuentas sociales: verify 501 del Worker se muestra **como error con su mensaje**, sin fingir éxito (app.js:681-690).
- **M14 (menor nuevo):** el empty state de Fuentes dice "Aún no hay fuentes procesadas. Cuando el Worker tenga endpoint, aquí verás cada VOD…" (app.js:904, index.html:214) — el endpoint `GET /api/sources` **sí existe** desde fase 2; la lista está vacía porque D1 `sources=0`, no por falta de endpoint. Copia desactualizada, no dato inventado.

### 5. Bug del punto rojo de Avisos — CORREGIDO en código (falta prueba en vivo)

- `loadNotifications()` (app.js:1809-1816) recalcula el badge desde el `GET` fresco (`unread = items.filter(n => !n.read_at).length`; 0 → `badge.hidden = true` + texto vacío; 1–9 → número; ≥10 → `"9+"`); `aria-label` de `#tabbtn-avisos` y `#notifCount` se recalculan en la misma función. `.badge[hidden] { display: none !important; }` en styles.css:246. Tras "Marcar leída": `POST …/read` y **solo si tiene éxito** se vuelve a llamar a `loadNotifications()` completa (app.js:1831-1840); si falla, mensaje de error y el badge no cambia; si el `GET` falla, el badge conserva su último valor. Foco al siguiente botón o al `h2 Avisos`.
- **Pendiente (coordinador, en vivo, ya estaba en qa-fase2 §5):** prueba de aceptación §4.6 (1 aviso → badge desaparece y no reaparece tras recargar; 12 avisos → "9+"; API caída → badge conserva valor). Sin navegador en vivo no se pudo hacer.

### 6. Ciclo del cerebro — VERIFICADO (run en success; CI en rojo, Bloqueante 1)

- **Run `44191029412` "Cerebro — Sebastián" (panel-start-sebastian, 22:32): `completed success`, 2m45s.** Job `vod-check-and-publish` ✓. Pasos: Set up job ✓, checkout ✓, setup-python ✓, Instalar ffmpeg ✓, Instalar TwitchDownloaderCLI ✓, Instalar dependencias ✓, Ruff ✓, **Tests de humo ✓**, Restaurar estado ✓, Puerta Iniciar ✓, Credenciales YouTube (solo si existen) ✓, **Revisión de VODs ✓**, **Cadena completa (detectar, cortar, subtitular, publicar) ✓**, Guardar estado ✓. Pasos skipped (no fallo): Registrar atasco / Subir registro de atascos.
- **Anotación no-bloqueante en ese run:** "Unable to download artifact(s): Artifact not found for name: cerebro-state-sebastian" en el paso Restaurar estado — esperado en el primer run sin estado previo; el paso marcó ✓.
- **Commits en `main` (repo local, rama `main`):** `652db2a` ("Fix: smoke test simula clip valido de 40 s") es `HEAD`; `a3e77c6` ("Fix: TwitchDownloaderCLI --help … ya no tumba el paso 5") es su padre. Ambos verificados ancestros de `main` con `git merge-base --is-ancestor`.
- **Secretos en `env sebastian` (`gh secret list`, solo nombres): existen 3 de 8** que referencian los workflows (`cerebro-sebastian.yml:53 environment: sebastian`): `CLIP_PUBLIC_BASE_URL`, `FACEBOOK_PAGE_ID`, `IG_USER_ID`. **Faltan 5:** `GEMINI_API_KEY`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `YOUTUBE_CLIENT_SECRETS_JSON_B64`, `YOUTUBE_TOKEN_JSON_B64`. A nivel de repo: **0 secretos**. Entorno `colega`: `gh secret list --env colega` → **404 (el entorno no existe)**.
- **Consecuencia (fuera del loop, ver §9):** sin los secretos de YouTube/Twitch/Gemini no hay publicación real ni detección VOD real; el paso de credenciales YouTube es condicional y se salta. Además D1 tiene `api_accounts=0`: aunque existieran los secretos del cerebro, el panel no tiene cuentas API creadas.
- **CI en rojo:** ver Bloqueante 1 (run `44191013315`, failure, 1 test de caras; en local pasa 2/2).

### 7. Worker (Cloudflare API) — APROBADO

- **Cron:** `GET /accounts/27afc37225a02fd92557f901c05dabee/workers/scripts/avc/schedules` → `{"cron": "*/5 * * * *", "created_on": "2026-11-17T20:24:51Z"}`. Activo.
- **Secretos del Worker (solo nombres, nunca valores):** `BRIDGE_TOKEN`, `CREDENTIAL_ENCRYPTION_KEY`, `GITHUB_TOKEN`, `SESSION_SECRET` (4, `secret_text`). Intactos; el despliegue no los tocó (DEPLOYED-…-2.md).
- **Salud en vivo:** `GET /api/health` 22:39 → 200 `{"ok":true,"app":"avc","tenants":["sebastian","colega"],"worker_running":{"sebastian":false,"colega":false}}` (ver Bloqueante 3).
- **Login con token (código, sin re-verificar el 200 del coordinador):** el Worker devuelve el token **además** de la cookie en `POST /api/auth/login` (index.ts, comentario: "Brave iOS bloquea la cookie cross-site"); `verifySession` acepta cookie **o** `Authorization: Bearer` (auth.ts:114-125); el front guarda el token en `localStorage` (`avc-session`) y lo reenvía como `Bearer` en `api()` y en el XHR de subida (app.js:69-73, 167, 588); el logout lo borra (app.js:1994). El login 200 + `GET /api/auth/me` con token ya lo verificó el coordinador por curl (según el encargo).

### 8. D1 (API del conector) — APROBADO

- `d1_migrations`: **13 filas** (0001–0013, incluida `0013_accounts_tenant.sql`).
- `influencers`: **1 fila** — `tenant_id=sebastian`, `name=IlloJuan`, `platform=twitch`, `language=es`, `permission=1`, `active=1`. IlloJuan activo con permiso, como exige la regla de lanzamiento.
- Conteos: `panel_users=2`, `clips=0`, `api_accounts=0`, `social_accounts=0`, `sources=0` (coherente con los vacíos reales del front).

## Menores abiertos (no bloquean)

- **M1** — Barra de prioridad sin degradado GTA 6 (clases `fill-*` sin usar). **M2** — Orden del nav distinto del propuesto en los diseños (sin orden canónico definido). **M3** — `tokens.css` no se carga como archivo aparte. **M4** — Header no sticky (solo el nav). **M5** — Tabla sin los rgba de OVERRIDES §8.3. **M6** — Formulario "Añadir influencer" cerrado en escritorio. **M11** — Preexistentes de fase 1 (foco no se mueve al cambiar de pestaña; hash con `replaceState`; `#healthLine` sin Reintentar; "Salir" sin confirmación; header/nav a 18 px). **M13** — Tres ratios de plataforma difieren por redondeo en §8.2; todos pasan AA, sin acción. **M10 — CORREGIDO** en fase 2 (ver §2).
- **M14 (nuevo)** — Copia desactualizada en el empty state de Fuentes: "Cuando el Worker tenga endpoint" (app.js:904, index.html:214) cuando el endpoint ya existe; la lista está vacía por D1, no por falta de endpoint.

## Qué queda fuera del loop por límites reales

- **Prueba visual en navegador real (Brave):** 360 px, escritorio, horizontal, lector de pantalla y texto grande. Sin navegador en vivo en este subagente; la verificación de layout es estática. La lectura de texto del front desplegado confirma contenido y vacíos, no layout ni interacción.
- **Prueba en vivo del badge de Avisos (§4.6 del diseño):** requiere sesión y avisos reales; pendiente del coordinador.
- **Login 200 con cookie/token:** ya verificado por el coordinador por curl (según el encargo); no se re-verificó aquí.
- **Secretos que solo el usuario tiene:** 5 secretos del cerebro en `env sebastian` (Gemini, Twitch ×2, YouTube ×2) + credenciales de plataforma en `api_accounts` (hoy 0 filas) + OAuth de YouTube. Sin ellos no hay publicación real; el usuario (o el coordinador con él) debe crearlos. Entorno `colega` de GitHub no existe (404 al listar secretos).
- **Auditoría TikTok y App Review de Meta:** pendientes preexistentes, fuera de este loop (canal ES completo salvo App Review; TikTok sin subida directa verificada).
- **CI en entorno:** el fallo de `test_faces_1fps` en CI no se reproduce en local; su depuración necesita acceso al runner o re-ejecución (Bloqueante 1, no es límite de este QA sino trabajo pendiente).
