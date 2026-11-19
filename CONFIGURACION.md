# Configuración inicial — preguntas para Muse (una sola vez)

Cuando alguien le entrega este repo a Muse, Muse tiene que hacer **todas** las preguntas de este archivo **en un solo mensaje**, no una por una. La persona responde todo junto, en un solo mensaje, y recién ahí Muse configura el repo.

## Instrucciones para Muse

1. Pregunta todo lo de abajo de una vez, en un solo mensaje, con los valores por defecto ya puestos.
2. No empieces a configurar, a cortar clips ni a publicar nada hasta tener las respuestas.
3. Si una respuesta dice "por defecto" o "no sé", usa el valor por defecto y márcalo como pendiente de confirmar.
4. Las claves y secretos **nunca** van en el chat, en el README ni en el repo. Van en un archivo `.env` en la raíz (ya está en el `.gitignore`) o por el flujo seguro de Muse. Nunca pidas una tarjeta de crédito: la API de Gemini gratis no la necesita (ver abajo).
5. Al terminar, escribe los valores en su lugar (`config.yaml`, `streamers.yaml`, `.env`, y las Cuentas e Influencers del panel web) y devuelve un resumen de qué quedó configurado y qué quedó pendiente.
6. **El panel web es la vía principal** (https://TU_PANEL.pages.dev, login `tu-usuario`): las cuentas de publicación y los streamers se configuran y se verifican ahí, en Cuentas e Influencers. Lo de este archivo es para que una sesión nueva sepa qué preguntar y dónde va cada respuesta; **todo cambio tiene que terminar desplegado y verificado en la web, nada local**.

## Cómo responder

Copia la plantilla del final, rellénala y mándala en un solo mensaje. Puedes escribir "por defecto" en lo que no quieras cambiar.

---

## 1. Canal

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Cómo se llama el canal? | Ej: Tu canal de clips | Tu canal de clips | `config.yaml > channel.name` |
| ¿Cuál es el handle / @? | Ej: @tu-cuenta | @tu-cuenta | `config.yaml > channel.handle` |
| ¿De qué juego son los clips? | Ej: GTA 6, GTA V RP, Counter-Strike 2 | GTA 6 | `config.yaml > channel.game` y `layout.game` |
| ¿En qué idioma es el canal? | es / en / both | es | `config.yaml > channel.language` |
| ¿Cuál es el nicho, en una frase? | Ej: clips de directos | clips de directos | `config.yaml > channel.niche` |
| ¿Dónde está el panel web? ¿Con qué login se entra? | Ej: TU_PANEL.pages.dev, usuario tu-usuario | https://TU_PANEL.pages.dev, usuario `tu-usuario` (tenant `tu-usuario`) | Panel web (Cuentas, Cola, Influencers) |

## 2. Streamers con permiso (lista blanca)

Solo se cortan y se publican clips de streamers que están en esta lista. Estar en la lista = permiso ya dado, no hay que pedir nada más ni registrar ninguna ficha.

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Qué streamers puedo usar? (uno por línea) | Ej: tu-streamer | Por configurar: añade tus streamers con permiso (uno por línea) | `streamers.yaml` (lista única) y panel web > Influencers |
| ¿En qué plataforma está cada uno? | twitch / kick | twitch | `streamers.yaml > plataforma` |
| ¿Qué juego hace cada uno? | Ej: GTA 6 | GTA 6 | `streamers.yaml > juego` |
| ¿En qué idioma habla cada uno? | es / en / otro | es | `streamers.yaml > idioma` |
| ¿Desde cuándo tienes su permiso? | Fecha, ej: 2026-11-18 | La fecha en que cada streamer te dio permiso (ej: 2026-11-18) | `streamers.yaml > fecha_permiso` |
| ¿En qué plataformas se pueden publicar sus clips? | youtube / tiktok / instagram / facebook | las 4 | `streamers.yaml > publish_platforms` |

## 3. Cuentas de publicación

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Ya tienes canal de YouTube? ¿Cuál es el @? ¿Está verificado? ¿Tiene OAuth configurado? | Ej: @tu-cuenta, creado, sin verificar, sin OAuth | Por configurar: @tu-cuenta, creado, sin verificar, OAuth por hacer (token con refresh en secretos de GitHub, nunca en el repo) | Panel web > Cuentas (verificar ahí) |
| ¿Ya tienes cuenta de TikTok? ¿Cuál es el @? ¿La app para publicar por API está aprobada? | Ej: @tu-cuenta, creada, app sin aprobar | Por configurar: @tu-cuenta, creada, app sin auditar (por API directa solo publica en privado; por Zernio, draft-first) | Panel web > Cuentas + Zernio |
| ¿Ya tienes cuenta de Instagram? ¿Es profesional? ¿Está vinculada a una Página de Facebook? | Ej: @tu-cuenta, profesional, vinculada a TuCanal | Por configurar: @tu-cuenta, profesional, vinculada a tu Página de Facebook (Meta se verifica en el panel) | Panel web > Cuentas (Meta, verificada) |
| ¿Ya tienes Página de Facebook? ¿Cuál es su nombre? | Ej: TuCanal, creada | Por configurar: página TuCanal, creada (Meta se verifica en el panel) | Panel web > Cuentas (Meta, verificada) |
| ¿Cuál es el ig_user_id de Instagram (para publicar Reels por API)? | El número de la cuenta en Meta (Graph API Explorer o la configuración de la app) | Por configurar en el panel (Cuentas > Meta, extra) | Panel web > Cuentas > Meta (extra) |
| ¿Cuál es el page_id de la Página de Facebook (para publicar Reels por API)? | El número de la Página en Meta | Por configurar en el panel (Cuentas > Meta, extra) | Panel web > Cuentas > Meta (extra) |
| ¿Dónde están servidos los clips con HTTPS (para que Instagram los descargue)? | La base de tu hosting estático, ej: https://clips.tudominio.com/ | R2 del panel (dominio público de Cloudflare, configurado en el Worker) | `wrangler.toml > PUBLIC_BASE_URL` / Worker |
| Al publicar en YouTube, ¿se publica también en TikTok, Instagram y Facebook automáticamente (fan-out)? | sí / no | Sí (cada plataforma con sus propias puertas; el fallo de una no bloquea a las otras) | `config.yaml > fanout.enabled` |
| ¿Cuántos clips al día se pueden publicar por plataforma? | Números por plataforma | YouTube plan 3 / máx 5; TikTok, Instagram y Facebook plan 2 / máx 3; cuentas nuevas 1/día; mínimo 2 h entre publicaciones | `limits.yaml` |
| ¿En qué horario se publica? | Ej: 18:00-23:59 hora de Chile | **Sin franja horaria** (decisión 2026-11-18: se publica a cualquier hora; los topes diarios se respetan igual) | Sin franja (eliminada 2026-11-18) |

## 4. Clips

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Cuántos clips por VOD? | Número, ej: 5 | 5 | `--top-n` / `config.yaml` |
| ¿Cuánto tiene que durar cada clip? | Objetivo y rango, ej: 40 s, entre 40 y 60 s | **Objetivo 40 s, entre 40 y 60 s, tope duro 60 s; 25-35 s prohibido, nunca 30 s** | `config.yaml > clips.target_duration_s / min_duration_s / max_duration_s` |
| ¿Cuántos segundos antes del pico empieza el corte? | Número, ej: 20 | 20 | `config.yaml > clips.pre_roll` |
| ¿Ventana de chat para detectar picos? | Número en segundos, ej: 10 | 10 | `config.yaml > clips.window` |
| ¿Separación mínima entre picos? | Número en segundos, ej: 60 | 60 | `config.yaml > clips.min_separation` |
| ¿Recorte vertical fijo o automático? | auto / center / left / right / stack / pip | auto (decide por fotogramas) | `config.yaml > layout.mode`, `clips.crop` |
| ¿Pre-filtro rápido antes de transcribir y renderizar? | sí / no | **Sí (por defecto)**: 1 punto por mensaje, 2 por reacción, tramo 10 s, pico ≥ 3x la mediana de ±5 min, separación 90 s; si no encuentra ningún pico en el VOD se desactiva solo (chat caliente) | `config.yaml > scoring.prefilter_min_multiple: 3.0`, `prefilter_separation_s: 90`; `--no-prefilter` lo apaga; `clipagent/prefilter.py` |
| ¿Puerta live para detectar picos en directo? | múltiplo 3x / hype por ráfaga | **Las dos**: múltiplo ≥ 3,0 (chat frío) O `hype_ratio ≥ 1,3 + score ≥ 14 + hype_score ≥ 1,0` (chat caliente); warmup 120 s; si la sesión termina sin picos, emite la mejor ventana (`hype_ratio ≥ 1,2`, `score ≥ 13`) | `clipagent/live.py` (`LIVE_HYPE_RATIO_MIN`, `LIVE_HYPE_SCORE_MIN`, `LIVE_WARMUP_S`) |

## 5. Subtítulos

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Qué estilo de subtítulo? | ref / tiktok / highlight / karaoke | **karaoke (por defecto desde 2026-11-18 19:17)**: 56 px negrita, palabra activa amarilla `#FFFF00`, resto blanco, borde 4, palabra por palabra | `config.yaml > subtitles.default_style` y `clipagent/config.py > subtitle_styles` |
| ¿Dónde van los subtítulos? | Al pie de la cámara / en el gameplay | **Al pie de la cámara (decisión 2026-11-18 19:02)**: `Alignment 8`, `MarginV 608`, banda 595–749; NUNCA en el gameplay, ahí van los subtítulos propios de GTA 6 y se solaparían (verificado en t=18 s y t=34 s, caso Lucia) | `clipagent/rdjavi.py > SUBS_ALIGN = 8`, `SUBS_MARGIN_V = 608` |
| ¿En qué idioma salen los subtítulos? | auto / es / en | auto | `config.yaml > subtitles.subtitle_lang` |
| ¿Se traduce lo que no es español al inglés? | sí / no | sí (español nunca se traduce) | `config.yaml > subtitle_policy` |
| ¿Qué palabras o nombres tiene que escribir bien Whisper? | Lista, ej: Lucia, Jason, Leonida, Vice City | Lista GTA 6 en `config.yaml` | `config.yaml > subtitles.gta6_terms / gta6_corrections` |
| ¿Qué pasa si Whisper no encuentra voz? | Bloquear / publicar igual | **Bloquear**: el clip queda `needs_human_transcription` + `publish_blocked` hasta que el usuario lo transcriba de oído; nunca se inventa texto ni se publica sin subtítulos | `config.yaml > subtitles.required / complete / block_without_subtitles` |

Estilos exactos (`clipagent/config.py > subtitle_styles`):

| Estilo | Letra | Color | Negrita | Borde | Fondo | Cómo se ve |
|---|---|---|---|---|---|---|
| `ref` | 32 px fina | Blanco | No | 2 | No | Una línea, sin resaltado (default hasta 2026-11-18 19:17) |
| `tiktok` | 48 px | Blanco | Sí | 3 | No | Una línea, blanca en negrita |
| `highlight` | 52 px | Amarillo | Sí | 2 | Sí (caja) | Palabras clave en amarillo con caja |
| `karaoke` | 56 px | Palabra activa amarilla, resto blanco | Sí | 4 | No | Palabra por palabra, cada palabra se ilumina al decirla |

## 6. Títulos y hashtags

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Hashtags en español? | Lista, ej: #GTA6 #GTA6Clips | #GTA6, #GTA6Clips, #TuCanal, #GTA, #RockstarGames, #gaming | `config.yaml > hashtags.es` |
| ¿Hashtags en inglés? | Lista, ej: #GTA6 #GTA6Clips | #GTA6, #GTA6Clips, #GTA, #RockstarGames, #gaming, #clips | `config.yaml > hashtags.en` |
| ¿Se puede publicar sin tu aprobación? | sí / no | **Sí, automática con puertas** (viralidad ≥ 50, SEO ≥ 90, subtítulos completos, lista blanca, topes diarios, preview visto); sin aprobación por clip | Panel web > Cola + puertas de publicación |
| ¿Cómo se mide el hype? | Total de comentarios / ráfaga | **Por ráfaga rápida**: mensajes por 10 s / por minuto contra el baseline rodante; una ráfaga repentina pesa más que el total | Detección de picos (ver README) |

## 7. APIs y claves (opcional, todo funciona sin ellas salvo que se diga lo contrario)

| Pregunta | Para qué es | Por defecto | Dónde va |
|---|---|---|---|
| ¿Tienes API key de Gemini? | Solo para la visión, con Gemini 3.5 Flash-Lite (3 fotogramas por clip): vetar títulos que prometen algo que no se ve en pantalla, y elegir el mejor frame para la miniatura (`thumbnails.py --vision`). La detección del momento es por el chat, nunca por Gemini. Sin key, la visión queda apagada y la app funciona igual. | Por crear en AI Studio, gratis y sin tarjeta; se guarda en secreto de GitHub y en `.env`, nunca en el repo; visión para miniaturas implementada, **veto de títulos todavía sin cablear en `auto_clips.py`** | `.env > GEMINI_API_KEY` |
| ¿Quieres que Gemini elija el mejor frame? | Sí / no | Sí (con `--vision`; sin key cae al fotograma del payoff) | `thumbnails.py --vision` |
| ¿Cómo se crea la key de Gemini, si no la tienes? | En https://aistudio.google.com, Get API key, Create API key in new project. Gratis, sin tarjeta. **No toques "Set up billing" ni "Upgrade": esa pantalla pide tarjeta y no es la de la key gratis.** Tener Gemini Pro (la app) no te da una API key sola. Cuesta ~$0,0006 por clip, capa gratuita 500 peticiones/día. | — | — |
| ¿Tienes Twitch Client ID y Client Secret? | Solo para el monitor de influencers (`watcher.py`) por la vía oficial Helix. Sin ellos, el monitor usa la ruta pública y sirve igual para vigilar. | Por configurar en secretos de GitHub (entorno `tu-entorno`), nunca en el repo; verificación Helix pendiente | Secretos de GitHub (nunca en el repo) |
| ¿Tienes alguna otra API que quieras usar? | Ej: ElevenLabs, OpenAI, fal.ai (OpenShorts las pide, este repo no) | Zernio (TikTok, draft-first) | Secretos de GitHub (nunca en el repo) |

## 8. Entorno (dónde corre)

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Dónde va a correr? | Tu computador / un servidor / otro | **Panel web en Cloudflare** (https://TU_PANEL.pages.dev) + cerebro en GitHub Actions; el repo local es solo para desarrollar | — |
| ¿Qué sistema operativo? | Ej: Linux, macOS, Windows | Linux | — |
| ¿Ya tienes Python 3.10+, FFmpeg y TwitchDownloaderCLI instalados? | sí / no | Ver tutorial del README | — |
| ¿Quieres que revise VODs nuevos solo, en algún horario? | Ej: todos los días a las 09:00 | Worker con cron cada 5 min en el panel; cortador en vivo en construcción (Fase 0 sombra → Fase 1 semi-manual 19 nov) | Panel web > Worker |

---

## 9. Flujo completo paso a paso (para otro Muse: sigue esto sin preguntar)

Orden fijo. Cada paso tiene su puerta: lo que no pasa se descarta
SIN gastar el paso siguiente. Los valores son los vigentes
2026-11-18; si un archivo dice otra cosa, manda este documento y
`docs/reglas-usuario.md`.

### 9.1 Detectar

```bash
# VOD completo (Twitch): descarga chat, detecta, corta y empaqueta
python3 auto_clips.py 2932415021 --lang es

# Solo ver picos, sin bajar vídeo ni cortar
python3 auto_clips.py 2932415021 --dry-run

# Ya sabes el momento (lo viste en directo): solo el chat de ±120 s
# alrededor del pico, nunca el chat entero
python3 auto_clips.py 2932415021 --peak 3220 --lang es
python3 auto_clips.py 2932415021 --peak 1:40:50 --chat-window 180 --dry-run
```

En vivo: el loop lo hace el cerebro (GitHub Actions `cerebro-live.yml`
+ Worker cron */5): `live_scan` barre todos los streamers enabled
ES+EN en vivo y GTA VI, y el `cut` corta 10 min por sesión. Puerta
live hype v2 (ver §4): múltiplo ≥ 3,0 O `hype_ratio ≥ 1,3 + score ≥ 14
+ hype_score ≥ 1,0`, warmup 120 s, fallback anti-cero.

### 9.2 Pre-filtro (barato) y revisión

1. **Pre-filtro Óscar** (`clipagent/prefilter.py`): 1 punto por
   mensaje, 2 por reacción, tramo 10 s, pico ≥ 3x la mediana de ±5
   min, separación 90 s. Solo los que pasan van a Whisper/viralidad/
   SEO/render. Si no pasa NADIE, se desactiva solo (chat caliente).
2. **Revisión automática** (top 3 picos por directo, viralidad auto
   40–49,9): 3 fotogramas + transcripción Whisper + juez Gemini con
   los 5 componentes humanos; se re-calcula la viralidad. ≥ 50 va a
   render; < 40 se descarta sin revisión. Si no hay Gemini, fallback
   a la puerta pre-corte (viral auto ≥ 45).
3. **Puerta pre-corte**: el clip SOLO se corta si el paquete
   provisional pasa (viralidad estimada ≥ 45, SEO ≥ 90 en al menos
   una plataforma). Si no pasa, se descarta SIN descargar el
   segmento ni renderizar.

### 9.3 Scores (fórmulas intocables)

- **Viralidad** (0–100, `clipagent/detect.py`): Hook×2,0 +
  Claridad×1,5 + Novedad×1,5 + ChatNorm×1,5 + Loop×1,5 + Audio×1,0 +
  Packaging×1,0. Bandas: <50 descarta, 50–69 revisar, 70–84
  prioritario, 85–100 urgente. No tocar pesos hasta 30 clips
  publicados medidos a 7 días (±25% máx. por ciclo).
- **SEO** (0–100 por plataforma, `clipagent/seo_score.py`): puerta
  90 para publicar; 70–89 solo con override registrado con motivo;
  <70 bloqueo duro.
- **Prioridad** (`clipagent/priority.py`): `viralidad^0,5 × SEO^0,3
  × chat_norm^0,2`. LIVE sobre VOD en la Cola (+15 solo para
  ordenar). Clip > 7 días: −10 pts/día. No dos clips del mismo
  momento el mismo día en la misma plataforma.
- **Hype**: velocidad del chat (msgs/10 s, msgs/min) contra el
  baseline rodante; `hype_score` v2 0–10; `score_chat` en
  `burst_peaks.json` y en el manifiesto. Frases del streamer
  (`clipagent/streamer_hype.py`) y gritos por decibeles
  (`clipagent/audio.py::shout_spikes`, ≥ +8 dB sobre la mediana de
  10 s y ≥ -30 dBFS) se registran pero NO tocan las fórmulas.

### 9.4 Corte (40–60 s, solo el segmento)

```bash
# El corte es 40–60 s, nunca menos de 40 ni más de 60, nunca 30 exacto.
# Objetivo 40 s (20 s antes del pico + 20 s después).
# Solo se baja el trozo del clip, nunca el VOD entero.
```

### 9.5 Render RDJavi C (plantilla común, `clipagent/rdjavi.py`)

```bash
# Una sola vez por clip: fondo pre-renderizado (bg.png)
# Render en un solo pase (opción C): layout + punch-in + karaoke + audio
python3 -m clipagent.rdjavi --segment runs/.../segments/seg.mp4 \
  --start 0 --duration 48.01 --ass runs/.../rdjavi/subs_karaoke.ass \
  --gradient runs/.../rdjavi/vice_gradient.png \
  --out runs/.../clips/clip_rdjavi_subs.mp4 --bg-frame 20

# Vía rápida recomendada si el segmento es HEVC 1440p60: pre-transcodificar
# una vez a h264 1080p30 y renderizar sobre el intermedio (coords ×0,75)
ffmpeg -y -v error -i runs/.../segments/seg.mp4 \
  -vf "fps=30,scale=1920:1080" -c:v libx264 -preset superfast -crf 23 \
  -pix_fmt yuv420p -c:a copy runs/.../rdjavi/seg_1080p30.mp4
python3 -m clipagent.rdjavi --segment runs/.../rdjavi/seg_1080p30.mp4 \
  --start 0 --duration 48.01 --ass runs/.../rdjavi/subs_karaoke.ass \
  --bg runs/.../clips/bg.png --gradient runs/.../rdjavi/vice_gradient.png \
  --cam-box 421:274:0:0 --cam-square 274:274:68:0 \
  --game-crop 1080:1080:465:0 --out runs/.../clips/clip_rdjavi_subs.mp4 \
  --bg-frame 20 --punch-in 16 26 --fps 30 --preset veryfast --crf 23
```

Layout fijo 1080×1920: webcam 583×595 en (248, 0), fondo y 0–749,
gameplay y 749–1786 (1080×1037, recorte 1:1), barra y 1786–1920.
Crops (segmento 2560×1440): cámara normal `365:365:90:0`, zoom
`310:310:125:27`, fondo `561:365:0:0`, gameplay `1440:1440:620:0`.
Punch-in 16–26 s con doble rama + `overlay enable` (sin trim ni
concat). Subtítulos karaoke al pie de la cámara (ver §5).
**Tiempos medidos p3220 (48,01 s, con otros ffmpeg a la vez, nunca
en solitario)**: v3 karaoke 6m49s, v2 tiktok 8m54s; contenido real
~3,5 min extrapolados. Por web (GitHub Actions, 4 vCPU): 2,5–3,5 min
estimados, sin medir. **Un solo render a la vez en la VM.**

### 9.6 Verificar antes de encolar (obligatorio)

```bash
# ffprobe: duración 48,01 s, 1080x1920, 60 fps, moov al frente
ffprobe -v error -show_entries format=duration,size \
  -show_entries stream=codec_name,width,height,r_frame_rate,codec_type \
  -of default=noprint_wrappers=1 <clip.mp4>
# Si dice "moov atom not found", el render NO terminó: no es un archivo válido.
# Fotogramas 15,9 / 16,1 / 25,9 / 26,1 s: punch-in exacto (ver README §4b).
# Fotogramas t=18 s y t=34 s: karaoke al pie de la cámara, sin solapar el
# subtítulo del juego.
```

### 9.7 Encolar y publicar

```bash
# Encolar en el panel (NO publica): POST /api/clips + PUT /api/clips/:id/video
PANEL_SESSION_TOKEN=... python3 runs/live/rubius_2026-11-18/enqueue_clip.py \
  runs/2932415021-p3220 \
  runs/2932415021-p3220/clips/clip_01_t53m20s_s14.3_r7h4_rdjavi_subs_C_v3_karaoke.mp4
# Verificar EN EL PANEL (TU_PANEL.pages.dev, Cola) que el clip aparece
# con su MP4 subido. La publicación pasa por publishGate y el fan-out a las
# 4 plataformas; topes: YT plan 3/máx 5 al día, TikTok/IG/FB plan 2/máx 3,
# cuentas nuevas 1/día, mínimo 2 h entre publicaciones, sin franja horaria.
```

El token/sesión del panel NUNCA se pide por chat ni se escribe en el
repo, en logs ni en docs. Si falta, el encolado queda bloqueado (es
el estado actual del 3220) y no se improvisa: se consigue por flujo
seguro o desde una sesión ya logueada.

### 9.8 Tests (antes de commitear)

```bash
# pytest NO existe en esta VM y el CI corre `pytest -q` (.github/workflows/ci.yml).
# OJO: no todos los archivos son unittest. Hay dos estilos mezclados:
# - unittest (TestCase): se corren con python3 -m unittest
python3 -m unittest test_prefilter -v            # 13/13 (medido 2026-11-18)
# - pytest-estilo (funciones test_* con runner propio en __main__):
python3 test_rdjavi.py                            # 6/6 OK (medido 2026-11-18)
python3 test_viral_score.py test_hype_v2.py       # tienen runner __main__ propio
# - pytest-estilo SIN runner (test_priority.py y otros): `python3 test_priority.py`
#   no hace nada (0 tests). Esos solo corren con pytest, que aquí no está
#   instalado: se verifican en el CI de GitHub. No escribas
#   `python3 -m unittest test_rdjavi test_priority`: da "0 tests" y no prueba nada.
```

## 10. Errores comunes (léelos antes de tocar nada)

| Síntoma | Causa | Arreglo |
|---|---|---|
| El render nunca termina / MP4 de 193 s o 6189 s con la cola congelada | `color=c=black` sin duración: es una fuente infinita | Poner `color:d=$DUR` **y** `-t $DUR` en la salida. Nunca un `color=` sin duración |
| `Trailing garbage after a filter` | Comentarios `# ...` DENTRO del string de `-filter_complex` | Sacar los comentarios fuera del string; `bash -n` NO detecta este error |
| `ffprobe: moov atom not found` | El render sigue corriendo o se mató antes de escribir el moov | No es un archivo válido: esperar a que termine o volver a renderizar; no entregar ni encolar |
| El archivo final se reinicia / baja de tamaño | Varios agentes o varios ffmpeg escribiendo el MISMO MP4 a la vez | **Un solo agente y un solo render a la vez**; cada render a su propio archivo de salida |
| El render tarda 28+ min | Varios ffmpeg a la vez en una VM de 1,5 CPU, o método viejo de 4 pases | Parar los demás, usar la plantilla C (un solo pase); mejor aún, GitHub Actions (4 vCPU) |
| `git status` no muestra `runs/.../render_rdjavi.sh` | `runs/` está en el `.gitignore`: los runs NO se commitean | La plantilla vive en `clipagent/rdjavi.py`, no en `runs/`; el script de un run es solo backup local |
| El pre-filtro tumba todos los clips de un VOD | Chat caliente: la mediana también es altísima y nada llega a 3x | Es la salvaguarda: si no encuentra ningún pico, se desactiva solo. No bajes el 3x a mano |
| El clip sale con subtítulos sobre los del juego | Subtítulos en el gameplay (MarginV 230 viejo) | Karaoke al pie de la cámara: Alignment 8, MarginV 608, banda 595–749 |
| `pytest: command not found` | pytest no está instalado en la VM | Usar `python3 -m unittest ...` (ver §9.8) |
| Push a GitHub con 403 | Permiso de escritura perdido o repo fuera de la selección de la app de GitHub | Revisar la instalación de la app de GitHub (Contents: Write); no reintentarlo a ciegas |

## Plantilla para responder (copiar, rellenar y mandar en un solo mensaje)

```text
CANAL
- Nombre del canal:
- Handle / @:
- Juego:
- Idioma (es / en / both):
- Nicho:
- Panel web (URL, usuario):

STREAMERS CON PERMISO (uno por línea: nombre, plataforma, juego, idioma, fecha del permiso; indica si está pausado)
-

CUENTAS
- YouTube (@, creado sí/no, verificado sí/no, OAuth sí/no):
- TikTok (@, creado sí/no, app aprobada sí/no):
- Instagram (@, creado sí/no, profesional sí/no, Página de Facebook vinculada sí/no):
- Facebook (nombre de la Página, creada sí/no):
- Clips por día por plataforma:
- Horario de publicación:

CLIPS
- Clips por VOD:
- Duración objetivo / mínima / máxima (segundos):
- Segundos antes del pico:
- Ventana de chat (segundos):
- Separación mínima entre picos (segundos):
- Recorte (auto / center / left / right):
- Hype (total / ráfaga):

SUBTÍTULOS
- Estilo (ref / tiktok / highlight / karaoke; por defecto karaoke):
- Posición (al pie de la cámara, Alignment 8, MarginV 608; nunca en el gameplay):
- Idioma de salida (auto / es / en):
- Traducir lo que no sea español (sí / no):
- Palabras que Whisper tiene que escribir bien:

TÍTULOS Y HASHTAGS
- Hashtags en español:
- Hashtags en inglés:
- ¿Publicar sin mi aprobación? (sí / no):

APIS Y CLAVES
- Gemini API key (la tengo / no la tengo / créala conmigo):
- Twitch Client ID y Secret (los tengo / no los tengo):
- Otras APIs:

ENTORNO
- Dónde corre (mi computador / servidor):
- Sistema operativo:
- Python, FFmpeg y TwitchDownloaderCLI instalados (sí / no):
- Revisión automática de VODs nuevos (sí / no, horario):
```

## Ejemplo rellenado (genérico — sustituye cada valor por el tuyo)

```text
CANAL
- Nombre del canal: Tu canal de clips
- Handle / @: @tu-cuenta
- Juego: GTA 6
- Idioma (es / en / both): es
- Nicho: clips de directos
- Panel web: https://TU_PANEL.pages.dev, usuario tu-usuario (tenant tu-usuario)

STREAMERS CON PERMISO
- tu-streamer, twitch, GTA 6, es, TU_FECHA_PERMISO (activo)
- otro-streamer, twitch, GTA 6, es, TU_FECHA_PERMISO (pausado, si lo pausas)

CUENTAS
- YouTube (@tu-cuenta, creado, OAuth por hacer; falta crear la cuenta API en el panel)
- TikTok (@tu-cuenta, creada, app sin auditar; por Zernio, draft-first)
- Instagram (@tu-cuenta, profesional, vinculada a la Página TuCanal, Meta por verificar en el panel)
- Facebook (TuCanal, creada, Meta por verificar en el panel)
- Clips por día: YouTube plan 3 / máx 5; TikTok, Instagram y Facebook plan 2 / máx 3; cuentas nuevas 1/día; mínimo 2 h entre publicaciones
- Horario: sin franja horaria (se publica a cualquier hora)

CLIPS
- Clips por VOD: 5
- Duración: objetivo 40 s, entre 40 y 60 s, tope 60 s; 25-35 s prohibido, nunca 30 s
- Segundos antes del pico: 20
- Ventana de chat: 10 s
- Separación mínima: 60 s
- Recorte: auto (layout RDJavi: streamer arriba, gameplay abajo)
- Hype: por ráfaga rápida (mensajes por 10 s / por minuto contra el baseline rodante)

SUBTÍTULOS
- Estilo: karaoke (por defecto desde 2026-11-18 19:17; 56 px negrita, palabra activa amarilla, resto blanco, borde 4, palabra por palabra)
- Posición: al pie de la cámara (Alignment 8, MarginV 608, banda 595–749); nunca en el gameplay, para no solapar los subtítulos del GTA 6
- Idioma de salida: auto
- Traducir lo que no sea español: sí (español nunca se traduce)
- Palabras: Lucia, Jason, Leonida, Vice City, GTA 6
- Regla: subtítulos siempre y completos, quemados, todo lo que dice el influencer; sin voz verificable el clip queda bloqueado, nunca se inventa texto

TÍTULOS Y HASHTAGS
- Hashtags ES: #GTA6 #GTA6Clips #TuCanal #GTA #RockstarGames #gaming
- Hashtags EN: #GTA6 #GTA6Clips #GTA #RockstarGames #gaming #clips
- ¿Publicar sin mi aprobación? Sí, automática con puertas (viralidad ≥ 50, SEO ≥ 90, subtítulos completos, lista blanca, topes diarios, preview visto)

APIS Y CLAVES
- Gemini API key: por crear en AI Studio, gratis y sin tarjeta; se guarda en secreto de GitHub y en .env, nunca en el repo; visión para miniaturas implementada, veto de títulos todavía sin cablear en auto_clips.py
- Twitch Client ID y Secret: por configurar en secretos de GitHub (entorno tu-entorno), nunca en el repo
- Otras APIs: Zernio (TikTok, draft-first)

ENTORNO
- Dónde corre: panel web en Cloudflare (TU_PANEL.pages.dev) + cerebro en GitHub Actions; repo local solo para desarrollar
- Sistema operativo: Linux
- Python, FFmpeg y TwitchDownloaderCLI: sí
- Revisión automática: Worker con cron cada 5 min; cortador en vivo en construcción (Fase 0 sombra → Fase 1 semi-manual)

PENDIENTE
- Crear las cuentas API de YouTube y TikTok en Cuentas (tu-usuario) y verificarlas
- Clip fuerte (viralidad ≥ 50, 40-60 s, subtítulos verificados) en la Cola de la web: el 3220 de Rubius ya está listo (v3 karaoke, 48,02 s, viralidad 53,8, SEO 92/94/94/94) pero el encolado está bloqueado por falta de PANEL_SESSION_TOKEN
- Re-render del 7775 con la plantilla RDJavi C + karaoke (pasa scores: viralidad 52,5, SEO 100/100/100/100)
- Primera publicación real desde la web
- Cortador en vivo y TikTok en público automático (auditoría en curso)
```
