# Proyecto Clips GTA 6 — Plan de independencia (fuera de Muse, en Cloudflare)

Fecha: 17 nov 2026. Estado: plan + auditorías completadas. Nada desplegado en producción, ninguna cuenta creada, ninguna credencial pedida ni guardada.

**Decisión del usuario 17 nov 2026 (tarde): "Todo automático".**
No acepta cortar manual en el teléfono con CapCut. La vía principal
pasa a ser **Cloudflare (panel/API/cola) + GitHub Actions como
compute principal $0** (detección, corte, subtítulos y publicación
100% automáticos tras pulsar Iniciar). Detalle en
`docs/plan-principal.md`. Solo Cloudflare + manual queda como
respaldo degradado (`docs/plan-cloudflare-solo.md`). Lo anterior
(decisión de la mañana: GitHub Actions "le complica", solo
Cloudflare) queda anulado.

## 1. Qué existe hoy (auditado, no inventado)

### Panel — `panel-de-clips-gta-6`, versión v17
- 10 pestañas: Worker, Crear, Editor, Cola, Tracking, Analíticas, Canales, Influencers, Cuentas, Ajustes.
- 51 acciones. Las importantes: `listApiAccounts` / `verifyApiAccount` (verificación real contra la API), `publishClipNow`, `setPlatformPublishState` (estado por plataforma), `getTracking` (11 pasos), `checkNewVods`, `requestClipPreview` / `saveClipPreview`, `generateSeo`, `listPanelUsers`.
- Cuentas hoy: YouTube ES Conectada, Meta · Español Conectada (Instagram @elclipdegta6 + Página Elclipdegta6, App ID 29102700319392159), TikTok ES Sin credencial. Cola: 32 clips.
- Base de datos: SQLite local del artifact (`app.db`, 172 KB, Drizzle, migraciones 0001–0010). **No es Cloudflare D1**: cero menciones a Cloudflare/D1 en el código. "Panel en Cloudflare con D1" era intención de diseño, no está implementado.
- Autenticación: no hay login. Sesión = `localStorage` del navegador (`panel-session-user`, solo guarda el id numérico). No hay Firebase/Google Auth en el código. Cualquiera con la URL puede cambiar de sesión con un clic.
- **Hallazgo crítico: publicar hoy no sube nada.** `publishClipNow` y `processPublishQueue` solo marcan `publicado` en la base de datos. No hay código de subida a YouTube/TikTok/Instagram/Facebook en el panel.
- El panel depende de Muse en: `ctx.db` (SQLite local), `ctx.agent.spawnTask` (revisión de VODs, análisis de fuentes, corte de previews), `ctx.inference.complete` (títulos/SEO con IA), cron gestionado `panel-clips-gta6-vod-check` (09:00 America/Santiago, hoy archivado en `~/workspace/cron.d/_archive/`).

### Cerebro — repo `~/workspace/gta6-clips/viral-clip-agent`, rama `main`, HEAD `7afaf79`, árbol limpio, 503 tests
- Paquete real `clipagent/` (41 archivos). Raíz con shims deprecados. `auto_clips.py` (1.509 líneas) orquesta VOD → chat → picos → corte → subtítulos → paquete SEO.
- Cerebro real: detección de picos por chat (`ingest.py:score_windows`, ventana 10 s), score viralidad 0–100 (`detect.py`, 7 componentes), corte 9:16 (`render.py`, split vertical juego arriba ~69% / cámara abajo ~31%), subtítulos faster-whisper (`transcribe.py`, estilos tiktok/highlight/karaoke), SEO por plataforma (`seo_score.py`, `metadata.py`), cola con prioridad `viralidad^0,5 × SEO^0,3 × chat_norm^0,2` (`priority.py`), publicación (`publish.py` + `platforms.py`: YouTube Data API v3, TikTok Direct Post, Instagram contenedor Graph API, Facebook 3 fases), fan-out (al publicar en YouTube se encolan TikTok/IG/FB), circuit breaker y kill-switch.
- Dependencias pesadas: Python ≥3.10, ffmpeg + ffprobe en PATH, TwitchDownloaderCLI (solo Twitch), PyYAML. Opcionales: faster-whisper, opencv, google-api-python-client. **No usa Playwright, Selenium, yt-dlp ni streamlink.**
- No hay cron, systemd ni Dockerfile en el repo. El scheduler es `clipagent start` (supervisor en foreground con 3 hijos: watcher cada 15 min, worker, publish_loop cada 5 min). La revisión VOD 09:00 es un cron de Muse, no del repo.
- Pendientes verificados en disco: `tokens/` vacío, no hay `.env` / `client_secrets.json` / `token.json`; `config.yaml` con `public_url.base_url: ""`, `ig_user_id: ""`, `page_id: ""` (Instagram cae a borrador sin URL pública HTTPS); métricas solo YouTube (TikTok/IG/FB devuelven `None`); visión Gemini no cableada en `auto_clips.py`; directo en vivo bloqueado (TwitchDownloaderCLI 1.56.5 no tiene comando live, streamlink no instalado); `KICK_PUSHER_KEY` sin verificar.
- Fuera del repo (vive solo en Muse / archivos sueltos): MÁSTER PROMPT FINAL (`~/workspace/user/files/MASTER-PROMPT-FINAL-viral-clip-agent.md`), diseño de trazabilidad + prioridad LIVE (`~/workspace/gta6-clips/trazabilidad-live-prioridad.md`), decisiones 16–17 nov, auditoría de cuentas Meta, scripts `vodcheck-2026-11-17/`.
- Push a GitHub bloqueado por 403 "Resource not accessible by integration" (no verificable desde el repo en esta pasada; `git ls-remote` se colgó).

## 2. Qué se pierde al salir de Muse (y cómo lo arregla el diseño, pieza por pieza)

| Lo que hoy vive en Muse | Qué pasa si solo se sube la página a Cloudflare | Cómo se arregla |
|---|---|---|
| Agentes de chat / subagentes / tribunal | Se pierde: un Worker no piensa ni decide | Se sustituye por **código**: workers + máquina de estados + cola en SQLite/D1 + cron + reintentos. No es un chat, son jobs con estados (`detectado → pico → cortado → subtitulado → aprobado → publicado/fallido`). |
| Edición por chat ("hazlo tú") | Se pierde: hay que editar código y desplegar | Un solo repo Git + `deploy-all.sh` (despliega el mismo commit a los dos paneles). Cambios por pull request / commit, no por chat. |
| Navegador de Muse (login guardado, captcha) | Se pierde: Workers no abren navegador con login | La subida a TikTok por navegador vive en el **compute externo** (ver §4), con sesión persistente y captcha entregado al humano. |
| `ctx.agent.spawnTask` (VODs, análisis, cortes) | Se pierde: eran prompts a un agente | Se reescribe como jobs reales: revisión VOD, análisis de fuente y corte de preview pasan a ser pasos del cerebro en GitHub Actions / VPS. |
| `ctx.inference.complete` (títulos/SEO) | Se pierde | Llamada directa a la API de Gemini con clave propia (Flash-Lite, ~500 pet./día en tier gratis, verificar en AI Studio). |
| Cron de Muse 09:00 | Se pierde (hoy ya está archivado) | Cron de GitHub Actions (`schedule` en YAML) + Cron Triggers de Cloudflare para el Worker. |
| Memoria / decisiones / MÁSTER PROMPT | Se pierde si queda solo en Muse | Se versiona en el repo: MÁSTER PROMPT, reglas, fórmula de prioridad, diseño de trazabilidad y decisiones pasan a `docs/` del repo. |
| Base `app.db` local del artifact | Se pierde al abandonar el artifact | Migración a D1 por panel (mismo esquema, migraciones 0001–0010) + blobs MP4 a R2. |
| Publicación (hoy solo marca estado) | Seguiría sin subir nada | Hay que **implementar las subidas reales** (YouTube Data API, TikTok, Graph API IG/FB) en el cerebro; hoy solo existe verificación de lectura. |

## 3. Arquitectura final (una instalación por persona, $0)

**Decisión de tenant: una instalación por usuario, no una app con `tenant_id`.**
Sebastián y su colega tienen cada uno su propio panel, cuentas, cola, credenciales y sesiones, sin verse. Solo se comparte el código base, desplegado dos veces. Un bug puede romper un panel, pero no puede leer ni tocar los datos del otro, porque el Worker de uno no tiene binding a la base ni al bucket del otro. Con una sola cuenta Cloudflare por persona, cada uno tiene su cuota gratis completa; con una sola cuenta compartida, la cuota (100.000 req/día, 5 crons, 5 GB D1) se comparte y el administrador ve los secretos de ambos (aislamiento parcial).

Por persona (ejemplo Sebastián; el colega igual con su nombre):

| Pieza | Recurso | Costo |
|---|---|---|
| Panel web | Cloudflare Pages `panel-sebastian` → `panel-sebastian.pages.dev` (o subdominio propio) | $0 |
| API + cola + estados | Cloudflare Worker `clips-worker-sebastian` (TypeScript; Python también es posible en Workers, Go no tiene SDK oficial) | $0 |
| Datos | D1 `clips-sebastian` (mismo esquema SQL, datos separados) | $0 |
| Videos | R2 `clips-sebastian` (10 GB gratis; borrar clips ya publicados en todas las plataformas) | $0 |
| Procesamiento de video | GitHub Actions, workflow propio `.github/workflows/cerebro-sebastian.yml`, Environment `sebastian` con sus secretos | $0 (repo público: runners ilimitados; repo privado: 2.000 min/mes o ~$12, verificar uso real) |
| Scheduler | Cron de GitHub Actions (mismo workflow) + 1–2 Cron Triggers en el Worker | $0 |
| Subida TikTok por navegador | Runner autoalojado en PC propia (Playwright Python, sesión guardada en disco local, navegador visible para resolver captcha) o humano desde su navegador | $0 |

Compartido (×1): código, tests, esquema SQL, migraciones, documentación, fórmula de prioridad, reglas de publicación.
Duplicado (×2): Pages, Worker, D1, R2, secretos Cloudflare, crons, subdominio, login/sesión, GitHub Environment + workflow, tokens de TikTok/YouTube/Meta/Gemini.

**Lo que NO puede un Worker de Cloudflare (honesto):** no corre Python con `subprocess`, no corre ffmpeg, no procesa video largo (límite 10 ms de CPU por invocación, 128 MB), no mantiene navegador con login/captcha (Browser Run: 10 min/día gratis, sesión se cierra a los 60 s de inactividad, IPs de Cloudflare identificables como bots). El Worker solo hace panel + API + cola. El video y el navegador viven fuera del Worker.

## 4. Lenguajes — recomendación

Informe completo: `~/workspace/gta6-clips/agentes-en-codigo-go-vs-python.md`.

- **Recomendación: todo en Python ahora (Opción A, 22/25, 2–4 días).** El cerebro ya existe en Python con 503 tests; la independencia no la da el lenguaje, la dan workers + cola con estados + cron + reintentos/idempotencia. No reescribir a Go.
- Go vs Python por pieza: ffmpeg empate (el trabajo lo hace ffmpeg en C); Whisper gana Python claro (faster-whisper es el estándar; `go-whisper-ct2` es nicho, cgo, más lento); navegador TikTok gana Python/TypeScript claro (Playwright oficial solo Node/Python/Java/.NET; `playwright-go` es comunitario con runtime Node ~50 MB; `go-rod` sin release desde jul 2024); APIs empate (todo es HTTP+OAuth); concurrencia: Go en teoría, empate a 1 clip/día (una tabla `jobs` en SQLite basta, sin Redis/Celery); binario/arranque gana Go (~10 MB, 5 ms) pero es irrelevante porque el cuello es ffmpeg/Whisper/red.
- TypeScript: sí para panel/API (Playwright nativo, Workers nativo), no para el cerebro de vídeo (sin faster-whisper).
- Rust: más rápido/seguro a largo plazo, pero el más lento de escribir; no ahora (6+ semanas).
- Ranking: 1º todo Python (22/25), 2º Go orquestador + Python vídeo/IA (20/25, 1–2 sem.) y TS panel/API + Python cerebro (20/25, 3–5 días), 4º todo Go (17/25, 3–6 sem.), 5º Rust (16/25), 6º todo TypeScript (14/25).
- **El aislamiento por tenant NO cambia el ranking.** Go no aísla más que Python: el aislamiento viene de recursos separados y bindings fijos. Lo que sí cambia: desplegar dos veces (Go: un binario + dos `.env`; Python: una sola imagen Docker, dos contenedores) y el riesgo de divergencia (mitigar con un solo repo/imagen versionada + `deploy-all.sh` + health check por instalación; nunca desplegar a mano a una sola).
- Fase 2 (opcional, después): reescribir solo orquestador/cola/APIs en Go (1–2 semanas), dejando cada paso pesado como comando CLI claro (`cortar.py`, `subtitular.py`) para no tocar vídeo/IA.

## 5. TikTok adaptado a la versión independiente

Veredicto del tribunal anterior (se mantiene): solución combinada = agente navegador atendido hoy + manual como respaldo + probar Zernio en paralelo + auditoría en paralelo. Riesgo ToS no es cero: TikTok prohíbe scripts de automatización, el captcha es señal de detección, y la nube (IP de centro de datos) es más sospechosa que el navegador de una casa.

En la versión independiente, por panel:
- **Agente navegador (método probado por Sebastián):** Playwright Python en runner autoalojado en su PC (o en el VPS si se acepta el riesgo), sesión persistente guardada en disco local, mismo dispositivo/IP, entra a `tiktok.com/upload`, sube el MP4 9:16, rellena título/descripción/hashtags, revisa preview, publica. 1 clip/día, sin excepciones en cuenta nueva.
- **Captcha:** el agente se detiene de inmediato, marca el clip "esperando captcha" y avisa con captura; el humano lo resuelve en esa misma sesión; el agente retoma verificando mismo MP4/título/descripción. Nunca auto-solver. Ante rechazo: pausa 2 h sin reintentos, sin cambiar IP/dispositivo/cuenta; un reintento con la misma sesión, y si vuelve a fallar, Plan B ese día y no se reintenta hasta el día siguiente.
- **Anti-duplicado:** clave de idempotencia `sha256(clip|tiktok|cuenta|fecha)` guardada antes de subir; estados `scheduled → processing → posted/failed`; `posted` solo con evidencia (toast + URL + captura + verificación en perfil). Si la clave ya existe en `processing` o `posted`, no se sube. No borrar el MP4 hasta confirmar `posted`.
- **Borrador por API (vía paralela):** scope `video.upload`, manda el clip a la bandeja de TikTok y el usuario lo publica con 1 toque. Exento de la restricción de privado de Direct Post, pero exige app review aprobado para `video.upload` y publicar cada borrador a mano; máx. 5 borradores pendientes/24 h. No está lista hoy.
- **Zernio (provisional, sin verificar en vivo):** el tribunal lo puso primero en proyección (135) por ser $0, vía oficial auditada según anuncia, sin navegador ni captcha. Exige prueba en vivo: conectar por OAuth (sin entregar contraseña de TikTok, sin conectar Gmail), subir 1 clip real respetando 1/día, visible en público desde otra cuenta/incógnito, con URL y hora. Si falla o pide pago/Gmail, se descarta.
- **Auditoría (paralela):** pedirla ya, sin esperar respuesta para publicar hoy. Ojo: TikTok no publica plazo oficial ("1–4 semanas" es estimación de terceros) y rechaza apps que son solo una utilidad para subir a cuentas propias.
- **Plan B si el agente falla hoy:** manual — el sistema entrega MP4 + título + descripción + hashtags copiables y el usuario sube a mano desde `tiktok.com/upload` o TikTok Studio (programa 15 min–10 días, pero probablemente exige cuenta Business o 10.000+ seguidores). No hacer: Appium/emulador, multicuentas/rotar, cuenta en privado, @bitsflux vieja, esperar a la auditoría sin publicar.

## 6. Qué queda listo, qué falta, qué se necesita del usuario

**Queda listo (esta pasada):**
- Auditoría del panel (v17, 51 acciones, hallazgo de que publicar hoy no sube nada, D1 no implementada, sin login).
- Auditoría del repo (HEAD 7afaf79, 503 tests, mapa completo del cerebro y pendientes).
- Investigación de alojamiento $0 (Workers, GitHub Actions, Render/Railway/Fly.io, Oracle Always Free, Cloudflare Containers $5/mes, Hetzner ~€5/mes).
- Lluvia extensa Go vs Python (+ TypeScript/Rust) con ranking y recomendación, en `~/workspace/gta6-clips/agentes-en-codigo-go-vs-python.md`.
- Diseño de despliegue por tenant (una instalación por usuario).
- Este plan maestro.

**Falta (trabajo de construcción, no hecho):**
1. Versionar en el repo lo que hoy vive solo en Muse: MÁSTER PROMPT FINAL, reglas, fórmula de prioridad, trazabilidad LIVE, decisiones 16–17 nov.
2. Partir el cerebro en workers con tabla `jobs` y máquina de estados; sacar los pasos pesados como comandos CLI (`cortar.py`, `subtitular.py`).
3. Backend propio del panel: reemplazar el SDK de Muse por Worker + D1 + R2; migrar `app.db` y los blobs MP4 de previews; login real por panel (Google/Firebase Auth o contraseña por despliegue); cifrar credenciales (hoy están en texto en SQLite).
4. Implementar las subidas reales (YouTube, TikTok Direct Post / Upload, IG/FB Graph API) — hoy el panel solo marca estado y el repo solo llega a draft en TikTok/IG/FB sin URL pública.
5. Workflows GitHub Actions por panel (uno por persona, Environments separados, `concurrency` por panel) + cron diario.
6. Agente navegador TikTok en Playwright Python con protocolo captcha + anti-duplicado.
7. Resolver el push 403 a GitHub (revisar instalación de la app de GitHub, permiso Contents: Write) — sin push, el despliegue por Git no funciona.
8. Probar Zernio en vivo y pedir la auditoría de TikTok.

**Qué necesita exactamente del usuario para desplegar (nada por chat, todo lo introduce él en su cuenta):**
- Por persona: cuenta Cloudflare propia (recomendado; si no, proyectos separados en la misma cuenta, con aislamiento parcial).
- Sus propias cuentas y credenciales por panel: TikTok, YouTube (clave API + OAuth), Meta (token largo de Página/Instagram, App ID 29102700319392159 solo para el panel de Sebastián; el colega usa su propia app), Gemini API key, token de API de Cloudflare para el despliegue.
- Dominio: aceptar `*.pages.dev` gratis o dar subdominio propio por persona.
- Acceso al repo para quien despliegue + GitHub Environments con sus secretos.
- Decisión tomada 17 nov 2026 ("Todo automático"): la publicación
  es automática tras Iniciar (GitHub Actions como compute
  principal); el agente navegador queda solo para la subida directa
  a TikTok (runner autoalojado) y lo manual solo como respaldo.
- Para hoy (17 nov): el clip nº1 listo para TikTok; si el agente navegador no está cableado aún, el Plan B manual es la única vía real hoy.

## 7. Orden de trabajo para hoy

1. **Hoy, sin esperar:** publicar el clip nº1 de TikTok con el método probado (agente navegador atendido) o manual si el agente falla; YouTube y Meta ya están conectados.
2. Versionar el cerebro en el repo (MÁSTER PROMPT, reglas, decisiones) y arreglar el push 403.
3. Partir el cerebro en workers + tabla `jobs` (Python, Opción A, 2–4 días).
4. Backend del panel en Cloudflare (Worker + D1 + R2) + login por panel + migración de `app.db`.
5. Workflows por panel en GitHub Actions + cron diario (vía
   principal: `.github/workflows/cerebro-sebastian.yml` y
   `cerebro-colega.yml`, ya creados; falta que el push 403 deje
   subirlos al repo).
6. Agente navegador TikTok con protocolo captcha + anti-duplicado.
7. En paralelo: pedir auditoría TikTok y probar Zernio con 1 clip (nunca duplicar el clip del día).
8. Cuando un panel esté listo, verificación por panel: cola vacía de datos del otro, cuentas propias conectadas, 1 clip de prueba solo en su panel.
