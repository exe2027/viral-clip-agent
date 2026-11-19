# Plantilla estilo Willyrex (GTA 6 / Vice City)

En el código es la plantilla `rdjavi` v2 (`clipagent/rdjavi.py`).

Estilo TOTAL por decisión del usuario (2026-11-18) para todos los clips
de GTA 6. El layout ancho alternativo (DJ Mariio) queda aparcado por ahora.
Sin referencias externas: el estilo es webcam a ancho completo arriba,
banda negra con título, gameplay abajo, subtítulos fijos amarillos.

## Layout (1080×1920, ver `clipagent/rdjavi.py`)

| Zona | Geometría | Contenido |
|---|---|---|
| Streamer arriba | 583×595, x=248, y=0 (~54% ancho, ~31% alto, casi 1:1) | Webcam centrada, cara |
| Fondo | y 0–749 | Webcam desenfocada (morado/magenta) + gradiente atardecer Vice City (rosa-violeta-naranja) |
| Gameplay abajo | y 749–1786 (39%–93%), 1080×1037, recorte 1:1 | Juego, sin tapar HUD (el recorte 1:1 centrado excluye minimapa y avisos laterales) |
| Barra inferior | y 1786–1920 | Negro |
| Cámara | Fija + un punch-in (zoom 1,18×, corte duro) en la reacción (por defecto 16–26 s del clip) | |

Webcam de Rubius medida (segmento 2560×1440): caja PiP `(0, 0, 561, 365)`;
recorte cuadrado `365:365:90:0` (cara centrada); punch-in `310:310:125:27`.
Si el streamer muestra su setup en plano ancho, el estilo DJ Mariio
(webcam ancha arriba, banda, gameplay 72%) es la alternativa — por ahora
no se usa.

## Subtítulos simples estilo Willyrex

- Una línea centrada, `Noto Sans Condensed` 42, blanca con contorno
  negro 2,5, sin fondo de color, sin karaoke ni resaltado palabra por
  palabra, solo con cada frase.
- **MarginV 200** (referencia 230; el 680 viejo queda prohibido).
- En la parte baja del gameplay, sin tapar la webcam ni el subtítulo
  propio del juego en pantalla (que queda visible en el recorte 1:1).
- Completos y verificados: si Whisper no encuentra voz verificable, el
  clip queda `publish_blocked` hasta transcripción humana; nunca inventar.

## Miniatura Vice City

- Texto `thumbnail_text` (p. ej. `LA REACCIÓN`), tipografía condensada.
- Portada = fotograma del pico (gameplay espectacular + reacción).
- El panel/API no admite subir imagen de miniatura: solo guarda
  `thumbnail_text`; cada plataforma pone la portada desde el fotograma
  del vídeo. La cover Vice City se genera aparte como archivo
  (`rdjavi/cover_vicecity.jpg`) para uso manual donde la plataforma
  permita elegir portada.

## Rendimiento (por qué es rápida)

- **Un solo pase** de ffmpeg: layout + punch-in + subtítulos + audio.
- **Fondo pre-renderizado y cacheado**: `rdjavi/bg.png` (un fotograma
  desenfocado + gradiente), hecho una vez por clip en segundos y
  reutilizado como entrada en loop — sin `gblur` por fotograma.
- **Sin re-escalados de más**: cada fuente se escala una sola vez.
- **Audio copiado** (`-c:a copy`): sin `loudnorm` (lento).
- **preset `veryfast`, fps 30** (antes: `fast`, 60 fps, 4 pases).
- El segmento de entrada ya es solo el trozo del clip (nunca el VOD).

## Uso

```bash
# 1. Fondo (una vez por clip; se cachea en rdjavi/bg.png)
# 2. Render en un solo pase (ASS de subtítulos simples ya hecho)
python -m clipagent.rdjavi --segment runs/.../segments/seg.mp4 \
  --start 0 --duration 48.01 --ass rdjavi/subs_simple.ass \
  --gradient rdjavi/vice_gradient.png \
  --out clips/clip_rdjavi_subs.mp4 --bg-frame 20
```

Cada clip nuevo solo cambia `--segment`, `--duration`, `--ass` y
`--out` (y `--bg-frame` / `--punch-in` si el momento lo pide).

```bash
# Vía rápida recomendada: transcode del segmento a h264 1080p30 (una vez)
ffmpeg -y -v error -i runs/.../segments/seg.mp4 \
  -vf "fps=30,scale=1920:1080" -c:v libx264 -preset superfast -crf 23 \
  -pix_fmt yuv420p -c:a copy runs/.../rdjavi/seg_1080p30.mp4
# Plantilla sobre el intermedio (coordenadas x0,75)
python -m clipagent.rdjavi --segment runs/.../rdjavi/seg_1080p30.mp4 \
  --start 0 --duration 48.01 --ass rdjavi/subs_simple.ass \
  --bg runs/.../clips/bg.png --gradient rdjavi/vice_gradient.png \
  --cam-box 421:274:0:0 --cam-square 274:274:68:0 \
  --game-crop 1080:1080:465:0 --out clips/clip_rdjavi_subs.mp4 \
  --bg-frame 20 --punch-in 16 26 --fps 30 --preset veryfast --crf 23
```

## Medición (p3220, 48,01 s, 2026-11-18)

- Render viejo (4 pases, fast, 60 fps, blur por fotograma, loudnorm):
  >28 min solo la parte 1 de 16 s (matado por lento; la VM tenía 3
  ffmpeg a la vez y 2 reinicios del servicio).
- Render plantilla DIRECTO desde el segmento HEVC 1440p60 (1 pase,
  superfast, crf 26, fps 30 primero, fondo cacheado): **matado a los
  28 min con 119 MB** (sesión `proc_8cec8c77f4ed`, 19:34–20:03 UTC).
  No terminó: el cuello de botella es la **decodificación HEVC
  1440p60 en esta VM** (~1 fps), no el filtro ni el preset. Además
  iba a superar el límite de subida del Worker (128 MB).
  **Conclusión: el objetivo "pocos minutos" NO se cumple con el
  segmento HEVC directo.** La plantilla es reutilizable, pero el
  decode del HEVC manda.
- Quemado rápido de subtítulos simples SOBRE EL CLIP BASE h264
  (layout split viejo, sin recomponer): **1m15s** (39 MB, ultrafast,
  crf 28). Prueba de que el encode no es el problema: lo es el
  decode HEVC 1440p.
- **Vía rápida recomendada (en curso, 20:10 UTC):** pre-transcodificar
  el segmento una vez a h264 1080p30 (`fps=30,scale=1920:1080`,
  superfast, crf 23) y luego correr la plantilla sobre ese
  intermedio con `--cam-box 421:274:0:0 --cam-square 274:274:68:0
  --game-crop 1080:1080:465:0` (coordenadas ×0,75). El transcode es
  decode-bound (~10 min), pero el pase estilo Willyrex sobre h264 1080p
  debería tardar ~2-3 min. Tiempo total estimado ~12-15 min por
  clip la primera vez; el intermedio se puede reutilizar para
  re-renders.
- **ACTUALIZACIÓN 21:03 UTC — la vía transcode-first TAMPOCO terminó:**
  transcode OK en 7m47s (intermedio `rdjavi/seg_1080p30.mp4`, 74 MB,
  h264 1920×1080 30 fps, REUTILIZABLE), pero el pase estilo Willyrex sobre el
  intermedio (veryfast, crf 23) llegó a 120 MB en 31 min y se mató
  antes de superar el límite de 128 MB del Worker. Mediciones de
  diagnóstico (10 s): encode plano sin filtros = **12 fps** (24 s);
  grafo de la plantilla (rdjavi v2) completo = **~1 fps** (>5 min para 10 s); grafo sin
  `ass` también ~1 fps; layout simple sin punch-in también ~1 fps.
  **PERO la medición está contaminada:** en ese momento había 4-5
  ffmpeg a la vez en la VM (1,5 CPU compartidas; otros agentes
  también renderizando p3220), así que cada proceso tenía ~0,4 CPU.
  No se puede concluir que el grafo sea patológico: con la VM
  tranquila el encode plano va a 12 fps. **Lección: NO renderizar
  en paralelo en esta VM.** El render pulido debe correr UNA vez,
  con la VM tranquila, sobre el intermedio ya guardado, o mejor en
  GitHub Actions (4 vCPU; tribunal 2026-11-18: 2,5-3,5 min).
- Nota: con el segmento HEVC directo, `--preset superfast` no basta.
  Si el transcode-first no baja de ~15 min, la siguiente opción es
  renderizar el layout a 720p y subir a 1080p, o mover el render a
  GitHub Actions (4 vCPU, ver tribunal 2026-11-18).

## Uso rápido (vía transcode-first, recomendada)
