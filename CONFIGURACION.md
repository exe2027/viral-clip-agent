# Configuración inicial — preguntas para Muse (una sola vez)

Cuando alguien le entrega este repo a Muse, Muse tiene que hacer **todas** las preguntas de este archivo **en un solo mensaje**, no una por una. La persona responde todo junto, en un solo mensaje, y recién ahí Muse configura el repo.

## Instrucciones para Muse

1. Pregunta todo lo de abajo de una vez, en un solo mensaje, con los valores por defecto ya puestos.
2. No empieces a configurar, a cortar clips ni a publicar nada hasta tener las respuestas.
3. Si una respuesta dice "por defecto" o "no sé", usa el valor por defecto y márcalo como pendiente de confirmar.
4. Las claves y secretos **nunca** van en el chat, en el README ni en el repo. Van en un archivo `.env` en la raíz (ya está en el `.gitignore`) o por el flujo seguro de Muse. Nunca pidas una tarjeta de crédito: la API de Gemini gratis no la necesita (ver abajo).
5. Al terminar, escribe los valores en su lugar (`config.yaml`, `influencers.yaml`, `accounts.yaml`, `.env`) y devuelve un resumen de qué quedó configurado y qué quedó pendiente.

## Cómo responder

Copia la plantilla del final, rellénala y mándala en un solo mensaje. Puedes escribir "por defecto" en lo que no quieras cambiar.

---

## 1. Canal

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Cómo se llama el canal? | Ej: El Clip de GTA 6 | El Clip de GTA 6 | `config.yaml > channel.name` |
| ¿Cuál es el handle / @? | Ej: @elclipdegta6 | @elclipdegta6 | `config.yaml > channel.handle` |
| ¿De qué juego son los clips? | Ej: GTA 6, GTA V RP, Counter-Strike 2 | GTA 6 | `config.yaml > channel.game` y `layout.game` |
| ¿En qué idioma es el canal? | es / en / both | es | `config.yaml > channel.language` |
| ¿Cuál es el nicho, en una frase? | Ej: clips de directos | clips de directos | `config.yaml > channel.niche` |

## 2. Streamers con permiso (lista blanca)

Solo se cortan y se publican clips de streamers que están en esta lista. Estar en la lista = permiso ya dado, no hay que pedir nada más ni registrar ninguna ficha.

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Qué streamers puedo usar? (uno por línea) | Ej: illojuan | illojuan | `influencers.yaml` y `allowed_streamers.yaml` |
| ¿En qué plataforma está cada uno? | twitch / kick | twitch | `influencers.yaml > plataforma` |
| ¿Qué juego hace cada uno? | Ej: GTA 6 | GTA 6 | `influencers.yaml > juego` |
| ¿En qué idioma habla cada uno? | es / en / otro | es | `influencers.yaml > idioma` |
| ¿Desde cuándo tienes su permiso? | Fecha, ej: 2026-11-16 | 2026-11-16 | `influencers.yaml > fecha_permiso` |
| ¿En qué plataformas se pueden publicar sus clips? | youtube / tiktok / instagram / facebook | las 4 | `influencers.yaml > alcance.plataformas_publicacion` |

## 3. Cuentas de publicación

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Ya tienes canal de YouTube? ¿Cuál es el @? ¿Está verificado? ¿Tiene OAuth configurado? | Ej: @elclipdegta6, creado, sin verificar, sin OAuth | YouTube ES @elclipdegta6, creado, sin verificar, sin OAuth | `accounts.yaml > youtube_shorts` |
| ¿Ya tienes cuenta de TikTok? ¿Cuál es el @? ¿La app para publicar por API está aprobada? | Ej: @elclipdegta6____bits, creada, app sin aprobar | TikTok ES @elclipdegta6____bits, creada, app sin aprobar | `accounts.yaml > tiktok` |
| ¿Ya tienes cuenta de Instagram? ¿Es profesional? ¿Está vinculada a una Página de Facebook? | Ej: @elclipdegta6, profesional, vinculada a Elclipdegta6 | Instagram @elclipdegta6, profesional, vinculada | `accounts.yaml > instagram_reels` |
| ¿Ya tienes Página de Facebook? ¿Cuál es su nombre? | Ej: El Clip de GTA 6, no creada | Página El Clip de GTA 6, no creada | `accounts.yaml > facebook_reels` |
| ¿Cuántos clips al día se pueden publicar por plataforma? | Números por plataforma | YouTube 3-5, TikTok 1, Instagram 1, Facebook 1 (cuentas nuevas) | Límites de publicación (ver README) |
| ¿En qué horario se publica? | Ej: 18:00-23:59 hora de Chile | 18:00-23:59, hora de Chile | Configuración de publicación |

## 4. Clips

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Cuántos clips por VOD? | Número, ej: 5 | 5 | `--top-n` / `config.yaml` |
| ¿Cuánto tiene que durar cada clip? | Objetivo y rango, ej: 30 s, entre 21 y 40 s | Objetivo 30 s, entre 21 y 40 s, tope duro 60 s | `config.yaml > clips.target_duration_s / min_duration_s / max_duration_s` |
| ¿Cuántos segundos antes del pico empieza el corte? | Número, ej: 20 | 20 | `config.yaml > clips.pre_roll` |
| ¿Ventana de chat para detectar picos? | Número en segundos, ej: 10 | 10 | `config.yaml > clips.window` |
| ¿Separación mínima entre picos? | Número en segundos, ej: 60 | 60 | `config.yaml > clips.min_separation` |
| ¿Recorte vertical fijo o automático? | auto / center / left / right / stack / pip | auto (decide por fotogramas) | `config.yaml > layout.mode`, `clips.crop` |

## 5. Subtítulos

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Qué estilo de subtítulo? | tiktok / highlight / karaoke | karaoke (palabra por palabra) | `config.yaml > subtitles.default_style` |
| ¿En qué idioma salen los subtítulos? | auto / es / en | auto | `config.yaml > subtitles.subtitle_lang` |
| ¿Se traduce lo que no es español al inglés? | sí / no | sí (español nunca se traduce) | `config.yaml > subtitle_policy` |
| ¿Qué palabras o nombres tiene que escribir bien Whisper? | Lista, ej: Lucia, Jason, Leonida, Vice City | Lista GTA 6 en `config.yaml` | `config.yaml > subtitles.gta6_terms / gta6_corrections` |

## 6. Títulos y hashtags

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Hashtags en español? | Lista, ej: #GTA6 #GTA6Clips | #GTA6, #GTA6Clips, #ElClipDeGTA6, #GTA, #RockstarGames, #gaming | `config.yaml > hashtags.es` |
| ¿Hashtags en inglés? | Lista, ej: #GTA6 #GTA6Clips | #GTA6, #GTA6Clips, #GTA, #RockstarGames, #gaming, #clips | `config.yaml > hashtags.en` |
| ¿Se puede publicar sin tu aprobación? | sí / no | **No, nunca**: todo clip queda en revisión hasta que lo apruebes | Cola + `jobqueue.py approve / reject` |

## 7. APIs y claves (opcional, todo funciona sin ellas salvo que se diga lo contrario)

| Pregunta | Para qué es | Por defecto | Dónde va |
|---|---|---|---|
| ¿Tienes API key de Gemini? | Solo para la visión, con Gemini 3.5 Flash-Lite (3 fotogramas por clip): vetar títulos que prometen algo que no se ve en pantalla, y elegir el mejor frame para la miniatura (`thumbnails.py --vision`). La detección del momento es por el chat, nunca por Gemini. Sin key, la visión queda apagada y la app funciona igual. | Sin key (visión apagada) | `.env > GEMINI_API_KEY` |
| ¿Quieres que Gemini elija el mejor frame? | Sí / no | Sí (con `--vision`; sin key cae al fotograma del payoff) | `thumbnails.py --vision` |
| ¿Cómo se crea la key de Gemini, si no la tienes? | En https://aistudio.google.com, Get API key, Create API key in new project. Gratis, sin tarjeta. **No toques "Set up billing" ni "Upgrade": esa pantalla pide tarjeta y no es la de la key gratis.** Tener Gemini Pro (la app) no te da una API key sola. Cuesta ~$0,0006 por clip, capa gratuita 500 peticiones/día. | — | — |
| ¿Tienes Twitch Client ID y Client Secret? | Solo para el monitor de influencers (`watcher.py`) por la vía oficial Helix. Sin ellos, el monitor usa la ruta pública y sirve igual para vigilar. | Sin credenciales | `.env > TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` |
| ¿Tienes alguna otra API que quieras usar? | Ej: ElevenLabs, OpenAI, fal.ai (OpenShorts las pide, este repo no) | Ninguna | `.env` (nunca en el repo) |

## 8. Entorno (dónde corre)

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Dónde va a correr? | Tu computador / un servidor / otro | Local | — |
| ¿Qué sistema operativo? | Ej: Linux, macOS, Windows | Linux | — |
| ¿Ya tienes Python 3.10+, FFmpeg y TwitchDownloaderCLI instalados? | sí / no | Ver tutorial del README | — |
| ¿Quieres que revise VODs nuevos solo, en algún horario? | Ej: todos los días a las 09:00 | Revisión diaria 09:00, hora de Chile | Cron / `watcher.py --loop` |

---

## Plantilla para responder (copiar, rellenar y mandar en un solo mensaje)

```text
CANAL
- Nombre del canal:
- Handle / @:
- Juego:
- Idioma (es / en / both):
- Nicho:

STREAMERS CON PERMISO (uno por línea: nombre, plataforma, juego, idioma, fecha del permiso)
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

SUBTÍTULOS
- Estilo (tiktok / highlight / karaoke):
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

## Ejemplo rellenado (El Clip de GTA 6, 2026-11-16)

```text
CANAL
- Nombre del canal: El Clip de GTA 6
- Handle / @: @elclipdegta6
- Juego: GTA 6
- Idioma (es / en / both): es
- Nicho: clips de directos

STREAMERS CON PERMISO
- illojuan, twitch, GTA 6, es, 2026-11-16

CUENTAS
- YouTube (@elclipdegta6, creado, sin verificar, sin OAuth)
- TikTok (@elclipdegta6____bits, creada, app sin aprobar)
- Instagram (@elclipdegta6, profesional, vinculada a la Página Elclipdegta6)
- Facebook (El Clip de GTA 6, página pendiente)
- Clips por día: YouTube 3-5, TikTok 1, Instagram 1, Facebook 1
- Horario: 18:00-23:59, hora de Chile

CLIPS
- Clips por VOD: 5
- Duración: objetivo 30 s, entre 21 y 40 s, tope 60 s
- Segundos antes del pico: 20
- Ventana de chat: 10 s
- Separación mínima: 60 s
- Recorte: auto

SUBTÍTULOS
- Estilo: karaoke
- Idioma de salida: auto
- Traducir lo que no sea español: sí (español nunca se traduce)
- Palabras: Lucia, Jason, Leonida, Vice City, GTA 6

TÍTULOS Y HASHTAGS
- Hashtags ES: #GTA6 #GTA6Clips #ElClipDeGTA6 #GTA #RockstarGames #gaming
- Hashtags EN: #GTA6 #GTA6Clips #GTA #RockstarGames #gaming #clips
- ¿Publicar sin mi aprobación? No, nunca

APIS Y CLAVES
- Gemini API key: creada el 2026-11-16 en AI Studio, gratis y sin tarjeta; visión todavía sin cablear, apagada por defecto
- Twitch Client ID y Secret: no
- Otras APIs: ninguna

ENTORNO
- Dónde corre: local
- Sistema operativo: Linux
- Python, FFmpeg y TwitchDownloaderCLI: sí
- Revisión automática: sí, todos los días a las 09:00, hora de Chile
```
