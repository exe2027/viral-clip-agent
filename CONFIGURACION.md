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

## 5. Subtítulos

| Pregunta | Opciones / ejemplo | Por defecto | Dónde va |
|---|---|---|---|
| ¿Qué estilo de subtítulo? | tiktok / highlight / karaoke | ref (simple, estilo RDJavi; karaoke como opción) | `config.yaml > subtitles.default_style` |
| ¿En qué idioma salen los subtítulos? | auto / es / en | auto | `config.yaml > subtitles.subtitle_lang` |
| ¿Se traduce lo que no es español al inglés? | sí / no | sí (español nunca se traduce) | `config.yaml > subtitle_policy` |
| ¿Qué palabras o nombres tiene que escribir bien Whisper? | Lista, ej: Lucia, Jason, Leonida, Vice City | Lista GTA 6 en `config.yaml` | `config.yaml > subtitles.gta6_terms / gta6_corrections` |

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
- Estilo: ref (simple, estilo RDJavi)
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
- Clip fuerte (viralidad ≥ 50, 40-60 s, subtítulos verificados) en la Cola de la web
- Primera publicación real desde la web
- Cortador en vivo y TikTok en público automático (auditoría en curso)
```
