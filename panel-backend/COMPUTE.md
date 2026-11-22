# Cómputo: plan principal Cloudflare + GitHub Actions ("Todo automático")

Decisión del usuario 17 nov 2026 ("Todo automático"): NO acepta
cortar manual en el teléfono con CapCut. La vía principal es
**Cloudflare (panel/API/cola) + GitHub Actions como compute
principal $0** para Python + ffmpeg + navegador. Detalle completo:
`viral-clip-agent/docs/plan-principal.md`. Solo Cloudflare + manual
queda como **respaldo degradado**, no como plan principal.

## Reparto: qué hace cada pieza

| Pieza | Dónde | Qué hace |
|---|---|---|
| Panel, login, configuración, cuentas | Pages `avc` + Worker `avc` + D1 `clips-avc` (una sola instalación, login por usuario) | Web, sin vídeo |
| Cola, estados, gates, Tracking | Worker + D1 | Máquina de estados, `run-log` con motivo y hora |
| Títulos / SEO por plataforma | Worker → Gemini API gratis | HTTP, sin vídeo |
| Publicación por API oficial (pasos pequeños) | Worker (`worker/src/publish/`) | YouTube resumable, TikTok Direct Post, IG contenedor, FB 3 fases, por pasos reanudables |
| **Detección, corte, subtítulos, empaquetado, publicación** | **GitHub Actions** (workflows `cerebro-sebastian.yml` / `cerebro-colega.yml`, un Environment por panel) | **Python + ffmpeg + faster-whisper; cron 09:00 America/Santiago; $0 dentro de los 2.000 min/mes (repo privado)** |
| Botón Iniciar / Detener | Panel → Worker → `repository_dispatch` a GitHub | El Worker dispara el workflow con `GITHUB_TOKEN` (secreto único del Worker avc, nunca en el repo); el evento es `panel-start-<tenant>` y el cron solo trabaja si `started=true` para ese tenant |
| TikTok por navegador (sesión + captcha) | Runner autoalojado en la PC propia | Solo esa pieza; misma IP/dispositivo, captcha atendido por humano |

## Lo que SÍ se automatiza en la vía principal

| Pieza | Cómo |
|---|---|
| Panel, login, configuración, cuentas | Pages + Worker + D1 ✅ |
| Detectar VODs nuevos de IlloJuan | GH Actions (`watch --once`, Twitch Helix) ✅ |
| Detectar picos por chat | GH Actions (auto_clips, `ingest.score_windows`) ✅ |
| Cortar el video 9:16 (ffmpeg) | GH Actions (auto_clips, `render.py`) ✅ |
| Subtítulos con Whisper + quemarlos | GH Actions (faster-whisper, extra `whisper`) ✅ |
| Títulos / SEO por plataforma | GH Actions (Gemini) + Worker ✅ |
| Cola, prioridad, gates (SEO ≥ 90, cupo, franja 18–24 Chile, permiso, preview visto) | Worker + D1 ✅ |
| Programar y publicar por API oficial | GH Actions (`publish loop --once --live`) + Worker por pasos ✅ |
| Evidencia, reintentos (máx. 3), anti-duplicado | GH Actions + Worker + D1 ✅ |
| Tracking con motivo y hora + avisos visibles en el panel | Worker + D1 ✅ |
| Métricas de YouTube | Worker → YouTube Data API (lectura) ✅ |

## El flujo real (vía principal)

```
1. Usuario: pulsa Iniciar en el panel (una vez)
2. Worker: pone started=true en D1 y dispara el workflow
   (repository_dispatch panel-start-<panel>)
3. GitHub Actions (cron 09:00): detecta VOD nuevo → picos por chat
   → corte 9:16 → subtítulos faster-whisper → empaquetado SEO →
   publicación por API con evidencia
4. Worker: cola, gates, Tracking y avisos; el panel muestra el
   estado de cada clip y cada atasco con motivo y hora
5. Si algo se atasca: Tracking con motivo y hora + aviso visible
   en el panel (campana). Nunca en silencio.
```

## Lo que sigue sin ser 100% automático (honesto)

| Pieza | Por qué no | Qué queda en su lugar |
|---|---|---|
| TikTok por navegador (captcha) | GH Actions tiene IP de centro de datos (mucho captcha) y no mantiene sesión persistente en disco | Runner autoalojado en su PC, solo en la ventana de publicación; el clip queda en draft hasta que el agente lo publica o se sube a mano |
| Captcha (cualquier vía) | Lo resuelve un humano | El agente se detiene, marca `waiting_captcha`, guarda captura y avisa; nunca auto-solver |
| App Review de Meta / auditoría de TikTok | Permisos pendientes | IG/FB/TikTok por API caen a draft con motivo hasta que se aprueben |
| Directo en vivo | TwitchDownloaderCLI no tiene comando live | La cadena automática cubre VODs; el vivo sigue en diseño (Fase 0 sombra → Fase 1 semi-manual 19-nov) |
| Métricas TikTok/IG/FB | Sus APIs no las devuelven sin permisos extra | Solo YouTube tiene métricas automáticas; el resto se revisa a mano |

## Respaldo degradado: solo Cloudflare + manual

Si GitHub Actions no estuviera disponible: el Worker sigue
vigilando, puntuando y publicando por API los MP4 que ya estén en
R2; el corte y los subtítulos vuelven a ser manuales (el usuario
corta el clip en el teléfono y sube el MP4 9:16 al panel).
Detalle: `viral-clip-agent/docs/plan-cloudflare-solo.md`. Es un
respaldo, no el plan: se pierde el corte, los subtítulos y la
detección automáticos.

Sin ningún compute externo, el 100% automático con corte de video
no es posible: el Worker no corre ffmpeg (128 MB / ~10 ms CPU),
no procesa video, no mantiene navegador con login. Por eso el
respaldo asume corte manual.

## Otras opciones gratis evaluadas (por si el respaldo se necesita)

| Opción | Veredicto |
|---|---|
| **Su cuenta de Google: Google Colab** (notebook gratuito) | Viable como *manual por clip*: abre el notebook, pulsa Ejecutar, corta/subtitula/sube en una pasada. Pero es manual cada vez (no se programa solo en gratis) y exige pegar tokens. No es automático; es una herramienta, no un servidor |
| **Su cuenta de Google: Apps Script** | Puede llamar a APIs de Google (YouTube) con límites de 6 min y 50 MB por llamada. No tiene ffmpeg ni puede descargar VODs. No resuelve el corte; solo serviría para subidas pequeñas. No recomendado |
| **Su teléfono (Android)** | No es un servidor: no corre 24/7, la batería y el sistema lo matan. Sirve para lo que ya hace: cortar con CapCut y subir el MP4 al panel. No se le pide nada más |
| **Cloudflare Queues** | Sirve para ordenar jobs dentro de Cloudflare, pero no añade cómputo de video: la cola no corta ni subtitula. Útil como mejora interna, no como solución al corte |
| **ffmpeg.wasm en el navegador (Pages)** | Experimental: el corte correría en su navegador al subir el VOD. Pesa ~30 MB de WASM, lento en teléfono, y el VOD de Twitch no se puede descargar desde el navegador (CORS). Investigado y **descartado** por ahora |
| **Cloudflare Browser Rendering** | 10 min/día gratis, sesión muere a los 60 s sin actividad, IPs de centro de datos. No sirve para TikTok con login/captcha |
| **Google Cloud free tier / Oracle / Fly.io** | Exigen crear cuenta, tarjeta o configurar servidor. Es exactamente lo que dijo que no quiere. Descartado |

## Publisher-bridge: mecanismo alternativo (no la vía principal)

`publisher-bridge/bridge.py` + workflows en
`publisher-bridge/github-actions/` (carpeta marcada como
ALTERNATIVA): el Worker pide jobs por `/api/jobs/next` y el puente
ejecuta la subida con el código ya probado de `viral-clip-agent`,
devolviendo la evidencia. Es un mecanismo **dirigido por el
Worker** (cada 30 min en franja 18:00–24:00 Chile), distinto de la
cadena del cerebro (`cerebro-*.yml`, cron 09:00, que es la vía
principal). Se mantiene como alternativa por si el Worker necesita
disparar subidas fuera del cron del cerebro. El Worker ya habla con
el puente por `/api/jobs/next` y `/api/jobs/:id/complete` (token
`BRIDGE_TOKEN`).
