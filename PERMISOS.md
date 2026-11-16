# Permisos — kit y ficha de permiso

Regla dura del proyecto: **sin permiso del streamer, no se publica**.
Ningún clip de un directo ajeno se sube a ninguna plataforma sin una
ficha de permiso completa en este archivo (o en `influencers.yaml`).
Un "ok" ambiguo, un "dale" sin contexto o un silencio **NO es permiso**.

## Cómo pedirlo

- Por el canal que el streamer **lee**: DM de Instagram/Twitter, Discord
  o correo. **Nunca por el chat en vivo** del directo (se pierde, no
  queda registro y molesta en medio del stream).
- **Sin bots de DM masivos**: un mensaje por streamer, escrito a mano,
  con su nombre y su juego.
- **Sin prometer ingresos ni "exposición"**: no se ofrece dinero, ni
  reparto, ni "te hago crecer". Se pide permiso, punto.
- **Acotado**: por juego y por periodo (por ejemplo, "clips de GTA 6
  durante noviembre"). Un permiso abierto e indefinido no se pide.
- Si no responde en 7 días, se da por no concedido y no se insiste.

## Kit de permiso — Español (ES)

> Hola [nombre], soy Sebastián, creador de contenido de GTA 6.
> Estoy haciendo clips cortos (60 s, vertical) de los mejores momentos
> de directos de GTA 6 y me gustaría incluir algunos de los tuyos,
> siempre con tu nombre y tu canal en el título y la descripción.
> ¿Te parece bien si uso clips de tus directos de GTA 6 durante
> noviembre para publicarlos en TikTok, Instagram, YouTube y Facebook?
> Si en algún momento quieres que baje alguno, lo borro sin preguntas.

## Permission kit — English (EN)

> Hi [name], I'm Sebastián, a GTA 6 content creator.
> I'm making short clips (60 s, vertical) of the best GTA 6 stream
> moments and I'd love to include some of yours, always crediting
> you and your channel in the title and description.
> Would it be OK if I use clips from your GTA 6 streams during
> November to post them on TikTok, Instagram, YouTube and Facebook?
> If you ever want one taken down, I'll remove it right away, no questions asked.

## Ficha de permiso — qué cuenta como evidencia

Una ficha está completa solo cuando tiene **todos** estos campos:

| Campo | Qué poner |
|---|---|
| `usuario` | Nombre del streamer y handle exacto (ej. `@locones`) |
| `plataforma` | Dónde está el directo original (Twitch / Kick) |
| `fecha` | Fecha en que concedió el permiso (AAAA-MM-DD) |
| `texto exacto` | El mensaje del streamer, copiado literal, sin resumir |
| `captura / enlace` | Captura guardada en `runs/permisos/` o enlace al DM/mensaje |
| `alcance` | Qué clips (qué juego, qué VODs), qué plataformas de publicación, por cuánto tiempo |
| `revocable` | Siempre sí: el streamer puede retirarlo cuando quiera y el clip se baja |

**Un "ok" ambiguo NO es permiso.** El texto exacto tiene que decir
claramente que sí se pueden usar sus clips y publicarlos. Si el mensaje
dice "ok 👍" a una pregunta vaga, se repregunta con el texto del kit
antes de darlo por concedido.

## Plantilla de ficha (vacía, lista para llenar)

```yaml
- usuario: ""
  plataforma: ""        # twitch | kick
  canal_original: ""    # ej. https://www.twitch.tv/locones
  fecha_permiso: ""     # AAAA-MM-DD
  canal_contacto: ""    # dm instagram | discord | correo | ...
  texto_exacto: ""      # copiar literal el mensaje del streamer
  captura: ""           # runs/permisos/<streamer>-permiso.png
  alcance:
    juego: ""           # ej. GTA 6
    vods: []            # ej. ["2929669422"]
    plataformas_publicacion: []  # youtube | tiktok | instagram | facebook
    hasta: ""           # AAAA-MM-DD o "indefinido" (solo si el streamer lo dijo)
  revocable: true
  estado: pendiente     # pendiente | concedido | denegado | revocado
  notas: ""
```

## Fichas concedidas

_(Vacío por ahora. Objetivo: 3-5 permisos antes del 19 nov 2026,
lanzamiento de GTA 6.)_

<!-- Copiar la plantilla de arriba por cada permiso concedido. -->
