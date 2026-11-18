# Cortador en vivo — `clipagent/live_cutter.py`

El cutter que faltaba: antes el vivo solo detectaba picos y avisaba
(`clipagent/live.py`: "BLOQUEADO (grabación)"). Desde 2026-11-18
el vivo **graba, corta, subtitula, empaqueta y encola** mientras
pasa el directo.

## Flujo por pico

```
IRC Twitch (o Pusher Kick) -> LivePeakDetector (hype v2 por ráfaga)
  -> GATE DE CATEGORÍA (solo GTA VI, por API oficial)
  -> esperar grabación hasta pico+20 s
  -> extraer ventana [pico-20, pico+20] (40 s) del ring buffer
  -> corte vertical split (juego arriba, cámara abajo)
  -> subtítulos quemados COMPLETOS (faster-whisper)
  -> título validado anti-clickbait + paquete SEO + prioridad
  -> Cola LIVE del panel (origin='live', MP4 en R2)
  -> publicación encolada (draft por defecto, ver abajo)
```

## Gate de categoría (regla del usuario 2026-11-18)

Solo se corta en **GTA VI (Grand Theft Auto VI)**. Si el streamer
cambia a Just Chatting u otra categoría, no se corta. La categoría
queda registrada en cada clip (ficha, paquete, motivo del panel,
tracking). Sin categoría verificable, **no se corta**.

Fuentes (APIs oficiales, nunca scraping de HTML):

| Caso | Fuente | Código |
|---|---|---|
| Twitch en vivo | Helix `GET /helix/streams?user_login` (`game_name`) y `GET /helix/channels?broadcaster_id` | `watcher.TwitchHelix.live_category` / `channel_category` |
| Twitch sin credenciales | GQL pública de Twitch (API estructurada de la web, JSON) | `watcher.TwitchPublicGQL` |
| Twitch VOD (directo cerrado / replay) | Capítulos del `chat.json` (`video.chapters`, `gameDisplayName` por tramo) | `clipagent.category.chapter_category_from_chat` |
| Kick en vivo | `GET https://kick.com/api/v2/channels/<slug>` (API pública, sin login) | `clipagent.live.kick_channel_category` |

Lógica pura en `clipagent/category.py` (`is_gta6`, `category_at`);
la red vive en `watcher.py` / `live.py`; el gate, en
`live_cutter.CategoryGate` y en `workers.detect_peaks` (VOD).

## Grabación (ring buffer, $0)

`streamlink` resuelve la URL HLS del directo y `ffmpeg` la graba
en segmentos de 60 s; se guardan los últimos 12 (12 min) y los
viejos se borran. Nunca se baja el VOD entero. La latencia HLS
(~10-15 s entre lo que pasa y lo que llega por HLS) se compensa
con `latency_s` (12 s por defecto): la ventana de corte es
`[pico-20+latencia, pico+20+latencia]` en tiempo de grabación.

Sin `streamlink` o si la grabación muere, el pico queda registrado
con el motivo y el clip se recupera del VOD al cerrar (modo replay,
mismo código).

## Publicación: a la Cola, sin publicar en público (todavía)

Orden del usuario 2026-11-18 14:37: los mejores clips van a la
**COLA del panel web**, luego se suben desde ahí; **sin publicar
en público todavía**. Por eso el cutter, por defecto:

- encola el clip en el panel (`POST /api/clips`, `origin='live'`,
  MP4 con `PUT /api/clips/:id/video`, pasos de tracking);
- encola las publicaciones publicables en el cerebro en modo
  **draft** (listas, no publicadas).

Con `auto_publish=True` (`clipagent live --cut --auto-publish`),
las plataformas publicables se encolan en mode `api` (publicación
automática, MÁSTER PROMPT FINAL). Las puertas duras no cambian
nunca: viralidad < 50 descartado, SEO < 90 sin override,
duración 40-60 s, rango 25-35 prohibido, subtítulos completos
(sin voz verificable = BLOQUEADO), streamer con permiso, topes
diarios y cooldown.

## Uso

```bash
# En vivo (Rubius, 25 min; el workflow lo relanza cada 30 min)
python -m clipagent live rubius --cut --minutes 25

# Solo detectar picos, sin cortar (comportamiento viejo)
python -m clipagent live rubius --minutes 60

# Replay de un VOD (mismo código, sin directo): picos del chat,
# cortes del archivo, categoría de los capítulos
python -m clipagent live-replay --chat runs/2932415021/chat.json \
    --video runs/2932415021-p2010/segments/seg_01_t33m10s.mp4 \
    --video-offset 1970 --streamer rubius
```

El workflow `.github/workflows/cerebro-live.yml` corre el cutter
en GitHub Actions (compute $0): cada 30 min mira si el streamer
está en vivo y en GTA VI; si lo está, graba y corta 25 min.
Secretos que necesita (ya existen salvo PANEL_*): `TWITCH_*`,
`PANEL_USERNAME`, `PANEL_PASSWORD` (login del panel). Sin
`PANEL_*`, el cutter sigue solo con la cola del cerebro y lo dice.

## Límites conocidos (no escondidos)

- Una corrida de Actions cubre ~25 min; entre corridas se pierde
  el pre-roll (el VOD lo recupera al cerrar).
- La alineación chat/vídeo tiene ±latencia HLS; el pico queda
  dentro de la ventana de 40 s, no siempre justo a los 20 s.
- El tick del detector re-puntúa el chat acumulado: en directos
  de varias horas cada tick tarda más (futuro: detector
  incremental).
- TikTok por API sin auditoría solo publica en privado: va por
  Zernio en draft-first (ver README).
- El Worker del panel acepta `origin` en `POST /api/clips`
  (parche 2026-11-18): sin él, un clip live caería en el segmento
  VOD de la Cola.
