# Twitch Auto Clips v2

Herramienta en Python para detectar automáticamente los mejores momentos de un directo de Twitch a partir del chat (`chat.json`) y recortarlos en vertical `1080x1920`, listos para TikTok, Reels o Shorts.

**Automático:** `auto_clips.py` hace todo en un solo comando — descarga chat + VOD, detecta, corta y empaqueta cada clip para viral. Ver [Automatización](#automatización-un-solo-comando).

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
- [TwitchDownloaderCLI](https://github.com/lay295/TwitchDownloader) en el PATH
- [FFmpeg](https://ffmpeg.org/) en el PATH (ffprobe opcional, para medir el VOD)

Sin dependencias de Python, sin credenciales, sin red.

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
```

Hace solo, en orden: descarga chat + VOD con TwitchDownloaderCLI (reutiliza si ya están), detecta los picos con `clips_chat_v2.py`, corta en vertical, empaqueta para viral con `viral_package.py` y deja todo en `runs/<vod-id>/` con `manifest.json`, `manifest.md` y `titles.txt`.

```bash
# Solo ver picos, sin bajar el video ni cortar
python auto_clips.py 1234567890 --dry-run
# Probar con archivos locales (sin Twitch)
python auto_clips.py --chat test_chat.json --video test_vod.mp4 --out runs/prueba
# Borrar el VOD después de cortar, para ahorrar espacio
python auto_clips.py 1234567890 --delete-vod
```

Lo único que queda a mano es subirlos a TikTok / Reels / Shorts: eso no se puede automatizar sin que cada plataforma te pida entrar y confirmar en tu teléfono.

## Empaquetado viral (youtube-agent-skill)

`viral_package.py` combina las skills de [youtube-agent-skill](https://github.com/Jakeschincariol/youtube-agent-skill):

- **yt-viral:** ranking por múltiplo sobre la mediana del propio stream, no por puntaje bruto. Un pico de 32 pts con mediana 17 es 1.88x.
- **yt-shorts:** cada clip lleva primera línea nueva, texto en pantalla en los primeros 2 s (distinto de lo hablado) y punto de loop, para que sobreviva sin el video alrededor.
- **yt-package:** título + miniatura como una unidad. Título ≤60 car. (40 en móvil), miniatura ≤3 palabras y sin repetir el título, máx. 2 palabras en MAYÚSCULAS.
- **yt-seo:** descripción con las 2 primeras líneas como snippet, tags máx. 15 y las 3 búsquedas que el video debería ganar.
- **yt-retention:** el hook se juega en los primeros 15 s.

Nada publica solo: esto escribe y tú publicas. Cada clip termina con la pregunta de las skills: ¿lo subo así, o lo cambio?

## Probado

Con `test_chat.json` (incluido, 43 mensajes de ejemplo en ES/EN) y un VOD de prueba de 100 s: detectó el pico de risas en español a los 30 s (32 pts, 16 risas, 1.88x la mediana) y el de hype a los 90 s (22 pts, 11 hype, 1.29x), cortó clips reales de 1080x1920 y generó el manifiesto viral con títulos, miniaturas y tags. Los falsos positivos (`extra`, `lollipop`) puntuaron 1, como mensaje normal.

## Aviso

Bajar el VOD de otro streamer y republicarlo puede infringir las normas de Twitch y el copyright del creador / del juego. Úsalo con VODs propios o con permiso.
