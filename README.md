# Viral Clip Agent — Twitch + Kick

Herramienta en Python para detectar automáticamente los mejores momentos de un directo de **Twitch o de Kick** a partir del chat (`chat.json`) y recortarlos en vertical `1080x1920`, listos para TikTok, Reels o Shorts.

**Automático:** `auto_clips.py` hace todo en un solo comando — descarga chat, detecta, corta y empaqueta cada clip para viral. Ver [Automatización](#automatización-un-solo-comando). En Twitch baja el VOD; en Kick baja solo el segmento de 60 s de cada pico, nunca el VOD entero.

## Qué cambió en la v2.4 (auditoría Claude Code + Shortia)

**Scoring corregido** (auditoría, bugs 1-6, ver `AUDIT-claude-code.md`):
- `xd` / `xdd` por fin puntúan como risa.
- Falsos positivos eliminados: "no puedo creer que no hayas ido", "qué bueno que llegaste", "dale gas", "vamos a ver qué pasa", "god of war" y "crazy cat" ya no puntúan (tokens sueltos ambiguos fuera; solo frases completas y formas alargadas: "vamooos", "dalee", "noooo").
- Rendimiento: un tokenizado por mensaje + conjuntos en vez de regex por token. Medido en 200k mensajes: score_message 1,28 s, score_windows 1,81 s (antes 9,6 s).
- Ventana deslizante de 10 s con paso de 5 s y suavizado (0.25/0.5/0.25): un pico entre dos ventanas ya no se parte.
- Mediana sin sesgo: se calcula sobre TODAS las ventanas, incluidas las vacías; `viral_package.viral_multiple` por fin usa el parámetro `median_score` que antes ignoraba.
- Usuarios únicos por ventana (`users` en el detalle), mensajes repetidos del mismo usuario solo cuentan una vez, bots conocidos (Nightbot, StreamElements...) no puntúan.
- Manifiesto incremental: `clips_chat_v2.py` escribe `manifest.json` tras cada clip cortado.
- Flags ffmpeg para plataformas en el corte: `-pix_fmt yuv420p`, `-movflags +faststart`, audio normalizado con `loudnorm` (-16 LUFS).

**Config del canal — `config.yaml`** (idea de Shortia): juego, streamer, nicho, hashtags ES/EN, parámetros de corte y estilos de subtítulo viven en `config.yaml`, no hardcodeados. `config.py` lo carga (PyYAML si está, si no un parser mínimo propio). Los hashtags ya no están fijos en el código.

**Estilos de subtítulo elegibles** (idea de Shortia: plantillas): `--subtitle-style` en `subtitles.py`, con 3 estilos definidos en `config.yaml`:
- `tiktok` (por defecto): blanco, negrita, borde negro, abajo.
- `highlight`: amarillo con caja semitransparente detrás.
- `karaoke`: palabra por palabra (bloques de 1-2 palabras, amarillo, fuente 56). Aproximación: no es karaoke `\kf` real de ASS, el SRT no lleva tiempos por palabra al quemar; se logra con bloques cortos (max_chars=8, max_duration=1.0 s). Para que el karaoke funcione, el SRT debe generarse con ese estilo (los SRT viejos llevan bloques de 20 caracteres).

```bash
.venv/bin/python subtitles.py clip.mp4 --subtitle-style highlight
.venv/bin/python subtitles.py clip.mp4 --subtitle-style karaoke
```

**Shortia** ([backsoul/shortia](https://github.com/backsoul/shortia), Apache 2.0, Go + SvelteKit, YouTube + Whisper API + DeepSeek, 1 estrella, 3 commits — no clonado, solo ideas):
- Tomado: plantillas de subtítulo elegibles con estilos, config del canal (juego/streamer/nicho/hashtags), y la idea del editor visual con aprobación (eso es la cola de aprobación, fase siguiente, aún no implementada).
- NO tomado: su stack (Go, Redis, Docker, SvelteKit), sus APIs pagas (OpenAI Whisper API, DeepSeek), YouTube-only (sin Twitch/Kick ni detección por chat), ni su renderizado en cliente con FFmpeg.wasm. Es un proyecto de 3 commits y 1 estrella, menos probado que openshorts.

## Qué cambió en la v2

**1. Español, por fin.** La v1 solo entendía risas en inglés (`lol, lmao, kekw, lul, xd`). La v2 suma:
- Risas ES: `jaja, jeje, jiji, jojo, jajaja, jejeje`, `qué risa, me muero, me parto, no puedo más`, y variantes alargadas (`jaaaajaja, jejejeje`).
- Risas EN: `lol, lmao, rofl, haha, hehe, kekw, lul` + emojis 😂🤣😹💀😆.
- Hype ES/EN (nuevo, la v1 no lo tenía): `qué jugada, brutal, increíble, insano, tremendo, épico, vamos, dale, qué grande, golazo` / `pog, clutch, insane, god, lets go, gg, clip it, wow`.

Puntaje por mensaje: 1 punto base, +1 si es risa, +1 si es hype. Un `JAJAJA QUÉ JUGADA` vale 3.

**2. Sin falsos positivos.** `xd`, `lol`, `gg`, `w` solo cuentan como palabra completa. Antes `extra` contaba como `xd` y `lollipop` como `lol`.

**3. Todo configurable.** Nº de clips, ventana, separación, pre-roll, duración, idioma, recorte y calidad ya no están fijos en el código.

**4. Más robusto.** Valida que existan el chat, el video y FFmpeg, no se cae si un clip falla, ajusta el último clip si el VOD es más corto, y `--dry-run` lista los picos sin cortar nada.

**5. Recorte a elegir.** `--crop center` (defecto), `left` o `right`, por si el centrado te corta el minimapa o el HUD de GTA 6.

## Requisitos

- Python 3.10+
- [TwitchDownloaderCLI](https://github.com/lay295/TwitchDownloader) en el PATH (solo para Twitch)
- [FFmpeg](https://ffmpeg.org/) en el PATH (ffprobe opcional, para medir el VOD)
- Kick no necesita nada extra: usa los APIs de Kick directamente (ver abajo)

Sin dependencias de Python, sin credenciales, sin red — salvo para los
subtítulos (ver abajo), que usan faster-whisper en un venv.

### Subtítulos: venv obligatorio

El Python del sistema NO sirve para faster-whisper: instalarlo ahí
falla por PEP 668 / `typing_extensions` de Debian. Usa el venv del
proyecto, que ya tiene faster-whisper 1.2.1 instalado:

```bash
python3 -m venv .venv
.venv/bin/pip install faster-whisper
```

## Subtítulos quemados — v2.2 (de openshorts)

`subtitles.py` adapta el `subtitles.py` de
[openshorts](https://github.com/faris-sait/openshorts) (MIT): transcribe
el clip con faster-whisper (modelo `tiny`, idioma automático ES/EN),
agrupa las palabras en líneas cortas estilo TikTok (máx. 20 caracteres,
máx. 2 s por bloque) y las quema en el vídeo vertical con ffmpeg
(blanco, negrita, borde negro, abajo, fuente 48, PlayRes 1080x1920).

```bash
# Transcribe y quema en un paso
.venv/bin/python subtitles.py runs/2929669422/clips/clip_7h12m_vertical.mp4
# Solo el SRT, sin quemar (revisar antes)
.venv/bin/python subtitles.py clip.mp4 --srt clip.srt --no-burn
# Forzar idioma o modelo
.venv/bin/python subtitles.py clip.mp4 --lang es --model tiny
```

El SRT queda en `<clip>.srt` y el clip con subtítulos en
`<clip>_subs.mp4`. Sin Gemini ni APIs de pago: todo local.

Qué se copió de openshorts y qué no:
- Copiado: transcripción con faster-whisper y `word_timestamps`,
  agrupación de palabras en SRT, quemado con el filtro `subtitles`
  de ffmpeg y estilo ASS.
- Cambiado: modelo `tiny` en vez de `base` (más rápido en CPU),
  idioma automático, sin cv2 (la duración se mide con ffprobe),
  estilo adaptado a 1080x1920.
- NO copiado: detección de momentos con Gemini (el agente ya detecta
  por el chat de Twitch), recorte con seguimiento de cara
  (MediaPipe + YOLOv8, pesado y pensado para vídeos con una persona
  hablando a cámara; en gameplay de GTA no hay cara que seguir y lo
  que importa es el HUD/minimapa, por eso el proyecto mantiene
  `--crop center/left/right`), publicación automática (Upload-Post),
  actores UGC ni doblaje con ElevenLabs (de pago).
- Detalle técnico: faster-whisper 1.2.1 falla con PyAV 19
  (`metadata_errors` ya no existe en `av.open`), y `av` 14 no tiene
  wheel para Python 3.12. Por eso `subtitles.py` extrae el audio a
  WAV 16 kHz con ffmpeg, lo lee con el módulo `wave` de la stdlib y
  lo pasa a faster-whisper como array numpy, evitando PyAV.

Limitación conocida: el modelo `tiny` se equivoca en nombres propios
y en frases rápidas. En la prueba real transcribió "Bezo" varias
veces (nombre/emote que no reconoce) y algunas frases quedaron con
gramática rota ("And I was post on discord"). Revisa el SRT antes de
publicar; el tribunal pidió un diccionario GTA 6 para esto, que aún
no está implementado.

## Política de traducción de subtítulos — v2.5

Regla del usuario (2026-11-16), en `config.yaml` (`subtitle_policy`):
- **Audio en español -> subtítulos SOLO en español, sin traducir**
  (`task="transcribe"`, `language="es"`). El español nunca se traduce,
  ni con `--subtitle-lang en` ni con `--translate`.
- **Audio en cualquier otro idioma -> subtítulos traducidos al inglés**
  (`task="translate"`, lo único que Whisper traduce).

`subtitles.py` detecta el idioma con `model.detect_language()` y decide
con `config.resolve_subtitle_task()`. Flags:
`--subtitle-lang auto|es|en` (default: `auto`, aplica la política;
`es` fuerza español, `en` fuerza inglés) y `--translate` / `--no-translate`
(fuerza o bloquea la traducción; por defecto manda la config).

```bash
# Rumano -> inglés (auto, por la política)
.venv/bin/python subtitles.py clip.mp4
# Español -> solo español, aunque se pida inglés (la regla dura gana)
.venv/bin/python subtitles.py clip.mp4 --subtitle-lang en
# Nunca traducir, transcribe en el idioma original
.venv/bin/python subtitles.py clip.mp4 --no-translate
```

Pruebas reales (modelo `tiny`):
- **Rumano -> inglés (Kick, Locones):** el clip del mejor pico
  (03h08m) no tenía voz que transcribir (solo audio del juego), así
  que se probó con el segmento del pico de 14m05s del mismo VOD:
  detectado `ro`, `task=translate`, SRT en inglés — "I don't want to
  see / you again, I don't / want / to see you again" (32,2–39,5 s) —
  quemado en `runs/kick-locones-01a1e069/clips/clip_14m05s_subs-en.mp4`
  (1080x1920, 60 s, verificado por fotograma). Solo hay ~7 s de voz
  en esos 60 s; el resto es audio del juego.
- **Español no se traduce (Kick, Coker):** detectado `es` (prob. 0,62),
  `task=transcribe`, subtítulos en español ("¿Me / te... / No,").
- **Inglés (Twitch, 2929669422):** detectado `en`, `task=transcribe`,
  subtítulos en inglés (ya estaba en inglés, no hay nada que traducir).
- Prueba unitaria de la decisión: `python3 test_subtitle_policy.py`
  (11/11 casos: es/ro/en/fr/de/pt, con y sin flags).

Limitaciones de la traducción con `tiny`:
- La detección de idioma en audio ruidoso de juego es poco fiable:
  en el segmento de 14m05s el auto-detect dijo `en` (prob. 0,13) y
  transcribió basura en inglés ("Oh my, you're no more of a patroth,
  why?"); forzando `--lang ro` la traducción salió bien. Si el idioma
  detectado sale con probabilidad baja (< 0,5), pasa `--lang` a mano.
- `task="translate"` a veces da palabras con `end == start`; el SRT
  les da una duración mínima de 0,3 s para que se vean al quemar.
- La traducción es usable para subtítulos cortos de clips, pero hay
  que revisar el SRT: el modelo `tiny` repite frases y se pierde con
  ruido de juego. Un modelo `base`/`small` traduciría mejor, a costa
  de más tiempo de CPU.

## Kick — v2.3 (KickDownloaderCLI vendorizado)

`auto_clips.py` acepta URLs de Kick igual que las de Twitch. El
soporte de Kick vive en el paquete `kick_downloader/`, vendorizado
de [KickDownloaderCLI](https://github.com/exe2027/kickdownloaderCLI)
(proyecto aparte, MIT): mismo código y mismos endpoints, sin
depender del `kick.py` suelto que había antes. Si Kick cambia algo,
se arregla ahí y se vuelve a vendorizar. Ojo: el escaneo rápido
`scan_chat_peaks()` está en el `core.py` vendorizado pero todavía
NO en kickdownloaderCLI v0.1.0 — hay que subirlo también al proyecto
aparte para que no diverja.

```bash
python auto_clips.py https://kick.com/locones/videos/01a1e069-8470-7626-8d5e-21ccb729c509
python auto_clips.py https://kick.com/locones/videos/01a1e069-8470-7626-8d5e-21ccb729c509 --dry-run
```

Cómo funciona por dentro (todo verificado en vivo el 2026-11-16):

- **Metadatos:** `GET kick.com/api/v1/video/{uuid-legacy}` y
  `GET kick.com/api/v2/channels/{slug}/videos` (el listado SÍ trae los
  VODs, con `source` = master.m3u8, `start_time` y `duration` en ms).
  La URL trae un UUIDv7 (sus primeros 48 bits son la fecha de creación);
  el API v1 solo acepta el UUIDv4 legacy, así que se resuelve buscando
  en el listado el VOD cuyo `start_time` esté a ≤5 s de esa fecha.
- **Chat replay:** Kick no tiene endpoint público documentado, pero la
  propia web usa uno sin documentar:
  `GET web.kick.com/api/v1/chat/{channel_id}/history?start_time=...`,
  que devuelve los mensajes de los 5 s siguientes. Un VOD de 10h27m son
  ~7.525 ventanas; se descargan en paralelo (8 workers, con reintentos:
  el endpoint se cae a veces con `IncompleteRead`, no es rate-limit).
  El chat se guarda en el mismo formato que TwitchDownloader, así que
  `clips_chat_v2` lo puntúa sin cambios.
- **Video:** el `source` es un master.m3u8 (HLS, Amazon IVS). Solo se
  baja el segmento de 60 s de cada pico con ffmpeg
  (`-ss <inicio> -i <variante-720p> -t 60 -c copy`), nunca el VOD entero.
- **Por qué no yt-dlp:** su extractor `kick:vod` está roto desde julio
  de 2026 (cambio de URLs de Kick → 404 / "0 bytes read",
  issue yt-dlp#17284, versión probada 2026.10.26). La ruta m3u8 directa
  es la que recomienda la comunidad.

```bash
# Solo el chat de Kick (sin cortar)
python -m kick_downloader chatdownload --id "https://kick.com/locones/videos/<uuid>" -o chat.json
# Solo un segmento de 60 s a partir de un segundo dado
python -m kick_downloader videodownload --id "https://kick.com/locones/videos/<uuid>" -o clip.mp4 -b 7000s -e 7060s
# Solo los metadatos del VOD
python -m kick_downloader info --id "https://kick.com/locones/videos/<uuid>"
```

Limitaciones reales de Kick (no son bugs del agente):

- El chat de Kick es mucho más ralo que el de Twitch: en el VOD de
  prueba (Locones, 10h27m) hay ventanas de 5 s con 0-4 mensajes y horas
  enteras sin chat (de 00:12 a 04:32 UTC, 0 mensajes). Los picos salen
  igual, pero con puntajes bajos y más ruido que en Twitch.
- Si el chat viene vacío, `--audio-fallback` busca picos por audio
  transcrito: muestrea 30 s de audio cada 15 min, lo transcribe con
  faster-whisper (tiny, en `.venv`) y puntúa la transcripción. Es un
  muestreo, no una detección continua: los picos son reales pero
  aproximados.
- Kick borra los VODs a los 7 días (30 días si el canal es verificado).
  El VOD de Locones es del 15 nov 2026: hay que usarlo antes de que
  expire.
- El API de Kick es inestable: a veces cierra la conexión a mitad de
  respuesta. `kick_downloader` reintenta (hasta 4 veces) y cae a `curl` si
  urllib no puede.

## Layout responsive — REGLA del proyecto (v2.5)

El recorte vertical **no** es un recorte ciego al centro. Lo demostró el
clip de Coker (2026-11-16): el directo eran dos webcams lado a lado con
una franja fina de gameplay arriba y barras negras, y el recorte centrado
tomaba media cara de cada uno + las barras: "no se ve nada". Desde v2.5,
cada clip decide su layout por fotogramas muestreados (`layout.py`,
solo stdlib + ffmpeg, sin dependencias nuevas):

- **Dos webcams lado a lado → se apilan**, una encima de la otra,
  llenando los 1080x1920 sin barras negras.
- **Una webcam + gameplay → PiP**: gameplay a pantalla completa con la
  webcam encima en una esquina superior (nunca abajo: ahí están el
  minimapa y el HUD de GTA).
- **Solo gameplay → recorte vertical centrado** normal.
- **Si el layout cambia dentro del clip, se adapta por tramos**, no un
  recorte fijo para todo el clip.

Cómo se detecta: franja de contenido apilada (alto ≤ 65% del fotograma)
+ costura vertical fuerte justo en el centro = dos cams; caja con borde
continuo en una esquina superior = webcam PiP. Calibrado con VODs reales
(Coker → stack; Locones y NoraExplorer → center; PiP verificado con un
sintético). La detección de PiP es heurística v1: si no está clara, cae
a centrado (seguro). Se puede forzar con `facecam_box` en `config.yaml`
o con `--layout stack|pip|center` (por defecto `auto`, configurable en
`config.yaml`, sección `layout`).

La decisión queda registrada en el manifiesto por clip (`layout` en
`manifest.json`, línea `Layout:` en `manifest.md`).

```bash
# Forzar un layout en un clip
python auto_clips.py <vod> --layout stack
python auto_clips.py <vod> --layout pip
```

## Uso

### 1. Descargar chat y VOD
```bash
TwitchDownloaderCLI chatdownload --id <vod-id> -o chat.json -E
TwitchDownloaderCLI videodownload --id <vod-id> -o vod.mp4
```

### 2. Ver los picos antes de cortar (recomendado)
```bash
python clips_chat_v2.py chat.json --dry-run
```

### 3. Extraer los 5 mejores clips
```bash
python clips_chat_v2.py chat.json --video vod.mp4 --out clips
```

### Ejemplos
```bash
# Solo chat en español, 3 clips de 30 segundos
python clips_chat_v2.py chat.json --video vod.mp4 --lang es --top-n 3 --duration 30

# Recorte a la izquierda (para no cortar el minimapa de la derecha)
python clips_chat_v2.py chat.json --video vod.mp4 --crop left

# Ventana de 5s y más separación entre clips
python clips_chat_v2.py chat.json --video vod.mp4 --window 5 --min-separation 90
```

## Opciones

| Opción | Defecto | Qué hace |
|---|---|---|
| `--top-n` | 5 | Nº de clips |
| `--window` | 10 | Ventana de chat en segundos |
| `--min-separation` | 60 | Separación mínima entre picos (s) |
| `--pre-roll` | 20 | Segundos antes del pico |
| `--duration` | 60 | Duración de cada clip (s) |
| `--lang` | both | `es`, `en` o `both` |
| `--crop` | center | `center`, `left` o `right` |
| `--crf` | 23 | Calidad x264 (menor = mejor) |
| `--preset` | fast | Preset x264 |
| `--dry-run` | — | Solo lista picos, sin cortar |

## Cómo funciona

1. Agrupa los mensajes en ventanas de 10 s (configurable).
2. Puntúa cada mensaje: 1 base, +1 risa, +1 hype.
3. Elige las ventanas con mayor puntaje, con al menos 60 s de separación.
4. Empieza cada corte 20 s antes del pico y exporta 60 s en `1080x1920` con recorte vertical.

El nombre de cada clip dice el pico, el puntaje y el desglose, por ejemplo `clip_01_t00m25s_s32_r16h0.mp4` = pico a los 25 s, 32 puntos, 16 risas, 0 hype.

## Automatización: un solo comando

```bash
python auto_clips.py 1234567890
python auto_clips.py https://www.twitch.tv/videos/1234567890 --lang es
python auto_clips.py https://kick.com/locones/videos/01a1e069-8470-7626-8d5e-21ccb729c509
```

Hace solo, en orden: descarga el chat, detecta los picos con
`clips_chat_v2.py`, corta en vertical, empaqueta para viral con
`viral_package.py` y deja todo en `runs/<vod-id>/` con `manifest.json`,
`manifest.md` y `titles.txt`. En Twitch descarga chat + VOD con
TwitchDownloaderCLI (reutiliza si ya están); en Kick descarga el chat
con `kick.py` y solo el segmento de 60 s de cada pico.

```bash
# Solo ver picos, sin bajar el video ni cortar
python auto_clips.py 1234567890 --dry-run
python auto_clips.py https://kick.com/locones/videos/<uuid> --dry-run
# Probar con archivos locales (sin Twitch)
python auto_clips.py --chat test_chat.json --video test_vod.mp4 --out runs/prueba
# Borrar el VOD después de cortar, para ahorrar espacio (solo Twitch)
python auto_clips.py 1234567890 --delete-vod
# En Kick, si el chat viene vacío, buscar picos por audio transcrito
python auto_clips.py https://kick.com/locones/videos/<uuid> --audio-fallback
```

Lo único que queda a mano es subirlos a TikTok / Reels / Shorts: eso no se puede automatizar sin que cada plataforma te pida entrar y confirmar en tu teléfono.

## Empaquetado viral — para las 4 plataformas, no solo YouTube

`viral_package.py` combina las skills de [youtube-agent-skill](https://github.com/Jakeschincariol/youtube-agent-skill), pero adaptadas a las 4 plataformas. Misma idea, mismo hook y mismo texto en pantalla; lo que cambia es caption, hashtags, portada y modo de subida. Nunca se copia el mismo texto literal en las 4.

| Plataforma | Título / caption | Miniatura | Subtítulos | Subida |
|---|---|---|---|---|
| YouTube Shorts | Título ≤60 car. + descripción + tags ≤15 | Frame del pico (por API no hay miniatura personalizada fiable) | SRT aparte + quemados | API oficial, 100% automatizable |
| TikTok | Caption corto ≤150 car. = título + 3-5 hashtags | Sin miniatura propia por API | QUEMADOS obligatorios | Borrador por API hasta auditoría (1-4 sem.) |
| Instagram Reels | Caption + hasta 10 hashtags | SÍ admite portada (cover_url) | QUEMADOS obligatorios | Graph API, requiere cuenta Profesional + Página de FB |
| Facebook Reels | Título ≤60 car. + caption, 3-90 s obligatorio | Frame del pico | QUEMADOS obligatorios | Graph API a Página, nunca a perfil |

Cada clip en `manifest.md` lleva su paquete por plataforma, con su métrica a mirar: YouTube visto vs deslizado, TikTok % finalización, Reels reproducciones y compartidos.

## Empaquetado viral (youtube-agent-skill)

`viral_package.py` combina las skills de [youtube-agent-skill](https://github.com/Jakeschincariol/youtube-agent-skill):

- **yt-viral:** ranking por múltiplo sobre la mediana del propio stream, no por puntaje bruto. Un pico de 32 pts con mediana 17 es 1.88x.
- **yt-shorts:** cada clip lleva primera línea nueva, texto en pantalla en los primeros 2 s (distinto de lo hablado) y punto de loop, para que sobreviva sin el video alrededor.
- **yt-package:** título + miniatura como una unidad. Título ≤60 car. (40 en móvil), miniatura ≤3 palabras y sin repetir el título, máx. 2 palabras en MAYÚSCULAS.
- **yt-seo:** descripción con las 2 primeras líneas como snippet, tags máx. 15 y las 3 búsquedas que el video debería ganar.
- **yt-retention:** el hook se juega en los primeros 15 s.

Nada publica solo: esto escribe y tú publicas. Cada clip termina con la pregunta de las skills: ¿lo subo así, o lo cambio?

## Supervivencia por plataforma

Publicar clips de otros streamers pone en riesgo las cuentas nuevas si se
hace en bloque o como si fuera un bot. Estas reglas son el checklist de
aprobación que el tribunal pidió: se cumplen siempre, y si cae el alcance
se sigue el protocolo de recuperación, no se insiste.

### Checklist de aprobación (antes de cada publicación)

- **Cuentas nuevas: 1 publicación por día por plataforma**, y no en bloque.
- **No a la misma hora en las 4 plataformas**: escalona las subidas
  (por ejemplo YT por la mañana, TikTok al mediodía, IG por la tarde,
  FB por la noche) para que no parezca una subida coordinada.
- **Nunca 5+ publicaciones en un día en una cuenta nueva.**
- **Nunca borrar y resubir el mismo clip** esperando que "esta vez sí"
  funcione: cada resubida idéntica es una señal de spam.
- **Nunca el mismo archivo en ES y EN el mismo día**: cada versión lleva
  edición, título, caption y hashtags distintos (ver `viral_package.py`,
  `package_for_platforms()`). Si el clip es el mismo, que al menos el
  empaquetado no lo sea.
- **Música del juego / del stream, fuera**: mutearla o reemplazarla por
  la biblioteca de música de cada plataforma. La música con copyright es
  la vía más rápida a un bloqueo de alcance o a una reclamación.
- **Sin marca de agua de otra plataforma**: un clip con el logo de TikTok
  en YouTube (o al revés) se penaliza en distribución.
- **No repetir título / descripción / hashtags idénticos en todos los
  clips**: cada clip tiene su propio título, caption y set de hashtags,
  aunque el formato se repita.
- **Calentar la cuenta nueva 1-2 días antes de publicar en serie**:
  ver vídeos, seguir alguna cuenta, completar el perfil, interactuar.
  Una cuenta que nace y publica 5 clips el mismo día huele a bot.
- **IG / FB: no publicar el primer Reel el día de crear la cuenta o la
  Page.** Esperar al día siguiente como mínimo.

### Qué mira cada plataforma

| Plataforma | No-ban | Shadowban | Algoritmo | Engagement |
|---|---|---|---|---|
| YouTube Shorts | No resubir el mismo archivo; música con licencia de su biblioteca; sin watermark de otra app | El alcance cae sin aviso si el vídeo se marca como reutilizado/spam | Prueba el Short con una audiencia pequeña y lo empuja según **% visto y finalización** | Retención y finalización; los likes importan menos que verlo entero |
| TikTok | 1/día en cuenta nueva; caption propio por clip; música de su biblioteca | Caída brusca de vistas a 0-200 sin aviso; revisar Estado de la cuenta | Empuja según **finalización, re-visualizaciones y compartidos** | Compartidos y replays pesan más que likes |
| IG Reels | No publicar el primer Reel el día de crear la cuenta; portada propia; sin watermark | Reels que dejan de aparecer en Explorar / a no-seguidores | Recomienda a **no-seguidores** según **finalización, guardados y DM** | Guardados y envíos por DM son la señal fuerte |
| FB Reels | Publicar en Page, nunca en perfil personal; 1/día al inicio | Alcance limitado a seguidores sin explicación | Pesan **compartidos y conversación** (comentarios con respuesta) | Comentarios y compartidos por encima de likes |

### Si cae el alcance

1. **Pausar 24-48 h**: no publicar nada en esa plataforma, no borrar en masa.
2. **Revisar Estado / Calidad de la cuenta** (YouTube Studio → Estado del canal, TikTok → Estado de la cuenta, IG → Estado de la cuenta, FB → Calidad de la Page) y leer qué marcó la plataforma.
3. **Solo el clip problemático a privado o eliminar**; nunca borrar todo el catálogo de golpe.
4. **Apelar solo con el permiso en mano**: si la reclamación es por reutilización o copyright, la ficha de permiso (`PERMISOS.md`) es la evidencia. Sin permiso, no apelar: esperar.
5. **Volver con 1/día y más transformado**: nuevo título, nueva edición (otro corte, otro layout, otro texto en pantalla), nuevos hashtags. Si vuelve a caer, parar esa plataforma una semana.

Sin permiso del streamer, no se publica: ver `PERMISOS.md`.

## Probado

Con `test_chat.json` (incluido, 43 mensajes de ejemplo en ES/EN) y un VOD de prueba de 100 s: detectó el pico de risas en español a los 30 s (32 pts, 16 risas, 1.88x la mediana) y el de hype a los 90 s (22 pts, 11 hype, 1.29x), cortó clips reales de 1080x1920 y generó el manifiesto viral con títulos, miniaturas y tags. Los falsos positivos (`extra`, `lollipop`) puntuaron 1, como mensaje normal.

Con un VOD real (NoraExplorer, 2929669422, 7h13m, 19.033 mensajes):
chat descargado con TwitchDownloaderCLI 1.56.5, 5 picos (el mejor
7h12m10s, 78 pts, 13x la mediana de 6), solo se bajó el segmento de
60 s del pico (no el VOD entero) y se cortó en vertical 1080x1920.
Con `subtitles.py` se transcribió ese segmento (inglés, probabilidad
0.98, 152 palabras, 44 bloques de subtítulos) y se quemaron los
subtítulos en `runs/2929669422/clips/clip_7h12m_subs.mp4`, verificado
con ffprobe (1080x1920, 60,01 s) y por fotogramas: el texto se ve
legible abajo del todo. Ojo: ese VOD es de GTA 5, no de GTA 6, y el
pico es la despedida del directo ("see you guys on the 19th"), no
una jugada; hay que revisar cada clip antes de publicar.

Con un VOD real de Kick (Locones, `01a1e069-8470-7626-8d5e-21ccb729c509`,
10h27m, Counter-Strike 2, 2.166 views, chat en rumano): el chat completo
habría tardado ~50 min (7.525 ventanas de 5 s) y el usuario lo canceló;
con el escaneo rápido (`scan_chat_peaks`, 126 muestras cada 5 min +
chat denso en las 5 regiones con más mensajes) el clip estuvo listo en
**9 min totales** (escaneo ~2 min, segmento 24 MB, corte vertical ~6 min).
Picos reales del chat: 08m55s 15,5 pts, 09m55s 17,75, 12m05s 19,0,
14m05s 22,25 y el mejor **03h08m25s 24,75 pts** (27 mensajes, 24,75x la
mediana), cortado en `runs/kick-locones-01a1e069/clips/clip_01_t03h08m05s_s24.75_r0h0.mp4`,
verificado con ffprobe (1080x1920, 60,00 s, 38 MB), con manifiesto viral
para las 4 plataformas. Ojo, dos cosas: el chat de Kick es rumano y
casi todo emotes, así que los picos son de **volumen** (0 risas, 0 hype
en ES/EN salvo 1) y el mejor pico es spam de emotes repetidos
(`loconeslocowwwwwwwwwww`, `collectiblesGoldenPitbull`), no una jugada:
el título "QUÉ JUGADA" que pone el empaquetado no corresponde a lo que
pasa en ese clip y hay que revisarlo antes de publicar. Y el VOD de
Kick expira a los 7 días (30 si verificado): este es del 15 nov 2026.

## Aviso

Bajar el VOD de otro streamer y republicarlo puede infringir las normas de Twitch y el copyright del creador / del juego. Úsalo con VODs propios o con permiso.
