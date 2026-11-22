# Reglas del usuario — GTA 6 clips (código, no solo chat)

> TODO lo que el usuario dice en el chat queda guardado en el
> REPOSITORIO (código/config/docs) y desplegado en la WEB
> (el panel en Cloudflare + Worker). Nada local cuenta.
> Si una regla cambia, cambia aquí Y en el código Y en el panel.

## Reglas vigentes (con su punto de aplicación)

| Regla | Desde | Dónde se impone | Test |
|---|---|---|---|
| **Duración: solo tope de 60 s** (la regla 40–60 s / 25–35 prohibido fue ELIMINADA 2026-11-21 08:47, para siempre, en todo el canal) | 2026-11-21 08:47 (P0 #2, reemplaza la 40–60 de 2026-11-18) | `clipagent/workers.py` (CLIP_MIN_DURATION_S eliminado); `config.yaml` clips.max_duration_s=60; panel `worker/src/jobs.ts` publishGate | `test_reglas_usuario_2026_11_17.py`, `test_worker_panel.py` |
| **Puerta de render PRE-CORTE**: el clip SOLO se corta/renderiza si ya pasa las puertas (viralidad estimada + SEO pre-corte >= 80 en al menos una plataforma); si no pasa, se descarta SIN descargar ni renderizar. Umbral pre-corte de viralidad **45 (calibrado, no 50)**: la estimación usa defaults conservadores 5,0 y subestima ~7 puntos (p3220 estima 46,3 vs real 53,8; p2010 44,5). Con 50, p3220 se descartaría. El 45 real sigue en publicación. SEO pre-corte con el paquete provisional (ficha sin clip + títulos del chat, juego normalizado); sin juego conocido o sin título, el SEO queda sin medir y decide solo la viralidad | 2026-11-18 | `clipagent/policy.py` render_gate (puerta 45/80, max SEO), `clipagent/workers.py` PRE_CORTE_VIRAL_MIN=45 + pre_corte_package + gate_peak_for_render + detect_peaks (descarta) + _h_corte (re-verifica) + advance_pipeline (pasa scores al corte), `auto_clips.py` (puerta antes de descargar el segmento); panel: POST /api/clips rechaza score < 50 y SEO máx. < 90 (cuando vienen seo_scores) | `test_render_gate.py` (p3220 real pasa, p2010 no, plano 44,0 no) |
| Hype por **ráfaga** v2: velocidad msgs/10 s, msgs/60 s y msgs/s contra baseline rodante (mediana 5 min); ratio pico/baseline; señales risas, emotes hype, repetición por usuarios distintos | 2026-11-18 | `clipagent/ingest.py` score_windows (hype_score 0–10 por ventana), `clipagent/detect.py` chat_component (bonus +0,25×hype_score antes de topes), `clipagent/workers.py` detect_peaks (hype guardado por pico) | `test_hype_v2.py` (ráfaga de 60 msgs/10 s supera a 120 repartidos) |
| **10 streamers activos, todos GTA VI**: Rubius, IlloJuan (REACTIVADO 2026-11-18 15:30, estuvo pausado 13:34–15:30), AuronPlay, Ibai, TheGrefg, Xokas, DjMaRiiO, Knekro, RDJavi, Westcol — platform twitch, language es, estado concedido, fecha_permiso 2026-11-18 (IlloJuan 2026-11-16), publish_platforms 4 plataformas, enabled true. clip_02 de IlloJuan sigue publish_blocked por subtítulos sin verificar (no publicar) | 2026-11-18 15:30 (reemplaza "Rubius prioritario, IlloJuan PAUSADO" de las 13:33) | `streamers.yaml` (10 entradas, game "GTA VI"); watcher vigila enabled; panel D1 influencers active=1 + game "GTA VI" (tenant sebastian) | `test_streamers.py::test_diez_activos_todos_gta_vi`, `test_reglas_usuario_2026_11_18.py` |
| **8 streamers EN, todos GTA VI**: Kai Cenat (twitch), IShowSpeed (youtube — su Kick existe pero ~3k seguidores, no es su plataforma principal; el watcher en vivo no cubre YouTube todavía), N3ON (kick), xQc (kick), TheBurntPeanut (twitch), Burn (twitch), Kaysan (twitch), WillNeff (twitch) — language en, estado concedido, fecha_permiso 2026-11-18, publish_platforms 4, enabled true. Handles verificados en vivo antes de agregarlos (KaiCenat y TheBurntPeanut en directo al verificar; xQc en directo en GTA VI) | 2026-11-18 | `streamers.yaml` (8 entradas EN, game "GTA VI"); `clipagent/streamers.py` PLATFORMS incluye youtube; watcher vigila enabled (twitch/kick; youtube se salta con nota); panel D1 influencers tenant sebastian | `test_streamers.py::test_ocho_ingleses_todos_gta_vi` |
| **Monitoreo en vivo SIMULTÁNEO (FALLO REAL 2026-11-18, arreglado)**: cerebro-sebastian 1×/día + cerebro-live solo rubius cada 30 min = nadie miraba a todos a la vez. Ahora: `cerebro-live.yml` cron */15 + dispatch `panel-live-<tenant>` del Worker cron */5 mientras running=1 (throttle 15 min/tenant en D1, y el Worker NO dispara si ya hay una corrida en curso/en cola); job `detect` (`clipagent.live_scan`) barre TODOS los enabled ES+EN (twitch/kick; youtube se salta con nota) en vivo + GTA VI por API oficial; job `cut` en matrix (1 job/streamer, max 4) corta 10 min por sesión y encola en la Cola LIVE vía PanelBridge. Causas raíz: sin `environment: sebastian` los secretos se resolvían vacíos, y PANEL_USERNAME/PANEL_PASSWORD ni existían (creados con login de servicio `muse-cerebro`, tenant sebastian, D1 id 7). MEDIDO: la corrida 44381513042 (detect OK, 6 en vivo) fue CANCELADA a los ~9 min por el siguiente trigger con `cancel-in-progress: true` y ciclo de 10 min — una corrida dura ~11-13 min, así que con cancelación NINGUNA termina nunca; por eso ciclo */15 + `cancel-in-progress: false` (las corridas encolan, nunca se cancelan) | 2026-11-18 | `clipagent/live_scan.py` + `.github/workflows/cerebro-live.yml`; secretos GitHub env sebastian (12/12, incl. PANEL_*); Worker `scheduled()` dispatch (panel) | `test_live_scan.py` (5 tests, clientes simulados); verificación en vivo: detect OK con 6 en vivo en GTA VI (illojuan, rubius, djmariio, kaicenat, xqc, theburntpeanut) en la corrida 44381513042; los 6 jobs de corte se cancelaron por el bug de concurrencia (arreglado a */15 + sin cancelación) |
| **Revisión automática de los mejores picos (APROBADO 2026-11-18)**: en el loop en vivo, los top 3 picos por directo con viralidad AUTO 40-44,9 pasan por revisión automática (3 fotogramas + transcripción Whisper + juez Gemini con los 5 componentes humanos) y se re-calcula su viralidad; si tras la revisión pasan >= 45 y SEO >= 80 (provisional pre-corte), se renderizan y se encolan en LIVE; si no, se descartan SIN render. La puerta de publicación sigue en 45 (NO se baja). >= 45 directo a render; < 40 se descarta sin revisión ni render. Si Gemini no está disponible, FALLBACK a la puerta pre-corte (viral auto >= 45, umbral VOD ya calibrado), registrado como `fallback-pre-corte`. La viralidad post-corte de un pico revisado usa sus valores REALES (human Gemini + audio medido) | 2026-11-18 | `clipagent/review.py` (`decide_render`, `review_peak`, `review_human_scores`); `clipagent/live_cutter.py` `process_peak` pasos 2-4 (puerta pre-render + revisión antes del corte; `self._reviewed_peaks` top 3) + `review` en record/paquete/reason del panel | `test_review.py` (17 tests: decide_render, parse, review con Gemini simulado sobre datos reales p3220, fallback, cableado process_peak) |
| Sin override de p2010 (viralidad 44,5 < 45) | 2026-11-18 | `priority.publish_status` (viralidad < 50 descarta incluso con override SEO) | `test_reglas_usuario_2026_11_18.py` |
| **SIN franja horaria**: da lo mismo la hora, solo se sube | 2026-11-18 14:25 | ELIMINADA de `clipagent/publish.py` check_limits (PUBLISH_WINDOW=None), `limits.yaml`, panel `jobs.ts` publishGate (sin check de hora), `index.ts`, front | `test_limits.py`, `test_platform_errors.py`, `worker/test/jobs.test.ts` |
| **Categoría GTA VI obligatoria**: clip solo válido si la categoría es GTA VI (Grand Theft Auto VI); Just Chatting u otro juego se descarta | 2026-11-18 | `clipagent/category.py` (is_gta6, category_at, capítulos del VOD), `watcher.py` TwitchHelix.live_category/channel_category (Helix oficial), `clipagent/live.py` kick_channel_category (Kick API v2), `auto_clips.py` chat_stream_info, `workers.py` detect_peaks (descarta picos fuera de GTA VI), `policy.py` gate 0.5 (categoría conocida no-GTA-VI → descartado; SIN categoría → pasa con nota, el gate vive en el corte — detect_peaks descarta/etiqueta — y en el panel POST/publishGate; así no se bloquean clips heredados como p3220), `moment_card.py` (category en ficha); panel: `clips.game` + `isGtaViGame` (`db.ts`), publishGate (`jobs.ts`), POST/PATCH (`index.ts`), Cola/Editor (front) | `test_category_gta6.py`, `test_policy.py`, `worker/test/jobs.test.ts` |
| Layout split vertical: juego arriba (1320), cámara abajo (600), sin tapar minimapa ni HUD; dos webcams apiladas; si el juego no se ve, la cámara se achica (480), nunca al revés | 2026-11-16 | `clipagent/render.py` (_split_params, split_game_h/split_cam_h/split_cam_small_h), `config.yaml` clips | `test_layout_split.py`, `test_reglas_usuario_2026_11_18.py` |
| **Layout estilo Willyrex (plantilla rdjavi v2) personalizado GTA 6 / Vice City (default para GTA 6)**: streamer ARRIBA, gameplay ABAJO; fondo desenfocado 0-730, webcam centrada 580x590 en (250,190) con esquinas redondeadas y 50 px de solape, gameplay 730-1770, banda inferior 1770-1920 con barra atardecer rosa/violeta/naranja + handle @TU-CANAL \| GTA VI; cortes duros; cámara fija salvo punch-in puntual | 2026-11-18 (reemplaza juego-arriba/cámara-abajo; DJ Mariio aparcado) | `clipagent/render.py` (RDJAVI_*, `_split_rdjavi_graph`, `detect_framing`, `resolve_split_variant`), `config.yaml` layout | `test_layout_referencia.py` (geometría exacta + render real 1080x1920) |
| **Subtítulos estilo Willyrex total**: una línea centrada en y≈1690 (MarginV 230, mínimo 200), letra chica 32 px fina blanca con contorno negro, sin fondo ni resaltado palabra por palabra; la regla vieja de 680 queda reemplazada | 2026-11-18 | `clipagent/transcribe.py` (SUBTITLE_REF_MARGIN_V=230, SUBTITLE_MIN_MARGIN_V=200), `clipagent/config.py` (estilo "ref", default), `config.yaml` subtitles | `test_reglas_usuario_2026_11_17.py`, `test_reglas_usuario_2026_11_18.py`, `test_layout_referencia.py` |
| **Miniaturas estilo GTA 6 / Vice City**: texto corto de reacción + fotograma elegido por Gemini con criterio Vice City (neón, palmeras, tercera persona); quemado con condensada negrita y sombra rosa neón | 2026-11-18 | `clipagent/metadata.py` (THUMBNAIL_TEXTS, THUMBNAIL_STYLE), `clipagent/vision.py` (_build_prompt), `clipagent/thumbnails.py` (thumbnail_with_text) | `test_thumbnails_gta6.py` |
| **Configuración desde la web (Ajustes)**: layout, estilo de subtítulos, duración mín/máx, viralidad/SEO mínimos, topes diarios, categoría, estilo de miniatura, hype on/off, Iniciar/Parar — guardado en D1 por tenant y leído por el Worker/brain; nada de editar código | 2026-11-18 | Panel `worker/src/` (GET/PUT /api/settings, GET /api/bridge/settings) + `clipagent/panel_settings.py` (apply/fetch, env PANEL_WORKER_URL/PANEL_BRIDGE_TOKEN) | `test_panel_settings.py` |
| **REGLA DE ORO — automatizado 100%**: si pulsa Iniciar, todo se hace automático y puede desloguearse; el trabajo vive en GitHub Actions (Cerebro) + Worker cron */5; nada depende de su sesión, navegador ni teléfono | 2026-11-18 15:05 | Panel Worker (`worker_running` persistente en D1, handler `scheduled()` sin sesión) + workflow Cerebro | Verificación en vivo en la web (ver informe) |
| **Topes diarios como REGLAS (los pone el agente)**: YT plan 3/máx 5, resto plan 2/máx 3, nuevas 1/día, Zernio 15/día + 5 borradores, 2 h entre publicaciones; **escalado automático**: nueva → establecida a los 30 días desde la 1ª publicación + 10 publicaciones sin sanciones (lo aplica el Worker cada día; se ve en Ajustes) | 2026-11-18 | `limits.yaml` (fuente + margen por plataforma), `clipagent/publish.py` (load_limits — bug 2026-11-18: antes no leía el archivo de la raíz, corregido), panel Worker + D1 | `test_limits.py`, `test_panel_settings.py` |
| Subtítulos SIEMPRE y COMPLETOS cuando hay voz, quemados, todo lo que dice el influencer; nunca inventar texto; sin voz verificable + pasa puertas (45/80) = se publica igual SIN subtítulos, sin voz + bajo puertas = fuera | 2026-11-17 22:10, matizada 2026-11-19 18:41 | `config.yaml` subtitles, `clipagent/transcribe.py`, `clipagent/config.py`, `policy.py` (no_speech_blocked); panel `publishGate` (publish_blocked) + migración 0014 | `test_subtitles_always.py`, `test_sin_voz_publica.py`, `test_policy.py` (TestNoSpeech) |
| Descargar SOLO la parte del clip, nunca el video entero; chat solo en ventana ±120 s del pico | 2026-11-17 23:00 | `auto_clips.py` download_twitch_segment / download_chat_window, `config.yaml` clips.chat_window_s=120 | `test_chat_window.py` |
| Prioridad **LIVE sobre VOD**; fórmula INTOCABLE viralidad^0,5 × SEO^0,3 × chat_norm^0,2; filtros duros viralidad < 45 descarta, SEO < 80 no publica salvo override registrado; clip > 7 días −10 pts/día; no dos clips del mismo momento el mismo día en la misma plataforma | 2026-11-16/17 | `clipagent/priority.py` (DEFAULT_WEIGHTS, VIRAL_MIN_PUBLISH=50, SEO_GATE=90, age_penalty, moment_key), `clipagent/policy.py`, panel `worker/src/db.ts` (LIVE primero, VOD después) | `test_priority.py`, `test_policy.py`, `worker/test/queue-order.test.ts` |
| Publicación automática con red de seguridad, sin aprobación por clip; puertas: preview visto, subtítulos completos, permiso streamer, SEO, topes diarios, espaciamiento 2 h | MÁSTER PROMPT FINAL | `clipagent/policy.py` evaluate, `clipagent/publish.py` check_limits, panel `jobs.ts` publishGate | `test_policy.py`, `worker/test/jobs.test.ts` |
| Regla Nº1 no ser baneados: topes por plataforma (YT plan 3/techo 5, resto 2/3), cuentas nuevas 1/día la primera semana | 2026-11-16 | `limits.yaml`, `clipagent/publish.py` DAILY_LIMITS/NEW_ACCOUNT_DAILY, panel `jobs.ts` (plan/techo) | `test_limits.py` |
| Todo **$0, gratis** (Gemini Flash-Lite gratis, sin servicios de pago) | 2026-11-16 | Sin claves de pago en `config.yaml` (test: sin api_key/client_secret en config versionada); secretos solo por env/GitHub Secrets | `test_reglas_usuario_2026_11_18.py` |
| Panel en **Cloudflare (tu dominio Pages)**, login por usuario, cada tenant ve solo lo suyo | 2026-11-17 | `config.yaml`/`DEFAULT_CONFIG` panel.base_url, `panel-backend/` (Worker + D1 + R2 + Pages), `worker/src/db.ts` (tenant_id en todo) | `test_reglas_usuario_2026_11_18.py` |
| **Umbrales 45/80, fuente única (P0 #1)**: viralidad ≥ 45 y SEO ≥ 80 en TODOS los caminos; se eliminaron los defaults 50/90 y los 90s sueltos | 2026-11-21 | `clipagent/priority.py` (VIRAL_MIN_PUBLISH=45, SEO_GATE=80), `clipagent/config.py` (defaults 80/45), `config.yaml` | `test_policy.py`, `test_score_wiring.py`, `test_reglas_usuario_2026_11_17.py` |
| **No amontonar, ni en manual (P0 #4)**: franja, espaciamiento, tope diario, calentamiento y 1 por plataforma por franja se aplican TAMBIÉN a "Publicar ahora"; nunca fuera de franja ni dos de la misma plataforma en la misma franja (orden 2026-11-21 09:25) | 2026-11-21 09:25 | Panel `worker/src/jobs.ts` publishGate + `tryEnqueueClipPublish` (checkSlot también en manual) | `worker/test/jobs.test.ts` |
| **Republicación `\|repubN` (P0 #5)**: republicar mismo clip+plataforma+cuenta+día usa sufijo `\|repub1`, `\|repub2`… hasta clave libre (la clave base choca con la UNIQUE) | 2026-11-21 | Panel `worker/src/jobs.ts` (`enqueuePublishJobs`) | Verificación en vivo (job 118 FB 168) |
| **Validador reforzado, sin bypass (P0 #6, caso scooter 2026-11-21)**: el validador final va CON el SRT (transcribe el MP4 y lo pasa al `template_gate`); sin SRT la capa de subtítulos queda "sin medida" (débil), nunca fuerte; un clip sin banner / con subs de línea completa / sin SFX ya no pasa | 2026-11-21 | `clipagent/validate_all.py` (`transcribe_clip_to_srt`), `clipagent/final_review.py` (`template_gate`) | `test_validate_all.py`, `test_final_review.py` |
| **Re-makes recalculan `quality_gate` (P0 #7)**: un re-make recalcula el veredicto con `build_quality_gate` sobre el review nuevo; el veredicto viejo (rechazado) ya no queda pegado | 2026-11-21 | `clipagent/remake_all.py` (`_quality_gate_for_review`) | `test_remake_all.py`, `test_quality_gate.py` |
| **Facebook en Python, mismo patrón que IG/TikTok (P0 #8)**: subir el resto del archivo en un solo POST (Meta rechaza trozos fijos de 8 MB); aceptar 200/206; un 200/206 con cuerpo de error es error; sin `end_offset` preguntar el offset al servidor | 2026-11-21 | `clipagent/platforms.py` (Facebook Reels) | `test_facebook_upload.py` |
| **Un solo publicador: el Worker (P0 #9 / #64)**: la subida directa por adapters de Python está RETIRADA (duplicaba con el Worker); el cerebro NO publica, sube a la Cola del panel y el ciclo del Worker publica con todos los gates | 2026-11-21 | `clipagent/publish.py` (`publish_direct` live → `PanelBridge.publish_clip`), `.github/workflows/cerebro.yml` + `cerebro-sebastian.yml` (sin `publish loop --live`) | `test_fanout.py`, `test_worker_panel.py` |
| **Publicar en TODAS las cuentas vinculadas (regla 14:10)**: al publicar para un tenant, se publica en todas sus cuentas vinculadas (conectadas y verificadas) del idioma del clip; plataforma sin cuenta vinculada queda bloqueada con su motivo; nunca en el canal de otro idioma ni se crea una cuenta | 2026-11-21 14:10 | Panel `worker/src/jobs.ts` (resolución de destinos desde `api_accounts` por tenant e idioma) | `worker/test/jobs.test.ts` |
| **Programación en el panel (regla 14:17)**: `POST /api/clips/:id/publish` acepta `scheduled_for` (ISO 8601); si es futuro, los jobs se crean con `next_run_at` en ese instante; el gate no bloquea por la franja actual al programar, solo valida que el destino caiga en una franja válida; el ciclo re-verifica todo al ejecutar. Las tareas programadas viven en el panel (Worker/D1), NO en crons de Muse | 2026-11-21 14:17 | Panel `worker/src/index.ts` (PATCH `schedule`/`scheduled_for`) + `worker/src/jobs.ts` (`nextRunAtMs`) | Verificación en vivo tras despliegue |
| **Horario POR PLATAFORMA (orden 2026-11-22 01:11)**: cada plataforma sube a SU hora (Chile), vídeos diferentes para kaez y seba (cada uno su cola; nunca el mismo vídeo en ambas): YouTube 12:00, 18:00, 21:00; TikTok 13:00, 19:00, 22:00; Instagram 12:00, 18:00, 21:00; Facebook 13:00, 19:00, 22:00 (3/día por plataforma, mínimo). El auto-enqueue programa `next_run_at` a la próxima hora de la plataforma; la manual ("Publicar ahora", `force_now`) sigue inmediata. Fuente única: `PLATFORM_SCHEDULE` en el Worker | 2026-11-22 01:11 | Panel `worker/src/db.ts` (`PLATFORM_SCHEDULE`, `nextPlatformHourMs`) + `worker/src/jobs.ts` (auto-enqueue usa la hora de la plataforma) | `worker/test/publish-rules-2026-11-19.test.ts` (horario por plataforma) |
| **"Publicar ahora" = AHORA (orden 2026-11-22 01:07)**: el botón del panel manda `force_now: true` a `POST /api/clips/:id/publish`; salta SOLO la franja (y el horario por plataforma), el resto de frenos (topes, calentamiento, espaciamiento, rolling 24 h, calidad, SEO) sigue. La API también acepta `force_now` en el body | 2026-11-22 01:07 | Panel `worker/src/jobs.ts` (`forceNow` en `tryEnqueueClipPublish`) + `worker/src/index.ts` (lee `force_now`) + `frontend/dist/app.js` (botón) | `worker/test/quality-gate.test.ts` (forceNow fuera de franja encola; sin él frena) |
| **MCP como respaldo (regla 14:18)**: si el panel se traba, el respaldo es el MCP (`retry_job`); el MCP NO es el mecanismo de programación | 2026-11-21 14:18 | `worker/src/mcp.ts` (`/mcp`, `retry_job`, `verify_url`) | Prueba en vivo pendiente tras 21:00 |
| **Render en CircleCI (rama `circleci-project-setup`, NO en main)**: job `render` que renderiza UN clip a rubius desde una URL pública, con efectos (punch-in/zoom, shake, impacto, banner deslizante, fundido, whoosh/riser + boom), valida cuadro por cuadro y deja el MP4 como artefacto; disparo manual por API/UI, sin cron; NO publica, NO escribe en D1, NO toca el panel | 2026-11-21 | Rama `circleci-project-setup`: `.circleci/config.yml` (job `render`), `clipagent/circle_render.py`, `clipagent/rubius.py`, `clipagent/render_gate.py`, `docs/circleci-render.md` | `test_circle_render.py`, `test_rubius.py`, `test_rubius_gate.py` (en esa rama) |

## Decisiones registradas (no son código, pero mandan)

- **p2010 (Rubius, 38 s, viralidad 44,5): NO override.** Buscar
  momentos más fuertes (viralidad ≥ 50, SEO ≥ 90, 40–60 s,
  subtítulos verificados). Sin publicar nada todavía.
- **clip_02 (IlloJuan, 40 s): PAUSADO.** No publicar hasta que el
  usuario confirme por oído si IlloJuan dice algo.
- **Cola del panel (2026-11-18 14:37):** encolar los MEJORES clips
  de Rubius en GTA VI (viralidad ≥ 50, SEO ≥ 90, 40–60 s,
  subtítulos verificados), con el MP4 subido, sin publicar en
  público todavía. (A 2026-11-18 14:37 no existe aún ningún clip
  que cumpla todo: p2010 falla duración+viralidad, p1115 falla
  duración+subtítulos; la búsqueda de momentos más fuertes sigue
  en el otro chat.)
- **LIVE +15 vs LIVE absoluto:** pendiente de decisión del usuario
  (diseño §7 opción B vs §4 absoluto, ver trazabilidad).
- **MÁSTER PROMPT vs workers.py:** el supervisor para en
  `empaquetar` esperando aprobación; el MÁSTER PROMPT dice
  automático sin aprobación por clip. Contradicción abierta, no
  cambiar el comportamiento sin orden del usuario.
- **TikTok:** sin auditoría, la API solo permite SELF_ONLY
  (privado); el camino es Zernio draft-first. Sin captcha solver
  de pago ni integradores de pago (no autorizados).
- **Credenciales:** NUNCA en el repo. GitHub Secrets (cerebro) y
  D1 cifrado (panel). La clave de Zernio y el token de YouTube
  viven en GitHub Secrets; el token de Meta en D1 (panel Cuentas).

## Historial de cambios de reglas

- 2026-11-17: mínimo 36 s, subtítulos siempre/completos,
  descargar solo el segmento, chat ±120 s.
- 2026-11-18 13:33: Rubius prioritario, IlloJuan pausado.
- 2026-11-18 15:30: 8 influencers nuevos (AuronPlay, Ibai, TheGrefg,
  Xokas, DjMaRiiO, Knekro, RDJavi, Westcol) + IlloJuan REACTIVADO:
  10 streamers activos, todos con categoría GTA VI (game "GTA VI"
  en streamers.yaml y en Influencers del panel). RDJavi verificado
  en Twitch (twitch.tv/rdjavi, en directo GTA 6) y Kick
  (kick.com/rdjavi).
- 2026-11-18 14:16: no override p2010; logins fáciles (seba).
- 2026-11-18 14:17: duración 40–60 s; hype por ráfaga.
- 2026-11-18 14:25: SIN franja horaria (18:00–24:00 eliminada).
- 2026-11-18 14:37: encolar mejores Rubius GTA VI en la Cola web.
- 2026-11-18: categoría GTA VI obligatoria (Helix/Kick + capítulos).
- 2026-11-18: HYPE v2 (mediana rodante, msgs/10 s y /60 s, señales).
- 2026-11-18 14:54-15:01: layout estilo Willyrex (plantilla rdjavi v2) personalizado GTA 6 / Vice
  City (default); DJ Mariio olvidado por ahora; subtítulos simples
  estilo Willyrex (una línea blanca con contorno negro, sin karaoke, sin
  fondo); MarginV mínimo 680 → 200 (referencia 230); miniaturas
  estilo GTA 6; clip p3220 de Rubius (48 s, viralidad 53,8,
  SEO 92/94/94/94) re-renderizado así y a la Cola web.
- 2026-11-18: puerta de render PRE-CORTE (viralidad estimada >= 45
  pre-corte — calibrado: p3220 estima 46,3 vs real 53,8; p2010 44,5
  — y SEO pre-corte >= 90 en al menos una plataforma); si no pasa,
  se descarta sin descargar ni renderizar. El 50 real sigue en
  publicación. Medido: SEO pre-corte p3220 89/91/92/92.
- 2026-11-18 15:05: REGLA DE ORO automatizado 100% desde la web
  (Iniciar → todo solo, puede desloguearse).
- 2026-11-18: Ajustes del panel editables desde la web (D1 por
  tenant, leídos por Worker/brain); topes diarios como REGLAS con
  fuente y margen + escalado automático nueva → establecida
  (30 días + 10 publicaciones sin sanciones).
- 2026-11-21 08:47: duración 40–60 / 25–35 ELIMINADA para siempre, en
  todo el canal; solo queda el tope de 60 s.
- 2026-11-21 09:25: NO AMONTONAR ni en manual — franja, espaciamiento,
  topes y calentamiento mandan también en "Publicar ahora"; 1 por
  plataforma por franja. ENGLISH TOO: la cola EN se publica con las
  mismas reglas, en sus propias franjas, nunca el mismo vídeo que ES.
- 2026-11-21: auditoría memoria-vs-proyecto (76 filas) → P0
  programados en v0.1.3 (umbrales 45/80 fuente única, sin clamp
  40–60, validador con SRT, quality_gate en re-makes, |repubN,
  Facebook Python, un solo publicador = el Worker).
- 2026-11-21 14:10: publicar en TODAS las cuentas vinculadas del
  tenant (regla programada en el panel).
- 2026-11-21 14:17: la programación vive EN EL PANEL (scheduled_for
  → jobs con next_run_at), no en crons de Muse; MCP solo como
  respaldo si el panel se traba.
- 2026-11-21: render en CircleCI (rama circleci-project-setup): job
  `render` con efectos (zoom, shake, impacto, whoosh/riser); $0,
  plan Free; NO publica ni toca D1/panel.
- 2026-11-21 20:45: CRON SOLO EN FRANJAS (ahorro D1, orden del
  usuario: no quemar el límite). El Worker solo despierta en las
  franjas hora Chile 12:00-14:00, 18:00-20:00, 21:00-23:00; fuera de
  franja ni despierta, así que no gasta escrituras D1. En UTC con
  1 h de margen por DST (-03/-04): `*/5 15-18 * * *` y
  `*/5 21-23,0-3 * * *` (132 ticks/día vs 1.440). La puerta sigue
  filtrando la franja exacta en hora Chile. El ciclo en vacío ya
  costaba ~0 escrituras (puerta A3 de solo lectura + salida
  silenciosa); el gasto real es publicar y el cerebro, no el cron.
  Programado en wrangler.toml, wrangler.sebastian.toml,
  scripts/deploy_with_token.py (SLOT_CRONS) y
  scripts/provision-tenant.sh; los nocron siguen con crons [].
- 2026-11-21 20:52: MÍNIMO 3 VÍDEOS POR PLATAFORMA AL DÍA (kaez y
  sebastián, en las 3 franjas 12-14, 18-20, 21-23). Orden del usuario
  sabiendo que choca con el calentamiento 1/día. Cambios: new_account_daily
  1→3 (tope de calentamiento TT/IG/FB) y tiktok_plan 2→3, en
  DEFAULT_PANEL_SETTINGS y en effectiveSettings (db.ts, los defaults
  viven duplicados: el objeto y los clampInt/planFor de
  effectiveSettings) y en la settings de la D1 de cada tenant.
  new_account_daily_youtube y los máx sin tocar. NOTA: con solo estos
  dos valores TikTok llega a 3/día, pero IG/FB (plan 1, máx 2) y
  YouTube (plan 1, máx 2) siguen topados a 1-2/día; para que lleguen
  a 3/día haría falta subir también instagram_plan/facebook_plan/
  youtube_plan y sus máx. Desplegado a kaez (d427c831); Sebastián
  pendiente al reseteo de las 21:00 (su D1 agotada).
- 2026-11-21 21:09: COMPLETA el mínimo 3 POR PLATAFORMA al día —
  el usuario ordena subir IG/FB a 3 y YouTube a 6 (YouTube "soporta
  más"). Cambios en db.ts (ambos sitios): instagram_plan 1→3,
  instagram_max 2→3, facebook_plan 1→3, facebook_max 2→3,
  youtube_plan 1→6, youtube_max 2→6, new_account_daily_youtube
  1→6 (+ daily_limit_* legacy a juego). Tests actualizados
  (settings-maturity, publish-rules-2026-11-19, endpoints-nuevos-2,
  manual-publish, clip-language 0019); tsc limpio, bun 740/749
  (6 fallos preexistentes). Settings en D1: kaez (su cuenta)
  IG 3/3, FB 3/3, YT 6/6, new_account_daily_youtube 6; Sebastián
  en clips-avc y clips-avc-sebastian (los 9 valores, YT ya estaba
  en 6/6). Desplegado a kaez (94665dbc, cron OFF). A Sebastián NO
  se le desplegó (lo hace el principal en el paquete 21:00).
- 2026-11-21 21:57: VIRALIDAD POR OUTLIER MULTIPLE (orden del
  usuario, del repo instagram-agent-skill, MIT): la fuente viral
  rankea por vistas / mediana de ESA cuenta, no por likes crudos.
  `viral_source.min_outlier_multiple: 3.0` en config.yaml (clave
  obligatoria, falla fuerte si falta). La mediana sale de los
  últimos 12 Shorts del canal (del mismo listado de yt-dlp, sin
  re-llamar); sin al menos 3 Shorts no hay mediana y el candidato
  pasa por fallback (vistas >= min_views y likes >= min_likes)
  marcado `outlier_status: sin-mediana`. Outlier < 3x se rechaza
  con motivo en el informe. El outlier queda en el manifiesto
  del candidato. `clipagent/viral_source.py`
  (channel_shorts_median, outlier_multiple, discover_candidates)
  | `test_viral_source.py::TestOutlier` (40x pasa, 1,33x se
  rechaza, sin-mediana por fallback, clave faltante falla fuerte).
- 2026-11-21 21:57: HASHTAGS A 5 + CAPTION CON FRASE BUSCABLE
  (orden del usuario, instagram-agent-skill): Instagram capó los
  hashtags a 5 por post (18-dic-2025). `metadata.py` PLATFORMS
  instagram_reels 10→5 y `package_for_platforms` IG `[:10]`→`[:5]`;
  `dedupe_hashtags` (únicos, sin duplicar, techo 5) en los 4
  paquetes; `config.yaml` hashtags en 6→5 (sale #clips, el más
  genérico). La primera línea del caption lleva la frase buscable
  como texto natural (GTA 6, streamer, momento) y el gancho, en
  <=125 caracteres (lo visible en el feed antes de "...more"),
  sin saludo ni hashtag como primer carácter
  (`caption_first_line`; "GTA VI" y "GTA 6" son el mismo juego).
  La fuente viral usa el título como primera línea de la
  descripción (`_viral_description`). `clipagent/metadata.py`,
  `clipagent/viral_source.py`, `config.yaml` |
  `test_metadata_caption.py` (14 tests).
- 2026-11-21 21:57: HOOK SCORE PARA MATAR TÍTULOS DÉBILES
  (orden del usuario, instagram-agent-skill): port a español de
  hookscore.py (5 checks + dealbreakers). USO HONESTO: caza malos
  (AUC 0.83) pero NO elige ganadores (AUC 0.56 del propio repo):
  score < 50 = débil y el título queda marcado `hook_weak` en el
  manifiesto para revisión humana; NUNCA bloquea el clip y NUNCA
  se usa para elegir "el mejor" entre dos títulos decentes.
  Integrado en la fuente viral (`process_candidate`: el score
  queda en el report). `clipagent/hookscore.py` (score_hook,
  WEAK_BELOW=50, CLI `python -m clipagent.hookscore --hook ...`)
  | `test_hookscore.py` (10 tests, títulos reales).
- 2026-11-21 21:57: AUDITORÍA DE NUESTROS CLIPS PUBLICADOS
  (orden del usuario, instagram-agent-skill): rankea NUESTROS
  reels por outlier multiple (vistas / mediana de NUESTRA cuenta)
  y envíos por alcance, no por views crudos. v1 solo YouTube
  Shorts (vistas públicas vía yt-dlp, sin login). TikTok/IG/FB
  sin métricas (sin scraping: lo prohíbe el repo y nos puede
  banear; pendientes de sus APIs oficiales). Alcance de
  no-seguidores, hold a 3s, envíos y follows por alcance tampoco
  están en v1 (vienen de YouTube Studio / Meta Insights; el
  informe dice qué falta). Solo LECTURA (D1 jobs posted o CSV de
  evidence_url; cero escrituras). Distingue problema de hook
  (pocas views) de problema de perfil (views sin follows, si hay
  dato de follows). `clipagent/audit_published.py`
  (`python -m clipagent.audit_published --tenant sebastian`,
  informe en texto + JSON en runs/audit-published-<fecha>/) |
  `test_audit_published.py` (11 tests, datos simulados).
