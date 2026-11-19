# Auditoría SFX — efectos de sonido en los clips de GTA 6

**Fecha:** 2026-11-18 · **Autor:** auditoría (subagente) · **Aprobada por el usuario:** 2026-11-18 20:07
**Objetivo:** saber EN QUÉ MOMENTO exacto poner cada efecto/sonido en un clip,
con reglas claras para automatizarlo después.
**Alcance:** solo informe. No se tocó la plantilla de render, no se re-renderizó
nada, no hay commit ni push.

## Método (qué se midió de verdad)

- Se descargaron los 4 audios de referencia con yt-dlp (IG/FB, públicos) a
  `/tmp/sfx-audit/` y se analizaron con las mismas funciones del repo
  (`clipagent/audio.py`: RMS por segundo, `shout_spikes`) + análisis espectral
  propio (centroide y fracción de energía ≤120 Hz por segundo, FFT 16 kHz).
- Un efecto tipo **vine boom** tiene una firma inconfundible: ~1 s de duración,
  energía casi toda en graves (fracción ≤120 Hz > 0,7), ZCR bajísimo (< 0,06,
  es un seno que cae, no voz) y pico de -10 a -11 dBFS. La voz tiene centroide
  900–2500 Hz, ZCR 0,06–0,24 y fracción de graves ≈ 0.
- Los clips propios (p3220, p7775) se analizaron sobre el MP4 ya cortado, con
  su transcripción Whisper y `streamer_shout_spikes` (gritos solo del streamer).
- **Limitación honesta:** no se pudo *escuchar* los reels; la identificación es
  por forma de onda + espectro + transcripción automática. Los "booms" se
  identifican por firma espectral, no por escucha. La transcripción de 3 de los
  4 reels no se completó (VM saturada, load 16): el mapeo de los booms de
  @nixuj_playz a beats narrativos es inferido por orden, no verificado con
  timestamps de voz. Whisper base en el reel de Willyrex oye mal algunas
  palabras; los segundos y los dB son los datos duros, el texto es aproximado.

## 1. Reels de referencia: qué se encontró en cada uno

### 1.1 @gta_vi_clips_v — Willyrex, "Atropello una gaviota en GTA 6" (IG Df4z, 64 s)

**Sin ningún SFX agregado.** Todo lo fuerte es voz del streamer + juego.

| Dato medido | Valor |
|---|---|
| Duración / media / rango | 64 s · -24,0 dBFS · 34,4 dB |
| Spikes +8 dB (gritos) | s=4, 18, 21, 22, 25 |
| Segundos más fuertes | s=2 (-12,6), s=4 (-12,9), s=6 (-14,0), s=22 (-14,0) |
| Fracción de graves en los picos | 0,00–0,04 (voz, no boom) |
| Silencios de edición (beats) | s=8 (-39), s=9 (-42), s=15 (-46), s=17 (-47), s=27 (-47), s=57 (-46) |

Línea de tiempo (transcripción Whisper base, palabras aproximadas):

- **s=0–3:** "¡una gaviota, no me lo creo!" — el atropello. RMS -13 a s=2.
- **s=4–7:** "¡No me lo creo! ¿Cómo voy a atropellar una gaviota?" — grito.
- **s=8–9:** silencio (-39/-42). Beat de edición.
- **s=10–15:** "No me lo puedo creer… ¿qué hago con una gaviota, tío?"
- **s=15–17:** silencio largo (-46/-47). Beat.
- **s=18–26:** "Sí, ¿qué pasa?… imagino qué podría hacer con vuestro cuello" —
  el momento de shock + risa. Picos a s=21 (-15,5), 22 (-14,0), 25 (-14,6).
  Aquí es donde el streamer suelta "cojones" y "olé" (según el análisis previo
  del reel). Todo voz, centroide 1378–2292 Hz.
- **s=27–63:** el chiste del autobús ("¿me puedo subir al bus con una gaviota
  encima?"), hablando continuo con picos a s=47, 50, 54, 59.

**Lección:** el reel vive de la voz. El editor deja *silencios* (beats) para que
el momento respire, no mete un boom encima del grito.

### 1.2 @nixuj_playz (IG Df45, 55 s) — EL ÚNICO CON SFX AGREGADOS

**6 booms de graves puros** en 55 s, uno por beat narrativo. Es el reel que el
usuario notó ("le ponen sonidos").

| Dato medido | Valor |
|---|---|
| Duración / media / rango | 55 s · -24,1 dBFS · 32,4 dB |
| Booms (graves puros) | **s=6, 14, 30, 34, 38, 49** (colas a s=35, 50) |
| Firma de cada boom | fracción ≤120 Hz 0,75–0,92 · ZCR 0,023–0,056 · RMS -10,4 a -11,1 dBFS |
| Ráfagas brillantes (risa o ding, sin confirmar) | s=25, 43, 48 (centroide 2113–2218 Hz, ZCR 0,16–0,21, casi sin graves) |
| Audio | original_sounds (mezcla propia del creador, no audio de tendencia) |

Los booms son ~5 dB más fuertes que los picos de voz de ese mismo reel
(voz: -15 a -17 dBFS; boom: -10,4 a -11,1 dBFS). Cada boom dura ~1 s.

Mapeo a la narrativa (inferido por orden de la historia, NO verificado con
timestamps de voz — la transcripción no completó por saturación de la VM):

| Boom | Momento probable de la historia |
|---|---|
| s=6 | El personaje se acerca a la mujer del vestido (inicio del ligue) |
| s=14 | "I need you to back on up" (la frase al acompañante) |
| s=30 | El NPC se levanta / se pone confrontacional |
| s=34 | Escalada del standoff |
| s=38 | Escalada / el jugador se echa atrás |
| s=49 | El remate: el streamer se ríe del absurdo |

**Lección:** el creador pone un boom en CADA beat de la historia (uno cada
~8 s), no solo en el payoff. Es un estilo de edición "meme" intenso. Para
nuestro canal automático, esa densidad es demasiado: ver reglas abajo.

### 1.3 GTA VI CLIPS — el auto (FB 961787350092093, 13,7 s)

**Sin SFX.** Audio plano y fuerte todo el rato: media -16,8 dBFS, rango solo
10,0 dB, **cero spikes +8 dB**. El momento (el encapuchado intenta robar el
auto y el conductor lo tira fuera) se sostiene solo con el grito/shock del
streamer y el audio del juego. Segundos fuertes: s=0–6 (-14,1 a -15,6), con
algo de grave de juego mezclado (fracción ≤120 Hz 0,18–0,57, no puro).

### 1.4 RDJavi (FB 1167322015763492, 62 s)

**Sin SFX.** Sin spikes +8 dB, rango 18,0 dB, media -18,7 dBFS. Picos de voz
a s=15–18 (-13,9 a -15,9: la pelea con el camionero) y s=60 (-14,2). Rumble
de motor mezclado con voz en s=39–46 y s=59–61 (fracción de graves 0,28–0,47,
no puro). El humor va en la risa sostenida del streamer, no en efectos.

### Conclusión de la parte 1

**Solo 1 de 4 reels de referencia usa SFX agregados.** Willyrex, RDJavi y
GTA VI CLIPS no ponen ningún efecto: viven de la voz del streamer + el juego.
Los SFX son un recurso de edición de *algunos* creadores (estilo meme), no una
norma del nicho. Si los agregamos, que sea con moderación: un boom bien puesto
vale más que seis.

## 2. Nuestros momentos reales: p3220 y p7775 (VOD 2932415021, Rubius)

Medido sobre el MP4 cortado (48 s, payoff en el segundo 20 del clip por
construcción: `CLIP_PRE_S = 20`).

### 2.1 p3220 — lancha con Lucia bajo el puente (viralidad 53,8)

| Dato | Valor |
|---|---|
| Media / rango / std | -20,6 dBFS · 14,0 dB · 4,1 |
| Gritos SOLO del streamer | **s=14, 15, 16** (clip) = VOD 3214–3216 |
| Segundos más fuertes | s=16 (-14,1), s=15 (-14,8), s=14 (-15,4) |
| RMS en el payoff (s=20) | **-24,6 dBFS (dip de ~4 dB bajo la media)** |
| Voz 16,0–19,7 | "Sí, pero lo voy a decir de nuevo, no me creo que estemos aquí, viendo esto, gente" |
| Voz 21,1–26,3 | "…de barbaridad… casi me la lío…" (reacción post-payoff, RMS -16,1 a s=23) |
| Chat | 134 msgs/10 s en el pico · ráfaga 1,31x · "cinema" x5 |

### 2.2 p7775 — cromos coleccionables (viralidad 52,5)

| Dato | Valor |
|---|---|
| Media / rango / std | -24,9 dBFS · 24,0 dB · 7,7 |
| Gritos SOLO del streamer | **s=16, 18** (clip) = VOD 7771, 7773 (el spike a s=15 es solo juego) |
| Segundos más fuertes | s=15 (-15,4), s=16 (-16,5), s=21 (-16,4), s=23 (-16,4) |
| RMS en el payoff (s=20) | **-39,4 dBFS (casi silencio, ~15 dB bajo la media)** |
| Voz 15,6–19,8 | "¡Goti! ¡Goti! Se puede coleccionar cartas, gente" |
| Voz 22,6–24,2 | "Ya están los cromos coleccionables" (la frase del reveal) |
| Chat | 118 msgs/10 s en el pico · ráfaga 1,92x (mayor del VOD) · "pog" x19, "goty" x18 |

### 2.3 El patrón común (la evidencia clave de la auditoría)

En los DOS clips el patrón es idéntico:

1. **Grito del streamer a s=14–19** (la reacción al descubrir el momento).
2. **Silencio/beat EXACTO en el payoff, s=20** (p3220: -24,6; p7775: -39,4).
   El streamer se calla justo cuando la imagen del payoff está en pantalla
   (la cara de asombro / la carta en primer plano).
3. **Reacción hablada a s=21–26** (la explicación del momento).

Ese hueco de silencio en el payoff es el sitio natural del efecto: el boom
llena el beat sin tapar ninguna palabra del streamer, y el oído lo lee como
"esto acaba de aterrizar". Es exactamente donde @nixuj_playz pone sus booms
(en los beats), y es lo contrario de ponerlo encima del grito.

## 3. Tabla de reglas: TRIGGER → EFECTO → MOMENTO EXACTO

Señales que ya existen en el pipeline (nada que inventar):

- `payoff_ts_clip` = 20 s del clip (el pico cae a los 20 s por construcción).
- `streamer_shout_spikes` = segundos de grito solo del streamer
  (`clipagent/audio.py`, ya en uso para el rescate de revisión).
- Frases de asombro del streamer (`clipagent/streamer_hype.py`: "no lo puedo
  creer", "Dios mío", "WTF", "OMG", "qué locura", "cojones", "olé").
- Ráfaga del chat (`hype_ratio`, `msgs_per_10s`) — el pico ES el payoff.
- Dip de silencio: RMS del segundo del payoff ≥ 6 dB bajo la media del clip.

| # | Trigger (señal medible) | Efecto | Momento exacto | Evidencia |
|---|---|---|---|---|
| 1 | **Payoff + dip de silencio** en s=20 (RMS cae ≥ 6 dB) | **Vine boom** (0,5–1,5 s) | **En el segundo 20 del clip**, empezando en 20,0 | p3220 dip -24,6; p7775 dip -39,4; df45 booms en beats |
| 2 | **Payoff sin dip** (el streamer sigue hablando en s=20) | **Nada** (o boom a -6 dB más bajo si el dip es leve) | — | No tapar voz: la voz manda |
| 3 | **Fail visual** (NPC muere, personaje sale volando, plan falla) Y **sin grito del streamer** en ±2 s | **Sad trombone / "bruh" / fail sting** (1–2 s) | En el segundo del fail +0,0–0,5 s | df4z: el fail (gaviota) va con grito, NO con SFX — por eso la condición "sin grito" |
| 4 | **Grito del streamer** (`streamer_shout_spikes`) | **NINGÚN SFX encima** | — | p3220 s=14–16; p7775 s=16, 18; df4z s=21–25; RDJavi s=15–18 |
| 5 | **Frase de asombro** ("no lo puedo creer", "Dios mío", "WTF") | **NINGÚN SFX encima**; opcional whoosh suave 0,3 s ANTES si hay silencio previo | La frase ES el hook sonoro | p3220 s=16,0–19,7 |
| 6 | **Risa sostenida del streamer** | **NINGÚN SFX** (la risa es el efecto) | — | df4z s=21–25; RDJavi todo el clip |
| 7 | **Reveal / giro inesperado** (carta legendaria, NPC hace algo absurdo) | **Vine boom o "ding"** (0,5–1 s) | En el segundo del reveal (= payoff, regla 1) | p7775 s=20 carta en primer plano |
| 8 | **Escalada / tensión** (NPC se levanta, persecución, standoff) | **Riser** (subida 1–2 s) ANTES del desenlace + boom en el desenlace | Riser: desenlace −2 s a desenlace; boom: desenlace | df45 booms a s=30, 34, 38 (escalada) |
| 9 | **Hook (s=0–3)** | **NINGÚN SFX** | — | El gancho es visual + título quemado; el audio entra limpio |
| 10 | **Loop (últimos 2 s del clip)** | **NINGÚN SFX** | — | El loop debe ser limpio o el corte se nota |
| 11 | **Clip sin voz / sin payoff claro** | **NINGÚN SFX** | — | No maquillar un clip flojo con efectos |

### Decisión automática (if/else, listo para implementar)

```
si payoff existe:
    si RMS(payoff) <= media - 6 dB:  boom en payoff          # regla 1
    si no:                          sin boom en payoff        # regla 2
si fail Y no hay grito del streamer en [fail-2, fail+2]:
    trombone/bruh en fail                                    # regla 3
si escalada Y desenlace claro:
    riser en [desenlace-2, desenlace] + boom en desenlace     # regla 8
nunca SFX en: gritos del streamer, frases de asombro, risas,
              s=0-3, últimos 2 s                             # reglas 4,5,6,9,10
máximo 2 SFX por clip de 48 s (1 boom payoff + 1 opcional)
```

## 4. Reglas de mezcla (cómo suena bien, con números medidos)

| Regla | Valor | Por qué (medido) |
|---|---|---|
| Pico del SFX | **-11 a -13 dBFS** | df45: booms a -10,4/-11,1 dBFS, ~5 dB sobre sus picos de voz (-15 a -17). Nuestros clips: voz pica -14 a -16 → SFX a -11 ≈ +4 dB sobre la voz |
| Ducking del audio original | **-6 dB durante el SFX** (fade 100 ms de entrada/salida) | El boom se oye sin borrar el ambiente del juego |
| Duración por efecto | **0,5–1,5 s** (riser 1–2 s) | Vine boom típico 1,4 s; más largo satura |
| Máximo por clip (48 s) | **2 efectos** (1 boom payoff + 1 opcional) | df45 mete 6 en 55 s y es un reel editado a mano; para el canal automático, 2 es el tope para no sonar a máquina de sonidos |
| Techo tras mezclar | RMS del segundo del SFX **≤ -10 dBFS** | Evita clipping/distorsión en el móvil |
| Formato | WAV 16-bit 44,1 kHz o MP3 192 kbps; **normalizar cada SFX a -11 dBFS de pico antes de mezclar** | Así el volumen no depende del archivo que se bajó |
| Nunca | SFX sobre voz del streamer; SFX en s=0–3; SFX en los últimos 2 s; música de fondo + SFX a la vez | Ver tabla de reglas |

## 5. Catálogo recomendado (todo $0, con fuente y link)

**Licencias (resumen):** CC0 = dominio público, sin atribución, uso comercial OK.
Pixabay License = gratis para uso comercial sin atribución (desde 2019 es
licencia propia, ya no CC0). CC BY = hay que dar crédito (evitar si se puede).
**OJO:** los rips originales de memes con copyright (Roblox "oof", error de
Windows XP, violín triste de TV) son zona gris para contenido monetizado:
usar recreaciones CC0, no el rip. MyInstants sirve para escuchar/probar, NO
como fuente para publicar.

| Efecto | Qué significa | Cuándo se usa | Fuente | Licencia | Link |
|---|---|---|---|---|---|
| **Vine boom** | "Esto acaba de aterrizar" | Payoff / reveal (regla 1, 7) | 712tools (sintetizado, 1,4 s, MP3/WAV) | Royalty-free, sin atribución | https://712tools.com/viral-sound-effects/ |
| Vine boom (variante) | Igual, más largo | Payoff | Freesound "Drama Boom 02" de modusmogulus ("sounds a bit like the vine boom", 3,6 s) | Verificar en la página antes de usar | https://freesound.org/people/modusmogulus/sounds/802074/ |
| Boom retro (corto) | Igual, 1,3 s | Payoff | Freesound "Boom_C_02" de cabled_mess (WAV) | Verificar en la página antes de usar | https://freesound.org/s/350975 |
| **Bruh** | Incredulidad muerta ("en serio…") | Fail tonto (regla 3) | Freesound "Bruh Sound Effect #1" de Autellaem (0,7 s, MP3) | Verificar en la página antes de usar | https://freesound.org/people/Autellaem/sounds/534387/?page=2 |
| **Airhorn MLG** | Victoria / hype | Clutch, logro, algo épico | memesoundboards (original Freesound 414208 de jacksonacademyashmore, hecho en Audacity) | **CC0** | https://www.memesoundboards.org/sound/airhorn-sound-effect/ |
| Sad trombone / fail | Derrota cómica | Fail sin grito (regla 3) | Freesound: buscar "sad trombone" con filtro CC0 | CC0 (filtrar) | Buscar en freesound.org con filtro "Creative Commons 0" |
| Wilhelm scream | Desastre teatral | Fail exagerado | Internet Archive | Dominio público | Buscar en archive.org |
| Riser / whoosh | Tensión antes del desenlace | Escalada (regla 8) | Pixabay (buscar "riser" / "whoosh") | Pixabay License | https://pixabay.com/sound-effects/ |
| Risas / crowd | Celebración | Remate con risa del chat | Pixabay (buscar "crowd laugh" / "applause") | Pixabay License | https://pixabay.com/sound-effects/ |
| Guías de fuentes | Dónde buscar cada sonido | — | VoxBooster | — | https://voxbooster.com/blog/meme-sound-effects-download/ · https://voxbooster.com/blog/soundboard-memes/ |

**Descarga recomendada (pack inicial, 5 archivos):** vine boom (712tools),
bruh (Freesound), airhorn CC0, sad trombone (Freesound CC0), riser (Pixabay).
Guardar en `clipagent/sfx/` con un `MANIFEST.json` que registre origen +
licencia de cada archivo, y la página guardada de cada sonido para mostrar
de dónde salió si llega un reclamo.

## 6. Próximo paso propuesto (NO hecho en esta auditoría)

1. El usuario elige el pack final (los 5 de arriba u otros).
2. Bajar los archivos a `clipagent/sfx/` + `MANIFEST.json` de licencias.
3. Mezclador ffmpeg con las reglas de la sección 3 y 4 (ducking -6 dB, pico
   -11 dBFS, máx. 2 por clip, nunca sobre voz) + tests con p3220/p7775
   (boom a s=20, verificar que no tapa la voz de s=21–26).
4. A/B: un clip con boom vs el mismo sin boom, y que el usuario diga cuál
   suena mejor antes de activarlo por defecto.

---
*Evidencia guardada en `/tmp/sfx-audit/` (audios WAV, RMS, espectros,
transcripción de df4z): `ig-df4z/df45`, `fb-auto`, `fb-rdjavi` + `.rms.json`,
`.spec.json`, `.transcript.json`. Los WAV son solo para esta auditoría; no se
publican ni se suben a ningún lado.*
