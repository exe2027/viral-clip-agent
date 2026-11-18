# Viral Clip Agent — Twitch + Kick

Herramienta en Python para detectar automáticamente los mejores momentos de un directo de **Twitch o de Kick** a partir del chat (`chat.json`) y recortarlos en vertical `1080x1920`, listos para TikTok, Reels o Shorts.

**Automático:** `auto_clips.py` hace todo en un solo comando — descarga chat, detecta, corta y empaqueta cada clip para viral. Ver [Automatización](#automatización-un-solo-comando). En Twitch baja el VOD; en Kick baja solo el segmento de 60 s de cada pico, nunca el VOD entero.

## Estado actual (2026-11-18) — Panel web AVC

El cerebro es este repo, pero **todo se usa y se verifica desde la web, nada local**: el panel vive en Cloudflare y la publicación sale desde ahí.

- **Panel:** https://avc-em9.pages.dev — login al entrar, **usuario `seba`** (tenant `sebastian`); cada persona entra con su propio login y ve solo su panel, sus cuentas, su cola y sus sesiones. 12 pestañas: Worker, Cola, Cuentas, Editor, Canales, Tracking, Analíticas, Usuarios, Influencers, Fuentes, Avisos, Ajustes. Worker en marcha (cron cada 5 min), D1 + R2 en Cloudflare.
- **Cuentas en Cuentas (seba):** Meta **conectada** (Instagram @elclipdegta6 + Facebook Elclipdegta6, verificada en el panel); **YouTube en conexión** (OAuth hecho: proyecto "El Clip de GTA 6", token con refresh guardado; falta crear la cuenta API en el panel); **TikTok en conexión** (conectada en Zernio, @elclipdegta6____bits, vía `ZernioAdapter`, draft-first; falta crear la cuenta API en el panel).
- **Cuentas del canal:** Twitch `elclipdegta6`, YouTube "El Clip de GTA 6" (@elclipdegta6), TikTok @elclipdegta6____bits, Instagram @elclipdegta6, Facebook Elclipdegta6. Correo del canal: Bits2025cl@gmail.com. Las claves y tokens **nunca** van en este repo: van en secretos de GitHub (entorno `sebastian`) y cifrados en el panel (D1).
- **Streamers:** **Rubius prioritario** (permiso dado 2026-11-18, GTA 6 en vivo + VOD 2932415021, 4 plataformas ES); **IlloJuan PAUSADO** desde 2026-11-18 (su clip_02 sigue bloqueado por subtítulos sin verificar por oído). Ver `streamers.yaml` (lista única; estar en la lista = permiso ya dado).

### Reglas vigentes (2026-11-18)

- **Clips de 40 a 60 segundos**: nunca menos de 40, nunca más de 60, nunca 30 exacto; el rango 25-35 s está prohibido (regla de oro). Objetivo 40 s (20 s antes del pico + 20 s después). `config.yaml > clips.min_duration_s: 40, target_duration_s: 40, max_duration_s: 60`.
- **Subtítulos SIEMPRE y COMPLETOS**, quemados, de TODO lo que dice el influencer, no solo del pico; nunca inventar texto. Sin voz verificable el clip queda BLOQUEADO (`needs_human_transcription` + `publish_blocked`) hasta transcribirlo de oído.
- **Layout split vertical**: juego arriba, cámara abajo más chica, sin tapar minimapa ni HUD; dos webcams se apilan. Si el juego no se ve, la cámara se achica, nunca al revés.
- **Descargar SOLO la parte del clip**, nunca el vídeo entero; del chat, solo la ventana alrededor del pico (`--peak`, ±120 s).
- **Hype por ráfaga rápida**: se mide la velocidad del chat (mensajes por 10 s / por minuto) y su pico contra el baseline rodante, no solo el total de comentarios. Una ráfaga repentina pesa más.
- **Filtros duros**: viralidad ≥ 50 y SEO ≥ 90 por plataforma, si no no se publica (salvo override del usuario). Prioridad **LIVE sobre VOD** en la Cola. Fórmula intocable: `viralidad^0,5 × SEO^0,3 × chat_norm^0,2`. Clip de más de 7 días: −10 pts/día.
- **Topes diarios por plataforma (anti-ban, `limits.yaml`)**: YouTube plan 3 / máx 5 al día; TikTok, Instagram y Facebook plan 2 / máx 3; cuentas nuevas 1/día; mínimo 2 h entre publicaciones.
- **SIN franja horaria desde 2026-11-18** (decisión del usuario): se publica a cualquier hora; los topes diarios se respetan igual.
- **Publicación automática con puertas**: sin aprobación humana por clip, pero solo si pasa todas las puertas (viralidad, SEO, subtítulos completos, permiso del streamer, lista blanca, topes, preview visto). Lo que no pasa queda en borrador con el motivo visible, nunca se finge una publicación.

### Lo que falta (2026-11-18)

- **Cortador en vivo real en construcción**: hoy no existe; el plan es Fase 0 sombra → Fase 1 semi-manual el 19 nov (detectar el pico en el directo y cortar mientras pasa).
- **TikTok en público automático en curso**: por API directa solo publica en privado sin auditoría (1-4 semanas); hoy va por Zernio en draft-first. La auditoría de TikTok está en borrador (`~/workspace/gta6-clips/independencia/tiktok-auditoria-borrador.md`).
- **Primera publicación real desde la web pendiente**: el flujo de publicar desde el panel ya se probó en seco (llega hasta la última puerta); falta un clip fuerte de Rubius (viralidad ≥ 50, 40-60 s, subtítulos verificados) y las cuentas de YouTube y TikTok en Cuentas.

## Tutorial: cómo usarlo (paso a paso)

¿Te acaban de entregar este repo? Empieza por `CONFIGURACION.md`: ahí están todas las preguntas que Muse te tiene que hacer en un solo mensaje (canal, streamers, cuentas, clips, subtítulos, APIs) antes de configurar nada.

### Qué hace y qué no hace

Hace: detecta los mejores momentos de un directo por el **chat** (risas e hype, no por la transcripción), los corta en vertical `1080x1920`, les pone subtítulos, les genera título + descripción + tags para TikTok, Reels y Shorts, y los deja listos para revisar.

Publica solo según la política (sin humano por clip): un clip se publica solo si pasa todos los cheques (viralidad ≥ 50, SEO ≥ 90 por plataforma, sin rojos del semáforo). Al publicar en YouTube Shorts, el fan-out lo intenta también en TikTok, Instagram Reels y Facebook Reels, cada una con sus propias puertas (ver "Fan-out" más abajo). Lo que no pasa queda retenido o en borrador con el motivo visible, nunca se finge una publicación.

No hace: no detecta los momentos con Gemini: la detección es por el chat, Gemini solo sirve para lo de abajo (visión, opcional).

### 1. Requisitos

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) en el PATH (también `ffprobe`, que viene con FFmpeg)
- TwitchDownloaderCLI en el PATH (solo para Twitch; Kick no lo necesita)
- Git, para clonar el repo

### 2. Instalación

```bash
git clone https://github.com/exe2027/viral-clip-agent.git
cd viral-clip-agent
python3 -m venv .venv
.venv/bin/pip install faster-whisper
```

El venv es obligatorio para los subtítulos: el Python del sistema no sirve para faster-whisper. El resto del pipeline no necesita dependencias de Python.

### 3. Uso rápido: un solo comando

```bash
# Twitch, por ID o URL del VOD
python auto_clips.py 1234567890
python auto_clips.py https://www.twitch.tv/videos/1234567890 --lang es

# Kick, por URL del vídeo
python auto_clips.py https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9 --top-n 5
```

Eso descarga el chat, detecta los picos, corta los clips en vertical, les pone el paquete viral y deja todo en `runs/<vod-id>/` con `manifest.json`, `manifest.md` y `titles.txt`.

Si ya sabes el momento (por ejemplo, lo viste en directo), no hace falta bajar el chat entero ni detectar picos: `--peak` baja solo el chat de la ventana alrededor de ese momento y corta ese clip. Regla del usuario 2026-11-17 23:00: del chat, solo la ventana alrededor del momento del clip, nunca el chat entero (el vídeo ya era solo el segmento por pico).

```bash
# Solo el momento 01h40m50s: chat en [5930s, 6170s] (±120 s, config clips.chat_window_s)
python auto_clips.py 2929847999 --peak 6050 --lang es
python auto_clips.py 2929847999 --peak 1:40:50 --chat-window 180 --dry-run
```

El chat de ventana queda en `runs/<vod-id>/chat_w<inicio>-<fin>.json` y el manifiesto registra `chat.scope: "window"` (la mediana y el múltiplo viral son locales a la ventana). Sin `--peak`, descubrir picos desde cero sigue necesitando el chat completo.

Antes de gastar tiempo y disco, prueba sin cortar nada:

```bash
# Solo ver los picos, sin bajar el vídeo ni cortar
python auto_clips.py 1234567890 --dry-run

# Probar con los archivos de ejemplo que trae el repo (sin Twitch ni Kick)
python auto_clips.py --chat test_chat.json --video test_vod.mp4 --out runs/prueba
```

Y si ya tienes el chat y el vídeo descargados, el modo manual es:

```bash
# 1. Ver los picos
python clips_chat_v2.py chat.json --dry-run
# 2. Cortar los 5 mejores
python clips_chat_v2.py chat.json --video vod.mp4 --out clips
```

### 4. Revisar antes de publicar (obligatorio)

1. Abre `runs/<vod-id>/manifest.md` y mira los clips, sus títulos y su puntaje.
2. Mira los 3 fotogramas de cada clip en `runs/<vod-id>/clips/thumbnails/` antes de elegir título y portada.
3. Si el título promete algo que no se ve en el clip (una jugada, un juego), no lo uses: el check anti-clickbait de `viral_package.py` lo rechaza y lo degrada a un título de reacción.
4. Sube a mano solo lo que quedó en borrador (ver "Fan-out" más abajo): si una plataforma no tiene token, auditoría o URL pública, el paquete manual está en `drafts/<clip>_<plataforma>/`.

### Fan-out: apenas se publica en YouTube, también en las otras 3

Cuando un clip se publica con éxito en YouTube Shorts, el bucle lo encola automáticamente en TikTok, Instagram Reels y Facebook Reels con el mismo clip y su paquete por plataforma (título, descripción y tags propios de cada una). Cada plataforma pasa por sus propias puertas: puntaje por plataforma (viralidad ≥ 50, SEO ≥ 90, SEO < 70 bloqueo duro), cuota y ritmo de `limits.yaml`, circuit breaker, lista blanca en el momento de publicar y pausas. Si una falla o no tiene credenciales, queda en **draft con el motivo visible** (en la cola y en el panel local) y las demás siguen; el fallo de una nunca revierte el publicado de YouTube ni de las otras. Se apaga con `fanout.enabled: false` en `config.yaml`.

| Plataforma | Cómo sube | Qué falta para que publique sola |
|---|---|---|
| YouTube Shorts | Data API v3 (`videos.insert`) | OAuth (`client_secrets.json` + token en `tokens/`) |
| TikTok | Content Posting API, Direct Post con subida de archivo por chunks (sin URL pública) | Token con scope `video.publish` + **app auditada** (sin auditoría, TikTok solo deja publicar en privado y el sistema cae a draft) |
| Instagram Reels | Graph API: contenedor → esperar FINISHED → publicar | Token + cuenta Profesional + `ig_user_id` (`platform_accounts.instagram_reels.ig_user_id` o `IG_USER_ID`) + **URL HTTPS pública del video** (`public_url.base_url`) |
| Facebook Reels | Graph API: start → subida por chunks a `rupload.facebook.com` → finish | Page access token + `page_id` (`platform_accounts.facebook_reels.page_id` o `FACEBOOK_PAGE_ID`) |

**URL pública para Instagram.** Instagram no acepta el archivo local por API: Meta descarga el video desde una URL HTTPS pública. Este repo **no sube nada a ningún hosting**: tú sirves el directorio de clips en un hosting estático con HTTPS y configuras la base en `config.yaml > public_url.base_url` (o `CLIP_PUBLIC_BASE_URL` en `.env`). La URL final es base + ruta relativa del clip en el repo (ej: `https://clips.tudominio.com/runs/v1/clips/c1.mp4`). Sin base configurada, Instagram queda en draft con el motivo "sin URL pública configurada".

**Reintentos.** Un fallo transitorio (5xx, rate limit) se reintenta en el siguiente ciclo del bucle, hasta 3 veces; después el ítem va a "failed" para revisión humana. Un draft por falta de credenciales, auditoría o configuración **no** se reintenta a ciegas: queda en draft hasta que lo resuelvas (ver `clipagent doctor`). Si TikTok o Instagram siguen procesando el video, el bucle retoma la misma publicación en el siguiente ciclo sin volver a subir (se guarda el `publish_id` / `container_id`).

**Dónde se ve.** En el panel local (`clipagent panel`): la tabla "Borradores (draft)" muestra plataforma, clip y motivo; "Últimos publicados" muestra lo publicado por plataforma. En la cola (`publish_queue.json`) cada ítem guarda su historial con motivos.

Con cola y aprobación, el flujo es el mismo pero ordenado:

```bash
python jobqueue.py add https://kick.com/coker/videos/<uuid> --lang es
python jobqueue.py list
python worker.py --once        # procesa un job y lo deja en ready_for_review
python jobqueue.py approve 3      # o: python jobqueue.py reject 3
```

### 5. Configuración

Todo lo del canal vive en `config.yaml`, no en el código: juego, streamer, hashtags ES/EN, duración del corte, estilos de subtítulo (`tiktok`, `highlight`, `karaoke`) y zonas seguras del HUD. Edítalo antes de tu primera corrida y no subas tus datos personales al repo.

### 6. API de Gemini (opcional, solo para la visión)

**Para qué es.** El texto ya genera los títulos, pero el texto no ve la pantalla. Gemini entra solo como veto barato: cuando un título promete algo visible (una jugada, un tiro, un juego), revisa 3 fotogramas del clip con Gemini Flash-Lite y dice si eso se ve o no. Si no se ve, se prueba otro título, y si no queda ninguno, se baja a un título seguro de reacción ("el chat no paraba de reírse"). La detección del momento sigue siendo por el chat, nunca por Gemini.

Ejemplo real: en el clip de Coker el mejor título solo-texto hablaba de errar un tiro, pero en pantalla solo había dos webcams riéndose, sin gameplay. Eso solo lo caza la visión.

**La visión también elige la miniatura.** `vision.py` + `thumbnails.py --vision` mandan los mismos 3 fotogramas (antes del payoff, payoff, después) y Gemini ordena cuál va mejor con el título. La portada pasa a ser ese frame; sin key o si la API falla, se cae al fotograma del payoff. Probado de verdad el 2026-11-16 con los 3 frames de Coker: Gemini eligió el frame 2 (payoff, 20 s), ranking [2, 1, 3].

```bash
# Elegir el mejor frame con Gemini (apagado por defecto)
python thumbnails.py runs/kick-coker-01a1dffd/clips/clip_01_t03h14m50s_s56.5_r14h1.mp4 \
  --payoff 20 --out-dir runs/kick-coker-01a1dffd/clips/thumbnails \
  --cover --vision --title "GTA V RP: Errar un tiro y el chat explotó"
# Guarda vision.json con el ranking y el motivo, y cover_instagram.jpg con el frame elegido
```

**Modelo.** `gemini-3.5-flash-lite`. Ojo: los modelos 2.5 ya no están disponibles para usuarios nuevos (la API devuelve 404 "no longer available"), por eso el código usa 3.5.

**Cuánto cuesta.** 1 llamada por clip con Gemini Flash-Lite. La capa gratuita da 500 peticiones al día y nosotros usaríamos unas 20, así que en la práctica no se paga nada.

**Cómo crear la key gratis, sin tarjeta.** Tener Gemini Pro (la app) no te da una API key sola: son dos cosas distintas y la key hay que crearla aparte, en Google AI Studio, con la misma cuenta de Google:

1. Entra a https://aistudio.google.com
2. Arriba a la derecha, **Get API key**
3. **Create API key in new project**

Listo, ahí te da la key. **No toques "Set up billing", "Configurar facturación" ni "Upgrade / Subir de nivel": esa es la pantalla que pide la tarjeta de crédito y no es la de la key gratis.** Si te pide tarjeta es porque estás en esa pantalla, sal de ahí y vuelve al paso 2.

**Dónde guardarla.** En un archivo `.env` en la raíz del proyecto (nunca en el README, nunca en el chat, nunca subida a GitHub: `.env` ya está en el `.gitignore`):

```bash
GEMINI_API_KEY=tu_key_aqui
```

**Estado actual (2026-11-18).** La key ya se creó en AI Studio, gratis y sin tarjeta ni cuenta de facturación, y quedó verificada con una llamada real a `gemini-3.5-flash-lite` y guardada como secreto de GitHub (entorno `sebastian`) y en `.env`. El módulo de visión para miniaturas ya está implementado (`vision.py`, `thumbnails.py --vision`): hoy la app funciona 100% sin ella, con títulos por texto + clasificador local de layout, y la visión queda apagada por defecto (`vision_skipped`). **El veto de títulos con visión todavía no está cableado en `auto_clips.py`.** Guarda la key en el `.env` para usar `--vision`; no hace falta para usar la app hoy.

### 7. Aviso

Bajar el VOD de otro streamer y republicarlo puede infringir las normas de Twitch/Kick y el copyright del creador o del juego. Úsalo solo con streamers de la lista blanca en `allowed_streamers.yaml` (estar en la lista = permiso ya dado).

## Qué cambió en la v2.4

**Scoring corregido:**
- `xd` / `xdd` por fin puntúan como risa.
- Falsos positivos eliminados: "no puedo creer que no hayas ido", "qué bueno que llegaste", "dale gas", "vamos a ver qué pasa", "god of war" y "crazy cat" ya no puntúan (tokens sueltos ambiguos fuera; solo frases completas y formas alargadas: "vamooos", "dalee", "noooo").
- Rendimiento: un tokenizado por mensaje + conjuntos en vez de regex por token. Medido en 200k mensajes: score_message 1,28 s, score_windows 1,81 s (antes 9,6 s).
- Ventana deslizante de 10 s con paso de 5 s y suavizado (0.25/0.5/0.25): un pico entre dos ventanas ya no se parte.
- Mediana sin sesgo: se calcula sobre TODAS las ventanas, incluidas las vacías; `viral_package.viral_multiple` por fin usa el parámetro `median_score` que antes ignoraba.
- Usuarios únicos por ventana (`users` en el detalle), mensajes repetidos del mismo usuario solo cuentan una vez, bots conocidos (Nightbot, StreamElements...) no puntúan.
- Manifiesto incremental: `clips_chat_v2.py` escribe `manifest.json` tras cada clip cortado.
- Flags ffmpeg para plataformas en el corte: `-pix_fmt yuv420p`, `-movflags +faststart`, audio normalizado con `loudnorm` (-16 LUFS).

**Config del canal — `config.yaml`:** juego, streamer, nicho, hashtags ES/EN, parámetros de corte y estilos de subtítulo viven en `config.yaml`, no hardcodeados. `config.py` lo carga (PyYAML si está, si no un parser mínimo propio). Los hashtags ya no están fijos en el código.

**Estilos de subtítulo elegibles:** `--subtitle-style` en `subtitles.py`, con 3 estilos definidos en `config.yaml`:
- `tiktok` (por defecto): blanco, negrita, borde negro, abajo.
- `highlight`: amarillo con caja semitransparente detrás.
- `karaoke`: palabra por palabra (bloques de 1-2 palabras, amarillo, fuente 56). Aproximación: no es karaoke `\kf` real de ASS, el SRT no lleva tiempos por palabra al quemar; se logra con bloques cortos (max_chars=8, max_duration=1.0 s). Para que el karaoke funcione, el SRT debe generarse con ese estilo (los SRT viejos llevan bloques de 20 caracteres).

```bash
.venv/bin/python subtitles.py clip.mp4 --subtitle-style highlight
.venv/bin/python subtitles.py clip.mp4 --subtitle-style karaoke
```

## Qué cambió en la v2.5 (detección)

**Baseline local + z-score.** Cada ventana ya no se compara con todo el VOD sino con su vecindad local (±12 min, `--local-half-min`). Score final = z-score local + 0.5 × pendiente de subida (media de los 60 s previos, cap 2) + magnitud (puntos ajustados / 10). La desviación tiene suelo 1.0 para que en tramos muertos un soplo no dé z=50; con VOD muy corto (<5 vecinos) se usa la media/desviación global. El z-score solo premiaba ráfagas pequeñas en tramos muertos (una ventana de 29 pts en una zona callada superaba al pico real de 67 pts); el término de magnitud mantiene el tamaño absoluto en el ranking. `--local-baseline` es el comportamiento por defecto (`--no-local-baseline` lo apaga y puntúa por puntos ajustados).

**Anti-spam v2.** Por ventana se calculan 3 ratios, que quedan en el detalle y en el manifiesto (`emote_ratio`, `rep_ratio`, `lex_diversity`, `penalty_factor`, `motivo_penalizacion`):
- **emote-only**: mensajes que son solo emotes (`[emote:id:nombre]`, solo emojis, o un solo token de nombre de emote conocido: KEKW, LUL, OMEGALUL, Kappa, ...). >60% → puntaje ×0.25, motivo `emote_spam`.
- **repetición**: 1 − cuerpos distintos / mensajes, sobre TODOS los mensajes aunque haya muchos usuarios únicos. >40% → ×0.5, motivo `repetition_spam`. Ambas → ×0.125.
- **diversidad léxica**: palabras distintas / palabras totales en los mensajes con texto. Se registra, no penaliza sola.

Un mismo emote repetido por 14 personas (KEKW×14) también es baja diversidad, pero con ~29% no llega al umbral del 40% y no se penaliza. `--no-anti-spam` apaga el anti-spam. La puntuación vieja (v2.4, puntos suavizados sin baseline ni anti-spam) se reproduce con `score_windows(..., local_baseline=False, anti_spam=False)` o con `--no-local-baseline --no-anti-spam`.

**Prueba de aceptación (2026-11-16, 3 chats reales, top-5 por puntaje):**

| VOD | Pico | Viejo (v2.4) | Nuevo (v2.5) | Veredicto |
|---|---|---|---|---|
| Locones (Kick, 2.517 msgs) | 03h08m25s | #1, 24.75 pts | fuera del top-5 (rank #39, z 3.84, raw 24.75 → adj 3.09, emote 96%, rep 52%, `emote_spam` + `repetition_spam`) | ✅ baja claramente |
| Coker (Kick, 8.269 msgs) | 03h15m10s | #1, 56.50 pts | #3, z 9.02 (raw 56.50, sin penalizar: emote 26%, rep 29%) | ✅ se mantiene arriba |
| Nora (Twitch 2929669422, 19.033 msgs) | 07h12m10s | #1, 67.75 pts | #3, z 12.59 (raw 67.75, sin penalizar: emote 26%, rep 31%) | ✅ se mantiene arriba |

Top-5 nuevo completo: Locones 03h10m45s (z 8.99), 12m15s (8.65), 04h45m35s (8.65), 01h47m55s (8.05), 01h45m50s (6.71) — VOD poco denso, los picos nuevos son ráfagas pequeñas; Coker 33m30s (9.53), 17m45s (9.15), 03h15m10s (9.02), 01h24m25s (8.92), 19m05s (8.33); Nora 06h04m55s (13.39), 01h37m35s (13.10), 07h12m10s (12.59), 03h53m40s (11.21), 06h47m45s (10.51).

**Limitación conocida:** `auto_clips.py` pasa los scores (ahora z) a `viral_package.viral_multiple` como `all_scores`; con mediana de z ≤ 0, `viral_multiple` devuelve el propio score, así que el "× la mediana" que imprime `auto_clips.py` en una corrida normal es en realidad el z-score. La detección, el corte y el manifiesto no se ven afectados. Queda para el bloque que toque `viral_package.py`.

## Qué cambió en la v2.6 (audio + recorte preciso)

**Audio como segunda señal (`audio.py`).** El chat es la señal principal, pero un pico de chat con el audio plano suele ser spam o música de fondo. Se mide el RMS por segundo (ffmpeg a WAV 16 kHz mono + numpy, sin librosa) solo en los top 10-15 candidatos, sobre el segmento ya descargado. Regla: chat alto + audio alto/dinámico = **confirmar**; chat alto + audio plano = **sospechar** (se corta igual, se revisa a mano antes de publicar). La transcripción es un bonus de scoring (+20% si hay risa/hype cerca del pico, +10% si hay voz, 0% si no hay nada), nunca un veto. Umbrales calibrados con los 3 VODs conocidos: sospechar si la transcripción tiene 0 palabras en 60 s, o si el audio es plano (std del RMS < 9 dB y ≥45% de segundos estables a ±2 dB de la mediana). OJO: el nivel medio NO discrimina — Locones es incluso más fuerte que Coker en media (-23,1 dBFS frente a -32,5 dBFS); lo que discrimina es la dinámica + la voz.

| Candidato | Chat | RMS medio | std | Rango | Estables | Palabras | Bonus | Veredicto |
|---|---|---|---|---|---|---|---|---|
| Locones 03h08m | 24,75 pts | -23,1 dBFS | 7,4 dB | 28,9 dB | 47% | 0 | +0% | **SOSPECHAR** (sin voz, cama constante) |
| Coker 03h15m | 56,5 pts | -32,5 dBFS | 11,5 dB | 35,3 dB | 15% | 77 | +10% | **CONFIRMAR** |
| NoraExplorer 7h12m | 78 pts | -42,3 dBFS | 19,8 dB | 86,5 dB | 36% | 152 | +10% | **CONFIRMAR** |

```bash
.venv/bin/python audio.py runs/kick-coker-01a1dffd/segments/seg_01_t03h14m50s.mp4 --peak 20 --chat-score 56.5 --transcript runs/kick-coker-01a1dffd/clips/clip_01_transcript.json
```

**100% local y gratis ($0) — decisión del usuario (2026-11-16).** Todo lo de este bloque corre en local, sin keys ni cuentas de pago: transcripción con faster-whisper (modelos `tiny` y `base` de Systran, descarga gratuita de HuggingFace, sin token ni cuenta), VAD incluido en faster-whisper (modelo Silero, también local) y diccionario de risa/hype por reglas en `clips_chat_v2.py`. El escalado tiny→base es solo entre modelos locales, nunca a una API. NO se usa la Whisper API de OpenAI ni ninguna API paga, y NO se usa Gemini: al crear la key Gemini pidió tarjeta, así que no se pone tarjeta ni se crea cuenta de facturación. No se pide ninguna key de pago para este bloque.

**Recorte preciso por frase/silencio (`precise_cut.py`).** El corte fijo (pico -20 s / +40 s) corta frases por la mitad. Ahora el inicio se afina a 2-3 s antes del disparador retrocediendo hasta el inicio de la frase que lo contiene, y el fin hasta el fin de esa frase/risa +1-2 s, con tope de 60 s. Fuentes: timestamps por palabra de faster-whisper (los que ya genera `subtitles.py`, reutilizados con `--transcript` o transcribiendo el segmento con el modelo tiny) + silencios detectados con ffmpeg `silencedetect` (para no empezar dentro de un silencio ni cortar dentro de una palabra). Solo trabaja dentro del segmento ya descargado, nunca baja más vídeo. Sin transcripción fiable (< 3 palabras) cae al corte fijo actual. El corte en sí lo hace `layout.py` (regla del proyecto) o el recorte centrado si no está.

```bash
.venv/bin/python precise_cut.py runs/kick-coker-01a1dffd/segments/seg_01_t03h14m50s.mp4 \
    --peak 11710 --segment-start 11690 \
    --transcript runs/kick-coker-01a1dffd/clips/clip_01_transcript.json \
    --out runs/kick-coker-01a1dffd/clips/clip_01_precise.mp4
# Coker: 17,36 s -> 24,98 s (7,62 s, 1080x1920, layout auto), inicio en fin de
# silencio y fin en fin de frase, sin cortar palabras por la mitad.
# Locones sin transcripción (--no-transcribe): cae al fijo 0,0 s -> 40,0 s.
# NoraExplorer 7h12m (segmento desde 25910, pico 25930): 5,28 s -> 25,34 s (20,07 s).
```

**Integración en `auto_clips.py`** (solo flags, sin tocar la detección ni el corte fijo por defecto):
- `--audio-check`: RMS + veredicto por candidato (top-N), bonus de transcripción, y `audio` en el manifiesto (`decision`, `reason`, `stats`, `words`, `transcription_bonus`). Un sospechoso se corta igual y queda marcado para revisión manual.
- `--precise-cut`: afina inicio/fin por clip con `precise_cut.py` (transcribe la ventana de 60 s del candidato: el segmento en Kick, una ventana temporal en Twitch) y guarda `precise_cut` en el manifiesto; el archivo lleva sufijo `_precise`.

```bash
python auto_clips.py https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9 --top-n 5 --audio-check --precise-cut
```

## Señales de Uri (2026-11-17): heatmap + movimiento + voz del juego

Tres ideas de `clipper-directos` de Uri Cortina, integradas como
**diagnóstico en el manifiesto, no como componentes de
viralidad**. La fórmula de viralidad (7 componentes), la de
prioridad, los filtros duros y el ritmo anti-ban NO cambiaron
(`test_uri_signals.py::TestSignalsIntegration::test_viral_score_unchanged_by_uri_signals`
lo prueba con el fixture Coker). $0: sin Mac, sin Claude Code de
pago, sin Groq, sin dependencias nuevas (ffmpeg + yt-dlp si está).

- **Heatmap de YouTube** (`clipagent/heatmap.py`): una lectura de
  `yt-dlp -J` por VOD, buckets de 10 s normalizados 0–1, top 15
  picos con ≥ 60 s entre sí (igual que `hype.py` de Uri). Un pico
  de chat solo se confirma con heatmap si hay un pico a ≤ 30 s
  (`signals.heatmap_peak_s`, `signals.heatmap_score`). Sin yt-dlp,
  sin heatmap en ese VOD o VOD de Twitch/Kick: `heatmap_available:
  False` con el motivo, el pipeline sigue solo con el chat.
  `--no-heatmap` lo apaga; `config.yaml` `signals.heatmap.*`.
- **% de juego en movimiento** (`clipagent/motion.py`): 1 fps,
  frames grises 160×90 en una pasada de ffmpeg (reutiliza
  `render.extract_gray_frames`), diferencia media entre frames
  consecutivos, umbral 3,0 (el `GAME_MOV` de Uri). `motion_pct` =
  % de segundos en movimiento. 0–10% = vídeo en pausa/pantalla
  fija (criterio de Uri): solo vale si la reacción es muy fuerte
  (viralidad ≥ 85, `pausa_pero_reaccion_fuerte`); si no,
  `pausa_pantalla_fija` = revisar a mano, no se publica a ciegas.
  Es un flag, no descarta ni bloquea nada por sí solo.
  `--no-motion` lo apaga; `config.yaml` `signals.motion.*`.
- **Voz del juego** (`clipagent/game_voice.py`): el streamer calla
  ≥ 2 s mientras el audio sigue activo (RMS > −45 dBFS) y el juego
  se mueve: tramo estimado de voz del juego, sin texto inventado
  (las palabras del juego no se transcriben, solo se marca el
  tramo). En el corte, esos tramos pasan a layout `game` (juego a
  pantalla completa, mismo recorte 9:16 centrado) y vuelven al
  layout anterior al terminar; los tramos < 1,5 s se fusionan con
  el vecino. En los subtítulos, estilo `Game` (cursiva, palabra
  activa azul #7FE3FF, etiqueta `JUEGO:`) y esos tramos quedan
  fuera de la comprobación final contra la transcripción del
  streamer (como Uri: un desacuerdo largo es el juego hablando,
  nunca un error a cortar). El manifiesto guarda `game_voice` por
  clip (`[{start, end, estimated, source}]`, `game_lang` opcional)
  y `manifest.md` lo resume. `--no-game-voice` lo apaga;
  `config.yaml` `signals.game_voice.*`.

Ejemplo en `manifest.json` (por clip):

```json
{
  "signals": {
    "heatmap_peak_s": 1200, "heatmap_score": 1.0,
    "heatmap_available": true,
    "motion_pct": 80.0, "motion_flag": "en_movimiento",
    "game_voice": [{"start": 5.0, "end": 8.0, "estimated": true,
                    "source": "estimada"}],
    "game_voice_estimated": true
  },
  "game_voice": [{"start": 5.0, "end": 8.0, "estimated": true,
                  "source": "estimada"}],
  "motion": {"available": true, "motion_pct": 80.0, "seconds": 40,
             "threshold": 3.0, "reason": "juego en movimiento"}
}
```


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
- TwitchDownloaderCLI en el PATH (solo para Twitch)
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

## Subtítulos quemados — v2.2

`subtitles.py` transcribe el clip con faster-whisper (modelo `tiny`, idioma automático ES/EN),
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

Qué hace y qué no hace:
- Hace: transcripción con faster-whisper y `word_timestamps`,
  agrupación de palabras en SRT, quemado con el filtro `subtitles`
  de ffmpeg y estilo ASS.
- Modelo `tiny` en vez de `base` (más rápido en CPU),
  idioma automático, sin cv2 (la duración se mide con ffprobe),
  estilo adaptado a 1080x1920.
- NO hace: detección de momentos por transcripción con Gemini (la detección es por el chat), recorte con seguimiento de cara (pesado y pensado para vídeos con una persona hablando a cámara; en gameplay de GTA no hay cara que seguir y lo que importa es el HUD/minimapa, por eso se mantiene `--crop center/left/right`), publicación automática, actores UGC ni doblaje (de pago).
- Detalle técnico: faster-whisper 1.2.1 falla con PyAV 19
  (`metadata_errors` ya no existe en `av.open`), y `av` 14 no tiene
  wheel para Python 3.12. Por eso `subtitles.py` extrae el audio a
  WAV 16 kHz con ffmpeg, lo lee con el módulo `wave` de la stdlib y
  lo pasa a faster-whisper como array numpy, evitando PyAV.

Limitación conocida: el modelo `tiny` se equivoca en nombres propios
y en frases rápidas. En la prueba real transcribió "Bezo" varias
veces (nombre/emote que no reconoce) y algunas frases quedaron con
gramática rota ("And I was post on discord"). Revisa el SRT antes de
publicar; el diccionario GTA 6 está implementado en v2.6 (ver abajo): `initial_prompt` + correcciones
deterministas post-SRT en `config.yaml`.

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
  (03h08m) se probó con el segmento del pico de 14m05s del mismo VOD
  (corrección v2.6: el clip de 03h08m SÍ tiene ~11 s de voz en
  rumano al inicio, ver abajo):
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

## Subtítulos — v2.6: VAD, anti-alucinación, puerta "sin voz", escalado tiny→base, idioma por tramos, diccionario GTA 6

En `subtitles.py` + `config.yaml`
(sección `subtitles`). Nada hardcodeado, todo configurable:

- **VAD + anti-alucinación:** faster-whisper con `vad_filter=True`
  (Silero VAD, incluido en faster-whisper) y umbrales
  `no_speech_threshold` 0,6, `compression_ratio_threshold` 2,4,
  `logprob_threshold` -1,0, `condition_on_previous_text=False`. El
  ruido de juego sin voz se descarta antes de transcribir; sin
  contexto previo no se encadenan alucinaciones de un segmento al
  siguiente.
- **Puerta "sin voz":** si el clip tiene <5 palabras por cada 60 s o
  la voz cubre <10% de la duración, NO se genera SRT ni se quema
  nada: el clip queda marcado como "solo gameplay/música" en el
  transcript JSON (`no_speech: true`,
  `audio_note: "solo gameplay/música"`) y el proceso termina con
  código 0, sin fallar. El clip sigue su camino sin subtítulos.
  Pendiente de conectar: que `auto_clips.py` / `viral_package.py`
  lean ese campo y lo reflejen en el manifiesto.
- **Escalado condicional tiny → base:** SOLO si salta un umbral
  objetivo — repetición de la misma palabra/frase ≥3 veces,
  <8 palabras/min con voz, logprob medio < -0,8, idioma con prob.
  <0,5. Nunca base por defecto: tiny es varias veces más rápido en
  CPU. El motivo queda en `transcript["escalation_reasons"]` y el
  modelo final en `transcript["model"]`.
- **Idioma por tramos:** se detecta en trozos de 30 s
  (`language_chunk_s`) y se vota por mayoría; el empate lo desempata
  el idioma mayoritario del chat (`--chat-lang` o
  `subtitles.chat_language`) y si no el de mayor probabilidad media;
  sin votos, el fallback de config (idioma del streamer o del
  canal). Siempre se guardan `source_language`, probabilidad y
  `task`, más los votos en `language_votes`.
- **Diccionario GTA 6 (~25 términos, `gta6_terms`):** va como
  `initial_prompt` a Whisper y como correcciones deterministas
  post-SRT (`gta6_corrections`: solo se toca lo que está en el mapa,
  por palabra/frase completa). El número de correcciones aplicadas
  se informa al generar el SRT.
- Flags nuevos: `--chat-lang es|en`, `--no-vad`, `--no-escalate`.

**100% local y gratis ($0) — decisión del usuario (2026-11-16).**
Todo este bloque corre en local, sin keys ni cuentas de pago:
faster-whisper `tiny`/`base` (modelos de Systran, descarga gratuita
de HuggingFace, sin token ni cuenta), VAD Silero local y ffmpeg. El
escalado tiny→base es solo entre modelos locales, nunca a una API.
NO se usa la Whisper API de OpenAI ni ninguna API paga, y NO se usa
Gemini: al crear la key Gemini pidió tarjeta, así que no se pone
tarjeta ni se crea cuenta de facturación. No se pide ninguna key de
pago para este bloque.

Detalle técnico (2026-11-17): la cola de aprobación se llamaba
`queue.py` y hacía sombra al módulo `queue` de la stdlib cuando
faster-whisper descarga el modelo VAD (concurrent.futures hace
`import queue` y falla con AttributeError). Se renombró a
`jobqueue.py` y se eliminaron los parches que cargaban la stdlib
por ruta explícita, así que ya no hay sombra ni parche.

Pruebas reales de aceptación (2026-11-16, `.venv`, modelo tiny con
escalado a base cuando saltó un umbral; SRT en
`runs/.../clips/*_v26.srt`, transcript en `*_v26_transcript.json`):
- **Español con voz (Kick, Coker, clip 03h14m50s):** detectado `es`
  (prob. 0,77, votos 2/2) → `task=transcribe`, subtítulos en
  español, sin traducir. Tiny: 100 palabras, 42% de habla, logprob
  medio -1,06 → escaló por repetición ("no" x4 seguidas) y logprob
  bajo. Base: 93 palabras, 11 segmentos, 31 bloques SRT, habla 56%,
  logprob -0,68, idioma re-detectado `es` 0,98. SRT en español, sin
  alucinaciones graves (algunas palabras sueltas con ruido de
  juego: "fombra y sovordo", "arrar"). Correcciones GTA 6: 0.
- **Rumano con poca voz (Kick, Locones, clip 03h08m05s):**
  corrección a v2.5: el clip SÍ tiene voz, ~11 s de rumano al
  inicio, y el resto (~48 s) es solo audio del juego. Tiny: `ro`
  0,44 (voto empatado 1-1, desempate por probabilidad) → escaló por
  logprob -1,68 e idioma poco fiable. Base: 30 palabras, 1 segmento
  (0–11,3 s), habla 13%, logprob -1,69, traducción a inglés
  coherente ("You'll see, if you can see it..."). **0 palabras
  inventadas en los ~48 s de solo gameplay.** La puerta "sin voz"
  NO se activó aquí porque sí hay voz (30 palabras ≥ 5, 13% ≥ 10%).
  Una primera pasada con base dio 47 palabras con el mismo
  contenido: Whisper varía la redacción en audio ruidoso, pero el
  contenido coincide.
- **Inglés (Twitch, peereira7/NoraExplorer, clip 7h12m):**
  detectado `en` (prob. 0,96, votos 0,99/0,92, sin empate) →
  `task=transcribe`, 150 palabras, 7 segmentos, 43 bloques SRT,
  habla 76%, logprob -0,31, sin escalado (ningún umbral saltó).
  SRT razonable ("It was a good stream... I'll see you guys on the
  19th"). "Bezo" sigue apareciendo (límite conocido de tiny).
- **Puerta "sin voz" (prueba directa):** recorte de 30 s del clip
  de Locones (20–50 s, solo audio del juego, sin voz). Tiny
  alucinó 16 palabras ("hype" x8 seguidas, logprob -1,03) → escaló
  a base, que lo redujo a 7 palabras y 7% de habla → puerta
  activada por cobertura (<10%): `no_speech: true`,
  `audio_note: "solo gameplay/música"`, **sin SRT, sin quemado,
  código de salida 0**. El VAD + los umbrales reducen las
  alucinaciones pero no las eliminan en audio de juego puro; la
  puerta es la red de seguridad.

Limitación conocida: el escalado se detiene en `base` (no hay
`small`/`medium` por defecto) y en audio muy ruidoso el logprob
medio de base sigue bajo (-1,69 en Locones); el resultado se
guarda igual y se revisa el SRT antes de publicar.

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

**Clasificación 100% local, sin visión por API — decisión del usuario
(2026-11-16).** La clasificación de layout es local: solo stdlib +
ffmpeg (muestreo de fotogramas en gris, costura vertical, caja en
esquina, estabilidad por diferencia de frames) y, si está disponible,
detección de rostros con OpenCV Haar local. No hay ninguna
clasificación de frames con Gemini Vision ni con ninguna API paga en
el pipeline: la opción de visión por API queda APAGADA por defecto
(`vision_skipped`), sin key, sin tarjeta y sin cuenta de
facturación. Todo el pipeline funciona 100% gratis: faster-whisper
tiny local, ffmpeg, Twitch/Kick gratis.

La decisión queda registrada en el manifiesto por clip (`layout` en
`manifest.json`, línea `Layout:` en `manifest.md`).

```bash
# Forzar un layout en un clip
python auto_clips.py <vod> --layout stack
python auto_clips.py <vod> --layout pip
```

### Escaneo por tramo, zonas seguras, fondo borroso, hoja de contacto

- **Escaneo por tramo** (`scan_segments` en `layout.py`): además de
  clasificar cada fotograma, muestrea 1 fotograma cada 2 s y compara
  fotogramas consecutivos por celdas de 10x10 px. Una webcam es una
  región *estable* (su fondo no cambia mientras el gameplay cambia en
  todas partes), así que cada tramo se clasifica en 0/1/2 cams y se
  marca si la cam es un avatar circular (esquinas inestables, centro
  estable). La detección se restringe a regiones estables para no
  confundir caras del juego con webcams. Ojo: una imagen estática
  (menú de pausa, carga) también es estable en ambas mitades; el
  discriminador es la costura vertical fuerte justo en el centro
  (el divisor entre las dos cams). Sin costura, el escaneo lo
  reporta con la advertencia y `decide_layout` no lo usa para
  corregir a stack (Locones 0-7 s: 67-72% estables sin costura).
  Si hay OpenCV disponible,
  los rostros detectados solo cuentan si caen dentro de una región
  estable; sin OpenCV (caso actual) la diferencia de frames es el
  método principal. `decide_layout` usa el escaneo para confirmar o
  corregir la clasificación: 2 cams persistentes sin franja apilada
  → stack; 1 cam persistente sin caja → PiP con la caja estable.
- **Vtuber / PNG tuber** (`_detect_vtuber_candidates`, 2026-11-17,
  VOD Rubius 2931565552): un avatar reactivo (anime, sin cara
  humana) no lo ve Haar y no tiene borde de caja superior, así que
  caía a center y se perdía del recorte. Heurística temporal sobre
  los fotogramas del escaneo: un parche pequeño en una esquina que
  cambia fuerte (boca que se abre/cierra, parpadeo) mientras el
  resto de su caja no se mueve y el avatar persiste en una racha
  larga de muestras. Una alerta transitoria (aparece, se anima y
  desaparece en 2-3 muestras) no pasa la racha. La caja va a pip y
  luego a split como una webcam normal; si la caja cambia dentro
  del clip (gato abajo-izquierda y luego webcam humana
  arriba-izquierda), los tramos se parten por caja. Sin parche que
  cumpla todo, cae a center como antes. Tests en `test_vtuber.py`.
- **Zonas seguras por juego**: GTA V tiene el minimapa
  abajo-izquierda y el HUD de salud abajo-derecha; el PiP nunca va
  abajo ni tapa esas zonas (si la esquina pedida solapa una zona, se
  prueba la otra superior). GTA 6 no tiene mapa confirmado el día 1:
  el fallback asume la misma convención Rockstar, la completa con
  esquinas persistentes detectadas por el escaneo (un HUD es
  persistente por definición) y se puede fijar a mano con
  `safe_zones` en `config.yaml` (manda sobre todo). El mapa
  definitivo de GTA 6 se fija después de los primeros clips reales.
- **Fondo borroso al apilar**: cada mitad se recorta completa (antes
  se recortaba al centro 9:8 de cada mitad, lo que podía cortar caras
  por los lados) y se ajusta por contenido sobre su propio frame
  difuminado (`boxblur`, radio configurable `blur_radius`), en vez de
  barras negras.
- **Hoja de contacto** (`contact_sheet` en `layout.py`): 9 fotogramas
  del clip en una imagen 3x3, cada uno con el rectángulo de recorte
  que el layout usará en ese instante (verde) y las zonas seguras
  (rojo). Para aprobar un clip, mirar 1 imagen basta.
- **100% local, $0 (decisión del usuario, 2026-11-16):** este bloque
  no usa ninguna API paga ni pide ninguna key: solo Python stdlib +
  ffmpeg local (y OpenCV opcional si está instalado, también local
  y sin red). Nada de Gemini, OpenAI, DeepSeek ni ElevenLabs: Gemini
  pidió tarjeta al crear la key, así que no se pone tarjeta ni se
  crea cuenta de facturación. La transcripción con faster-whisper
  tiny (modelo local) vive en `subtitles.py`, no aquí; `layout.py`
  no transcribe nada y no sale a la red.

```bash
# Escaneo por tramo + hoja de contacto de un segmento
python -c "
from layout import scan_segments, decide_layout, contact_sheet
seg = 'runs/kick-coker-01a1dffd/segments/seg_01_t03h14m50s.mp4'
print(scan_segments(seg, 60))
d = decide_layout(seg, 60)
contact_sheet(seg, 'contacto-coker.jpg', decision=d)
"
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
| `--local-baseline` | on | Baseline local ±12 min con z-score + subida (default: activado) |
| `--no-local-baseline` | — | Apaga el baseline local: puntúa por puntos ajustados |
| `--no-anti-spam` | — | Apaga el anti-spam v2 |
| `--local-half-min` | 12 | Media vecindad local en minutos a cada lado |

## Cómo funciona

1. Agrupa los mensajes en ventanas de 10 s (configurable), con paso de 5 s y suavizado (0.25/0.5/0.25).
2. Puntúa cada mensaje: 1 base, +1 risa, +1 hype.
3. Anti-spam v2: penaliza la ventana si es mayormente emotes (>60% → ×0.25) o mensajes repetidos (>40% → ×0.5), y registra el motivo.
4. Baseline local: cada ventana se puntúa con z-score contra su vecindad de ±12 min + pendiente de subida + magnitud (ver v2.5 arriba).
5. Elige las ventanas con mayor puntaje, con al menos 60 s de separación.
6. Empieza cada corte 20 s antes del pico y exporta 60 s en `1080x1920` con recorte vertical.

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

Lo que queda a mano es solo lo que una plataforma no deja hacer por API (TikTok sin auditoría: borrador por Zernio, a publicar con 1 toque). Todo lo demás se publica solo desde el panel web (ver "Estado actual" arriba) o por fan-out.

## Cola FIFO + worker + aprobación

`jobqueue.py` + `worker.py`: un VOD entra en la cola, un solo worker lo
procesa con `auto_clips.py` y el clip queda en `ready_for_review`
hasta que un humano lo aprueba o lo rechaza. **Nada se publica desde
aquí.**

```bash
python jobqueue.py add https://kick.com/coker/videos/<uuid> --lang es
python jobqueue.py list            # ver la cola
python jobqueue.py approve 3       # aprobar un job en ready_for_review
python jobqueue.py reject 3        # rechazarlo
python jobqueue.py status          # resumen + disco + kill-switch
python worker.py --once         # procesar un job y salir
python worker.py                # procesar la cola hasta vaciarla
```

- **Persistente:** la cola vive en SQLite (`queue.db`, gitignored),
  nunca solo en memoria. Estados: `queued → processing →
  ready_for_review → approved/rejected → published/failed/dead`.
- **Entrada:** solo streamers en la lista blanca de `streamers.yaml` (lista única desde Fase 4.1; hoy Rubius activo, IlloJuan pausado desde 2026-11-18. REGLA 2026-11-16: estar en la lista que sube el usuario = permiso ya dado, sin ficha), dedup por
  VOD + idioma, y no se encola si quedan menos de 2 GB libres.
  `--force` en `add` salta solo la lista blanca (pruebas locales) y
  deja el job marcado.
- **1 solo worker:** `worker.lock` (fcntl) impide un segundo worker.
  Si un worker muere con un job en `processing`, el siguiente lo
  devuelve a `queued` sin gastar intento.
- **Reintentos:** máx 3 y solo para fallos transitorios (red/descarga);
  lo permanente va a `failed`, y tras 3 intentos a `dead`.
- **Semáforo:** `approve` se niega si hay rojos, salvo
  `--force-confirm` explícito (queda registrado). Rojo si: el juego
  no es GTA 6, el VOD es anterior al 19 nov 2026, el título/categoría
  hace sospechar música con copyright, o el manifiesto viene sin
  clips, con archivos que faltan o sin decisión de layout.
  (2026-11-16: el permiso YA NO es un rojo — estar en la lista
  blanca que sube el usuario = permiso ya dado.)
- **Kill-switch:** si existe el archivo `queue.kill`, el worker no
  arranca o se pausa inmediatamente (incluso a mitad de un job, que
  vuelve a `queued`). Se reanuda borrando el archivo.
- Antes de correr `auto_clips.py`, el worker respalda
  `manifest.json/.md` y `titles.txt` a `*.bak` si ya existen.

Pruebas: `python3 test_queue.py` (32 tests, BD temporal, sin red).

## Monitor de influencers — `watcher.py` (ver `WATCHER.md`)

Vigila la lista blanca de `streamers.yaml` (**hoy Rubius activo,
IlloJuan pausado desde 2026-11-18**,
Twitch, español, GTA) y detecta directos en vivo y VODs nuevos, para
no pegar la URL a mano. El encolado manual por URL queda como opción
secundaria.

```bash
python3 watcher.py --once --dry-run     # una pasada, sin encolar (defecto)
python3 watcher.py --once --live        # una pasada, encola lo que pase los frenos
python3 watcher.py --loop --live        # bucle cada poll_minutes (config.yaml, 15)
```

- **En vivo ≠ encolar.** Si varios están en vivo a la vez, la
  detección es para todos, pero no se sube nada en el momento: los
  clips se hacen solo cuando termina el directo y sale el VOD, y
  siempre con aprobación humana. El watcher detecta en vivo y
  registra; encola **solo VODs terminados**.
- **Frenos antes de encolar:** kill-switch (`queue.kill`), lista
  blanca (streamers en `streamers.yaml`; REGLA 2026-11-16:
  estar en la lista que sube el usuario = permiso ya dado), solo
  GTA 6 verificado, solo VOD terminado, dedup (`seen_vods.json`,
  gitignored, + dedup de la cola por VOD + idioma), tope de 5 VODs
  nuevos por influencer y día, nunca más de 1 job por influencer en
  `processing` (regla nº 1), y aprobación humana en
  `ready_for_review`. `--dry-run` es el defecto: sin `--live`
  explícito no se toca la cola.
- **Fuentes (gratis, $0):** Kick por API pública sin key (reutiliza
  `kick_downloader.core`); Twitch por Helix oficial si hay
  credenciales (`TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET`, app
  gratis en dev.twitch.tv) o, sin ellas, por la ruta pública GQL que
  usa la propia web de Twitch (no oficial, puede cambiar; Helix es
  la vía estable).
- **Probado 2026-11-16 con IlloJuan:** en vivo (GTA V #7, 60.371
  espectadores), último VOD terminado 2929822269 (GTA V #6) no
  encolado (es GTA V, no GTA 6);
  segunda pasada sin propuestas nuevas (dedup); `--live` sin encolar
  nada (frenos aguantan) y cola sin tocar. Tests offline:
  `python3 test_watcher.py` (11 tests, sin red).
- **Fase 2:** cron cada 15 min en producción, Twitch con
  credenciales Helix, encolado automático continuo (hoy: pasadas
  manuales).

## Empaquetado viral — para las 4 plataformas, no solo YouTube

`viral_package.py` empaqueta cada clip para las 4 plataformas. Misma idea, mismo hook y mismo texto en pantalla; lo que cambia es caption, hashtags, portada y modo de subida. Nunca se copia el mismo texto literal en las 4.

| Plataforma | Título / caption | Miniatura | Subtítulos | Subida |
|---|---|---|---|---|
| YouTube Shorts | Título ≤60 car. + descripción + tags ≤15 | Frame del pico (por API no hay miniatura personalizada fiable) | SRT aparte + quemados | API oficial, 100% automatizable |
| TikTok | Caption corto ≤150 car. = título + 3-5 hashtags | Sin miniatura propia por API | QUEMADOS obligatorios | Borrador por API hasta auditoría (1-4 sem.) |
| Instagram Reels | Caption + hasta 10 hashtags | SÍ admite portada (cover_url) | QUEMADOS obligatorios | Graph API, requiere cuenta Profesional + Página de FB |
| Facebook Reels | Título ≤60 car. + caption, 3-90 s obligatorio | Frame del pico | QUEMADOS obligatorios | Graph API a Página, nunca a perfil |

Cada clip en `manifest.md` lleva su paquete por plataforma, con su métrica a mirar: YouTube visto vs deslizado, TikTok % finalización, Reels reproducciones y compartidos.

## Ficha del momento + títulos reales + anti-clickbait — v2.6

La v2.4 generaba 3 títulos fijos con "GTA 6" hardcodeado y prometía una
"jugada" que no existía: en Coker (GTA V RP) y en Locones
(Counter-Strike 2) el título viejo mentía en el juego y en la jugada.
Desde v2.6 el título sale de la ficha del momento, sin LLM en caliente:

- **`moment_card.py`** — ficha del momento como única fuente de verdad,
  de la transcripción (SRT si existe) + el chat del pico. Juego real
  (nunca se infiere del chat ni del canal: lo pasa quien llama, con su
  fuente en `game_source`), streamer, qué pasa (verbo+objeto, solo si
  una acción del léxico aparece ≥2 veces en chat/transcripción),
  frase literal del streamer (SRT, cerca del payoff) y del chat (la
  más repetida en la ventana del pico, con ≥2 menciones), reacción
  dominante (risas/hype/volumen), timestamp del payoff. Si no hay
  evidencia, la ficha lo dice (`evidence: low`, `what: null`).
- **`viral_package.py`** — `generate_titles(moment_card, lang)` genera
  10 títulos por clip desde la ficha (juego real, acción evidenciada,
  frase literal, reacción, múltiplo). `check_title(title, moment_card)
  -> (bool, motivo)` es BLOQUEANTE: falla si el juego del título no es
  el juego real, si el sustantivo prometido no aparece en
  transcripción/chat/ficha, si el payoff no ocurre dentro del clip, o
  si hay un superlativo sin evidencia (múltiplo/risas) que lo respalde;
  también falla el lint de título (≤60 car., ≤2 palabras en
  MAYÚSCULAS; >40 car. es solo aviso, se corta en móvil). Anti-
  repetición: ningún título repetido en el mismo VOD ni el mismo día,
  registro en `titles_seen.json` (gitignored por `*.json`); solo se
  anota el título elegido (`best_title`), no los 10 candidatos.
  "GTA 6" solo si el juego real es GTA 6; tags y hashtags salen del
  juego real (`game_hashtags_tags`), no de #GTA6 fijo. Sin ficha, el
  juego es desconocido y ningún título nombra un juego.
- **`thumbnails.py`** — miniatura real: 3 fotogramas del clip con
  ffmpeg (payoff −5 s, payoff, payoff +5 s), guardados para elección
  humana en la aprobación; portada de Instagram = copia del fotograma
  del payoff (`cover_instagram.jpg`). Sin texto quemado por ahora.
  Sin payoff conocido usa 25 % / 50 % / 75 % de la duración.

```bash
# Ficha + títulos + miniaturas de un clip real
python moment_card.py --clip runs/kick-coker-01a1dffd/clips/clip_01_t03h14m50s_s56.5_r14h1.mp4 \
  --chat runs/kick-coker-01a1dffd/chat.json \
  --srt runs/kick-coker-01a1dffd/clips/clip_01_t03h14m50s_s56.5_r14h1.srt \
  --streamer coker --game "GTA V RP" --peak 11710 --start 11690 \
  --score 56.5 --risas 14 --hype 1 --median 1.0 --out runs/kick-coker-01a1dffd/clips/moment_card.json
python thumbnails.py runs/kick-coker-01a1dffd/clips/clip_01_t03h14m50s_s56.5_r14h1.mp4 \
  --card runs/kick-coker-01a1dffd/clips/moment_card.json \
  --out-dir runs/kick-coker-01a1dffd/clips/thumbnails --cover
python -c "
from viral_package import check_title
import json; card = json.load(open('runs/kick-coker-01a1dffd/clips/moment_card.json'))
print(check_title('GTA 6: La jugada que rompió el chat', card))"
```

Probado con los clips reales (2026-11-16, sin push, nada publicado):

- **Coker** (`clip_01_t03h14m50s_s56.5_r14h1.mp4`, GTA V RP, pico
  03h15m10s, 56.5 pts, 14 risas, 1 hype, 50 mensajes/10 s, 127
  usuarios/30 s, múltiple 56.5x): ficha `evidence: medium`,
  `what: "errar un tiro"` (15 menciones de "erra"), cita del streamer
  "No, no, no, no, no." (19.2 s del clip, junto al payoff a 20 s) y
  del chat "ESTANDO ARMADO Y LO ERRA" (x2). 10/10 títulos generados,
  ninguno dice GTA 6. Mejores 3: `GTA V RP: El chat: "Estando armado
  y lo erra"` (45 car.), `GTA V RP: Errar un tiro y el chat explotó`
  (41 car.), `Errar un tiro que nadie se creyó` (32 car.). El título
  viejo **FALLA** el check: "el título dice 'GTA 6' pero el juego
  real es 'GTA V RP'; el título promete 'jugada' pero no aparece en
  transcripción/chat/ficha".
- **Locones** (`clip_01_t03h08m05s_s24.75_r0h0.mp4`, Counter-Strike 2,
  pico 03h08m25s, 24.75 pts, 0 risas, 0 hype, 57 mensajes/10 s, 21
  usuarios/30 s, múltiple 24.75x): ficha `evidence: low` — sin
  transcripción (segmentos vacíos) y solo 1 mensaje de texto en la
  ventana del pico, resto spam de emotes rumanos; `what: null` ("sin
  evidencia: no se sabe qué pasa"). Solo 5 títulos, todos de volumen/
  reacción, ninguno promete una jugada. Mejores 3: `57 mensajes en 10
  segundos por esto` (35 car.), `Counter-Strike 2: El chat explotó
  con esto` (42 car.), `24.75x la mediana: Counter-Strike 2`
  (35 car.). El título viejo **FALLA** el check: "el título dice
  'GTA 6' pero el juego real es 'Counter-Strike 2'; el título promete
  'jugada' pero no aparece en transcripción/chat/ficha; evidencia
  baja: la ficha no sabe qué pasa, no se puede prometer ['jugada']".
- Miniaturas: 3 frames 1080x1920 por clip (15/20/25 s, payoff a 20 s)
  + `cover_instagram.jpg` (copia del frame del payoff), en
  `runs/<vod>/clips/thumbnails/`. El frame del payoff de Coker muestra
  las dos webcams lado a lado (layout stack), verificado a vista.

## Empaquetado viral

`viral_package.py` aplica estas técnicas de empaquetado viral:

- **Ranking viral:** ranking por múltiplo sobre la mediana del propio stream, no por puntaje bruto. Un pico de 32 pts con mediana 17 es 1.88x.
- **Formato Shorts:** cada clip lleva primera línea nueva, texto en pantalla en los primeros 2 s (distinto de lo hablado) y punto de loop, para que sobreviva sin el video alrededor.
- **Título + miniatura:** título + miniatura como una unidad. Título ≤60 car. (40 en móvil), miniatura ≤3 palabras y sin repetir el título, máx. 2 palabras en MAYÚSCULAS.
- **SEO:** descripción con las 2 primeras líneas como snippet, tags máx. 15 y las 3 búsquedas que el video debería ganar.
- **Retención:** el hook se juega en los primeros 15 s.

Nada publica solo: esto escribe y tú publicas. Cada clip termina con la pregunta: ¿lo subo así, o lo cambio?

## Estudio de reels virales — `reel_study.py` (2026-11-16)

Idea del reel de David Madrazo (@davidmadrazo.ia, "content-os"): vigilar reels virales, descomponer hook/estructura/CTA y reescribir con reglas. De eso se toma solo la primera mitad, adaptada: estudiar reels de GTA 6 por URL, clasificar hook (pregunta/número/superlativo/cita/promesa) y CTA (seguir/comentar/compartir/guardar/link), medir engagement relativo al autor y agregar los patrones. No se copia ningún caption ni título: las plantillas son genéricas con huecos (`{what}`, `{game}`) y los títulos sugeridos salen de la ficha del momento y pasan el check promesa-payoff; si no pasan, no se sugieren. Sin voz clonada (el audio es el del directo) y sin publicar solo: lo sugerido va a la aprobación humana.

```bash
# Estudiar reels por URL (máx. 10 por corrida, pasada manual, no scrapeo)
python reel_study.py --url https://www.instagram.com/reel/DfpHUNFiKke/ \
  --url https://www.instagram.com/reel/DcmuKhJAe3I/ \
  --account-id 17841429035950873 --out reel_patterns.json
# O desde un JSON con reels (salida de instagram-cli post o lista)
python reel_study.py --from-json reels.json --out reel_patterns.json
# auto_clips.py usa reel_patterns.json si existe (o --reel-patterns <ruta>);
# sin informe sigue sin estudio de reels. El best_title NO cambia.
python auto_clips.py https://kick.com/coker/videos/<uuid>
```

Probado 2026-11-16 con 4 reels reales (Rockstar, Bars, Overtime, David Madrazo): hook dominante statement, CTA dominante seguir/none, engagement mediano 7,28% con mucha dispersión entre autores (Rockstar 3,77% con 33M de seguidores; David 318% con 25 mil — el engagement se lee siempre junto a los seguidores, no en absoluto). Muestra pequeña (mínimo orientativo 5): los patrones son orientativos hasta ampliarla. Pruebas: `python3 test_reel_study.py` (20 tests). Un dígito que es solo el nombre del juego ("GTA 6") o parte de un @usuario no cuenta como hook de número.

## Publicación — v2.6

`publisher.py` es el único módulo que publica. Ver `PUBLISHING.md`
para el detalle completo.

- **YouTube Shorts por API** (Data API v3, `videos.insert`): la única
  plataforma con publicación directa. OAuth 2.0 (`client_secrets.json`
  + `token.json`, nunca en git). Cuota ~10.000 unidades/día, ~1.600 por
  subida → máx. ~6 Shorts/día; plan 3-5/día.
- **TikTok / Instagram / Facebook: draft-first.** `publish_draft()`
  genera el paquete listo para subir a mano en `drafts/` (archivo +
  caption + hashtags + portada). Nunca publicación directa sin
  auditoría aprobada.
- **Auditoría** (`python publisher.py audit`): checklist pasa/no-pasa
  por cuenta. Estado 2026-11-16: YT 🟡 AMBER (falta verificar canal +
  OAuth), TikTok 🟡 AMBER (falta app aprobada), IG 🔴 RED (cuenta no
  creada), FB 🔴 RED (Page no creada). Sin fila en verde, draft-first.
- **Cola** (`publish_queue.json`): `queued → ready_for_review →
  approved → published / failed`, más `draft` y `quarantined`.
  Topes: YT 3-5/día, resto 2-3/día, espaciamiento 2-3 h.
  SIN franja horaria desde el 2026-11-18 (decisión del usuario).
- **Lista blanca** (`streamers.yaml` canónica, Rubius concedido
  2026-11-18, IlloJuan pausado): se verifica
  EN EL MOMENTO de publicar, no solo al detectar. Si el streamer
  ya no está en la lista, no se publica.
- **Rechazos:** formato → 1 reintento; copyright/contenido →
  cuarentena y revisión humana, nunca reintento ciego.
- **Dry-run por defecto:** sin `--live` no se sube nada. Prueba
  2026-11-16 con el clip de Coker: paquete válido, pero NO publicable
  (Coker no está en la lista blanca, YT sin OAuth, auditoría amber).

## Auto-publicación — contador "funciona", 4 fases y circuit breaker

`counter.py` decide si se PUEDE publicar; no publica nada. Ver
`AUTOPUBLISH.md` para el detalle completo.

- **Contador "funciona"** (`state/counter.json`): clips seguidos
  aprobados sin editar título/caption Y publicados sin fallo
  técnico. Umbral propuesto (a validar): **15 seguidos, 0
  rechazados por contenido en los últimos 15**. Se reinicia a cero
  con un rojo, un rechazo por contenido o un fallo de subida.
  `python3 counter.py status` / `record <evento>`.
- **4 fases, cambio MANUAL con confirmación** (`counter.py set-phase
  N --yes`): 1 manual (19 nov) → 2 semi-auto (el lote se propone;
  sin respuesta no se publica) → 3 auto con ventana de veto
  (30 min YT / 2 h resto, configurable) → 4 auto total (solo con
  semáforo verde + lista blanca + métricas en verde). Cada fase
  mínimo 3-5 días y 10 clips; avanzar es secuencial salvo `--force`.
- **Circuit breaker**: auto-pausa con 2 fallos de subida seguidos en
  una plataforma, copyright/strike, 1 rojo que intentó colarse,
  idioma conf. < 0,5 en 2 clips seguidos, o 3 clips seguidos muy por
  debajo de la mediana. Reanudación solo manual
  (`counter.py resume --yes`).
- **Kill-switch**: archivo `queue.kill` en la raíz del repo, el mismo
  que usa `jobqueue.py` (bloque 1). Si existe, nada se publica.
- **La cola consulta la puerta** antes de publicar:
  `counter.py gate --platform youtube --clip c1` (exit 0 =
  permitido) o `gate_publish()` desde Python. La lista blanca
  canónica es `streamers.yaml` (Rubius concedido 2026-11-18,
  IlloJuan pausado): si la lista está vacía, la fase 4 queda bloqueada.
- **Simulación**: `python3 counter.py simulate` (10 aprobados sin
  editar → 1 rechazo → contador a cero, más demostración del breaker
  con 2 fallos seguidos; usa estado temporal, no toca el real).

## Supervivencia por plataforma

Publicar clips de otros streamers pone en riesgo las cuentas nuevas si se
hace en bloque o como si fuera un bot. Estas reglas son el checklist de aprobación: se cumplen siempre, y si cae el alcance
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
4. **Apelar solo con la lista blanca como respaldo**: si la reclamación es por reutilización o copyright, estar en la lista que subió el usuario es la evidencia de permiso. Si el streamer ya no está en la lista, no apelar: esperar.
5. **Volver con 1/día y más transformado**: nuevo título, nueva edición (otro corte, otro layout, otro texto en pantalla), nuevos hashtags. Si vuelve a caer, parar esa plataforma una semana.

REGLA 2026-11-16: estar en la lista blanca que sube el usuario = permiso ya dado; no hay ficha de permiso (PERMISOS.md eliminado).

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

Bajar el VOD de otro streamer y republicarlo puede infringir las normas de Twitch y el copyright del creador / del juego. Úsalo solo con streamers de la lista blanca que sube el usuario (estar en la lista = permiso ya dado).
