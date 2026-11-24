# SPEC — Corte automático de clips desde YouTube (sin Muse, sin manual)

Fecha: 2026-11-24 ~01:00 (v1) / ~01:15 (v2) / ~01:20 (v3) / ~01:30 (v4) / ~01:45 (v5) / ~01:50 (v6) / ~01:56 (v7) / ~02:00 (v8).
Orden del usuario (2026-11-24 00:53, chat del plan maestro, como
regla): el corte de clips — incluido desde un video de YouTube — lo
hace EL PROGRAMA solo, nunca Muse a mano. Caso que la motiva: video de
Rubius «I Got 6 Stars in GTA 6 ⭐»
(https://youtu.be/NrNdw_Y6_bI, momento de las 6 estrellas en el
aeropuerto).
Método: EGM (esta spec → revisión pez dorado → aprobación del usuario →
implementar).
Estado: v8, APROBADA CON CORRECCIONES por el pez dorado (vuelta 7) y
correcciones aplicadas. Pendiente de aprobación del usuario para
implementar.

## 1. Estado actual (verificado en código 2026-11-24 00:55–01:30)

- `clipagent/youtube.py` (2026-11-21): `youtube_check` (yt-dlp
  --flat-playlist en JSON sobre /streams Y /videos del canal, sin API
  key, $0, misma forma (live, vods) que kick/twitch),
  `download_youtube_segment` (yt-dlp --download-sections, solo el
  segmento), verificación del juego por regex de título + canal.
  3 intentos con backoff (bot-check intermitente). Formato
  `298+140/bv[height<=720]+ba/b`. Invocación que funciona en la VM:
  `.venv/bin/python -m yt_dlp -J --compat-options no-certifi`.
- `clipagent/vod_fallback.py`: `latest_vod_for` (l. 343) despacha POR
  PLATAFORMA: la rama YouTube exige `platform == "youtube"` y
  `channel_url`; una entrada con `platform == "twitch"` va siempre
  por el chequeo de Twitch y su `channel_url` de YouTube se ignora
  (esto es la evidencia de §3.5: el pase normal NO ve el canal
  YouTube de un streamer de Twitch). El job vod-fallback de
  `cerebro-live.yml` ya corre con `--youtube`. `run_auto_clips`
  (l. 423) ejecuta `auto_clips.py <vod_url> --lang --top-n
  --llm-select` y espera `manifest.json`. Puertas duras
  (`clip_passes_gates`, l. 235): viralidad ≥45, SEO ≥80 en las 4
  plataformas, 40–60 s, juego GTA VI. Falla cerrado. `burn_and_enqueue`
  (l. 598) quema subtítulos y encola en el panel (origin='vod'), SIN
  publicar. Dedup por (vod_id, start_sec) contra el panel
  (`already_enqueued`, l. 262). Límites: máx. 2 VODs y 2 clips por
  corrida, 6 h de enfriamiento por VOD, estado en
  `runs/vod-fallback/state.json`.
- `auto_clips.py` es Twitch/Kick: baja el chat (TwitchDownloaderCLI),
  detecta picos con `clips_chat_v2.py` (velocidad del chat), baja solo
  segmentos de ~80 s, transcribe, selecciona con LLM, renderiza,
  manifiesta. El fallback por audio (`--audio-fallback`) es SOLO para
  Kick (`auto_clips.py:1021`): usa `kick_downloader/core.py:551`
  `sample_audio_peaks` (muestrea el audio 30 s cada 900 s, faster-
  whisper tiny, puntúa risas/hype) y construye el detail como
  `{ts: {"msgs": 0, "risas": 0, "hype": 0}}`.
- El hueco (docstrings de youtube.py y vod_fallback.py): con el flag
  activado el fallback LISTA los VODs de YouTube y el watcher los VE,
  pero `run_auto_clips` sobre una URL de YouTube falla y el VOD se
  salta sin clips. «El corte automático desde YouTube (segmento +
  picos por heatmap en vez de chat) es trabajo posterior, no este
  cambio.» ESTA SPEC ES ESE TRABAJO.
- Precedente manual (NO automático, no repetir a mano):
  `~/workspace/gta6-clips/runs/speed-52h-save-2026-11-21/` — VOD de
  YouTube de Speed (OIeLbmyraRQ, 22.061 s), ventana 05:31:46–05:32:34
  (48 s), solo el segmento con yt-dlp --download-sections; el
  bot-check bloqueó los intentos 1–2 y el 3º funcionó. SEO YT 90 /
  TikTok 91 (IG 89 / FB 88, estado "override"), motor local
  `clipagent/seo_score.py` (el SEO NO lo puntúa Gemini; Gemini pone
  solo el título). Viralidad sin score de chat (el manifiesto dice
  literalmente que no existe score de chat para un VOD de YouTube).
  El clip quedó como ARCHIVO, sin clip ni job en D1.
- **Video de aceptación, VERIFICADO 2026-11-24 01:10 con**
  `.venv/bin/python -m yt_dlp -J --compat-options no-certifi
  --no-warnings https://youtu.be/NrNdw_Y6_bI`:
  `NrNdw_Y6_bI`, «Conseguí 6 Estrellas en GTA 6 ⭐», **duración 1.750
  s (29 min 10 s)** — un video largo, NO un clip corto editado;
  canal Rubius Z
  (https://www.youtube.com/channel/UCcjIvuxmWlS5IEQ0JdPV4Ng);
  3.530.194 vistas; 62.831 likes; subido 2026-11-22; no es directo;
  **SIN heatmap de replay** (el campo `heatmap` no viene en el -J).
  Se asume que trae webcam, como los videos GTA 6 de Rubius; la
  corrida de aceptación lo verifica.
- `cerebro-live.yml`: `workflow_dispatch` con inputs (tenant,
  streamers, minutes, force). Job vod-fallback: `if:
  needs.detect.outputs.count == '0'`, `needs: detect`,
  `timeout-minutes: 60`. `run()` (`vod_fallback.py:781+`) retorna 0
  si hay directos en GTA VI antes de mirar VODs; no tiene parámetro
  `youtube_url` y `main()` (l. 961) no tiene flag `--youtube-url`.
  El token de un solo uso vive SOLO en
  `client_payload.brain_token` del dispatch `panel-live` y lo canjea
  el job `detect` (su bootstrap hace `exit 1` sin token); la sesión
  pasa a los demás jobs por el artefacto
  `panel-session-<tenant>-<run_id>` que sube `detect`. Un
  `workflow_dispatch` no trae `client_payload`, así que no hay token
  que canjear. El job vod-fallback restaura y sube
  `runs/vod-fallback/state.json` como artefacto (mismo artefacto
  que usa para el enfriamiento de 6 h). El tenant llega por env
  `PANEL_TENANT` (`main()` NO acepta flag `--tenant`; acepta
  `--max-vods, --max-clips, --top-n, --streamers, --state,
  --out-dir, --dry-run, --ignore-cooldown, --youtube`).
- Puntuación (`clipagent/detect.py`): `score_clip(detail,
  chat_multiple, ...)` (l. 436) con WEIGHTS que suman 10 (hook 2,0;
  claridad 1,5; novedad 1,5; chat 1,5; audio 1,0; loop 1,5;
  packaging 1,0). Sin entrada humana, los 5 componentes humanos se
  estiman en 5,0 (`ESTIMATED_DEFAULT`, l. 82, BETA por definición).
  **Si `detail is None` o `chat_multiple is None`, el componente
  chat se estima en 5,0.** Si se pasa un detail, `chat_component`
  (l. 196) lo calcula — y tiene un early return: `if msgs <= 0:
  return 0.0` (l. 226-227), ANTES de aplicar `_chat_base` y ANTES
  del bonus hype v2. Con `detail={"msgs": 0, ...}` el componente
  chat es **0,0**, no la base del múltiplo. (Esto es lo que rompía
  el adaptador de la v2: el detail de Kick con msgs=0 da chat 0,0.)
  `_chat_base(multiple)` (l. 182): 1,0 → 1,0; 2,0 → **3,0** (la
  banda 3–4 empieza en 3,0: `3,0 + (2,0−2,0)/2,0`); 3,0 → 3,5;
  8,0 → 7,33. `filter_passed` (l. 252) exige múltiplo ≥3x O ráfaga
  hype + mensajes en la ventana; con `detail=None` falla («sin
  mensajes en la ventana»). `clip_passes_gates` NO lo consulta, así
  que no bloquea el encolado, pero el manifiesto lleva
  `filter_passed: false`.
- Componente de audio (`detect.py:323-363`): `audio_component(audio)`
  acepta `None` («sin audio medido») o un dict con `stats`
  (`std_db`, `steady_frac`, `range_db`) + `words`, o `std_db` /
  `steady_frac` / `words` directos. Mapeo: plano (std < 9 dB y ≥45%
  estables) → 2; ≥15 → 9; 11–15 → 7–8; 9–11 → 5–6; <9 → 4; rango
  (range_db = máx − mín del RMS por segundo) ≥25 dB → +1, 15–25 dB
  → +0,5; 0 palabras → máx. 4; ≥50 palabras → +1 (tope 10).
  **El valor medido va de 2 a 10, no de 0 a 10.** El productor de
  esas estadísticas es `clipagent/audio.py`: `rms_per_second`
  (l. 122), `audio_stats(rms_list)` (l. 145) y
  `transcript_words(transcript)` (l. 267). El peso del audio es
  `audio_weight_for(platform)` (l. 390): 1,0 por defecto (1,5 para
  kick); con `platform=None` es 1,0.
- Prioridad (`clipagent/priority.py`): media geométrica
  `viral^0,5 × SEO^0,3 × chat_norm^0,2`, con `chat_normalized(multiple)
  = _chat_base(multiple) × 10`. Auto-archivo:
  `should_auto_archive(age_days, uploaded, priority_value,
  virality)` (l. 138), `STALE_PRIORITY_MAX = 70`, `STALE_DAYS = 3`
  (sin parámetro `min_priority` todavía). `order_queue(clips,
  platform, now=None, weights=None)` (l. 172) RECALCULA la
  prioridad desde los puntajes que le pasan y llama a
  `should_auto_archive` (l. 194); el `score` guardado en D1 en el
  encolado NO se usa ahí. Su único llamador es
  `clipagent/publish.py:712` (`_order_items`), que arma los ítems
  con `_clip_scores` (l. 679-700): lee el **manifiesto local**
  (`load_clip_package`) y devuelve `{"virality", "seo",
  "chat_multiple": (clip.get("viral") or {}).get("viral_multiple")
  or 1.0, "vod_id", "start_sec", "created_at", "uploaded",
  "seo_override"}`; `_order_items` pasa
  `dict(scores, _item=item)` a `order_queue`. Es decir: en el
  momento de publicar, el múltiplo de chat viene del manifiesto
  local (`viral.viral_multiple`), y el umbral de auto-archivo
  tiene que viajar en ese mismo dict de puntajes.
- Encolado y puente (verificado): `burn_and_enqueue` hace
  `vod_id_int = int(vod_id)` con fallback a 0 (l. 723) y su payload
  NO lleva `viral`: lleva `score` (prioridad), `seo_title`,
  `seo_description`, `seo_tags`, `reason` (texto «viralidad X —
  SEO ...») y `thumbnail_text`. El título del clip sale del
  paquete del manifiesto: `title = viral.get("best_title") or
  clip.get(title_key) or ""` (l. 633-637), donde `viral =
  clip.get("viral") or {}` y `title_key` es `title_es`/`title_en`
  según el idioma; el POST /api/clips exige título no vacío
  («Falta el título del clip»). El Worker YA acepta el objeto
  viral: `parseViralInput(body)` (`db.ts:324`, contrato 2026-11-18)
  lee una **pick-list** del objeto `viral`: `seo_scores`,
  `score_kind`, `viral_score`, `hype_score`, `priority`,
  `viral_breakdown`, `quality_gate`, `platforms`. **Cualquier otro
  campo (`chat_multiple`, `components`, `source`, ...) se descarta
  en silencio.** `createClip` (`db.ts:1963`) guarda `viral` en la
  columna `clips.viral` (TEXT, migración 0005); `clipOut` expone
  `seo_scores: viral.seo_scores ?? null` y `viral_breakdown`.
  `origin` en el Worker solo admite live/vod (`index.ts:1428`:
  `body.origin === "live" ? "live" : "vod"`). **Auto-SEO
  (2026-11-24): `burn_and_enqueue` YA envía el SEO al panel por
  PATCH después del POST** (`vod_fallback.py:774-802`:
  `bridge.update_clip(panel_id, {"viral": {...}, "seo_scores":
  _scores})`; el objeto `viral` del PATCH solo lleva
  `quality_gate` + `seo_source`, NO `viral_score`);
  `mergeViralJson` (`db.ts:470`) fusiona (POST y PATCH no se
  borran). Es decir: el SEO ya llega hoy vía PATCH para los clips
  del fallback; lo que NO llega es `viral_score`.
- `source_id` en D1 es ENTERO (`optIntId` en la validación de POST
  /api/clips; `createClip` recibe `source_id: number | null`). Un
  id de video de YouTube (`NrNdw_Y6_bI`) no cabe: `int()` falla y
  queda en 0 para TODOS los clips de YouTube, y `already_enqueued`
  compara `str(source_id) == str(vod_id)` (`"0" ==
  "NrNdw_Y6_bI"` siempre falso). El dedup funciona dentro de una
  misma corrida (append en memoria con el string) pero entre
  corridas nunca detecta el duplicado; el enfriamiento de 6 h solo
  lo mitiga 6 horas.
- Influencers (verificado): la tabla `influencers` (migración
  0002) es `(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
  platform TEXT, profile_url TEXT, language, permission, active,
  notes, created_at)` — **SIN restricción UNIQUE sobre name**.
  `createInfluencer` es un INSERT plano sin chequeo de duplicados;
  `parseInfluencerInput` (`index.ts:792`) pone `permission` por
  defecto en **0** (sin permiso → `estado="pendiente"` →
  `fallback_order` la excluye). El panel ya tiene entradas con
  `platform=youtube` (IShowSpeed). La fuente de verdad es el panel
  (`load_streamers`, `clipagent/streamers.py:143`; `_norm` guarda
  `channel_url` desde `profile_url`/`url`, l. 130; `_norm` NO tiene
  clave `youtube_url` y `_from_panel` tampoco la mapea).
  `get_streamer(name)` (`streamers.py:187`) devuelve el PRIMER
  match por nombre (sin distinguir plataforma). El watcher salta
  las entradas youtube con nota visible (cabecera de
  `streamers.py`). `clipagent/viral_source.py:177`
  (`youtube_streamers`) ya resuelve canales YouTube por entrada
  (`platform == "youtube"` → `channel_url`; si trae `youtube_url`
  → esa URL); `vod_fallback` NO usa `viral_source`.
- Heatmap de replay de YouTube (`clipagent/heatmap.py`):
  `peaks_from_heatmap_data` + `heatmap_for_url` (yt-dlp -J, $0, sin
  credenciales). Su docstring declara R1: «MÁSTER PROMPT FINAL, R1:
  el sistema de puntaje es intocable en fórmulas, pesos y umbrales»
  y el heatmap es «SEÑAL DIAGNÓSTICA, no como parte de la fórmula
  de viralidad ni de prioridad». No existe en el video de
  aceptación.
- Subtítulos: `clip_passes_gates` NO comprueba subtítulos. La
  excepción sin-voz aplica (regla 2026-11-19 18:41): un clip sin
  voz que pase viralidad/SEO se publica igual sin subtítulos.
- Rescate por gritos (Worker, vigente): viralidad 40–44 + 2+ s de
  gritos + SEO máximo ≥80 → entra a la Cola BLOQUEADO para revisión
  humana. Aplica a Twitch/Kick; ver §3.3 para YouTube.
- Dependencia conocida (NO es de esta spec, se anota): la puntuación
  SEO general del cerebro estaba en revisión en el chat del plan
  maestro (clips 180–185 sin SEO, no encolables solos; y la decisión
  SEO-D1 pendiente). **CERRADA 2026-11-24 ~01:56 por auto-seo
  v5.5.0 (commits 2113d36 + 1d7a842, desplegado avc 35a9b5d1 y
  avc-kaez 4ecae04c)**: el cerebro ya encola con `viral.seo_scores`
  en las 4 plataformas + `viral.seo_inputs` + `quality_gate`, y el
  loop `revalidate_pending.py` revalida/backfillea lo pendiente.
  Los clips de YouTube usan el mismo camino de SEO del manifiesto;
  la verificación en vivo de §4 comprueba que el SEO llega al
  registro del panel.

## 2. Decisión

**Añadir el cortador automático de YouTube al cerebro, reutilizando todo
lo que ya existe (listado, descarga de segmentos, render rubius,
validador, puertas, encolado, contrato viral del Worker).** Las piezas
nuevas son: (a) el cortador `clipagent/youtube_cut.py` (§3.1), (b)
muestreo de audio con cobertura garantizada para videos cortos (§3.2),
(c) adaptador de viralidad que alimenta la fórmula INTOCABLE SIN pasar
un detail con msgs=0 (§3.3), (d) puerta de pico de audio (§3.4),
(e) dedup por `source_key` (migración 0044, §3.7), (f) entrada
`youtube_url` en el dispatch `panel-live` (§3.6), (g) umbral de
prioridad 53 para clips sin chat, aplicado al encolar (§3.5). Nada manual, nada por Muse, nada por el
panel: el corte corre en GitHub Actions (cerebro-live.yml) como el
fallback de Twitch. Dos entradas, mismo cortador:
1. **Barrido automático:** el vod-fallback con --youtube barre los
   canales YouTube de los influencers con entrada `platform=youtube`
   (§3.5) y corta los videos GTA VI que califiquen.
2. **URL única:** el dispatch `panel-live` con `youtube_url` en el
   `client_payload` corre el job `youtube-single`. Muse solo pasa
   la URL (como pulsar INICIAR); el programa descarga, detecta,
   corta, valida y encola solo.

## 3. Cambios

### 3.1 El cortador
- Nuevo módulo **`clipagent/youtube_cut.py`**, función
  `run_youtube_clips(vod_url, out_dir, top_n=2, lang="es",
  single_url=False) -> dict | None`.
- **Contrato de salida (obligatorio)**: devuelve el mismo
  `manifest.json` que `run_auto_clips` produce para Twitch, con
  `clips[]` en la forma que `clip_passes_gates` y
  `burn_and_enqueue` esperan. Cada clip trae:
  `start` (s), `duration` (s), `file` (MP4 final renderizado),
  `viral_score` (dict de `score_clip`), `seo_scores` (por
  plataforma, motor local), `moment_card` (`{game, title}`),
  `transcript` (del audio real), `source: "youtube"`,
  **`viral`: `{best_title, description, tags, thumbnail_text,
  viral_multiple}`** (el título ES el de este dict:
  `burn_and_enqueue` lo lee como `viral.best_title`; sin él el
  POST da 400 por título vacío), **`title_es` / `title_en`**
  (fallback del título si `viral.best_title` falta),
  **`source_key`** (id del video de YouTube, para dedup y para el
  umbral de §3.5), **`vod_id`** (id del video; `_clip_scores` lo
  usa para `moment_key`, vacío colisiona entre videos distintos
  con el mismo `start_sec`), **`vod_layout`** (lo lee `run()`
  para el log de facecam), **`n`** (índice del segmento, mismo
  que `auto_clips.py:1831` pone en cada clip del manifiesto:
  `_rerender_vod_clip` busca `segments/seg_{n:02d}_t*.mp4` con
  `n = clip.get("n")`; sin `n` el re-render no encuentra el
  segmento y degrada a revisión humana). Los segmentos
  descargados quedan en
  `segments/` con el mismo nombrado que usa el camino Twitch
  (`segments/seg_{n:02d}_t*.mp4`) para que `_rerender_vod_clip`
  pueda re-cortar desde ellos; si el cortador no dejara los
  segmentos así, el re-render degradaría a revisión humana.
  **El cortador ESCRIBE el manifiesto a disco**:
  `run_youtube_clips` escribe `out_dir/manifest.json` (igual que
  `auto_clips.py` lo escribe para Twitch) — no basta con
  devolver el dict en memoria, porque en el momento de publicar
  `_clip_scores` lee `manifest.json` DEL DISCO
  (`load_clip_package`, `publish.py:1305`) y sin él el clip cae
  en `no_score` y nunca se publica (`scored_items`:
  «Los ítems sin manifiesto no se puntúan y no aparecen aquí»).
  El job `youtube-single` sube los manifiestos con el mismo paso
  «Guardar manifiestos» que `vod-fallback`
  (`runs/vod-fallback/*/manifest.json`, artefacto
  `vod-fallback-<run_id>`).
  Prueba de conformidad obligatoria: un manifiesto de YouTube debe
  pasar `clip_passes_gates` y `burn_and_enqueue` con bridge
  mockeado, y el test afirma que el payload del POST lleva título
  no vacío (la conformidad de la v3 no lo cazaría: `clip_passes_gates`
  no mira el título y `burn_and_enqueue` no tiene modo dry-run)
  **y que `out_dir/manifest.json` existe en disco tras correr el
  cortador** (no solo el dict en memoria).
- **`is_youtube_url(url)`** (nuevo, en `youtube.py`): host en
  (`youtube.com`, `www.youtube.com`, `m.youtube.com`,
  `youtu.be`). Es el predicado del dispatch de `run()`.
- **Pasos internos** (cada uno reusa una pieza existente, nombrada):
  1. Metadatos con un solo `yt-dlp -J` (duración, vistas, likes,
     heatmap si existe, channel_url, título). Pre-chequeo de título
     GTA VI (`youtube_vod_is_gta6`, fail open).
  2. Muestreo de audio (§3.2) → candidatos con (inicio, puntos de
     hype, texto) + curva RMS.
  3. Top-N ventanas de 40–60 s; descarga SOLO esos segmentos con
     `download_youtube_segment` (yt-dlp --download-sections).
  4. Transcripción precisa del segmento (faster-whisper, mismo
     motor que los subtítulos); subtítulos del audio real, nunca
     inventados. Título Gemini (`auto_clips.suggest_titles`, mismo
     que hoy) + SEO local por plataforma
     (`seo_score.score_all_platforms`).
  5. Viralidad con el adaptador de §3.3 (`detect.score_clip`).
  6. Render rubius con el mismo motor que el camino Twitch
     (`clipagent/rubius` + `workers.subtitle_clip` /
     `workers.apply_sfx`, mismo que usa `_rerender_vod_clip`) y
     validador final (`final_review.validate_final_clip`) sobre el
     render. Un veredicto «rechazado» no se guarda sin revisar los
     fotogramas. La separación cámara/juego para YouTube queda como
     riesgo residual hasta que la prueba de conformidad corra (el
     run de Speed fue manual; nada en lo leído demuestra que el
     camino automático separe webcam/gameplay de un segmento de
     YouTube).
  7. Puertas (§3.4) y encolado por `burn_and_enqueue` (origin='vod').
- **Dispatch en `run()`**: por VOD, `manifest =
  run_youtube_clips(...) if is_youtube_url(vod_url) else
  run_auto_clips(...)`. `run()` acepta un `youtube_clips_fn`
  inyectable para tests, en paralelo a `auto_clips_fn`. El
  barrido de YouTube y el barrido normal comparten contadores y
  `state.json`: `vods_tried`, `enqueued`, enfriamiento de 6 h por
  video id y los límites (máx. 2 VODs y 2 clips por corrida ENTRE
  las dos fuentes).

### 3.2 Muestreo de audio («picos de audio», nunca «heatmap»)
- Esquema base, mismo que `sample_audio_peaks` de Kick: muestras
  de 30 s, faster-whisper tiny, puntuación de risas/hype con los
  mismos tokens que `clips_chat_v2`. En el camino de muestreo se
  baja SOLO cada muestra de 30 s con yt-dlp --download-sections
  por muestra, **en formato solo-audio (bestaudio)** — las
  muestras son para transcripción y RMS, no hace falta video.
- **Camino de audio completo (corrección de la contradicción de la
  v3)**: URL única o video ≤ 30 min (1.800 s) → transcripción
  COMPLETA del audio con faster-whisper tiny (29 min de audio
  ≈ ≤ 6 min; cobertura 100%). Esto exige descargar el AUDIO
  completo del video (solo audio, bestaudio, tope 50 MB — nunca el
  video). **Mecanismo del tope**: la descarga usa
  `yt-dlp --max-filesize 50M`; **fallback si el audio supera
  50 MB** (un bestaudio de 29 min a bitrate alto puede superarlo):
  no se falla — se usa el camino de muestreo (§3.2) sobre ese
  video en vez del audio completo. **Detección del exceso**: el
  fallo de yt-dlp cuyo stderr contiene «max-filesize» (o el
  archivo descargado supera 50 MB en la comprobación de tamaño
  tras la descarga) se trata como «audio demasiado grande →
  muestreo»; cualquier otro fallo de yt-dlp sigue el camino
  normal de reintentos. La frase absoluta «nunca el
  audio entero» queda eliminada y sustituida por: «en el camino
  de muestreo solo se bajan muestras de 30 s de audio; en el
  camino ≤30 min se baja el audio completo (bestaudio, ≤50 MB),
  nunca el video».
- Cobertura del muestreo: barrido, video < 1 h → intervalo 120 s
  (un momento de longitud L se detecta si una muestra lo solapa:
  P ≈ (L+30)/120 → 40 s: 58%, 50 s: 67%, 60 s: 75%); barrido,
  video ≥ 1 h → intervalo 300 s, **máximo 24 muestras por VOD
  repartidas uniformemente por toda la duración**
  (`t_i = (i+0,5) × duración/24`, no las primeras 24). Es un
  MUESTREO (los picos son reales pero aproximados, mismo caveat
  documentado que en Kick).
- El heatmap de replay de YouTube (`heatmap.py`) es SOLO una señal
  opcional de apoyo cuando existe (se lee del -J ya obtenido para
  metadatos, `peaks_from_heatmap_data`): si está disponible, sus
  picos afinan la ventana del candidato; si no está (caso del video
  de aceptación), el muestreo de audio decide solo. La palabra
  «heatmap» queda reservada a `heatmap.py`; la señal de audio se
  llama **«picos de audio»** en código, logs y manifiesto.
- Presupuesto de tiempo (desglose rehecho con los reintentos):
  la rama YouTube usa **≤ 25 min de cortador** (no 20):
  metadatos -J ~10 s; muestras ≤ 24 × 3 intentos × ~10 s ≈ 12
  min en el peor caso; transcripción (12 min de audio con tiny
  ≈ 2–4 min; 30 min completos ≈ ≤ 6 min); descargas de segmento
  2 × ~30 s; renders 2 × ~2–3 min; validador ~1 min. Típico
  ≈ 10–12 min, **peor caso del cortador ≈ 24 min** (12 + 4 + 1
  + 6 + 1 + 0,17 ≈ 24,2 — la v4 decía «19–23», que subestimaba
  sus propios números). **El timeout del job es 60 min, igual
  que `vod-fallback`**: los 25 min son solo del cortador; el job
  necesita además checkout + setup-python + pip install + ffmpeg
  + credenciales del panel (≈ 4–6 min con caché caliente, y sin
  caché de pip torch/whisper hasta ~28 min): 24,2 + 28 ≈ 52 min
  en el peor caso sin caché, que NO cabe en 45 — por eso 60.
  El job `youtube-single` copia el **bloque completo de setup**
  de `vod-fallback` (checkout, setup-python, caché de pip,
  ffmpeg, visión, credenciales del panel), no solo los pasos de
  artefactos nombrados en §3.6.
- Coste estimado por VOD: 1 flat-playlist por canal y barrido,
  1 -J por VOD (metadatos), ≤ 24 descargas de muestra (30 s de
  audio) o 1 descarga de audio completo (≤50 MB) en el camino
  ≤30 min, ≤ 2 descargas de segmento (≤ 60 s de video),
  faster-whisper tiny local, Gemini 1 llamada por clip (solo el
  título; el SEO es local), Actions ≈ 5–10 min por VOD. $0 en APIs
  (sin API key; yt-dlp + Gemini ya existentes).

### 3.3 Viralidad sin chat: la fórmula NO se toca (R1) — adaptador
- **R1 se respeta**: ni WEIGHTS, ni umbrales (45), ni la fórmula de
  prioridad cambian. Lo nuevo es el ADAPTADOR de entrada para una
  fuente sin chat, no la fórmula.
- La v2 pasaba `detail={"msgs": 0, ...}` y `chat_component`
  devuelve 0,0 por el early return (`detect.py:226`), así que el
  total real era 42,5 < 45 y ningún clip pasaría jamás. **La v3
  (verificada correcta) NO pasa detail**: llama a `score_clip`
  con `detail=None, chat_multiple=None`. Con eso el componente
  chat queda **estimado en 5,0** (rama prevista del código) y el
  total es **45 + valor_audio** (peso 1,0) → 47–55 (el valor de
  audio medido va de 2 a 10, ver abajo). La aritmética de la spec
  es cierta por construcción.
- **Construcción de la entrada de audio (corrección del
  bloqueador B4 de la vuelta 3)**: sobre el audio muestreado
  (o el audio completo en el camino ≤30 min) se calcula
  `rms = rms_per_second(wav)` (`clipagent/audio.py:122`),
  `stats = audio_stats(rms)` (`audio.py:145`, devuelve
  `std_db`, `steady_frac`, `range_db`), `words =
  len(transcript_words(transcript))` (`audio.py:267` devuelve
  una LISTA de palabras con timestamps — `audio_component`
  espera un NÚMERO: su docstring dice «nº de palabras
  transcritas» y hace `words == 0` y `words >= 50`; pasar la
  lista revienta con `TypeError` en cada clip; la prueba está
  en el propio repo: `audio_verdict` devuelve
  `"words": len(words)` y `review.py:290` pasa ese entero).
  Se pasa `audio = {"stats": stats, "words": words}` a
  `score_clip`. La llamada es `score_clip(detail=None,
  chat_multiple=None, audio=audio, platform=None)` (peso de
  audio 1,0 por defecto; 1,5 es solo para kick). El total es
  45 + valor_audio, con valor_audio ∈ [2, 10] (plano → 2, no 0).
  El test de §4 llama a `score_clip` con el dict construido
  (no solo comprueba su forma — un test de forma no cazaría
  este crash).
- **El múltiplo de audio vive por separado** (no dentro de
  `score_clip`): se calcula y se guarda en el manifiesto como
  **`viral.viral_multiple = audio_multiple`** (el campo que
  `burn_and_enqueue` usa como `chat_multiple` en el encolado,
  §3.5) y dentro de
  `viral_breakdown` (`viral_breakdown.audio_multiple`, campo que
  SÍ sobrevive en `parseViralInput`). `panel_score` lo usa como
  `chat_multiple` (se calcula en el cerebro en el encolado, no
  en el Worker).
- **Definición exacta del múltiplo**: curva RMS en buckets de 10 s
  **solo sobre los buckets muestreados** (los huecos entre
  muestras se excluyen); `audio_multiple = pico / mediana`, donde
  el pico es el bucket RMS máximo **dentro de la ventana
  candidata** y la mediana es la mediana de **todos los buckets
  muestreados del VOD**. Si la mediana es 0 (silencio) o no hay
  buckets muestreados en la ventana, el múltiplo es 1,0 y el
  candidato se descarta por la puerta de §3.4. Herramienta:
  decodificar las muestras a wav con ffmpeg (dependencia
  existente) y RMS con numpy (dependencia existente del
  validador). La curva RMS para el múltiplo (buckets de 10 s,
  amplitud lineal) es distinta de la estadística por segundo
  para `audio_component` (dB, 1 s) — ambas se calculan del mismo
  audio muestreado, no se confunden.
- **Consecuencia honesta, escrita en la spec**: con humanos en 5,0
  y chat estimado en 5,0, el total es 45 + valor_audio. La puerta
  ≥45 por sí sola NO discrimina los clips de YouTube. El filtro
  real es la puerta de pico de audio (§3.4) + SEO ≥80 + juego +
  duración.
- **Sin prior de alcance**: vistas/likes NO entran en la fórmula
  (no tienen sitio en los 7 componentes). Se registran en el
  manifiesto como contexto (vistas del listado flat-playlist;
  likes del -J por video, ya obtenido para metadatos).
- `filter_passed` será false (sin detail) y **no se usa como
  puerta para YouTube** (lo sustituye §3.4); el manifiesto lo
  lleva tal cual, sin esconderlo.
- **Rescate por gritos NO aplica a YouTube**: con el adaptador
  el total es siempre ≥ 47 (audio medido 2–10; si el audio no se
  puede medir, el clip se descarta, no se estima). Un clip de
  YouTube no puede caer en la banda de rescate 40–44. La regla de
  rescate del Worker queda intacta para Twitch/Kick. Sin pista de
  audio en el video → descarte con motivo (no hay RMS ni
  transcripción que medir).

### 3.4 Puerta de pico de audio (sustituye al filter_passed de chat, solo YouTube)
- Un candidato se encola solo si: (a) `audio_multiple ≥ 2,0` Y (b)
  `hype_points > 0` (risas/hype medidos en su muestra transcrita,
  mismos tokens que `clips_chat_v2`) O el LLM lo selecciona como
  momento (`--llm-select`, mismo que hoy). Si no pasa, se descarta.
- El umbral 2,0 es la **calibración inicial**, anclada así: para
  RMS en amplitud lineal, un grito frente a conversación normal
  da típicamente 2–5x (no es el múltiplo de chat: los 1,19x/1,87x
  documentados en `detect.py` son de chat, no de audio, y no
  calibran esto). El implementador lo mide en el video de
  aceptación y en el VOD de Speed y deja la tabla de valores
  medidos en un comentario del código + fixtures de test. Si 2,0
  no separa los momentos buenos de los planos, se ajusta con los
  números medidos en la mano (el ajuste queda registrado en la
  spec, no silencioso).
- Puertas SIN CAMBIO: viralidad ≥45 (fórmula intacta), SEO ≥80 en
  las 4 plataformas (motor local `seo_score.py`), 40–60 s, GTA VI
  verificado, subtítulos del audio real (o excepción sin-voz
  vigente). Falla cerrado.

### 3.5 Barrido y auto-archivo
- **Barrido**: NO hay pase separado nuevo ni campo `youtube_url`
  en la tabla de influencers. El barrido usa la rama YouTube
  EXISTENTE de `latest_vod_for` (que exige `platform ==
  "youtube"` y `channel_url`). **Rubius se barre creando una
  segunda entrada en el panel: `platform=youtube`,
  `profile_url=https://www.youtube.com/@rubius`, `language=es`,
  `permission=1`, `active=1`** (misma persona, dos entradas, una
  por plataforma — el mismo modelo que IShowSpeed, que ya es una
  entrada `platform=youtube`). `permission` y `active` son
  obligatorios: `parseInfluencerInput` pone `permission` por
  defecto en 0 (`index.ts:792`) y sin permiso `estado="pendiente"`
  la excluye `fallback_order`. La tabla `influencers` no tiene
  UNIQUE sobre name (migración 0002) y `createInfluencer` es un
  INSERT plano sin chequeo de duplicados, así que el esquema lo
  permite. Crear la entrada es una acción normal del panel
  (formulario de influencers), no código.
- **Ambigüedad de `get_streamer` (M5)**: `get_streamer(name)`
  devuelve el PRIMER match por nombre. Con dos Rubius, los
  llamadores por nombre pueden recibir la entrada equivocada. La
  corrección: `get_streamer` gana el parámetro `platform` con
  valor por defecto `"twitch"` (preserva el comportamiento actual
  de todos los llamadores existentes). Los dos llamadores que
  importan se resuelven así (no se delegan a una auditoría sin
  hacer): (a) `publish.py:1931` (en el momento de publicar,
  comprueba `entry.get("enabled", True)` del streamer): resuelve
  por (nombre, plataforma) — la plataforma de origen NO está en
  el ítem de la cola (el ítem es `{"clip", "platform",
  "streamer", ...}` y su `platform` es la de PUBLICACIÓN, p. ej.
  `tiktok`, no la de origen): se alcanza por el manifiesto con
  `load_clip_package(item["clip"])` → `clip.get("source_key")`
  (id de YouTube → plataforma youtube, si no twitch); (b)
  `is_allowed` (`streamers.py:199`, usado por el cortador en vivo
  en `live_cutter.py:1554`): gana el mismo parámetro `platform`
  (defecto `"twitch"`) y el cortador en vivo pasa la plataforma
  que está cortando (`LiveCutter` ya tiene `self.platform`).
  **Y la promesa de producción (bloqueador M5(a) de la vuelta 5)**:
  `publish.py:1931` vive en `publish_one`, dentro del bucle
  local (la cola `publish_queue.json`, que solo corre en
  `cerebro-kaez.yml`); en producción el Worker publica desde D1
  y **no hay ningún chequeo de influencer activo/permiso al
  publicar** (cero coincidencias de `permission`/`active`/
  `getInfluencer` en `gate.ts`, `jobs.ts` ni `daily-plan.ts`):
  desactivar una entrada hoy NO frena la publicación de lo ya
  encolado. Por eso el Worker gana el chequeo: en `gate.ts`, al
  publicar un job, se resuelve `clip.influencer_id` → fila de
  `influencers`; si `permission=0` o `active=0`, el job queda
  bloqueado/retenido con el motivo «streamer desactivado en el
  panel». Es la implementación en producción de la regla ya
  documentada en `publish.py:1931` y aplica a todos los clips,
  no solo a los de YouTube. **Pero el chequeo solo vale si el
  `influencer_id` guardado es el de la entrada correcta
  (bloqueador de la vuelta 6)**: hoy POST /api/clips resuelve
  el handle por PRIMER match por nombre
  (`index.ts:2788`: `listInfluencers(...).find((r) =>
  normalizeHandle(r.name) === normalizeHandle(handle))`, y
  `listInfluencers` ordena `ORDER BY id ASC`, `db.ts:2625`):
  la entrada de Twitch (id menor, creada antes) gana siempre,
  y `burn_and_enqueue` manda `"influencer_id": streamer` (el
  nombre, `vod_fallback.py:732`), así que TODO clip de YouTube
  queda con el id de la entrada **Twitch**. El gate miraría la
  fila Twitch: desactivar solo la entrada YouTube no frenaría
  nada, y desactivar solo la Twitch bloquearía también los
  clips de YouTube. **Corrección**: la resolución del handle en
  POST /api/clips es POR PLATAFORMA — si `body.source_key` es
  un id de YouTube no vacío → se resuelve entre las entradas
  `platform=youtube`; si no, entre las de Twitch.
  **Job retenido**: un job bloqueado por el gate va a draft con
  `retryable = 0` (`jobs.ts:1363`, `markDraft`) y NO se
  reintenta solo: al reactivar el streamer, el usuario
  re-encola el clip desde el panel (una línea en §6 y en la
  aceptación). **Clip sin `influencer_id`**: los clips viejos
  sin `influencer_id` existen (la columna admite null); si el
  clip no lo tiene, el chequeo pasa (fail-open con log) — si se
  implementara como bloqueo, clips viejos dejarían de
  publicarse para siempre. Verificación: GET
  /api/influencers/live con una fila `platform=youtube` presente
  (el watcher salta las entradas youtube con nota visible; el
  endpoint no debe romperse).
- El listado es /streams Y /videos. Los límites del run son
  compartidos: máx. 2 VODs y 2 clips por corrida entre las dos
  fuentes, 6 h de enfriamiento por video id (mismo `state.json`;
  un video que falla por bot-check tras los 3 reintentos entra en
  enfriamiento, no se reintenta cada corrida).
- Juego: pre-chequeo de título (`youtube_vod_is_gta6`, fail open
  como el de Twitch) + puerta precisa del manifiesto. Un video que
  claramente no es GTA VI se salta sin gastar cupo ni enfriamiento.
- Idioma: el de la entrada del streamer para el barrido. Para la
  URL única (§3.6): lookup por channel_url contra las entradas
  `platform=youtube`; si no casa con ninguna entrada, idioma **es**
  por defecto + log visible, influencer = handle del canal.
- **Umbral de prioridad al encolar — OPCIÓN A (corrección del
  bloqueador B1 de la vuelta 5)**:
  - El umbral es **53 en toda la spec** (cableado, tabla y §6).
    La v3 decía 50 en el párrafo de cableado y 53 en la tabla —
    contradicción propia, corregida aquí: **una sola cifra, 53**.
  - **Por qué NO en `publish.py`**: las vueltas 3–5 cablearon el
    umbral en `_clip_scores` → `_order_items` → `order_queue` →
    `should_auto_archive` con `min_priority`. Esa cadena solo se
    ejecuta dentro del bucle local `publish_loop`, sobre la cola
    local `publish_queue.json` (ítems = rutas MP4 locales,
    `publish.py:451-462`). Los clips del fallback —incluidos los
    de YouTube— se encolan con `burn_and_enqueue` → POST
    /api/clips → **D1** y jamás entran en `publish_queue.json`.
    En producción publica el **Worker**: `dueJobs`
    (`jobs.ts:1150`) reclama jobs de D1 y `queueClipCompare`
    (`db.ts:1837`) ordena por `manual_rank` y por `score` de D1
    (`baseQueuePriority`, `db.ts:1750`: `viral.priority` si
    existe, si no `clips.score`). El Worker **nunca lee un
    manifiesto, nunca llama a `_clip_scores`, nunca recomputa la
    prioridad desde `viral_multiple`** — y no existe ningún
    archivo por prioridad en producción (ningún
    `SET archived = 1` en todo `panel-backend/` ni en
    `clipagent/`; `cerebro-live.yml` no tiene job de
    publicación). Un clip de YouTube con prioridad 50,4 (el
    «flojo» de la tabla) quedaría en la Cola y el Worker lo
    publicaría en su slot: las puertas del Worker (`gate.ts`)
    solo exigen viralidad ≥45 y SEO ≥80, que pasa por
    construcción. **El umbral 53 en `publish.py` no tendría
    ningún efecto.** La opción `order_queue` /
    `should_auto_archive` se ELIMINA de esta spec (ese bucle
    queda como legacy local, sin efecto sobre clips del panel).
  - **El umbral se aplica AL ENCOLAR, en el cerebro**: en
    `burn_and_enqueue`, con viralidad, SEO y múltiplo de audio ya
    calculados, se calcula `priority = panel_score(total, seo,
    chat_multiple)` (`vod_fallback.py:284`, que llama a la misma
    `priority()` de `priority.py` con el SEO de YouTube y
    redondea a entero: 55,7 → 56, 50,4 → 50, 49,3 → 49). Si el
    clip es de YouTube (`source_key` no vacío — los clips de
    Twitch llevan `source_key ''` por construcción, migración
    0044; un id de YouTube todo dígitos también es YouTube, así
    que el test «no todo dígitos» de la v6 es innecesario e
    inexacto) y `priority < 53`, **no se encola**: se descarta
    con el motivo en el log y como evento `clip_descartado` en
    el panel (la rama `else` de `run()` ya reporta los fallos de
    `burn_and_enqueue` como evento, así que el descarte por
    umbral ES visible, no solo una línea de log). **El chequeo
    va AL INICIO de `burn_and_enqueue`, ANTES de
    `subtitle_clip`**: los tres inputs ya están disponibles a la
    entrada (`clip["viral_score"]["total"]`,
    `clip["seo_scores"]` y `chat_multiple` derivable de
    `viral_score`/`viral` al instante); si el chequeo fuera tras
    la validación, cada clip flojo quemaría subtítulos + SFX +
    validador + posible re-render antes de descartarse. Un clip
    flojo retorna antes de quemar nada (test: `subtitle_clip`
    no se llama). Nota de redondeo: `panel_score` redondea a
    entero, así que un crudo 52,5–53,4 → 53 → pasa; el umbral
    efectivo en crudo es 52,5, no 53. El
    `score` de D1 ya lleva el múltiplo de audio
    (`chat_multiple = viral_score.get("chat_multiple") or
    (viral or {}).get("viral_multiple") or 1.0`,
    `vod_fallback.py:716`, y `"score": panel_score(...)`,
    l.730), así que el orden del Worker ya es correcto — lo
    único que faltaba era el umbral, y ahora está en el sitio
    que se ejecuta. Cambio solo del cerebro
    (`vod_fallback.py`); el Worker no interviene en el umbral.
  - **Tabla (verificada por cálculo directo contra
    `priority.py`, `_chat_base(2,0) = 3,0` → chat_norm 30; es la
    misma fórmula que `panel_score`, con el SEO de YouTube)**:
    | caso | viral | SEO | mult | prioridad |
    |---|---|---|---|---|
    | bueno | 52 | 85 | 3,0 | 55,7 |
    | flojo | 47 | 80 | 2,0 | 50,4 |
    | mínimo | 45 | 80 | 2,0 | 49,3 |
    | excepcional | 60 | 90 | 8,0 | 70,5 |
    | máximo teórico mult 1,0 | 100 | 100 | 1,0 | 63,1 |
    **Umbral para sin-chat: 53** (punto medio entre 50,4 y 55,7):
    el bueno (55,7 → 56) se encola, el flojo (50,4 → 50) NO se
    encola y el mínimo (49,3 → 49) NO se encola. Es una decisión
    que el usuario aprueba con esta spec (§6).
  - El marcador durable es `source_key` (migración 0044): NO se
    usa un campo `source` en el objeto viral (lo descartaría
    `parseViralInput`) y `origin` solo admite live/vod. Es el
    marcador que usa el chequeo al encolar (id de YouTube = clip
    sin chat) y el que usa `burn_and_enqueue` para el dedup.

### 3.6 Entrada URL única (job `youtube-single`, en el dispatch `panel-live`)
- **Corrección del bloqueador B3 de la vuelta 3**: el job NO se
  define como `workflow_dispatch` suelto. Un `workflow_dispatch`
  no trae `client_payload` y sin token no hay sesión: el job
  fallaría en la primera llamada al panel y el enfriamiento no
  funcionaría (el `state.json` nacería vacío en cada runner).
- **Definición**: el job `youtube-single` corre en el evento
  `panel-live` (repository_dispatch), con `needs: [detect]` y
  `if: github.event.client_payload.youtube_url` (**chequeo de
  veracidad, no `!= ''`**: en un `panel-live` normal o en un
  `workflow_dispatch` el campo es `null`, y `null != ''` es
  `true` en expresiones de GitHub — el job correría en todas las
  corridas con la URL vacía; con veracidad, `null` y `''` son
  falsy y solo una URL presente dispara el job),
  `timeout-minutes: 60` (igual que `vod-fallback`, §3.2). Copia el **bloque completo de setup**
  de `vod-fallback` (checkout, setup-python, caché de pip,
  ffmpeg, visión, credenciales del panel) + los pasos de
  artefacto: descarga el artefacto de sesión
  `panel-session-<tenant>-<run_id>` que sube `detect` y restaura
  + sube `runs/vod-fallback/state.json` (mismos pasos de
  artefacto que el job vod-fallback) + el paso «Guardar
  manifiestos» (§3.1). Comando:
  `python -m clipagent vod-fallback --youtube-url <url> [--force]`
  con env `PANEL_TENANT` (NO existe flag `--tenant`).
  Como tiene `needs: [detect]`, corre aunque haya directos en
  vivo (el gate de directos es del job vod-fallback, no de
  `detect`): la URL despachada corta aunque alguien esté en vivo.
- **Exclusión mutua (B7)**: `youtube-single` y `vod-fallback`
  tienen ambos `needs: [detect]` y nada más — sin dependencia
  entre ellos GitHub los ejecuta en paralelo cuando un dispatch
  con URL llega sin directos en vivo, y los dos restauran y
  suben el MISMO artefacto de estado
  (`cerebro-live-vod-state-<tenant>`; el último en subir gana y
  los intentos del otro se pierden) y pueden encolar el mismo
  momento (el dedup lee la Cola al inicio de cada job; dos
  encolados paralelos pasan el chequeo los dos). El
  `concurrency: group: cerebro-live-<tenant>` serializa
  CORRIDAS, no jobs dentro de una corrida. Corrección: el `if`
  de `vod-fallback` pasa a
  `needs.detect.outputs.count == '0' &&
  !github.event.client_payload.youtube_url` (chequeo de
  veracidad, no `!= ''`): con URL despachada solo corre
  `youtube-single`.
- **Camino completo del dispatch (corrección del bloqueador B3
  de la vuelta 4)**: hoy el control de ejecución del panel es
  `POST /api/worker/start` → `dispatchCerebro(env,
  "panel-start", tenant)` (`index.ts:2556`), pero
  `cerebro-live.yml` solo escucha `panel-live` (+
  `workflow_dispatch`) — un `panel-start` NO dispara este
  workflow; el único llamador de `dispatchCerebro` con
  `panel-live` es el cron del Worker (`index.ts:654`), que no
  tiene ninguna URL que mandar; y `dispatchCerebro` frena los
  `panel-live` si se emitió un token hace menos de 15 min
  (`hasRecentBrainToken`, `LIVE_DISPATCH_MIN_INTERVAL_MS`,
  `index.ts:502-522`) y por presupuesto — un dispatch explícito
  con URL dentro de esos 15 min se descartaría en silencio y la
  URL se perdería (no queda persistida en ningún sitio). Camino
  definido de punta a punta: (1) endpoint NUEVO
  `POST /api/worker/run-youtube` (con sesión, como
  `/api/worker/start`) que recibe `{youtube_url}` y llama a
  `dispatchCerebro(env, "panel-live", tenant, youtube_url)`;
  **el endpoint valida la URL** (mismo chequeo de host que
  `is_youtube_url`, §3.1: 400 si no es YouTube — sin validación,
  una URL cualquiera emitiría un token, dispararía una corrida
  y el job la descartaría tarde) **y devuelve `ok: false` con el
  motivo cuando `dispatchCerebro` devuelve `false`** (circuito
  de cuota, sin token, freno de reintentos: no debe responder
  éxito); (2)
  `dispatchCerebro` gana el parámetro opcional `youtube_url`
  (se incluye en el `client_payload` cuando viene) y **salta el
  throttle de 15 min, el freno de presupuesto Y el freno de
  reintentos tras dispatches fallidos (`dispatchRetryAllowed`,
  `index.ts:495`: `panel-live` es «automático», así que un
  dispatch con URL tras un `panel-live` fallido reciente
  devolvería `false` — con la exención el dispatch explícito
  tampoco se frena ahí, misma regla que `panel-start` («NUNCA
  se frenan aquí», `index.ts:516`); (3) el campo opcional «URL
  de YouTube» del control de ejecución existente del panel
  llama a ese endpoint (un campo en el control que ya existe, NO
  una pestaña ni una sección nueva). Muse solo pasa la URL que el
  usuario comparte a ese dispatch; el programa hace el resto.
- `run()` gana el parámetro `youtube_url` (CLI `--youtube-url`)
  que se atiende ANTES del chequeo de directos: si viene,
  procesa ESA URL (pre-chequeo de título, dedup, cortador,
  puertas, encolado) y retorna, saltando el barrido Y el chequeo
  de directos. Una URL despachada corta aunque alguien esté en
  vivo (petición explícita del usuario). **La rama de URL única
  escribe en `runs/vod-fallback/<video_id>/manifest.json`**,
  el mismo layout que el barrido (`out_dir =
  os.path.join(out_root, str(vod_id))`, `vod_fallback.py:922`),
  para que el glob del paso «Guardar manifiestos»
  (`runs/vod-fallback/*/manifest.json`) lo cubra.
- Re-despacho: una URL re-despachada respeta el enfriamiento de
  6 h salvo `--force` (mismo `state.json`, restaurado y subido
  por el job).
- El canal y el idioma se resuelven del propio video (yt-dlp -J:
  channel_url; §3.5).

### 3.7 Dedup (B3)
- Migración **0044** en **los DOS tenants** (clips-avc Y
  clips-avc-kaez): `ALTER TABLE clips ADD COLUMN source_key TEXT
  DEFAULT ''`. Si la columna falta en kaez, el INSERT de
  `createClip` (lista fija de columnas) fallaría y kaez no
  encolaría ningún clip de YouTube.
- Puntos a tocar (listados, no resumidos): `createClip`
  (INSERT + tipo `NewClip`), validación de POST /api/clips
  (aceptar `source_key` opcional, string ≤ 64), `clipOut`
  (devolver `source_key`). Deploy del Worker por el flujo
  habitual (`wr_v2.py` + chequeo V6 de salud y crons), en ambos
  tenants.
- `burn_and_enqueue` manda `source_key` = id del video de YouTube
  y deja `source_id` = 0 (fallback actual de `int()`); para Twitch
  no cambia nada. `already_enqueued` compara (source_key,
  start_sec) cuando `source_key` no está vacío; si no, (source_id,
  start_sec) como hoy. El `append` en memoria de `run()` lleva
  también `source_key` y `title` (para el cruce de §3.8). El test
  de dedup cubre el caso ENTRE corridas (lista fresca del panel),
  no solo en memoria.
- `source_key` es además el marcador durable de clip de YouTube
  para §3.5 (`source_key` no vacío ⟺ YouTube).

### 3.8 Encolado (sin cambio de Worker para viral/SEO)
- `burn_and_enqueue` añade al payload el campo **`viral`** = JSON
  con **SOLO los campos que sobreviven en `parseViralInput`**:
  `{viral_score, seo_scores, viral_breakdown, hype_score,
  priority, score_kind}`. El `audio_multiple` va dentro de
  `viral_breakdown` (objeto; `viral_breakdown.audio_multiple`).
  **Forma de `seo_scores` en el POST**: NÚMEROS PLANOS por
  plataforma (`{youtube_shorts: 90, tiktok: 91, ...}`), extraídos
  del manifiesto como hace el PATCH (`_entry.get("score")` si es
  dict, el número si ya lo es — `vod_fallback.py:774-802`).
  `parseSeoScoresInput` (`db.ts:264`) exige
  `{plataforma: número}` y rechaza con 400 cualquier dict: el
  manifiesto lleva `seo_scores` como
  `{plataforma: {score: n}}` (así lo lee `clip_passes_gates`),
  así que mandar el manifiesto tal cual daría 400. Los campos
  `chat_multiple`, `components` y `source` de la v2 se ELIMINAN
  del payload (los descartaba `parseViralInput` en silencio). El
  Worker YA lo acepta, lo guarda en `clips.viral` (`createClip`)
  y `clipOut` expone `seo_scores` y `viral_breakdown` desde ahí.
  **Sin migración ni deploy del Worker para viral/SEO** (la
  migración 0044 es solo para `source_key`).
- **Reconciliación con el PATCH auto-seo (M1)**: el SEO ya llega
  hoy vía PATCH (`bridge.update_clip` con `seo_scores`); el POST
  lleva `viral_score` / `viral_breakdown` / `priority` (y
  `seo_scores`, que `mergeViralJson` fusiona sin borrar). El
  comentario del código («el POST no lleva seo_scores (la puerta
  SEO del POST tiraría los de máximo < 80)», `vod_fallback.py:774-780`)
  describe el diseño ANTERIOR: con esta spec el POST SÍ lleva
  `seo_scores`, y es seguro porque `clip_passes_gates` ya exige
  los 4 ≥ 80 antes de encolar, así que el máximo es ≥ 80 y la
  puerta SEO del POST pasa siempre. El
  criterio de aceptación de §4 comprueba **`viral_score`
  visible en GET /api/clips**, no solo `seo_scores` (que pasaría
  igual solo con el PATCH).
- Render, subtítulos, título y SEO con el camino existente
  (plantilla rubius por defecto, subtítulos del audio real, título
  Gemini + SEO local por plataforma). Validador final sobre el
  render (mismo que Twitch); un veredicto «rechazado» no se guarda
  sin revisar los fotogramas. Las puertas de cara/plantilla
  aplican como hoy (el render re-compone cámara + juego del
  segmento, como en el run de Speed; la prueba de conformidad de
  §3.1 lo verifica en seco); si la fuente no trae webcam
  separable, la puerta de cara decide y el veredicto se reporta.
- Clips a la Cola con origin='vod', language del streamer, MP4,
  viralidad + SEO en el registro del panel. Sin publicar: la
  publicación la decide el panel después, con sus topes.
- Bandas de duración del video de entrada: < 40 s → ningún clip
  posible (resultado documentado, no fallo); 40–60 s → el video
  entero es el candidato (sin detección de picos, directo a
  render/validar); ≥ 60 s → muestreo de §3.2.
- Cruce con Twitch (mismo momento, distinto id): en el encolado,
  si un clip del panel del mismo streamer, **mismo día Chile
  (fecha Chile de `created_at` del clip — `posted_day` es
  columna de jobs, no de clips, y `clipOut` no la devuelve;
  `created_at` es epoch en ms y se convierte con
  `santiago_day_key` (`clipagent/retry.py:121`:
  `datetime.fromtimestamp(ms/1000, tz=ZoneInfo(SANTIAGO_TZ))`
  → `YYYY-MM-DD`); `_now_chile` NO sirve: no admite argumentos
  y devuelve la hora actual. El test afirma la conversión con
  un `created_at` fijo)** y misma plataforma tiene un título normalizado
  con solape de tokens ≥ 0,7 con el título del candidato
  (minúsculas, sin puntuación ni emoji; solape = |A∩B| /
  min(|A|,|B|); se exige min(|A|,|B|) ≥ 3 tokens, si no no hay
  bloqueo), el encolado se BLOQUEA y el motivo queda en el log
  (regla del usuario: no dos clips del mismo momento el mismo
  día en la misma plataforma). **«Mismo streamer» se resuelve
  por nombre**: los clips del panel llevan `influencer_id`
  (entero) y el candidato lleva un nombre; `_from_panel` no
  mapea ningún `id`, así que antes del cruce se resuelve el
  `influencer_id` → nombre con `GET /api/influencers` (la lista
  que el puente ya consulta) y se compara por nombre
  normalizado. **Sin condición de idioma** (la regla del usuario
  no la tiene: un clip ES no dejaría de bloquear uno EN del
  mismo momento). El cruce corre también contra los clips
  encolados en la misma corrida: el `append` lleva los 5 campos
  (`{"source_id": vod_id, "start_sec": int(start), "source_key":
  source_key, "title": title, "streamer": name}` — `source_key`
  y `title` los exige §3.7, `streamer` lo exige este cruce; el
  append de hoy solo lleva `source_id` y `start_sec`,
  `vod_fallback.py:972`, sin los cuales el cruce
  mismo-corrida no puede comprobar «mismo streamer»: o compara
  entre streamers (bloqueos falsos) o salta las entradas en
  memoria (se pierde el bloqueo justo cuando el corte Twitch y
  el YouTube del mismo momento caen en la misma corrida, el
  escenario exacto, pues ambas entradas Rubius están en el
  orden del fallback). **Sitio del cruce**: en `run()`, junto a
  `already_enqueued` (que ya tiene `panel_clips` en la mano).
  **Carrera con el job `cut`**: `youtube-single` corre aunque
  haya directos en vivo (decisión explícita, §3.6) y `cut`
  corre en paralelo en la misma corrida; `run()` carga
  `panel_clips` una sola vez al inicio, así que un clip en vivo
  encolado por `cut` después de esa carga es invisible para su
  cruce. Mitigación: `run()` re-lee la lista del panel
  (`bridge.list_clips()`) justo antes de cada encolado. Ventana
  residual (TOCTOU): entre la re-lectura y el POST sigue
  habiendo ventana, sin enforcement en el servidor — el cruce
  es solo del cerebro; se declara como riesgo aceptado.

## 4. Verificación (antes de darlo por hecho)
- Tests nuevos: cortador YouTube con yt-dlp mockeado (contrato
  de manifiesto completo: `viral.best_title` no vacío,
  `viral.viral_multiple`, `source_key`, `vod_id`, `vod_layout`,
  `n`, `title_es`/`title_en`, segmentos en `segments/` con el
  nombrado; `clip_passes_gates` + `burn_and_enqueue` con bridge
  mockeado y título no vacío en el payload del POST; **el
  manifiesto existe en disco** (`out_dir/manifest.json` tras
  correr el cortador); `viral.seo_scores` del POST son números
  planos por plataforma, no dicts del manifiesto);
  `is_youtube_url` (youtube.com / youtu.be / m.youtube.com sí,
  twitch.tv no); picker de picos de audio con fixtures
  deterministas (audio sintético + transcripción fija → picos
  esperados, múltiplo calculado, mediana 0 → 1,0 y descarte);
  construcción del dict de audio (`rms_per_second` →
  `audio_stats` → `{"stats", "words"}` con
  `words = len(transcript_words(transcript))`; `platform=None` →
  peso 1,0; plano → 2,0) **llamando a `score_clip` con el dict
  construido** (un test de forma no cazaría el crash de la
  lista); adaptador de viralidad (detail=None → chat estimado
  5,0; total 47–55; `viral_multiple` en el manifiesto y
  `audio_multiple` en `viral_breakdown`); puerta de pico de audio
  (múltiplo ≥ 2,0 + hype > 0 o LLM); puertas con manifiesto
  YouTube (sin chat, con SEO completo; falta de SEO = no pasa;
  `filter_passed: false` no bloquea); **umbral al encolar**:
  `burn_and_enqueue` con bridge mockeado, clip de YouTube con
  prioridad 50 (flojo) → NO se encola y el motivo queda en el
  log **y como evento `clip_descartado`**; prioridad 56 (bueno)
  → se encola; clip de Twitch con prioridad 50 → se encola (el
  umbral es solo para YouTube); `panel_score` redondea a entero
  (55,7 → 56, 50,4 → 50; crudo 52,5 → 53 pasa, 52,4 → 52 no
  pasa); **el chequeo va antes de `subtitle_clip`** (con el
  `subtitle_clip` mockeado se afirma que NO se llama para un
  clip flojo);
  dedup
  ENTRE corridas por (source_key, inicio) con lista fresca
  simulada; cruce con Twitch por solape de título (≥ 0,7
  bloquea, < 0,7 pasa, < 3 tokens no bloquea, sin condición de
  idioma, día = `santiago_day_key(created_at)` con un
  `created_at` fijo, `influencer_id` resuelto a nombre vía la
  lista de influencers); **el `append` de `run()` lleva el
  nombre del streamer** y el cruce mismo-corrida bloquea solo
  con mismo streamer (test: mismo momento, distinto streamer →
  no bloquea); **re-lectura de la lista justo antes de cada
  encolado** (un clip encolado por `cut` en la misma corrida es
  visible para el cruce); enfriamiento 6 h con `state.json`
  restaurado y subido; URL única con pre-chequeo de título
  (GTA VI sí, no-GTA VI se salta) y job con `needs: [detect]`;
  **exclusión mutua**: con `youtube_url` en el
  `client_payload`, el `if` de `vod-fallback` es falso y solo
  corre `youtube-single` (verificación del `if` en el workflow,
  no solo en Python);
  muestreo con tope de 24 muestras repartidas uniformemente en
  un VOD de 6 h simulado; transcripción completa en video ≤ 30
  min (audio bestaudio ≤ 50 MB; **si el audio supera 50 MB, el
  camino de muestreo** — no un fallo; la detección es el stderr
  «max-filesize» o el tamaño tras la descarga); rescate: ningún
  clip de YouTube cae en 40–44 (mínimo 47); segunda entrada
  Rubius con permission=1 y active=1; `get_streamer(name,
  platform="twitch")` por defecto; `publish.py:1931` resuelve
  por (nombre, plataforma) vía `load_clip_package` e
  `is_allowed` por plataforma; **Worker**: POST /api/clips con
  dos Rubius — con `source_key` YouTube → id de la entrada
  youtube, con `source_id` numérico → id Twitch; `gate.ts`
  bloquea el job de un clip cuyo influencer tiene
  `permission=0` o `active=0` (job retenido con el motivo, a
  draft con `retryable = 0`); endpoint
  `POST /api/worker/run-youtube` con URL no-YouTube → 400 y
  con dispatch fallido → `ok: false` (incluido tras un
  `panel-live` fallido reciente: el freno de reintentos no lo
  frena);
  GET /api/influencers/live con una fila `platform=youtube`
  presente.
- Suite del cerebro en verde; tests del Worker en verde
  (migración 0044 + `source_key` en POST/GET + endpoint
  `POST /api/worker/run-youtube` + dispatch con `youtube_url`
  opcional y salto del throttle para dispatches explícitos +
  chequeo de influencer en `gate.ts` + resolución por
  plataforma en POST /api/clips). CI
  en verde.
- En vivo (aceptación, criterio DURO): dispatch `panel-live` con
  `youtube_url=https://youtu.be/NrNdw_Y6_bI` (tenant sebastian) y
  verificar en el panel: **≥ 1 clip en la Cola con viralidad ≥45,
  SEO ≥80 en las 4 plataformas, 40–60 s, subtítulos del audio
  real y veredicto del validador**, con **`viral_score` visible
  en GET /api/clips** (prueba de que el `viral` del POST llega,
  no solo el SEO del PATCH) y `source_key = NrNdw_Y6_bI`.
  Verificar que no duplica un clip de Twitch del mismo momento
  (revisión previa en el panel: si ya existe un clip de Rubius
  de 6 estrellas / aeropuerto del mismo día, el cruce de §3.8
  debe bloquearlo y eso se registra). Verificar que la entrada
  `platform=youtube` de Rubius existe en el panel (con
  permission=1 y active=1) y que el barrido la lista. **Y la
  promesa del chequeo de streamer**: desactivar la entrada
  youtube de Rubius → el job de un clip YouTube suyo se bloquea
  con el motivo «streamer desactivado en el panel» (a draft, sin
  reintento); desactivar la entrada Twitch → bloquea los clips
  de Twitch, no los de YouTube; al reactivar, el clip se
  re-encola a mano desde el panel.
- Un resultado «sin clip» con motivo documentado (sin momento,
  sin webcam, sin pico de audio) es un RESULTADO REPORTADO,
  **no aceptación**: no cuenta como pasada y el trabajo vuelve a
  calibración, no se cierra.
- El barrido automático queda verificado en la siguiente corrida
  normal del fallback con --youtube (que Rubius aparezca barrido
  por canal y sus videos se listen, no se salten sin motivo).

## 5. Riesgos y no-gos
- Bot-check de YouTube intermitente: reintentos + backoff
  existentes; sin login, sin cookies, sin descargas masivas (máx. 2
  VODs/corrida, ≤ 24 muestras o 1 audio completo ≤ 50 MB). Mismo
  riesgo que el barrido actual.
- No inventar viralidad ni subtítulos: todo medido del audio real;
  sin chat no hay atajo. Sin prior de alcance en la fórmula.
- R1 intacto: ni fórmulas, ni pesos, ni umbrales de puntuación
  cambian. Lo único nuevo es el adaptador de entrada (llamar a
  `score_clip` con detail=None y guardar el múltiplo por
  separado) y la puerta de pico de audio, ambos documentados aquí.
- No tocar el camino Twitch/Kick (rama nueva, sin regresiones).
- No escribir en D1 a mano ni encolar por fuera del puente.
- No crear sección ni pestaña nueva en el panel. No tocar crons.
  Cambios de Worker en este trabajo (listados, no escondidos):
  (1) migración 0044 (`source_key`, ambos tenants); (2) aceptar
  `source_key` en POST/GET + devolverlo en `clipOut`; (3) endpoint
  NUEVO `POST /api/worker/run-youtube` (con sesión) que recibe
  `{youtube_url}`, valida que sea URL de YouTube (400 si no) y
  llama a `dispatchCerebro(env, "panel-live",
  tenant, youtube_url)` (devuelve `ok: false` con el motivo si
  el dispatch no se emite); `dispatchCerebro` gana el parámetro
  opcional `youtube_url` (en el `client_payload`) y salta el
  throttle de 15 min, el freno de presupuesto Y el freno de
  reintentos tras dispatches fallidos (`dispatchRetryAllowed`)
  cuando `youtube_url` está presente (petición explícita, misma
  regla que `panel-start`); un campo opcional «URL de YouTube»
  en el control de ejecución existente llama a ese endpoint;
  (4) AÑADIR un chequeo de duplicado por (name, platform) en POST
  /api/influencers (400 si ya existe esa pareja): hoy NO existe
  ningún chequeo (el handler hace parse → chequeo de activable →
  `createInfluencer`, INSERT plano, `db.ts:2650`). Sin él, dos
  entradas `platform=youtube` con el mismo nombre reintroducen
  la ambigüedad de primer match DENTRO de la plataforma y la
  corrección de la resolución por plataforma quedaría a medias
  en ese caso; (5) **chequeo
  de influencer activo en `gate.ts`**: al publicar un job, se
  resuelve `clip.influencer_id` → fila de `influencers`; si
  `permission=0` o `active=0`, el job queda bloqueado/retenido
  con el motivo «streamer desactivado en el panel» (aplica a
  todos los clips, no solo a los de YouTube: es la
  implementación en producción de la regla ya documentada en
  `publish.py:1931`); (6) **resolución por plataforma en POST
  /api/clips**: si `body.source_key` es un id de YouTube no
  vacío → el handle se resuelve entre las entradas
  `platform=youtube`; si no, entre las de Twitch (sin esto, el
  chequeo (5) miraría la entrada Twitch para todos los clips
  de YouTube). Nada más.
- No publicar nada desde este trabajo: los clips van a la Cola y el
  panel decide.

## 6. Qué cambia para el usuario
- Nada que configurar (salvo una acción normal del panel: la
  segunda entrada de Rubius con platform=youtube, permission=1 y
  active=1, que se crea en el formulario de influencers como
  cualquier otra, y pegar la URL de un video en el campo del
  control de ejecución cuando quiera cortar uno concreto): los
  videos GTA VI de los canales YouTube de sus influencers se
  cortan solos como los VODs de Twitch, y un video concreto que
  comparta se corta con solo pasar su URL (el programa lo hace
  todo: bajar, buscar el momento, cortar, validar, encolar).
- El video de Rubius de las 6 estrellas es el caso de aceptación:
  si la spec se implementa, ese video produce clips en la Cola sin
  que nadie lo toque a mano.
- Decisiones que el usuario aprueba con esta spec: (a) los clips
  de YouTube con prioridad < **53** no se encolan (en producción
  no existe el archivo a los 3 días: la publicación va por el
  Worker desde D1, así que el filtro se aplica al encolar; tabla
  en §3.5); (b) el rescate por gritos no aplica a clips
  de YouTube (su total no baja de 47 por construcción). (c) Un
  job bloqueado porque el streamer está desactivado NO se
  reintenta solo: al reactivar el streamer, el clip se
  re-encola a mano desde el panel.

## 7. Historial de revisión (EGM)
- v1 (2026-11-24 ~01:00): borrador del elefante.
- Pez dorado vuelta 1 (2026-11-24 ~01:05): **NO APRUEBA** — 6
  bloqueadores (B1–B6) + mayores M1–M4 + menores M5–M12.
- v2 (2026-11-24 ~01:15): correcciones aplicadas.
- Pez dorado vuelta 2 (2026-11-24 ~01:14): **NO APRUEBA** — B3,
  B5, M1, M2 (núcleo), M5–M11 corregidos de verdad; pero B1, B2 y
  B4 siguen rotos (B1: el adaptador con detail msgs=0 da chat
  0,0 y total 42,5 < 45; B2: `_chat_base(2,0) = 3,0`, el flojo es
  50,4 y min_priority 50 no separa; B4: `_norm` no incluye
  `youtube_url`, la ruta del dato no está definida) + N1 nuevo
  (el cortador no está definido) + mayores 7–11 y menores 12–16.
- v3 (2026-11-24 ~01:20): correcciones aplicadas.
- Pez dorado vuelta 3 (2026-11-24 ~01:25): **NO APRUEBA** — el
  núcleo de B1 (adaptador detail=None), la tabla de B2 y el
  extremo B4 (segunda entrada creable) están bien; pero 6
  bloqueadores nuevos o pendientes: B1 (la prioridad se
  recalcula en el momento de publicar con `viral_multiple`, que
  el contrato no definía); B2 (la v3 se contradecía: 50 en el
  cableado, 53 en la decisión); B3 (el job `youtube-single` como
  `workflow_dispatch` no puede autenticarse: sin
  `client_payload` no hay token; sin restauración de estado no
  hay enfriamiento; `--tenant` no existe); B4 (la entrada de
  audio de `score_clip` sin definir: `std_db`/`steady_frac`/
  `words`); B5 (el contrato omitía el título: `viral.best_title`
  + `vod_id` + `vod_layout` + `is_youtube_url` sin definir);
  B6 (la transcripción completa contradecía «nunca el audio
  entero») + mayores M1–M7 y menores m1–m6.
- v4 (2026-11-24 ~01:30): correcciones aplicadas — B1: contrato
  con `viral.viral_multiple` + `source_key` y cableado por
  `_clip_scores` → `order_queue` → `should_auto_archive` con
  `min_priority`; B2: **53 en toda la spec** (la v3 tenía 50 en
  el cableado por error); B3: job `youtube-single` en el evento
  `panel-live` con `needs: [detect]`, artefacto de sesión y
  `state.json` restaurado/subido, env `PANEL_TENANT`, y
  `youtube_url` opcional en el `client_payload` del dispatch +
  campo en el control de ejecución existente; B4: dict de audio
  desde `audio.py` (`rms_per_second` → `audio_stats` →
  `{"stats", "words"}`) y `platform=None` (peso 1,0), rango 2–10;
  B5: contrato con `viral` (best_title, description, tags,
  thumbnail_text, viral_multiple) + `title_es`/`title_en` +
  `vod_id` + `vod_layout` + `is_youtube_url` definido +
  segmentos en `segments/` + prueba de conformidad con título
  no vacío; B6: audio completo permitido explícito para ≤30 min
  (bestaudio ≤50 MB, nunca el video); M1: reconciliación con el
  PATCH auto-seo (aceptación comprueba `viral_score`); M2:
  24 muestras repartidas uniformemente; M3: cobertura 58–75%;
  M4: presupuesto ≤25 min; M5: permission=1 y active=1 +
  `get_streamer(name, platform="twitch")` + verificación de
  /api/influencers/live; M6: día = fecha Chile de `created_at`,
  sin condición de idioma; M7: layout de segmentos en el
  contrato; m1–m6 (comando sin --tenant, limpieza de «pase
  separado», `youtube_clips_fn` inyectable, rango 2–10,
  get_streamer, riesgo residual cámara/juego).
- Pez dorado vuelta 4 (2026-11-24 ~01:36): **NO APRUEBA** — la v4
  corrige de verdad el núcleo de B1, B2, B5, M1, M2/M3 y M5
  (datos); pero 5 bloqueadores: B4 residuo (`words` es una
  LISTA donde `audio_component` espera un NÚMERO —
  `transcript_words` devuelve lista, `audio_verdict` devuelve
  `len(words)`, `review.py:290` pasa el entero: crash TypeError
  en cada clip); B1 residuo (el manifiesto de YouTube nunca se
  escribe a disco: `_clip_scores` lee del disco y sin él el clip
  cae en `no_score` y jamás se publica); B3 residuo (el dispatch
  de la URL no tiene camino: el control dispara `panel-start`,
  que no activa el workflow; `panel-live` solo lo dispara el
  cron; y el throttle de 15 min tragaría la URL); B3 nuevo (el
  `if` con `!= ''` dispara el job en todas las corridas:
  `null != ''` es `true` en expresiones de GitHub); B7 nuevo
  (`youtube-single` y `vod-fallback` corren en paralelo y se
  pisan el `state.json` y pueden duplicar el encolado) +
  mayores M4 (timeout 30 sin setup; peor caso 24,2 no 19–23),
  B6 residuo (tope 50 MB sin mecanismo ni fallback), M1 residuo
  (`seo_scores` del manifiesto son dicts y el POST da 400),
  M5 residuo (los dos llamadores de `get_streamer` sin
  resolver), M6 residuo («mismo streamer» sin resolución
  `influencer_id` → nombre), M7 residuo (el contrato omite `n`)
  + menores 12–15.
- v5 (2026-11-24 ~01:45): correcciones aplicadas — B4:
  `words = len(transcript_words(transcript))` + test que llama a
  `score_clip` con el dict construido; B1: el cortador escribe
  `out_dir/manifest.json` y el job sube los manifiestos con el
  paso «Guardar manifiestos» + test del archivo en disco; B3:
  endpoint `POST /api/worker/run-youtube` → `dispatchCerebro`
  con `youtube_url` y salto del throttle y del freno de
  presupuesto para dispatches explícitos; B3-`if`: chequeo de
  veracidad `github.event.client_payload.youtube_url`; B7:
  `vod-fallback` excluye la corrida con URL
  (`&& !github.event.client_payload.youtube_url`); M4: timeout
  45 min y peor caso del cortador ≈ 24 min + setup, con el
  bloque completo de setup copiado; B6: tope con
  `--max-filesize 50M` y fallback al muestreo + muestras en
  bestaudio; M1: `seo_scores` del POST como números planos;
  M5: `publish.py:1931` por (nombre, plataforma) e `is_allowed`
  por plataforma; M6: `influencer_id` → nombre vía
  `GET /api/influencers` y helper de Chile nombrado; M7: `n` en
  el contrato; menores 12–15 (origen `clip.get("source_key")`,
  helper de Chile, setup completo listado, cifra 24,2).
- Pez dorado vuelta 5 (2026-11-24 ~01:43): **NO APRUEBA** — la v5
  corrige de verdad B4, B1-manifiesto, B3-dispatch, B3-`if`, B7,
  B6, M1, M5(b), M6 y M7; pero 2 bloqueadores de fondo: B1 nuevo
  (el umbral 53 está cableado en `publish.py`, que solo corre
  sobre la cola local `publish_queue.json`; en producción
  publica el Worker desde D1 — `dueJobs` + `queueClipCompare` +
  `baseQueuePriority` — que nunca lee un manifiesto ni llama a
  `_clip_scores`, y no existe ningún archivo por prioridad: el
  umbral no tendría ningún efecto) y M5(a) (la corrección de
  `publish.py:1931` también vive en el bucle legacy: en
  producción no hay chequeo de influencer activo al publicar) +
  mayores M4 (timeout 45 no cubre el peor caso sin caché:
  24,2 + 28 ≈ 52 > 45) y menor 13 mal corregido (`_now_chile`
  no admite argumentos) + menores 5–9.
- v6 (2026-11-24 ~01:50): correcciones aplicadas — B1: **opción
  A, umbral al encolar** (`burn_and_enqueue` no encola clips de
  YouTube con `panel_score < 53`; la opción `order_queue` /
  `should_auto_archive` se elimina: ese bucle es legacy local
  sin efecto sobre clips del panel); M5(a): el Worker gana el
  chequeo en `gate.ts` (`permission=0` o `active=0` → job
  bloqueado/retenido; aplica a todos los clips) + `publish.py:1931`
  resuelve por (nombre, plataforma) vía `load_clip_package`;
  M4: timeout 60 min (igual que `vod-fallback`); menor 13:
  `santiago_day_key` (`retry.py:121`) en vez de `_now_chile`;
  menores 5–9 (validación del endpoint 400 + `ok: false` si el
  dispatch no se emite; `out_dir` de URL única fijado a
  `runs/vod-fallback/<video_id>/`; detección del exceso de
  50 MB por stderr «max-filesize» o tamaño tras la descarga;
  bullet duplicado de §4 borrado).
- Pez dorado vuelta 6 (2026-11-24 ~01:54): **NO APRUEBA** — la v6
  cierra de verdad B1 (opción A), M4, menor 13, menores 5–9 y
  las regresiones; pero 1 bloqueador de fondo: B1-M5(a) (POST
  /api/clips resuelve el handle por PRIMER match por nombre,
  `index.ts:2788`, `ORDER BY id ASC` → la entrada Twitch gana
  siempre; `burn_and_enqueue` manda el nombre,
  `vod_fallback.py:732`: todo clip de YouTube queda con el id
  Twitch y el gate miraría la fila equivocada) + mayores M1
  (el umbral sin ubicación fija: tras la validación, cada clip
  flojo quemaría subtítulos + SFX + validador + re-render),
  M2 (el `append` de `run()` no lleva el nombre del streamer:
  el cruce mismo-corrida no puede comprobar «mismo streamer»),
  M3 (job retenido va a draft con `retryable = 0` y NO se
  reintenta solo al reactivar) + menores m1–m5.
- v7 (2026-11-24 ~01:56): correcciones aplicadas — B1-M5(a):
  POST /api/clips resuelve el handle POR PLATAFORMA
  (`source_key` YouTube no vacío → entradas `platform=youtube`,
  si no → Twitch) + test con dos Rubius + línea de aceptación
  (desactivar la youtube bloquea YouTube, desactivar la Twitch
  bloquea Twitch, no YouTube); M1: el chequeo del umbral va AL
  INICIO de `burn_and_enqueue`, antes de `subtitle_clip`, y el
  descarte se reporta como evento `clip_descartado` (visible en
  el panel); M2: el `append` lleva `streamer` (nombre) y el
  cruce mismo-corrida lo comprueba; M3: documentado que el job
  retenido no se reintenta solo y se re-encola a mano al
  reactivar (+ decisión (c) en §6); m1: redondeo documentado
  (crudo 52,5–53,4 → 53 pasa; umbral efectivo 52,5); m2: test
  de clip YouTube = `source_key` no vacío (sin «no todo
  dígitos»); m3: `dispatchCerebro` con `youtube_url` salta
  también `dispatchRetryAllowed`; m4: sitio del cruce fijado
  (`run()` junto a `already_enqueued`, o en
  `burn_and_enqueue` vía `bridge.list_clips()`); m5: `run()`
  re-lee la lista del panel justo antes de cada encolado
  (carrera con `cut`).
- Pez dorado vuelta 7 (2026-11-24 ~01:59): **APRUEBA CON
  CORRECCIONES** — la v7 cierra de verdad B1-M5(a), M1, M2, M3,
  m1, m2, m3 y m5; no hay bloqueadores. Quedan 9 menores (H1–H9),
  ninguno impide implementar: H1 (§5(4) parte de una premisa
  falsa: NO existe chequeo de duplicados en POST
  /api/influencers), H2 (`influencer_id` NULL sin definir), H3
  (m4 sigue con la disyunción del sitio del cruce), H4 (el
  ejemplo del append omite `source_key` y `title`), H5 (§3.7
  conserva el texto viejo de m2), H6 (§3.3 atribuye
  `viral.viral_multiple` al cableado eliminado), H7 (§3.8 no
  reconcilia con el comentario de auto-seo del código; §1 con la
  dependencia SEO-D1 obsoleta), H8 (TOCTOU residual sin
  reconocer), H9 (dos mecanismos de resolución sin elegir uno).
- v8 (2026-11-24 ~02:00): correcciones H1–H9 aplicadas — H1:
  §5(4) ahora AÑADE el chequeo de duplicado por (name,
  platform); H2: clip sin `influencer_id` → el chequeo pasa
  (fail-open con log); H3: sitio del cruce fijado en `run()`
  junto a `already_enqueued`; H4: ejemplo del append unificado
  con los 5 campos; H5: paréntesis de §3.7 borrado (marcador =
  `source_key` no vacío ⟺ YouTube); H6: `viral.viral_multiple`
  lo lee `burn_and_enqueue`; H7: §3.8 escribe el razonamiento
  de la puerta SEO del POST (los 4 ≥ 80 ya garantizados) y §1
  marca la dependencia SEO-D1 como CERRADA por auto-seo v5.5.0;
  H8: ventana residual declarada como riesgo aceptado; H9:
  mecanismo único = `source_key` (paréntesis de la pista
  explícita borrado).
- Implementación (2026-11-24 ~02:00–06:30, usuario aprobó v8):
  cerebro + Worker + workflow + panel. **Corrección medida en
  implementación (no cambia el diseño)**: la spec decía «crudo
  52,5 → 53 pasa», pero `panel_score` usa `int(round(...))` y
  Python redondea al par (`round(52,5) = 52`); el umbral efectivo
  es 52,5 EXCLUIDO (52,5 → 52 no pasa; 52,7 → 53 pasa). Los
  valores de la tabla (55,7 → 56, 50,4 → 50, 49,3 → 49) no
  cambian. Los tests fijan el comportamiento real. Cambiar a
  half-up sería tocar `panel_score`, compartido con el cortador
  en vivo — fuera de alcance. Además: `detect.py:267` reventaba
  con `multiple=None` en `filter_passed` (`TypeError`); fix
  mínimo `{(multiple or 0.0):.1f}` sin tocar fórmula ni pesos.
