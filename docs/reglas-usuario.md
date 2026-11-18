# Reglas del usuario — GTA 6 clips (código, no solo chat)

> TODO lo que el usuario dice en el chat queda guardado en el
> REPOSITORIO (código/config/docs) y desplegado en la WEB
> (https://avc-em9.pages.dev + Worker). Nada local cuenta.
> Si una regla cambia, cambia aquí Y en el código Y en el panel.

## Reglas vigentes (con su punto de aplicación)

| Regla | Desde | Dónde se impone | Test |
|---|---|---|---|
| Clips **40–60 s**, nunca < 40 ni > 60, nunca 30 s exacto, rango 25–35 prohibido | 2026-11-18 (reemplaza mínimo 36 s de 2026-11-17) | `clipagent/config.py` RULES_MIN_DURATION_S=40, `config.yaml` clips/rules, `precise_cut.py`, `validate.py`, `workers.py`, `policy.py`; panel `worker/src/jobs.ts` publishGate, `index.ts` validadores, front `durationBand` + texto Editor | `test_reglas_usuario_2026_11_17.py`, `test_reglas_usuario_2026_11_18.py`, `worker/test/jobs.test.ts` |
| Hype por **ráfaga** v2: velocidad msgs/10 s, msgs/60 s y msgs/s contra baseline rodante (mediana 5 min); ratio pico/baseline; señales risas, emotes hype, repetición por usuarios distintos | 2026-11-18 | `clipagent/ingest.py` score_windows (hype_score 0–10 por ventana), `clipagent/detect.py` chat_component (bonus +0,25×hype_score antes de topes), `clipagent/workers.py` detect_peaks (hype guardado por pico) | `test_hype_v2.py` (ráfaga de 60 msgs/10 s supera a 120 repartidos) |
| **Rubius prioritario, IlloJuan PAUSADO** (no publicar, no encolar clip_02) | 2026-11-18 | `streamers.yaml` (rubius enabled, illojuan enabled=false); watcher filtra enabled; `publish.py` retiene clips de streamer desactivado; panel D1 influencers active=0 + `LAUNCH_INFLUENCER_HANDLES` | `test_streamers.py`, `test_reglas_usuario_2026_11_18.py` |
| Sin override de p2010 (viralidad 44,5 < 50) | 2026-11-18 | `priority.publish_status` (viralidad < 50 descarta incluso con override SEO) | `test_reglas_usuario_2026_11_18.py` |
| **SIN franja horaria**: da lo mismo la hora, solo se sube | 2026-11-18 14:25 | ELIMINADA de `clipagent/publish.py` check_limits (PUBLISH_WINDOW=None), `limits.yaml`, panel `jobs.ts` publishGate (sin check de hora), `index.ts`, front | `test_limits.py`, `test_platform_errors.py`, `worker/test/jobs.test.ts` |
| **Categoría GTA VI obligatoria**: clip solo válido si la categoría es GTA VI (Grand Theft Auto VI); Just Chatting u otro juego se descarta | 2026-11-18 | `clipagent/category.py` (is_gta6, category_at, capítulos del VOD), `watcher.py` TwitchHelix.live_category/channel_category (Helix oficial), `clipagent/live.py` kick_channel_category (Kick API v2), `auto_clips.py` chat_stream_info, `workers.py` detect_peaks (descarta picos fuera de GTA VI), `policy.py` gate 0.5, `moment_card.py` (category en ficha); panel: `clips.game` + `isGtaViGame` (`db.ts`), publishGate (`jobs.ts`), POST/PATCH (`index.ts`), Cola/Editor (front) | `test_category_gta6.py`, `test_policy.py`, `worker/test/jobs.test.ts` |
| Layout split vertical: juego arriba (1320), cámara abajo (600), sin tapar minimapa ni HUD; dos webcams apiladas; si el juego no se ve, la cámara se achica (480), nunca al revés | 2026-11-16 | `clipagent/render.py` (_split_params, split_game_h/split_cam_h/split_cam_small_h), `config.yaml` clips | `test_layout_split.py`, `test_reglas_usuario_2026_11_18.py` |
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
- 2026-11-18 14:16: no override p2010; logins fáciles (seba).
- 2026-11-18 14:17: duración 40–60 s; hype por ráfaga.
- 2026-11-18 14:25: SIN franja horaria (18:00–24:00 eliminada).
- 2026-11-18 14:37: encolar mejores Rubius GTA VI en la Cola web.
- 2026-11-18: categoría GTA VI obligatoria (Helix/Kick + capítulos).
- 2026-11-18: HYPE v2 (mediana rodante, msgs/10 s y /60 s, señales).
