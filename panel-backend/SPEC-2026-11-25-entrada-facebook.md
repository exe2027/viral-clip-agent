# SPEC — Entrada de Facebook al programa de clips GTA 6 (reels como fuente de cortes)

Fecha: 2026-11-25.
Orden del usuario (2026-11-25 18:53, chat del plan maestro): «Sí, programa la
entrada de Facebook» (pregunta: programar una entrada de Facebook igual a la
de YouTube para meter reels; el programa lo corta solo, nunca Muse a mano).
Método: EGM (esta spec → revisión pez dorado → aprobación del usuario →
implementar).
Estado: v1, borrador para revisión goldfish (no implementado).

## 1. Estado actual (verificado en código y en vivo 2026-11-25)

- **El corte automático de YouTube existe y es el modelo** (spec
  `SPEC-2026-11-24-corte-automatico-youtube.md` v8, commit `392d180`):
  cerebro `clipagent/youtube_cut.py` (muestreo de audio 30 s, transcripción
  completa ≤30 min bestaudio ≤50 MB, adaptador `detail=None` / chat estimado
  5,0, puerta `audio_multiple ≥ 2,0`, contrato de manifiesto completo a
  disco), endpoint `POST /api/worker/run-youtube`, chequeo de streamer en
  `gate.ts`, campo «URL de YouTube» en el panel, dedup por `source_key`
  (migración 0044), job `youtube-single` + exclusión mutua con
  `vod-fallback`, umbral de prioridad 53 al encolar en `burn_and_enqueue`
  (antes de `subtitle_clip`, solo clips con `source_key` no vacío).
  Archivos que tocó el commit `392d180` (la lista a espejar):
  `.github/workflows/cerebro-live.yml`, `clipagent/cli.py`,
  `clipagent/detect.py`, `clipagent/live_cutter.py`,
  `clipagent/publish.py`, `clipagent/streamers.py`,
  `clipagent/vod_fallback.py`, `clipagent/youtube.py`,
  `clipagent/youtube_cut.py`, la spec,
  `panel-backend/worker/src/index.ts`,
  `panel-backend/worker/src/gate.ts`,
  `panel-backend/frontend/dist/app.js`.
  (`youtube_cut.py:30`, docstring del módulo: «paquete suggest-only
  63–70; paquete con título del vídeo 82–88» — la medición que §3.1
  reconcilia con el 84–85 de Noni.)
- **La fuente viral** (`clipagent/viral_source.py`): solo Shorts (el vídeo YA
  es el momento, **15–60 s**) y solo YouTube (yt-dlp, $0, sin API key).
  El patrón probado: reel viral → re-make a rubius real → validación
  fotograma a fotograma → cola del panel, con puertas viralidad ≥45 y
  SEO ≥80. El suelo de 15 s viene de `config.yaml:829`
  (`viral_source.min_duration_s: 15`, «un Short viral dura entre
  15 s...»), no solo del docstring de `viral_source.py`; y
  `config.yaml:73,728` registra `min_duration_s: 1` («sin mínimo,
  regla 40-60 eliminada 2026-11-21»).
- **No existe código de Facebook en el cerebro**: `grep facebook
  clipagent/*.py` solo devuelve la plataforma de publicación
  `facebook_reels` (`accounts.py`, `cli.py`). No hay descargador, ni
  `is_facebook_url`, ni rama `facebook_url` en `vod_fallback.py`, ni
  endpoint `run-facebook`, ni job en `cerebro-live.yml`.
- **Antecedente a mano (NO repetir)**: el clip de Noni de la playa (reel
  `4105414956418236`) se bajó con yt-dlp desde esta VM
  (`--no-check-certificate`) y se rehizo a mano — eso fue ANTES de la
  regla «lo hace el programa solo» (2026-11-24 00:53). Ahora todo debe
  hacerlo el programa: nada de descargar/transcribir/cortar/validar a
  mano. Su SEO (título Gemini) llegó a 84–85: es la prueba de que un
  título Gemini de un reel de Noni PUEDE pasar la puerta SEO ≥80 (§3.1).
- **Caso de prueba, VERIFICADO en vivo 2026-11-25 con yt-dlp desde esta
  VM** (`.venv/bin/python -m yt_dlp -J --compat-options no-certifi`,
  mismas base opts que YouTube; **sin `--no-check-certificate`**):
  reel `1918676049105232` («Noni intenta un vuelo cinematográfico con
  una avioneta entre edificios, se queda sin combustible y se estrella»,
  split-screen, audio español), autor `Clips de Noni`
  (`uploader_id: 61559354617566`, perfil
  `https://facebook.com/people/Clips-de-Noni/61559354617566/`), publicado
  2026-11-22, **duración 18,853 s**, `view_count: 15816`,
  `like_count: None` (el -J de Facebook NO da likes; el navegador decía
  719 likes y el título del -J dice «31K views · 894 reactions» — el
  título del -J es texto de engagement, NO el título del reel; ver
  §3.1). **La descripción del -J SÍ es usable: `'#gta6 #noni
  #noniclips #clipsdenoni #gta'`** (verificado 2026-11-25) — es la
  señal del pre-chequeo GTA de §3.1 (`tags: None`, `categories:
  None`). Formatos: solo vídeo AV1 (540x960, 720x1280, 1080x1920) +
  audio m4a, más los progresivos `sd`/`hd` de Facebook.
  **Descargas verificadas**: (a) `-f "hd"` → `720x1280 H.264 + AAC,
  18,97 s, 4,58 MB` (un solo mp4 progresivo, el mismo tipo que el reel
  de la playa); (b) `-f "bv*[height<=1080]+ba/b[height<=1080]/b"` →
  `540x960 AV1 + AAC, 18,855 s, 832 KB` (merge). El enlace que el
  usuario pegó (`https://www.facebook.com/share/r/18cMsFx95G/`) se
  resuelve con el mismo `-J` (un primer intento devolvió salida vacía
  — fallo transitorio — y el reintento devolvió el JSON con el mismo
  id; los 3 intentos con backoff de `_run_with_retries` lo cubren).
- **Noni en el panel (D1, verificado en vivo 2026-11-25, fuera del
  repo)**: seba tiene DOS filas `Noni` (`id 21` e `id 40`, ambas
  `platform=twitch`,
  `profile_url=https://www.twitch.tv/noni`, `language=es`,
  `permission=1`, `active=1`); kaez tiene una (`id 40`, twitch).
  **No existe ninguna entrada `platform=facebook` de Noni** en ningún
  tenant. Hace falta crearla (una acción normal del panel, mismo modelo
  que la segunda entrada de Rubius `platform=youtube`): `handle =
  noni` (**el handle es `noni`, igual que las filas de Twitch: la
  resolución por nombre y el fallback de POST dependen de él; crearla
  como «Clips de Noni» rompería la resolución**), `platform=
  facebook`, `profile_url=https://facebook.com/people/Clips-de-Noni/
  61559354617566/`, `language=es`, `permission=1`, `active=1`.
- **Puertas duras del fallback** (`clipagent/vod_fallback.py:76-79`,
  `clip_passes_gates`): `MIN_VIRALITY = 45`, `MIN_SEO = 80`,
  **`MIN_DUR_S = 40`, `MAX_DUR_S = 60`**. El reel de aceptación dura
  18,85 s: **con la puerta actual no pasa** (§3.2 resuelve esto).
  **La puerta de duración del Worker NO es el problema**: valida
  contra los ajustes del tenant (`panel-backend/worker/src/index.ts:
  1167,1264` y `gate.ts:257`, `eff.min_duration_s` /
  `eff.max_duration_s` de `getSettings`), cuyo valor por defecto es
  **1–60 s** (`panel-backend/worker/src/db.ts:555`, `clampInt` con
  mínimo 1 en `db.ts:688`). **Verificado en vivo 2026-11-25 en las
  D1**: seba (`settings` id 1 e id 2) y kaez (id 1) tienen
  `"min_duration_s":1,"max_duration_s":60`. Un clip de 18,85 s pasa
  el Worker sin ningún cambio; el cambio de duración es solo el del
  cerebro (§3.2). (El comentario de la cabecera de POST /api/clips
  que habla de «la regla de oro (40–60 s...)» es un comentario
  obsoleto: la validación real lee los ajustes, y los ajustes ya
  dicen 1–60.)
- **`source_key` hoy significa YouTube en tres sitios** (la spec los
  lista, no los esconde): (1) `panel-backend/worker/src/index.ts:2857`,
  resolución del handle en POST /api/clips:
  `wantPlatform = sourceKey ? "youtube" : "twitch"`; (2)
  `clipagent/vod_fallback.py:837-840`, umbral 53: `if source_key:`
  (cualquier `source_key` no vacío; la l. 831 es el comentario);
  (3) `clipagent/publish.py:1940`,
  `origin_platform`: `if source_key: origin_platform = "youtube"`.
  El chequeo de streamer en `gate.ts:637` NO distingue plataforma: lee
  `clip.influencer_id` → fila y mira `permission`/`active` (no hay que
  tocarlo: con la resolución de (1) correcta, el id ya es el de la
  entrada facebook).
- **La resolución de POST /api/clips tiene un fallback** que la v1
  no declaró (`index.ts:2857-2859`): `rows.find(plataforma
  preferida) ?? rows.find(cualquier plataforma por nombre)` — con
  `source_key "fb:..."` y sin entrada facebook, hoy caería a la
  entrada **Twitch** del mismo nombre. §3.3 lo prohíbe para `fb:`
  (400 sin fallback).
- **`vod_id` / `source_id` / enfriamiento (lo que la v1 decía mal)**:
  `burn_and_enqueue` hace `vod_id_int = int(vod_id)` con fallback a
  0 (`vod_fallback.py:949-952`) — `int("1918676049105232")` **sí**
  cabe (1,9e15 < 2^63), así que `source_id` sería el id del reel,
  no 0 (solo los ids de YouTube no numéricos caen a 0, como dice el
  docstring de `already_enqueued`, `vod_fallback.py:272`). El
  enfriamiento se clavea por `str(vod_id)`
  (`vod_on_cooldown`/`mark_vod_attempted`, `vod_fallback.py:483-498`),
  no por `source_key`. §3.3 fija: `vod_id` = id numérico del reel
  en todas partes (clave de estado, `source_id`, `out_dir`);
  `source_key = "fb:<reel_id>"` es solo el marcador de dedup y la
  clave de resolución de POST.
- **El cruce mismo-momento con Twitch es solo del mismo día Chile**
  (`cross_blocks`, `vod_fallback.py:425-460`: `_clip_day(entry) !=
  today → skip`) y su condición de «misma plataforma»
  (`_entry_platforms(entry) & set(PLATFORMS)`) es vacua en la
  práctica (un clip real siempre tiene alguna plataforma en
  `PLATFORMS`). El clip de Noni de la playa del 23-nov NO puede
  bloquear la avioneta del 25-nov sea cual sea el título: §3.3
  retira ese control y lo sustituye por un test unitario.
- **Eventos `clip_descartado`**: `_process_manifest_clips` los
  reporta con `_report_event` (`vod_fallback.py:1078,1099,1127`;
  el descarte por umbral 53 lo reporta su rama `else`, no
  `burn_and_enqueue` — comentario en `vod_fallback.py:834`); los
  retornos tempranos de `_run_single_*` NO reportan nada (solo
  log). `_report_event` nunca levanta y en dry-run no reporta
  (`vod_fallback.py:119-130`). §3.1.1 fija dónde se reporta el
  fail-closed del autor.
- **`streamers.py:30`: `PLATFORMS = ("twitch", "kick", "youtube")`**
  — no incluye `facebook` y `add_streamer` rechaza otras
  plataformas (`streamers.py:243`). El camino del panel
  (`_from_panel`) no valida, pero la tupla es la única fuente de
  nombres de plataforma: §3.3 añade `"facebook"`.
- **Dispatch** (`panel-backend/worker/src/index.ts:482-597`):
  `dispatchCerebro(env, eventType, tenant, youtubeUrl?)` incluye
  `youtube_url` en el `client_payload` y la exención explícita
  (`explicitYoutube`) salta el freno de reintentos, el de presupuesto y
  el throttle de 15 min. El endpoint `POST /api/worker/run-youtube`
  (`index.ts:2606`) valida con `isYoutubeUrl` (400 si no) y devuelve
  `ok: false` (502) si el dispatch no se emite. El campo del panel es
  `btnRunYoutube` + input `youtubeUrl` en `frontend/dist/app.js:3593`
  (el dist es el archivo desplegado; la spec de YouTube lo tocó
  directamente, igual aquí). **La URL base del puente NO es el
  `PANEL_URL` de los pasos del workflow**: `PanelBridge` lee
  `PANEL_BASE_URL` (`clipagent/panel_bridge.py:43,66`, default avc),
  que el workflow fija a nivel global con un ternario por tenant
  (`.github/workflows/cerebro-live.yml:104-105`: kaez →
  `avc-kaez.clipsultravirales768.workers.dev`, si no avc) — kaez ya
  está cubierto; el `PANEL_URL` hardcodeado a avc en los pasos
  (líneas 169, 273, 391, 521) lo usan `remake_all.py` /
  `validate_all.py`, no el puente.
- **Workflow** (`.github/workflows/cerebro-live.yml`): job
  `youtube-single` (l. 419-552, `if: client_payload.youtube_url`,
  `needs: [detect]`, `timeout-minutes: 60`, bloque completo de setup,
  artefacto de sesión, estado restaurado/subido, manifiestos subidos,
  comando `python -m clipagent vod-fallback --youtube-url ...` con
  `${{ inputs.force == 'true' && '--ignore-cooldown' || '' }}`); el `if`
  de `vod-fallback` (l. 292) excluye la corrida con URL
  (`&& !github.event.client_payload.youtube_url`); `cleanup-session`
  (l. 552-554) depende de `[cut, vod-fallback, youtube-single]` y
  `youtube-single` descarga el artefacto de sesión
  (`panel-session-<tenant>-<run_id>`, paso «Descargar sesión del
  panel») — **si `facebook-single` no se añade a `needs` de
  `cleanup-session`, la sesión puede borrarse antes de que lo
  descargue** (§3.4).
- **Orquestación de URL única** (`clipagent/vod_fallback.py`):
  `run()` atiende `youtube_url` en el paso 0, ANTES del chequeo de
  directos (l. 1280-1287), y `main()` tiene el flag `--youtube-url`
  (l. 1455-1462). `_run_single_youtube` (l. 1170-1258) es la secuencia
  a espejar: metadatos → id → pre-chequeo GTA → streamers → resolver
  influencer → enfriamiento → puente → lista del panel → dry-run →
  `out_dir = out_root/<video_id>` (l. 1236; el manifiesto queda en
  `runs/vod-fallback/<video_id>/manifest.json`, mismo layout que el
  barrido) → cortador → `_process_manifest_clips` →
  `mark_vod_attempted` + `save_state`. La rama por VOD del barrido en
  `run()` (l. 1385-1390) NO se extiende a Facebook en la v1: no hay
  barrido y `latest_vod_for` devuelve `None` para `platform=facebook`
  (l. 545-549, «sin VODs»).
- **El cortador de YouTube, piezas que la v1 daba por hechas**:
  `run_youtube_clips(vod_url, out_dir, top_n=2, lang="es",
  single_url=False, llm_select=False, metadata_fn=None, audio_fn=None,
  sample_fn=None, transcribe_fn=None, rms_fn=None, segment_fn=None,
  render_fn=None, validate_fn=None, titles_fn=None, layout_fn=None,
  runner=None)` (`youtube_cut.py:638-645`); `plan_audio_samples`
  con `single_url=True` devuelve modo `"full"` (audio completo),
  no muestreo (`youtube_cut.py:171-187`); `rms_buckets_10s` acepta
  `bucket_s` como parámetro (`youtube_cut.py:215-236`); el
  descargador de segmentos es `download_youtube_segment` con
  `YOUTUBE_FORMAT = "298+140/bv[height<=720]+ba/b"`
  (`youtube.py:72,577-582`); `_run_with_retries` solo reintenta si
  `returncode != 0` (`youtube.py:177-193`) — una salida vacía con
  código 0 se devuelve como éxito; `YTDLP_BASE_OPTS` y
  `_run_with_retries` viven en `clipagent/youtube.py`.
- **Qué es «verificado en vivo fuera del repo»** (no está en los
  archivos citados y se marca como tal, no como hecho del código):
  las filas D1 de Noni, todos los datos del `-J` del reel, el `grep
  facebook clipagent/*.py`, `frontend/dist/app.js:3593`, la lista de
  archivos del commit `392d180` y el SEO 84–85 del reel de la playa
  (run manual 2026-11-23).

## 2. Decisión

**Añadir la entrada de Facebook al programa, espejando el corte de
YouTube donde aplique.** Las piezas nuevas son: (a) el cortador
`clipagent/facebook_cut.py` (§3.1), (b) la puerta de duración 15–60 s
(§3.2, cambio compartido y declarado), (c) el marcador `source_key =
"fb:<reel_id>"` y la resolución por plataforma en los tres sitios de
§1 (§3.3), (d) la entrada `facebook_url` en el dispatch `panel-live`
(§3.4), (e) el job `facebook-single` + exclusión mutua (§3.4).
**Alcance v1: URL única solamente.** El barrido automático de páginas
de Facebook (listar los reels de un autor) queda FUERA de la v1: no
hay mecanismo verificado para listar reels de una página desde Actions
(ni baseline para el múltiplo outlier), y el caso que el usuario pidió
es el reel concreto que comparte. El barrido es una v2 con su propia
verificación, no una promesa de esta spec. Nada manual, nada por Muse,
nada por el panel salvo pegar la URL y la entrada del influencer: el
corte corre en GitHub Actions (`cerebro-live.yml`) como el de YouTube.

## 3. Cambios

### 3.1 El cortador
- Nuevo módulo **`clipagent/facebook_cut.py`**, función
  `run_facebook_clips(reel_url, out_dir, top_n=2, lang="es",
  single_url=True, llm_select=False, metadata_fn=None, audio_fn=None,
  sample_fn=None, transcribe_fn=None, rms_fn=None, segment_fn=None,
  render_fn=None, validate_fn=None, titles_fn=None, layout_fn=None,
  runner=None) -> dict | None`. Espeja `run_youtube_clips`
  (`youtube_cut.py:638-645`, **firma completa con `llm_select` y
  todos los inyectables**, no «misma firma» en abstracto) y el mismo
  contrato de manifiesto, mismas funciones inyectables para tests.
  `YTDLP_BASE_OPTS` y `_run_with_retries` se importan de
  `clipagent/youtube.py` (donde viven), no se duplican.
- **Contrato de salida (obligatorio)**: el mismo `manifest.json` que
  `run_youtube_clips` produce, con `clips[]` en la forma que
  `clip_passes_gates` y `burn_and_enqueue` esperan. Cada clip trae:
  `start`, `duration`, `file` (MP4 final renderizado), `viral_score`
  (dict de `score_clip`), `seo_scores` (motor local),
  `moment_card` (`{game, title}`), `transcript` (del audio real),
  `source: "facebook"`, **`viral`: `{best_title, description, tags,
  thumbnail_text, viral_multiple}`** (el título ES el de este dict;
  sin él el POST da 400 por título vacío), **`title_es` /
  `title_en`**, **`source_key`** (`"fb:<reel_id>"`, §3.3),
  **`vod_id`** (el id numérico del reel, como string),
  **`vod_layout`**, **`n`** (índice del segmento, mismo nombrado
  `segments/seg_{n:02d}_t*.mp4` que `_rerender_vod_clip` espera),
  **`viral_breakdown`** (con `audio_multiple`, que
  `burn_and_enqueue` lee para el POST, `vod_fallback.py:955-960`),
  **`hype_score`**, **`score_kind`**, **`peak`**.
  **El cortador ESCRIBE `out_dir/manifest.json` a disco** (igual que
  `youtube_cut.py`: sin él el clip cae en `no_score` y nunca se
  publica). `out_dir` de URL única: `runs/vod-fallback/<reel_id>/
  manifest.json` (**mismo layout que YouTube,
  `out_root/<video_id>`, `vod_fallback.py:1236` — sin prefijo
  `fb_`**: la fuente se distingue por `source_key`, no por el
  directorio; el glob «Guardar manifiestos»
  (`runs/vod-fallback/*/manifest.json`) lo cubre).
- **`is_facebook_url(url)`** (nuevo, en `facebook.py` o en
  `facebook_cut.py` — el implementador elige el sitio siguiendo
  `youtube.py:is_youtube_url`): host en (`facebook.com`,
  `www.facebook.com`, `m.facebook.com`, `fb.watch`) y ruta con
  `/reel/`, `/share/r/` o `/watch/` (con `v=`), **o `fb.watch` con
  cualquier ruta** (un enlace `fb.watch/<código>` tiene ruta
  `/<código>` y no pasaría la regla de rutas: el host manda en
  ese caso). Es el predicado del dispatch de `run()`, y el
  predicado del Worker (`isFacebookUrl`, §3.4) es **idéntico**,
  con tests para ambos.
- **Metadatos con un solo `yt-dlp -J`** (mismas base opts que
  YouTube: `YTDLP_BASE_OPTS = ["--compat-options", "no-certifi",
  "--no-warnings"]`; **NUNCA `--no-check-certificate`** — verificado
  2026-11-25 que no hace falta desde esta VM, y quitaría la
  verificación TLS). **El id del reel se toma del `id` del -J, NO de
  parsear la URL**: el enlace `/share/r/` no contiene el id y el
  -J lo resuelve (verificado §1). Si el -J no trae `id`, no hay
  corte (fallo con motivo, no estimación). **El reintento cubre
  también la salida vacía**: `_run_with_retries` solo reintenta si
  `returncode != 0` (`youtube.py:177-193`), y el fallo transitorio
  observado sobre el enlace `/share/r/` fue **salida vacía** (código
  de salida no establecido). `fetch_facebook_metadata` reintenta
  (3 intentos con backoff) cuando el intento devuelve código != 0
  **o** stdout vacío **o** JSON inválido **o** JSON sin campo `id`:
  el predicado de reintento es «código 0 Y stdout parsea como JSON
  con `id`», no solo el código. Campos que SÍ trae el -J
  y se usan: `id`, `duration`, `uploader`, `uploader_id`,
  `view_count`, `description` (los hashtags). Campos que NO trae y
  NO se usan: título útil (el `title` es texto de engagement:
  «31K views · 894 reactions | #gta6 ...»), `like_count` (None),
  `channel_url`/`uploader_url` (None), heatmap de replay (no existe
  en Facebook). **El título del clip NO sale del -J** (ver Títulos
  abajo).
- **Descarga del vídeo** (verificado §1): selector de formato
  **`FACEBOOK_FORMAT = "hd/sd/bv*[height<=1080]+ba/b[height<=1080]/b"`**
  — primero el mp4 progresivo de Facebook (`hd` = 720x1280 H.264 +
  AAC, 4,58 MB en el caso de prueba; `sd` si no hay `hd`), y solo si
  no hay progresivo, merge bestvideo ≤1080 + bestaudio. Razón: H.264
  es el códec probado de la cadena de render (el reel de la playa era
  H.264 y el render funcionó); el AV1 queda como fallback y la
  corrida de aceptación debe verificar que el render lo maneja si el
  reel solo trae AV1 (ffmpeg lo decodifica, pero no se da por hecho
  sin medirlo: el log de la aceptación lleva el ffprobe del archivo
  descargado). **El selector combinado literal NO se ejecutó en la
  verificación previa**: se probaron `-f "hd"` y `-f
  "bv*[height<=1080]+ba/b[height<=1080]/b"` por separado. La
  aceptación (§4) ejecuta el selector combinado literal y registra
  el formato resuelto en el log; un test con runner mockeado afirma
  el argv exacto. Los segmentos se bajan con la función nueva
  **`download_facebook_segment(video_url, start_s, end_s, out_path,
  format=FACEBOOK_FORMAT, ...)`**, espejo de
  `download_youtube_segment` (`youtube.py:577-582`, que lleva el
  formato YouTube fijo `298+140/bv[height<=720]+ba/b` y no sirve
  para Facebook), con `yt-dlp --download-sections` y el mismo
  backoff de 3 intentos.
- **Bandas de duración del reel de entrada** (distintas de YouTube a
  propósito, ver §3.2):
  - `< 15 s` → ningún clip posible (resultado documentado, no fallo).
  - **15–60 s → el reel entero es el candidato** (sin detección de
    picos, directo a render/validar): es la definición de Short de
    `viral_source.py` (el vídeo YA es el momento). El reel de
    aceptación (18,85 s) cae aquí. El audio del candidato se extrae
    del vídeo ya descargado (`extract_audio_from_video(video_path,
    out_wav)`: `ffmpeg -vn` a wav, sin segunda descarga — distinto
    del camino YouTube, que baja bestaudio aparte; si el vídeo es
    AV1, ffmpeg lo decodifica y la aceptación lo verifica).
  - `> 60 s` → en modo URL única, **audio completo** (no muestreo):
    `plan_audio_samples` con `single_url=True` devuelve modo
    `"full"` (`youtube_cut.py:171-187`); audio completo ≤30 min
    bestaudio ≤50 MB, y si supera, el llamador cae a muestreo
    (muestras de 30 s cada 120 s en vídeo <1 h / cada 300 s en
    ≥1 h, máximo 24 repartidas uniformemente — el muestreo solo
    existe por ese tope, porque en la v1 no hay barrido). Ventanas
    top-N de 40–60 s alrededor de los picos RMS
    (`CLIP_TARGET_DUR_S = 50`, mismo valor que YouTube). Los
    segmentos se bajan SOLO con `download_facebook_segment` y
    `FACEBOOK_FORMAT`.
- **Autor e idioma** (`_resolve_single_facebook_influencer(meta,
  streamers)`, espejo de `_resolve_single_youtube_influencer`):
  (1) match por `uploader_id` contenido en el `profile_url` de una
  entrada `platform=facebook`; (2) si no, normalizar el nombre del
  autor (minúsculas; quitar el prefijo `clips de ` / `clips ` /
  `clipsde`; quitar no-alfanuméricos) y comparar con los nombres
  normalizados de las entradas `platform=facebook`; (3) si no casa
  con ninguna, **fail closed**: no se encola, motivo «autor sin
  entrada facebook en el panel» en el log y como evento
  `clip_descartado` (el usuario crea la entrada en el panel, como
  la segunda de Rubius; no se inventa un influencer ni se usa la
  entrada de Twitch del mismo nombre como permiso: el permiso es
  por plataforma, y la entrada de Twitch no autoriza Facebook).
  El idioma es el de la entrada (Noni: `es`).
- **Verificación de GTA VI (mecanismo que la v1 no tenía)**:
  `vod_clearly_not_gta6` no sirve para Facebook: el título del -J
  es texto de engagement (sin mención) y la categoría es `None` →
  devuelve `None` (fail open, `vod_fallback.py:575-588`), y
  `clip_passes_gates` lee el juego de `moment_card.game` /
  `viral.game` (`vod_fallback.py:238-241`), que el cortador de
  YouTube fija a `"GTA VI"` sin medir (`youtube_cut.py:466-529`) —
  la puerta sería tautológica si Facebook hiciera lo mismo. La
  función nueva es **`facebook_clearly_not_gta6(meta)`** (pre-chequeo
  en `_run_single_facebook`, §3.1.1): (1) si la descripción trae
  hashtags y alguno casa con un token GTA (compacto, como
  `_title_mentions_gta6`: `gtavi`, `gta6`, `grandtheftautovi`) →
  **verificado**; (2) si trae hashtags y ninguno casa → se salta
  («no es GTA VI: hashtags sin GTA»); (3) si no trae hashtags →
  chequeo por transcripción tras transcribir el candidato (texto
  compacto con `gta6` / `gtavi` / `grandtheftautovi`) → verificado
  o no; (4) si ninguna señal confirma → **fail closed** («juego no
  verificado», no se encola, evento `clip_descartado`). El reel de
  aceptación trae `'#gta6 #noni #noniclips #clipsdenoni #gta'` →
  verificado por (1). **`moment_card.game` / `viral["game"]` se
  fijan a `"GTA VI"` SOLO cuando la verificación pasa** (nunca
  hardcodeado): `build_facebook_package` recibe `gta_verified` y
  sin él no emite el juego — así la puerta de `clip_passes_gates`
  deja de ser tautológica.
- **Títulos + SEO**: constructor nuevo
  **`build_facebook_package(video_description, transcript_txt,
  streamer, lang, risas, hype, audio_multiple_value, gta_verified)`**,
  espejo de `build_youtube_package` (`youtube_cut.py:466-529`):
  el título principal sale de `auto_clips.suggest_titles` (Gemini)
  sobre el detalle real (risas/hype de la transcripción) — **no
  hay título de vídeo en Facebook** — y el SEO es
  **`metadata.score_package_seo`** (la misma función que usa
  YouTube, no `seo_score.score_all_platforms`, que la v1 nombraba
  mal). La descripción es la del reel (los hashtags del -J) o, si
  no hay, un extracto real de la transcripción + una línea factual
  («Clip de X en GTA 6»). Nada inventado. **Reconciliación
  84–85 frente a 63–70**: el docstring de YouTube
  (`youtube_cut.py:30`) mide paquetes suggest-only en 63–70 y con
  título del vídeo en 82–88 (vídeo largo de Rubius); el 84–85 es
  el run manual de Noni de la playa (2026-11-23, verificado en
  vivo fuera del repo), un título suggest-only + la descripción
  real del reel con un momento concreto. Los dos difieren en la
  entrada, no en la función; la aceptación es el árbitro y registra
  el desglose SEO por plataforma en el log, para que un fallo sea
  diagnosticable. Si el mínimo es <80, el clip NO se encola (falla
  cerrado) y la calibración vuelve, no se baja la puerta.
- **Viralidad**: el mismo adaptador de YouTube (§3.3 de su spec):
  `score_clip(detail=None, chat_multiple=None, audio=audio,
  platform=None)` (chat estimado en 5,0; total 45 + valor_audio,
  47–55); `viral.viral_multiple = audio_multiple` y
  `viral_breakdown.audio_multiple`. `score_kind: "facebook_audio"`.
  Sin pista de audio → descarte con motivo (no hay RMS ni
  transcripción que medir).
- **Puerta de pico de audio** (igual que YouTube §3.4 en la regla,
  distinta en la medida — **la v1 era matemáticamente imposible
  para el reel de aceptación**): `audio_multiple ≥ 2,0` Y
  (`hype_points > 0` o LLM lo selecciona), con `audio_peak_gate`
  (`youtube_cut.py:313`). El múltiplo es pico/mediana sobre
  buckets RMS (`audio_multiple`, `youtube_cut.py:239`), y con
  buckets de 10 s (`RMS_BUCKET_S = 10`, l. 107) un reel de 18,85 s
  tiene solo **2 buckets** (0 s y 10 s): la mediana es la media de
  los dos y el múltiplo es `2·máx/(máx+mín) < 2,0` salvo silencio
  exacto — **ningún reel normal de 15–20 s podía pasar la puerta
  por construcción**. Para la banda 15–60 s los buckets son **de
  1 s**: `rms_buckets_10s(rms_list, bucket_s=1)`
  (`youtube_cut.py:215`, que acepta `bucket_s` como parámetro; la
  estadística por segundo ya existe para `audio_component`). Pico
  = bucket de 1 s máximo dentro de la ventana (= todo el reel);
  mediana = mediana de TODOS los buckets de 1 s (la definición de
  `audio_multiple`: la mediana es de todos los buckets, no solo
  de la ventana). Con 19 buckets en 18,85 s, un momento fuerte
  (el choque) frente a una mediana de habla normal puede superar
  2,0. **La aceptación registra el múltiplo medido y los RMS por
  segundo en el log**: es la prueba de que la puerta es alcanzable
  para este reel; si el múltiplo medido es < 2,0, el resultado es
  «sin clip» documentado (no aceptación) y la calibración vuelve.
  En la banda >60 s los buckets son de 10 s como en YouTube.
  La calibración inicial (2,0) es la misma que YouTube; la
  aceptación deja los valores medidos en un comentario del código
  + fixtures.
- **Render + validación**: mismo motor que YouTube
  (`clipagent/render.cut_clip_with_layout` rubius +
  `workers.subtitle_clip` / `workers.apply_sfx` vía
  `burn_and_enqueue` + `final_review.validate_final_clip` sobre el
  render). Un veredicto «rechazado» no se guarda sin revisar los
  fotogramas. **Riesgo residual (declarado, igual que YouTube)**:
  el reel ya viene en split-screen (cámara arriba, juego abajo); la
  separación cámara/juego la hace `detect_vod_facecam` sobre el
  segmento. Nada en lo leído demuestra que el camino automático
  separe webcam/gameplay de un reel de Facebook: la puerta de cara
  y la de plantilla del validador deciden, y la aceptación lo
  verifica con el render real.
- **Puertas SIN CAMBIO**: viralidad ≥45, SEO ≥80 en las 4
  plataformas, juego GTA VI verificado, subtítulos del audio real
  (o excepción sin-voz vigente). Falla cerrado. El rescate por
  gritos NO aplica (total ≥47 por construcción, igual que YouTube).
- **Dispatch en `run()`**: `run()` gana los parámetros
  `facebook_url=None` y `facebook_clips_fn=None` (en paralelo a
  `youtube_url` / `youtube_clips_fn`), y su paso 0 atiende
  `facebook_url` ANTES del chequeo de directos:
  `if facebook_url: return _run_single_facebook(...)` (igual que
  el paso 0 de YouTube, `vod_fallback.py:1280-1287`). **La rama por
  VOD del barrido en `run()` (l. 1385-1390) NO se extiende a
  Facebook en la v1** (no hay barrido; `latest_vod_for` devuelve
  `None` para `platform=facebook`): el dispatch por URL que la v1
  escribía (`run_facebook_clips(...) if is_facebook_url(url) else
  ...`) era la rama del barrido y en la v1 es código muerto. `run()`
  acepta un `facebook_clips_fn` inyectable para tests.

### 3.1.1 Orquestación de URL única (`_run_single_facebook`)
- Función nueva **`_run_single_facebook(facebook_url, max_clips,
  top_n, state_path, out_root, dry_run, bridge, enqueue_fn,
  facebook_clips_fn, ignore_cooldown)`** en
  `clipagent/vod_fallback.py`, espejo paso a paso de
  `_run_single_youtube` (`vod_fallback.py:1170-1258`):
  (1) si no es `is_facebook_url(facebook_url)` → `(0, "facebook_url
  no es de Facebook")`; (2) `meta = fetch_facebook_metadata(
  facebook_url)` (con reintento en JSON vacío/inválido, §3.1) →
  sin metadatos `(0, "sin metadatos de la URL (...)")`;
  (3) `reel_id = str(meta.get("id") or "")` (sin parseo de la URL:
  el `/share/r/` no trae el id) → sin id `(0, "sin id de reel en
  la URL")`; (4) pre-chequeo GTA `facebook_clearly_not_gta6(meta)`
  → `(0, "no es GTA VI: ...")`; (5) **puente ANTES de la
  resolución del autor** (reorden frente a YouTube: el puente se
  crea aquí para poder reportar el fail-closed; si `bridge is None`
  y no es dry-run → `PanelBridge.from_env()`, y si sigue siendo
  None → `(0, "sin credenciales del panel")`); (6) `streamers =
  load_streamers()` y `lang, name =
  _resolve_single_facebook_influencer(meta, streamers)` → sin
  match: **`_report_event("clip_descartado", f"{name}: autor sin
  entrada facebook en el panel — reel {reel_id}", bridge=bridge,
  dry_run=dry_run)`** + log + `(0, "autor sin entrada facebook en
  el panel")` (sin puente — sin credenciales — solo log, porque
  `_report_event` sin bridge no llega al panel);
  (7) enfriamiento `vod_on_cooldown(state, reel_id)` salvo
  `--ignore-cooldown` → `(0, "reel ... en enfriamiento")`;
  (8) `panel_clips = bridge.list_clips()` (si falla, se sigue sin
  dedup contra el panel, con log); (9) dry-run →
  `mark_vod_attempted(state, reel_id)`, `save_state`, `(0,
  "dry-run URL única")`; (10) `out_dir = os.path.join(out_root,
  str(reel_id))`, `manifest = (facebook_clips_fn or
  run_facebook_clips)(facebook_url, out_dir, top_n=top_n, lang=lang,
  single_url=True)` → sin manifiesto, marcar + guardar + `(0, "el
  cortador de Facebook no dejó manifiesto")`;
  (11) `_process_manifest_clips(bridge, name, reel_id, out_dir,
  manifest, panel_clips, session_clips, lang, top_n, max_clips,
  enqueue_fn, dry_run, influencer_names)` — **`vod_id` es el id
  numérico del reel** (clave de estado, `source_id` y `out_dir`,
  §3.3); (12) `mark_vod_attempted(state, reel_id, starts)`,
  `save_state`, `(n, "URL única {reel_id}: {n} clip(s) en la Cola
  del panel (sin publicar)")`.
- `main()` gana el flag `--facebook-url` (mismo texto de ayuda que
  `--youtube-url`) y lo pasa a `run()` como `facebook_url`.

### 3.2 Puerta de duración: 40 → 15 s (cambio compartido, declarado)
- `clip_passes_gates` (`vod_fallback.py:76-79`): `MIN_DUR_S = 40` →
  **`MIN_DUR_S = 15`** (`MAX_DUR_S = 60` no cambia). Justificación:
  el usuario ordenó el 2026-11-21 quitar el mínimo de 40 s «para
  siempre» (solo queda el tope duro de 60 s; registrado en
  `config.yaml:73,728` como `min_duration_s: 1` y en MEMORY.md) y
  el rango 25–35 s también quedó eliminado; la banda 40–60 s de las
  puertas es anterior a esa orden y está obsoleta. El nuevo suelo
  (15 s) es el `min_duration_s` de la sección `viral_source` de
  `config.yaml:829` («un Short viral dura entre 15 s... y 60 s»),
  no solo el docstring de `viral_source.py`. **Reconciliación con
  la spec de YouTube v8 (2026-11-24, posterior a la orden)**: esa
  spec mantuvo 40–60 s **a nivel de cortador**
  (`CLIP_MIN_DUR_S = 40`, `youtube_cut.py:102,714`: un vídeo de
  YouTube de <40 s no da clips), no a nivel de puerta. Esta spec
  cambia la **puerta compartida** y NO toca la banda del cortador
  de YouTube. **Asimetría declarada**: un vídeo de YouTube de 18 s
  seguirá sin dar clips (banda del cortador intacta); un reel de
  Facebook o un clip de Twitch/VOD de 18 s sí pasará la puerta.
  Revisitar la banda del cortador de YouTube es un follow-up aparte,
  no un cambio silencioso. **El Worker NO cambia**: su duración es
  por ajustes (1–60 s en ambas D1 en vivo, §1), así que un clip de
  18,85 s pasa el POST sin tocar el Worker. Test propio (§4): un
  clip de 18 s pasa el componente de duración, uno de 10 s no, y
  uno de 45 s de Twitch sigue pasando; y un test del Worker afirma
  que POST /api/clips acepta 18,85 s con los ajustes por defecto.

### 3.3 Marcador `source_key = "fb:<reel_id>"` y resolución por plataforma
- `source_key` del clip de Facebook = `"fb:<reel_id>"` (para el caso
  de prueba: `"fb:1918676049105232"`, 19 caracteres; el límite de
  POST es ≤64). **`vod_id` es el id numérico del reel en todas
  partes**: clave del enfriamiento (`str(vod_id)`,
  `vod_fallback.py:483-498`), `out_dir` (`out_root/<reel_id>`) y
  **`source_id` = `int(vod_id)` = el id del reel** (cabe en 64
  bits: 1,9e15 < 2^63; solo los ids de YouTube no numéricos caen a
  0 — la v1 decía «`source_id` queda en 0» y era falso para
  Facebook). `source_key` es **solo** el marcador de dedup
  (`already_enqueued` compara `(source_key, start_sec)` cuando no
  está vacío, `vod_fallback.py:272`) y la clave de resolución de
  POST.
- **Dedup**: `already_enqueued` compara (source_key, start_sec)
  cuando `source_key` no está vacío — funciona sin cambios con el
  prefijo `fb:`. Enfriamiento de 6 h en `state.json` con clave
  `str(vod_id)` = id del reel (mismo archivo que YouTube).
  **Cruce mismo-momento con Twitch** (`cross_blocks`,
  `vod_fallback.py:425-460`): solo bloquea clips del **mismo día
  Chile** y su condición de «misma plataforma» es vacua en la
  práctica (§1). **La v1 usaba el clip de Noni de la playa del
  23-nov como control y ese control es vacuo**: otro día, así que
  no puede bloquear la avioneta sea cual sea el título. El control
  del cruce es un **test unitario** (§4: mismo día, mismo streamer,
  solape ≥0,7 bloquea; otro día no bloquea), no la aceptación. Las
  protecciones operativas contra el mismo momento en la v1 son el
  dedup `(source_key, start_sec)` y el enfriamiento de 6 h; la
  aceptación verifica que re-despachar la misma URL se bloquea.
- **Resolución del handle en POST /api/clips** (`index.ts:2857`):
  `wantPlatform = sourceKey.startsWith("fb:") ? "facebook" :
  sourceKey ? "youtube" : "twitch"`. Un cambio de una línea + test
  (dos Nonis: con `fb:` → id de la entrada facebook; sin prefijo →
  youtube; vacío → twitch). **Estricto para `fb:`**: si
  `wantPlatform` es `"facebook"` y ninguna fila facebook casa con
  el handle, **400 sin fallback** — se elimina el fallback a
  cualquier plataforma por nombre (`index.ts:2857-2859`) para este
  caso. La entrada de Twitch del mismo nombre NO autoriza un clip
  de Facebook (es lo que §3.1 exige y el fallback actual
  derrotaría). El fallback se mantiene para YouTube/Twitch como
  hoy (compat declarada en la spec de YouTube).
- **`publish.py:1940` (`origin_platform`)**: misma regla en tres
  vías (`fb:` → `"facebook"`; no vacío → `"youtube"`; vacío →
  `"twitch"`). **Salvedad**: en producción publica el Worker desde
  D1 y el bucle de `publish.py` es legacy local (solo
  `cerebro-kaez.yml`, como establece la spec de YouTube §3.5); el
  cambio es de coherencia del camino local, no del camino de
  producción.
- **`gate.ts`**: SIN CAMBIO (el chequeo lee `clip.influencer_id` →
  fila; con la resolución de arriba el id ya es el de la entrada
  facebook). Se declara para que no se busque ahí un cambio.
- **`streamers.py:30`**: añadir `"facebook"` a `PLATFORMS`
  (`("twitch", "kick", "youtube", "facebook")`): `add_streamer`
  rechaza otras plataformas y la tupla es la única fuente de
  nombres de plataforma (el camino del panel `_from_panel` no
  valida, pero el cambio deja el alta de la entrada facebook
  válida por todos los caminos).
- **Umbral 53 al encolar** (`vod_fallback.py:837-840`, comentario
  en 831): el chequeo `if source_key:` ya cubre las claves `fb:`
  (el umbral aplica a Facebook con el mismo fundamento que a
  YouTube: clips sin chat que puntúan 47–55 por construcción y a
  los que las puertas 45/80 no discriminan). **Renombre mecánico**:
  `YOUTUBE_PRIORITY_MIN` → `URL_CUT_PRIORITY_MIN` (el nombre
  miente si queda; son dos referencias + tests). El valor sigue
  siendo 53 (misma tabla que YouTube §3.5: bueno 55,7 → 56 se
  encola; flojo 50,4 → 50 no se encola). El chequeo va AL INICIO
  de `burn_and_enqueue`, ANTES de `subtitle_clip`, y el descarte
  se reporta como evento `clip_descartado` por la rama `else` de
  `_process_manifest_clips` (no dentro de `burn_and_enqueue` —
  comentario en `vod_fallback.py:834`).

### 3.4 Entrada URL única (job `facebook-single`, en el dispatch `panel-live`)
- **Definición**: el job `facebook-single` corre en el evento
  `panel-live` (repository_dispatch), con `needs: [detect]` y
  `if: github.event.client_payload.facebook_url` (**chequeo de
  veracidad, no `!= ''`**: `null != ''` es `true` en expresiones
  de GitHub — el mismo bug que la spec de YouTube corrigió),
  `timeout-minutes: 60`. Copia el **bloque completo de setup** de
  `youtube-single` (checkout, setup-python, caché de pip, ffmpeg,
  visión, credenciales del panel) + los pasos de artefacto
  (sesión `panel-session-<tenant>-<run_id>`, estado
  `runs/vod-fallback/state.json` restaurado/subido, paso «Guardar
  manifiestos»). Comando (espejo exacto del de YouTube, incluido
  `--ignore-cooldown`):
  `python -m clipagent vod-fallback --facebook-url "<url>"
  --max-clips 2 --top-n 2 \
  ${{ inputs.force == 'true' && '--ignore-cooldown' || '' }}`.
  **Y `cleanup-session` gana `facebook-single` en su `needs`**
  (`[cut, vod-fallback, youtube-single, facebook-single]`):
  `facebook-single` descarga el artefacto de sesión al empezar y
  `cleanup-session` lo borraría antes si no espera a este job.
  El `env: PANEL_URL` del paso de corte se espeja tal cual (avc);
  la URL del puente la fija el ternario global `PANEL_BASE_URL`
  (cerebro-live.yml:104-105, §1), que ya cubre kaez.
- **Exclusión mutua (simétrica, no solo en `vod-fallback`)**:
  el `if` de `vod-fallback` pasa a
  `needs.detect.outputs.count == '0' &&
  !github.event.client_payload.youtube_url &&
  !github.event.client_payload.facebook_url`; **además** el `if`
  de `youtube-single` (l. 422) gana `&&
  !github.event.client_payload.facebook_url` y el de
  `facebook-single` lleva `github.event.client_payload.facebook_url
  && !github.event.client_payload.youtube_url`. Si un dispatch
  llevara ambas URLs, sin esto ambos jobs correrían en paralelo y
  competirían por el mismo artefacto de estado
  (`cerebro-live-vod-state-<tenant>`). La defensa en profundidad
  es que cada endpoint acepta una sola URL; los `if` simétricos
  son la segunda capa.
- **Camino completo del dispatch** (igual que YouTube §3.6):
  (1) endpoint NUEVO `POST /api/worker/run-facebook` (con sesión,
  como `/api/worker/run-youtube`) que recibe `{facebook_url}` y
  llama a `dispatchCerebro(env, "panel-live", tenant,
  undefined, facebookUrl)`; **valida la URL** con `isFacebookUrl`
  (400 si no es Facebook) **y devuelve `ok: false` con el motivo
  cuando `dispatchCerebro` devuelve `false`** (502, como YouTube);
  `isFacebookUrl` es el **mismo predicado** que
  `is_facebook_url` del cerebro (§3.1: `fb.watch` con cualquier
  ruta; `facebook.com` con `/reel/`, `/share/r/` o `/watch/?v=`),
  con tests para ambos; (2) `dispatchCerebro` gana el parámetro
  opcional `facebook_url` (se incluye en el `client_payload` cuando
  viene) y la exención explícita se generaliza a **una sola
  variable**: `explicitUrl = eventType === "panel-live" &&
  (!!youtubeUrl || !!facebookUrl)` — salta el throttle de 15 min,
  el freno de presupuesto Y el freno de reintentos tras dispatches
  fallidos (petición explícita, misma regla que `panel-start`); sin
  URL no cambia nada; (3) el campo opcional «URL de Facebook» del
  control de ejecución existente del panel llama a ese endpoint (un
  campo en el control que ya existe, NO una pestaña ni una sección
  nueva). Muse solo pasa la URL que el usuario comparte; el
  programa hace el resto.
- `run()` / `main()` / CLI: ver §3.1.1 (parámetro `facebook_url`,
  flag `--facebook-url`, paso 0 antes del chequeo de directos; una
  URL despachada corta aunque alguien esté en vivo).
- Re-despacho: una URL re-despachada respeta el enfriamiento de
  6 h salvo `--force` (mismo `state.json`; el flag llega al comando
  del job como `--ignore-cooldown`, §3.4 arriba).
- El canal y el idioma se resuelven del propio reel (§3.1).

### 3.5 Barrido: fuera de la v1 (declarado, no escondido)
- No hay job de barrido de Facebook, ni campo `facebook_url` en la
  tabla de influencers, ni listado de reels de una página. Motivo:
  no está verificado que `yt-dlp --flat-playlist` sobre una página
  de Facebook funcione desde Actions ni que haya baseline para el
  múltiplo outlier; inventarlo sería prometer un barrido sin
  evidencia. La v2 (barrido) necesita su propia verificación
  (listado desde Actions + baseline de vistas) y su propia spec.
  La v1 cubre el caso pedido: el usuario comparte un reel y el
  programa lo corta.

## 4. Verificación (antes de darlo por hecho)
- Tests nuevos (espejo de la spec de YouTube §4): cortador Facebook
  con yt-dlp mockeado (contrato de manifiesto completo:
  `viral.best_title` no vacío, `viral.viral_multiple`,
  `viral_breakdown` con `audio_multiple`, `hype_score`,
  `score_kind`, `peak`, `source_key` `"fb:..."`, `vod_id`,
  `vod_layout`, `n`, `title_es`/`title_en`, segmentos en
  `segments/` con el nombrado; `clip_passes_gates` +
  `burn_and_enqueue` con bridge mockeado y título no vacío en el
  payload del POST; **el manifiesto existe en disco**;
  `viral.seo_scores` del POST son números planos por plataforma);
  `is_facebook_url` (facebook.com/reel, /share/r/, fb.watch con
  cualquier ruta, m.facebook.com sí; youtube.com, twitch.tv no);
  resolución del autor (match por `uploader_id` en `profile_url`;
  normalización «Clips de Noni» → «noni»; sin match → fail closed
  con motivo **y con el evento `clip_descartado` reportado**
  — puente mockeado que registra el evento); bandas de duración
  (14 s → ningún clip; 18,85 s → el reel entero es el candidato;
  70 s en URL única → audio completo, no muestreo);
  **puerta de pico con buckets de 1 s** (el test que caza el
  bloqueador 1: 2 buckets de 10 s con valores 1,0 y 0,6 → múltiplo
  1,25 < 2,0, no pasa; los mismos segundos con buckets de 1 s y
  pico 2,5× la mediana → pasa); **puerta de duración 15–60**
  (18 s pasa, 10 s no, 45 s de Twitch sigue pasando); selector de
  formato (**el argv exacto con el selector combinado literal**
  `FACEBOOK_FORMAT`, con runner mockeado; `hd` preferido; fallback
  AV1; el test afirma códec/resolución del archivo descargado con
  ffprobe simulado); `fetch_facebook_metadata` reintenta con
  stdout vacío y con JSON inválido (3 intentos; el caso observado
  en §1); dedup ENTRE corridas por (source_key, inicio) con lista
  fresca simulada (re-despachar la misma URL → bloqueado);
  **cruce con Twitch** (mismo día, mismo streamer, solape ≥0,7
  bloquea; otro día no bloquea; <3 tokens no bloquea;
  día = `santiago_day_key(created_at)`); **`source_id` y clave de
  estado** (`source_id` == id numérico del reel; el enfriamiento se
  clavea por `str(vod_id)`, no por `source_key`);
  **pre-chequeo GTA** (descripción con `#gta6` → verificado;
  hashtags sin GTA → se salta; sin hashtags y transcripción sin
  mención → fail closed «juego no verificado»; `moment_card.game`
  solo existe cuando la verificación pasa); **umbral al encolar**:
  `burn_and_enqueue` con bridge mockeado, clip de Facebook con
  prioridad 50 (flojo) → NO se encola y el motivo queda en el log
  **y como evento `clip_descartado`**; prioridad 56 (bueno) → se
  encola; clip de Twitch con prioridad 50 → se encola (el umbral es
  solo para `source_key` no vacío); **renombre
  `URL_CUT_PRIORITY_MIN`** (las referencias viejas no existen);
  **orquestación** (`run(facebook_url=...)` + CLI `--facebook-url`
  + `_run_single_facebook`: enfriamiento, pre-chequeo GTA,
  dry-run, autor sin entrada → `(0, motivo)` + evento);
  **Worker**: POST /api/clips con dos Nonis — con `source_key`
  `"fb:..."` → id de la entrada facebook, con id de YouTube → id
  youtube, sin `source_key` → id twitch; **`fb:` sin entrada
  facebook → 400 sin caer a Twitch**; **POST /api/clips acepta un
  clip de 18,85 s con los ajustes por defecto**
  (`min_duration_s=1`, §1); `publish.py:1940` resuelve
  `origin_platform` en tres vías; endpoint `POST
  /api/worker/run-facebook` con URL no-Facebook → 400 y con dispatch
  fallido → `ok: false` (incluido tras un `panel-live` fallido
  reciente: el freno de reintentos no lo frena); `GET
  /api/influencers/live` con una fila `platform=facebook` presente;
  **workflow**: con `facebook_url` en el `client_payload`, el `if`
  de `vod-fallback` es falso y el de `youtube-single` también
  (exclusión simétrica); `cleanup-session` incluye
  `facebook-single` en su `needs`; enfriamiento 6 h con
  `state.json` restaurado y subido. (Matiz: «solo corre
  `facebook-single`» no es literal — el job `cut` puede correr en
  paralelo si hay directos en vivo, igual que con
  `youtube-single`; el test afirma los `if`, no la ausencia de
  `cut`.)
- **Fail-before/pass-after**: cada test nuevo de comportamiento
  (duración 18 s, resolución `fb:`, `is_facebook_url`, umbral
  Facebook, endpoint, pico con 2 buckets, `source_id`, `fb:` sin
  entrada → 400, Worker acepta 18 s) falla contra el código viejo
  y pasa con el fix (verificado con `git stash`).
- Suite del cerebro en verde; tests del Worker en verde (resolución
  por plataforma + endpoint `run-facebook` + dispatch con
  `facebook_url` y exención explícita + chequeo de influencer en
  `gate.ts` ya existente). CI en verde.
- **Despliegue del Worker** (la v1 no tenía este paso): desplegar
  con `wr_v2.py seba` y `wr_v2.py kaez` (kaez siempre con
  `wrangler.kaez.toml`, nunca nocron), verificar `/api/health`
  tras cada deploy (tenant correcto, `worker_running` true) y que
  los crons siguen intactos (GET de schedules). La aceptación exige
  `run-facebook` desplegado en el Worker de sebastian (una llamada
  con URL no-Facebook → 400 prueba que el código nuevo está vivo).
- En vivo (aceptación, criterio DURO): dispatch `panel-live` con
  `facebook_url=https://www.facebook.com/share/r/18cMsFx95G/`
  (tenant sebastian, **el enlace tal como lo pegó el usuario**) y
  verificar en el panel: **≥ 1 clip en la Cola con viralidad ≥45,
  SEO ≥80 en las 4 plataformas, 15–60 s, subtítulos del audio
  real y veredicto del validador**, con **`viral_score` visible
  en GET /api/clips** y `source_key = "fb:1918676049105232"`.
  Verificar que la entrada `platform=facebook` de Noni existe en
  el panel (handle `noni`, con permission=1 y active=1) y que el
  log de la corrida muestra el formato descargado (ffprobe: códec,
  resolución, duración) **y el múltiplo de audio medido con los
  RMS por segundo** — es la prueba de que la descarga funcionó
  **desde Actions**, no solo desde esta VM, y de que la puerta de
  pico es alcanzable para este reel. Verificar que re-despachar la
  misma URL se bloquea por dedup/enfriamiento. (El clip de la playa
  del 23-nov NO es el control del cruce: otro día, no puede
  bloquear — §3.3.)
  **Y la promesa del chequeo de streamer**: desactivar la entrada
  facebook de Noni → el job de un clip Facebook suyo se bloquea
  con el motivo «streamer desactivado en el panel» (a draft, sin
  reintento); desactivar la entrada Twitch → bloquea los clips de
  Twitch, no los de Facebook; al reactivar, el clip se re-encola
  a mano desde el panel.
- Un resultado «sin clip» con motivo documentado (sin momento, sin
  webcam, sin pico de audio, SEO <80) es un RESULTADO REPORTADO,
  **no aceptación**: no cuenta como pasada y el trabajo vuelve a
  calibración, no se cierra.
- **La descarga desde Actions es el riesgo que la aceptación
  verifica**: si la corrida de aceptación falla en la descarga
  (Facebook bloquea la IP del runner o exige login), la v1 NO se
  da por hecha. El fallo queda con el stderr exacto de yt-dlp en
  el log y el motivo en el panel; **no hay plan B con cookies ni
  login en la v1** (eso exigiría la sesión del usuario en Actions,
  contra la regla de no-configuraciones, y es frágil por diseño).

## 5. Riesgos y no-gos
- **Descarga desde GitHub Actions (riesgo principal, primero)**:
  verificado desde esta VM (2 descargas + 2 `-J` OK, 1 fallo
  transitorio del `-J` sobre el enlace `/share/r/`, reintentado
  OK). NO verificado desde Actions: los runners son también IPs de
  datacenter (Azure) y Facebook bloquea IPs de datacenter y exige
  login/cookies en muchos casos. La aceptación (§4) ES la prueba:
  si falla ahí, la v1 no se cierra. **Y el selector combinado
  literal (`FACEBOOK_FORMAT`) no se ejecutó nunca**: sus dos
  componentes se probaron por separado; la aceptación lo ejecuta y
  registra el formato resuelto. Sin login, sin cookies, sin
  descargas masivas (máx. 2 clips por corrida, enfriamiento 6 h).
  Mismo riesgo que el barrido de YouTube, pero con menos evidencia:
  YouTube tenía el run de Speed (3 intentos, el 3º funcionó);
  Facebook tiene 2 descargas desde esta VM y cero desde Actions.
- No inventar viralidad ni subtítulos: todo medido del audio real;
  sin chat no hay atajo. Sin prior de alcance en la fórmula
  (vistas/likes NO entran: `view_count` 15816 se registra en el
  manifiesto como contexto; `like_count` no existe en el -J).
- El título no viene del -J (texto de engagement): si el SEO
  suggest-only no llega a ≥80 en la aceptación, no se baja la
  puerta — se reporta y se calibra.
- R1 intacto: ni fórmulas, ni pesos, ni umbrales de puntuación
  cambian. Lo único nuevo es el adaptador de entrada (el mismo de
  YouTube) y la puerta de pico de audio, ambos ya documentados en
  la spec de YouTube.
- No tocar el camino Twitch/Kick ni el de YouTube (rama nueva, sin
  regresiones). **Cambios compartidos de este trabajo (todos
  declarados, no solo §3.2)**: (a) puerta de duración 40→15 s
  (§3.2, cerebro); (b) renombre `YOUTUBE_PRIORITY_MIN` →
  `URL_CUT_PRIORITY_MIN` (§3.3); (c) `dispatchCerebro` + endpoint
  `run-facebook` (§3.4, Worker); (d) resolución estricta por
  plataforma en POST /api/clips, incluido el 400 sin fallback para
  `fb:` (§3.3, Worker); (e) `origin_platform` en tres vías en
  `publish.py` (§3.3, camino local); (f) `"facebook"` en
  `PLATFORMS` de `streamers.py` (§3.3); (g) `if` de
  `youtube-single` con exclusión de `facebook_url` y
  `cleanup-session` con `facebook-single` en su `needs` (§3.4,
  workflow). `gate.ts` NO cambia.
- No escribir en D1 a mano ni encolar por fuera del puente.
- No crear sección ni pestaña nueva en el panel. No tocar crons.
  Cambios de Worker en este trabajo (listados, no escondidos):
  (1) endpoint NUEVO `POST /api/worker/run-facebook` (con sesión)
  que recibe `{facebook_url}`, valida que sea Facebook (400 si no)
  y llama a `dispatchCerebro(env, "panel-live", tenant, undefined,
  facebookUrl)` (devuelve `ok: false` con el motivo si el dispatch
  no se emite); (2) `dispatchCerebro` gana el parámetro opcional
  `facebook_url` (en el `client_payload`) y la exención explícita
  se generaliza a `explicitUrl` (youtube_url O facebook_url);
  (3) **resolución por plataforma en POST /api/clips** (`fb:` →
  facebook, no vacío → youtube, vacío → twitch) **y 400 sin
  fallback para `fb:` sin entrada facebook**; (4) campo
  opcional «URL de Facebook» en el control de ejecución existente
  del panel (dist/app.js, igual que el de YouTube). **Despliegue**:
  `wr_v2.py seba` y `wr_v2.py kaez` con verificación de
  `/api/health` y crons tras cada deploy (§4). **Sin
  migración** (`source_key` ya existe, migración 0044).
- No publicar nada desde este trabajo: los clips van a la Cola y el
  panel decide.

## 6. Qué cambia para el usuario
- Una acción normal del panel (crear la entrada de Noni con
  `handle=noni`, `platform=facebook`, como la segunda de Rubius con
  `platform=youtube`) y pegar la URL de un reel en el campo del
  control de ejecución cuando quiera cortar uno concreto: los reels
  de Facebook de sus influencers se cortan solos como los videos
  de YouTube, y un reel concreto que comparta se corta con solo
  pasar su URL (el programa lo hace todo: bajar, buscar el
  momento, cortar, validar, encolar).
- El reel de Noni de la avioneta es el caso de aceptación: si la
  spec se implementa, ese reel produce clips en la Cola sin que
  nadie lo toque a mano.
- Decisiones que el usuario aprueba con esta spec: (a) **v1 = URL
  única solamente** (sin barrido de páginas de Facebook); (b) la
  puerta de duración pasa de 40–60 s a **15–60 s** (cambio
  compartido, conforme a su orden de quitar el mínimo de 40 s);
  (c) el umbral de prioridad **53 aplica también a los clips de
  Facebook** (misma razón que YouTube: sin chat); (d) un clip cuyo
  autor no tiene entrada `platform=facebook` en el panel **no se
  encola** (fail closed); (e) si Facebook bloquea Actions, la v1
  **no se da por hecha** y no hay cookies ni login en la v1.

## 7. Historial de revisión (EGM)
- v1 (2026-11-25): borrador del elefante.
- v2 (2026-11-25): revisión pez dorado (30 hallazgos; 7 bloqueadores,
  14 importantes, 9 menores) — todos resueltos en la spec:
  (1) puerta de pico imposible con buckets de 10 s para el reel de
  18,85 s → buckets de 1 s en la banda 15–60 s (§3.1) con test que
  caza el caso; (2) `_run_single_facebook` sin especificar y dispatch
  por VOD como código muerto → §3.1.1 con los 12 pasos + `run()` /
  `main()` / `--facebook-url`; (3) `cleanup-session` sin
  `facebook-single` → añadido a `needs` (§3.4); (4) puerta de
  duración del Worker → verificada: es por ajustes, 1–60 s en ambas
  D1 en vivo, sin cambio (§1, §3.2); (5) `source_id`/`source_key`/
  enfriamiento → `vod_id` = id del reel en todas partes,
  `source_id` = id del reel, enfriamiento por `str(vod_id)` (§3.3);
  (6) `is_facebook_url` se contradecía con `fb.watch` → host manda
  (§3.1) e idéntico en el Worker; (7) «juego GTA VI verificado» sin
  mecanismo → `facebook_clearly_not_gta6` por hashtags de la
  descripción + transcripción, fail closed, `game` solo si verificado
  (§3.1); (8) fallback de POST derrotaba el fail-closed → 400
  estricto para `fb:` (§3.3); (9) exclusión mutua no simétrica →
  `if` simétricos en los dos jobs single (§3.4); (10) título
  suggest-only frente a 63–70 → `build_facebook_package` +
  `metadata.score_package_seo` + reconciliación 84–85 (§3.1);
  (11) justificación §3.2 frente a YouTube v8 → fuente
  `config.yaml:829` + asimetría declarada cortador/puerta (§3.2);
  (12) selector combinado nunca ejecutado + sin descargador de
  segmentos → `FACEBOOK_FORMAT` literal + `download_facebook_segment`
  (§3.1, §4); (13) plan de audio por bandas sin definir → banda
  corta extrae del vídeo, banda >60 s audio completo en URL única
  (§3.1); (14) reintento del -J no cubría salida vacía →
  predicado de reintento «código 0 Y JSON con `id`» (§3.1);
  (15) handle de la entrada → `noni` (§1, §6); (16) sin despliegue
  del Worker → pasos `wr_v2.py` + verificación (§4, §5);
  (17) control del cruce vacuo → test unitario, no la playa (§3.3);
  (18) evento `clip_descartado` sin cableado → puente antes de la
  resolución del autor + `_report_event` (§3.1.1); (19) tests
  incompletos → lista ampliada (§4); (20) comando sin `--force` y
  `PANEL_URL` → `--ignore-cooldown` espejado + `PANEL_BASE_URL`
  ternario (§3.4); (21) contradicción en §5 → cambios compartidos
  enumerados (a–g); (22–30) menores: líneas corregidas
  (`dispatchCerebro` 482–597, umbral 837–840, `youtube-single`
  419–552, `wantPlatform` 2857), lista del commit completada,
  contrato de manifiesto ampliado (`viral_breakdown`, `hype_score`,
  `score_kind`, `peak`), firma completa de `run_facebook_clips`,
  salvedad legacy de `publish.py`, `out_dir` sin prefijo `fb_`,
  matiz «solo corre `facebook-single`», fuentes «fuera del repo»
  marcadas, `"facebook"` en `PLATFORMS`.
