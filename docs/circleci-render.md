# Render en CircleCI (job `render`)

El job `render` renderiza UN clip a la plantilla rubius desde una
URL pública, en CircleCI, sin tocar el panel, sin escribir en D1
y sin publicar. El MP4 final queda como artefacto del job, solo
si pasa el gate cuadro por cuadro (`clipagent/render_gate.py`).

## Disparo (manual por API/UI, sin cron, sin auto-disparo)

El workflow `render` solo corre cuando se dispara a mano con
`source_url`. En un push normal `source_url` va vacío y el
workflow se salta (el job `tests` sigue corriendo como siempre).

### Por API (curl)

Necesitas un **token personal de CircleCI** (no va en el repo):
créalo en https://app.circleci.com/settings/user/tokens y
guárdalo como `CIRCLE_TOKEN` en tu entorno (nunca en un archivo
del repo ni en un log).

```bash
export CIRCLE_TOKEN="tu-token-personal-de-circleci"
curl -X POST https://circleci.com/api/v2/project/gh/exe2027/viral-clip-agent/pipeline \
  -H "Circle-Token: $CIRCLE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "branch": "circleci-project-setup",
    "parameters": {
      "source_url": "https://www.youtube.com/watch?v=mRkTtXaNDNU",
      "streamer": "DjMaRiiO",
      "title": "GTA 6: DjMaRiiO roba un avión COMERCIAL gigante",
      "start_sec": "223",
      "end_sec": "281",
      "lang": "es",
      "punch_zoom": "1.3",
      "shake": true,
      "shake_amp": "5",
      "impact": true,
      "banner_slide": true,
      "fade_in": true,
      "whoosh": true,
      "payoff": "0",
      "cam_box": "",
      "cam_square": "",
      "model": "tiny"
    }
  }'
```

Ejemplo: el remake de DjMaRiiO (58 s, del VOD `mRkTtXaNDNU`).
El MP4 sale en el artefacto `rubius-render` del job (`clip_rubius.mp4`
+ `result.json` con el resultado del gate).

### Por UI

CircleCI -> Pipelines -> Trigger Pipeline -> rama
`circleci-project-setup` -> rellenar los parámetros (los mismos
de arriba). Sin `source_url` el workflow no corre.

## Parámetros

| Parámetro | Tipo | Default | Qué hace |
|---|---|---|---|
| `source_url` | string | (obligatorio) | URL del vídeo fuente (YouTube/Instagram/Twitch o MP4 directo) |
| `streamer` | string | (obligatorio) | Nombre del streamer |
| `title` | string | (obligatorio) | Título del banner amarillo |
| `start_sec` | string | (obligatorio) | Inicio del tramo en segundos (decimal) |
| `end_sec` | string | (obligatorio) | Fin del tramo (máximo 60 s de tramo) |
| `lang` | string | `es` | Idioma de los subtítulos: `es` \| `en` |
| `punch_zoom` | string | `1.3` | Zoom del punch-in (1,18 receta, 1,30 marcado) |
| `shake` | boolean | `true` | Shake de cámara en el payoff |
| `shake_amp` | string | `5` | Amplitud del shake en px |
| `impact` | boolean | `true` | Destello de brillo en el payoff |
| `banner_slide` | boolean | `true` | El banner entra deslizándose |
| `fade_in` | boolean | `true` | Fundido desde negro al inicio |
| `whoosh` | boolean | `true` | Whoosh al arrancar el punch-in |
| `payoff` | string | `0` | Segundo del momento fuerte (0 = auto: 20 o duración-4) |
| `cam_box` | string | (vacío = auto) | Caja de la webcam `w:h:x:y` en px del original |
| `cam_square` | string | (vacío = auto) | Cuadrado de la cara `w:h:x:y` |
| `model` | string | `tiny` | Modelo faster-whisper para transcribir |

## Qué hace el job (paso a paso)

1. **Descarga** la fuente con yt-dlp (`--download-sections`, solo
   el tramo; `--compat-options no-certifi`, nunca
   `--no-check-certificates`) y corta el tramo con ffmpeg.
   La fuente tiene que ser horizontal 16:9 (un VOD/directo); una
   fuente vertical falla fuerte con el motivo.
2. **Detecta la caja de la webcam** con `render.decide_layout`
   (o usa `cam_box`/`cam_square` explícitos). Sin cámara
   detectable y sin caja explícita, FALLA FUERTE: la plantilla
   rubius exige cámara arriba y juego abajo; un juego sin cámara
   no se renderiza a rubius en silencio.
3. **Transcribe** el tramo con faster-whisper (tiny, local) ->
   SRT + `.transcript.json` (para la decisión SFX).
4. **Renderiza** con `rubius.render`: banner amarillo, subtítulos
   palabra por palabra, punch-in, zoom del gameplay, shake,
   impacto, fundido, banner deslizante + SFX (boom/riser en el
   payoff + whoosh al arrancar el punch-in, en la misma mezcla).
5. **Valida** el MP4 final con `render_gate` (cuadro por cuadro:
   dimensiones, gameplay, sin bandas, banner, subtítulos blancos
   de una palabra, cámara viva, SFX, Arial Black). **Si el gate
   NO pasa, el job falla y NO se guarda el artefacto.**
6. **Guarda** `clip_rubius.mp4` + `result.json` (+ fotogramas del
   gate) como artefacto `rubius-render`. El segmento descargado
   se borra antes de guardar (es re-derivable de la URL).

## Coste ($0)

Plan Free de CircleCI: 30.000 créditos/mes, sin tarjeta.
Executor `small` (1 vCPU, 5 créditos/min). Un render de ~60 s:
setup ~4-6 min en frío (~1-2 min con caché de pip caliente) +
descarga/corte ~1 min + transcripción tiny ~1-2 min + render
~1-2 min + gate ~1 min ≈ 8-13 min ≈ **40-65 créditos por clip**
(~500 clips/mes con la cuota free, mucho más que el uso real).

## Límites (honestos)

- Un render por disparo; sin re-renders en bucle (si el gate no
  pasa, se revisa el motivo y se vuelve a disparar a mano).
- La fuente tiene que tener webcam visible y voz (sin voz no hay
  SRT y el gate deja los subtítulos como débil; sin cámara no hay
  render rubius).
- El corte es de un VOD 16:9: el recorte del gameplay se calcula
  del tamaño real de la fuente (1440:1440:560:0 en 2560x1440,
  1080:1080:420:0 en 1920x1080).
- El job NO publica, NO escribe en D1, NO toca el panel: el MP4
  hay que subirlo al panel a mano (o con el flujo normal del
  cerebro) después de verlo.
- El gate de este job es el espejo compacto de `template_gate`
  de main (`clipagent/render_gate.py`): mismas comprobaciones y
  umbrales, sin portar las ~1400 líneas de `final_review.py` de
  main. La comprobación "cámara a ancho completo" no se mide aquí
  (necesita el eslabón de bordes de main): en el render rubius la
  cámara siempre es a ancho completo por construcción.

## Qué falta para probarlo en vivo

1. **Commitear y pushear** esta rama (`circleci-project-setup`):
   el trabajo está sin commitear en el worktree
   `~/workspace/circleci-render-wip` (el usuario decide el
   commit/push).
2. **Conectar el proyecto en CircleCI** (si no lo está):
   app.circleci.com -> Add Projects -> `exe2027/viral-clip-agent`
   -> Set Up Project (usa la config del repo, no hace falta
   `config.yml` de ejemplo). La rama `circleci-project-setup`
   tiene que existir en el remoto para disparar sobre ella.
3. **Token personal** de CircleCI (ver arriba) para disparar por
   API. Por UI no hace falta token.
4. Disparar el ejemplo de DjMaRiiO de arriba y revisar el
   artefacto `rubius-render` (el MP4 + `result.json` con el gate).
