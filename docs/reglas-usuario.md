# Reglas del usuario — GTA 6 clips (código, no solo chat)

> TODO lo que el usuario dice en el chat queda guardado en el
> REPOSITORIO (código/config/docs) y desplegado en la WEB
> (https://avc-em9.pages.dev + Worker). Nada local cuenta.
> Si una regla cambia, cambia aquí Y en el código Y en el panel.

## Reglas vigentes (con su punto de aplicación)

| Regla | Desde | Dónde se impone | Test |
|---|---|---|---|
| Clips **40–60 s**, nunca < 40 ni > 60, nunca 30 s exacto, rango 25–35 prohibido | 2026-11-18 (reemplaza mínimo 36 s de 2026-11-17) | `clipagent/config.py` RULES_MIN_DURATION_S=40, `config.yaml` clips/rules, `precise_cut.py`, `validate.py`, `workers.py`, `policy.py`; panel `worker/src/jobs.ts` publishGate, `index.ts` validadores, front `durationBand` + texto Editor | `test_reglas_usuario_2026_11_17.py`, `test_reglas_usuario_2026_11_18.py`, `worker/test/jobs.test.ts` |
| **Puerta de render PRE-CORTE**: el clip SOLO se corta/renderiza si ya pasa las puertas (viralidad estimada + SEO pre-corte >= 90 en al menos una plataforma); si no pasa, se descarta SIN descargar ni renderizar. Umbral pre-corte de viralidad **45 (calibrado, no 50)**: la estimación usa defaults conservadores 5,0 y subestima ~7 puntos (p3220 estima 46,3 vs real 53,8; p2010 44,5). Con 50, p3220 se descartaría. El 50 REAL sigue en publicación. SEO pre-corte con el paquete provisional (ficha sin clip + títulos del chat, juego normalizado); sin juego conocido o sin título, el SEO queda sin medir y decide solo la viralidad | 2026-11-18 | `clipagent/policy.py` render_gate (puerta 50/90, max SEO), `clipagent/workers.py` PRE_CORTE_VIRAL_MIN=45 + pre_corte_package + gate_peak_for_render + detect_peaks (descarta) + _h_corte (re-verifica) + advance_pipeline (pasa scores al corte), `auto_clips.py` (puerta antes de descargar el segmento); panel: POST /api/clips rechaza score < 50 y SEO máx. < 90 (cuando vienen seo_scores) | `test_render_gate.py` (p3220 real pasa, p2010 no, plano 44,0 no) |
| Hype por **ráfaga** v2: velocidad msgs/10 s, msgs/60 s y msgs/s contra baseline rodante (mediana 5 min); ratio pico/baseline; señales risas, emotes hype, repetición por usuarios distintos | 2026-11-18 | `clipagent/ingest.py` score_windows (hype_score 0–10 por ventana), `clipagent/detect.py` chat_component (bonus +0,25×hype_score antes de topes), `clipagent/workers.py` detect_peaks (hype guardado por pico) | `test_hype_v2.py` (ráfaga de 60 msgs/10 s supera a 120 repartidos) |
| **10 streamers activos, todos GTA VI**: Rubius, IlloJuan (REACTIVADO 2026-11-18 15:30, estuvo pausado 13:34–15:30), AuronPlay, Ibai, TheGrefg, Xokas, DjMaRiiO, Knekro, RDJavi, Westcol — platform twitch, language es, estado concedido, fecha_permiso 2026-11-18 (IlloJuan 2026-11-16), publish_platforms 4 plataformas, enabled true. clip_02 de IlloJuan sigue publish_blocked por subtítulos sin verificar (no publicar) | 2026-11-18 15:30 (reemplaza "Rubius prioritario, IlloJuan PAUSADO" de las 13:33) | `streamers.yaml` (10 entradas, game "GTA VI"); watcher vigila enabled; panel D1 influencers active=1 + game "GTA VI" (tenant sebastian) | `test_streamers.py::test_diez_activos_todos_gta_vi`, `test_reglas_usuario_2026_11_18.py` |
| Sin override de p2010 (viralidad 44,5 < 50) | 2026-11-18 | `priority.publish_status` (viralidad < 50 descarta incluso con override SEO) | `test_reglas_usuario_2026_11_18.py` |
| **SIN franja horaria**: da lo mismo la hora, solo se sube | 2026-11-18 14:25 | ELIMINADA de `clipagent/publish.py` check_limits (PUBLISH_WINDOW=None), `limits.yaml`, panel `jobs.ts` publishGate (sin check de hora), `index.ts`, front | `test_limits.py`, `test_platform_errors.py`, `worker/test/jobs.test.ts` |
| **Categoría GTA VI obligatoria**: clip solo válido si la categoría es GTA VI (Grand Theft Auto VI); Just Chatting u otro juego se descarta | 2026-11-18 | `clipagent/category.py` (is_gta6, category_at, capítulos del VOD), `watcher.py` TwitchHelix.live_category/channel_category (Helix oficial), `clipagent/live.py` kick_channel_category (Kick API v2), `auto_clips.py` chat_stream_info, `workers.py` detect_peaks (descarta picos fuera de GTA VI), `policy.py` gate 0.5 (categoría conocida no-GTA-VI → descartado; SIN categoría → pasa con nota, el gate vive en el corte — detect_peaks descarta/etiqueta — y en el panel POST/publishGate; así no se bloquean clips heredados como p3220), `moment_card.py` (category en ficha); panel: `clips.game` + `isGtaViGame` (`db.ts`), publishGate (`jobs.ts`), POST/PATCH (`index.ts`), Cola/Editor (front) | `test_category_gta6.py`, `test_policy.py`, `worker/test/jobs.test.ts` |
| Layout split vertical: juego arriba (1320), cámara abajo (600), sin tapar minimapa ni HUD; dos webcams apiladas; si el juego no se ve, la cámara se achica (480), nunca al revés | 2026-11-16 | `clipagent/render.py` (_split_params, split_game_h/split_cam_h/split_cam_small_h), `config.yaml` clips | `test_layout_split.py`, `test_reglas_usuario_2026_11_18.py` |
| **Layout RDJavi personalizado GTA 6 / Vice City (default para GTA 6)**: streamer ARRIBA, gameplay ABAJO; fondo desenfocado 0-730, webcam centrada 580x590 en (250,190) con esquinas redondeadas y 50 px de solape, gameplay 730-1770, banda inferior 1770-1920 con barra atardecer rosa/violeta/naranja + handle @ELCLIPDEGTA6 \| GTA VI; cortes duros; cámara fija salvo punch-in puntual | 2026-11-18 (reemplaza juego-arriba/cámara-abajo; DJ Mariio aparcado) | `clipagent/render.py` (RDJAVI_*, `_split_rdjavi_graph`, `detect_framing`, `resolve_split_variant`), `config.yaml` layout | `test_layout_referencia.py` (geometría exacta + render real 1080x1920) |
| **Subtítulos estilo RDJavi total**: una línea centrada en y≈1690 (MarginV 230, mínimo 200), letra chica 32 px fina blanca con contorno negro, sin fondo ni resaltado palabra por palabra; la regla vieja de 680 queda reemplazada | 2026-11-18 | `clipagent/transcribe.py` (SUBTITLE_REF_MARGIN_V=230, SUBTITLE_MIN_MARGIN_V=200), `clipagent/config.py` (estilo "ref", default), `config.yaml` subtitles | `test_reglas_usuario_2026_11_17.py`, `test_reglas_usuario_2026_11_18.py`, `test_layout_referencia.py` |
| **Miniaturas estilo GTA 6 / Vice City**: texto corto de reacción + fotograma elegido por Gemini con criterio Vice City (neón, palmeras, tercera persona); quemado con condensada negrita y sombra rosa neón | 2026-11-18 | `clipagent/metadata.py` (THUMBNAIL_TEXTS, THUMBNAIL_STYLE), `clipagent/vision.py` (_build_prompt), `clipagent/thumbnails.py` (thumbnail_with_text) | `test_thumbnails_gta6.py` |
| **Configuración desde la web (Ajustes)**: layout, estilo de subtítulos, duración mín/máx, viralidad/SEO mínimos, topes diarios, categoría, estilo de miniatura, hype on/off, Iniciar/Parar — guardado en D1 por tenant y leído por el Worker/brain; nada de editar código | 2026-11-18 | Panel `worker/src/` (GET/PUT /api/settings, GET /api/bridge/settings) + `clipagent/panel_settings.py` (apply/fetch, env PANEL_WORKER_URL/PANEL_BRIDGE_TOKEN) | `test_panel_settings.py` |
| **REGLA DE ORO — automatizado 100%**: si pulsa Iniciar, todo se hace automático y puede desloguearse; el trabajo vive en GitHub Actions (Cerebro) + Worker cron */5; nada depende de su sesión, navegador ni teléfono | 2026-11-18 15:05 | Panel Worker (`worker_running` persistente en D1, handler `scheduled()` sin sesión) + workflow Cerebro | Verificación en vivo en la web (ver informe) |
| **Topes diarios como REGLAS (los pone el agente)**: YT plan 3/máx 5, resto plan 2/máx 3, nuevas 1/día, Zernio 15/día + 5 borradores, 2 h entre publicaciones; **escalado automático**: nueva → establecida a los 30 días desde la 1ª publicación + 10 publicaciones sin sanciones (lo aplica el Worker cada día; se ve en Ajustes) | 2026-11-18 | `limits.yaml` (fuente + margen por plataforma), `clipagent/publish.py` (load_limits — bug 2026-11-18: antes no leía el archivo de la raíz, corregido), panel Worker + D1 | `test_limits.py`, `test_panel_settings.py` |
| Subtítulos SIEMPRE y COMPLETOS, quemados, todo lo que dice el influencer; nunca inventar texto; sin voz verificable = BLOCKED / needs_human_transcription | 2026-11-17 22:10 | `config.yaml` subtitles, `clipagent/transcribe.py`, `clipagent/config.py`, `policy.py`; panel `publishGate` (publish_blocked) + migración 0014 | `test_subtitles_always.py`, `test_reglas_usuario_2026_11_17.py` |
| Descargar SOLO la parte del clip, nunca el video entero; chat solo en ventana ±120 s del pico | 2026-11-17 23:00 | `auto_clips.py` download_twitch_segment / download_chat_window, `config.yaml` clips.chat_window_s=120 | `test_chat_window.py` |
| Prioridad **LIVE sobre VOD**; fórmula INTOCABLE viralidad^0,5 × SEO^0,3 × chat_norm^0,2; filtros duros viralidad < 50 descarta, SEO < 90 no publica salvo override (70–89); clip > 7 días −10 pts/día; no dos clips del mismo momento el mismo día en la misma plataforma | 2026-11-16/17 | `clipagent/priority.py` (DEFAULT_WEIGHTS, VIRAL_MIN_PUBLISH=50, SEO_GATE=90, age_penalty, moment_key), `clipagent/policy.py`, panel `worker/src/db.ts` (LIVE primero, VOD después) | `test_priority.py`, `test_policy.py`, `worker/test/queue-order.test.ts` |
| Publicación automática con red de seguridad, sin aprobación por clip; puertas: preview visto, subtítulos completos, permiso streamer, SEO, topes diarios, espaciamiento 2 h | MÁSTER PROMPT FINAL | `clipagent/policy.py` evaluate, `clipagent/publish.py` check_limits, panel `jobs.ts` publishGate | `test_policy.py`, `worker/test/jobs.test.ts` |
| Regla Nº1 no ser baneados: topes por plataforma (YT plan 3/techo 5, resto 2/3), cuentas nuevas 1/día la primera semana | 2026-11-16 | `limits.yaml`, `clipagent/publish.py` DAILY_LIMITS/NEW_ACCOUNT_DAILY, panel `jobs.ts` (plan/techo) | `test_limits.py` |
| Todo **$0, gratis** (Gemini Flash-Lite gratis, sin servicios de pago) | 2026-11-16 | Sin claves de pago en `config.yaml` (test: sin api_key/client_secret en config versionada); secretos solo por env/GitHub Secrets | `test_reglas_usuario_2026_11_18.py` |
| Panel en **Cloudflare avc-em9.pages.dev**, login por usuario, cada tenant ve solo lo suyo | 2026-11-17 | `config.yaml`/`DEFAULT_CONFIG` panel.base_url, `panel-backend/` (Worker + D1 + R2 + Pages), `worker/src/db.ts` (tenant_id en todo) | `test_reglas_usuario_2026_11_18.py` |

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
- 2026-11-18 14:54-15:01: layout RDJavi personalizado GTA 6 / Vice
  City (default); DJ Mariio olvidado por ahora; subtítulos simples
  RDJavi (una línea blanca con contorno negro, sin karaoke, sin
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
