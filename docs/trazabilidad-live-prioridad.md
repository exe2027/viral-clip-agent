# Trazabilidad + prioridad LIVE — diseño

Fecha: 2026-11-17. Pedido por Sebastián en chat. Estado: DISEÑO APROBADO
con opción B (LIVE con impulso +15, decidida por él el 2026-11-17
12:59). Pendiente de que termine el arreglo del panel en curso (4 cosas
del 12:50: Crear no corta solo, previews, Cola con preview + botón
Publicar, sección salud). Este cambio va justo después de ese arreglo,
no en paralelo.

## Lo que pidió (sus palabras)

- Trazabilidad paso a paso: una sección que muestre el tracking desde que
  el programa ve un video/live hasta que se sube.
- El live/stream tiene prioridad contra el VOD: si hay un influencer en
  vivo, primero se sacan los clips de ahí.
- En la Cola debe haber segmentos VOD y LIVE.
- Algoritmo con todo lo que ya tenemos de score y puntuación, más la
  prioridad del live.

## 1. Cambio de decisión — ojo

El 2026-11-16 se acordó lo contrario: clips desde VODs terminados antes
que desde el vivo, porque el VOD deja comparar todos los picos contra la
mediana y quedarse solo con los mejores, mientras el vivo solo gana
velocidad. Este pedido lo revierte: **LIVE primero, VOD después**.

Consecuencia honesta: un clip live se juzga con información parcial
(baseline rodante de 15 min, sin poder re-rankear contra el resto del
stream). Puede verse muy bueno en el momento y quedar flojo contra el
VOD completo. Mitigación (§5): el score live es **provisional** y cuando
el VOD cierra se re-puntúa contra el VOD completo; el tracking muestra
ambos ("provisional → confirmado" o "bajó").

Qué entiende él por "live" (aclarado por él el 2026-11-17): ir sacando
clips del directo mientras pasa ("sacar VOD en vivo"). Los picos se ven
y se puntúan EN EL MOMENTO, con baseline rodante, no después. Lo único
provisional es el re-ranking final contra el VOD completo; la detección
y el puntaje del pico son en vivo y tienen que verse así en el Tracking
(vista "Ahora": pico actual, z, chat/min, score al momento).

## 2. Pipeline paso a paso (lo que registra el tracking)

Cada clip lleva un historial: cada cambio con hora de Chile, quién lo
hizo (watcher / worker / usuario / sistema), de qué paso a qué paso,
motivo, y snapshot de scores en ese momento. Si un paso falla o se
atasca, queda en rojo con el motivo, no en silencio.

| Paso | Quién | Qué se registra |
|---|---|---|
| 1. Detectado | watcher | LIVE: canal en vivo, juego, viewers, hora de inicio. VOD: VOD nuevo terminado, duración, juego. |
| 2. Pico detectado | watcher/scorer | Score de chat, z, usuarios, risas/hype, mensajes de ejemplo. Live: baseline rodante 15 min. VOD: múltiplo vs mediana + rank top-5. |
| 3. Admisión a la cola | watcher | Entra o queda como candidato, con motivo: cooldown, topes (3/h/stream, 6/h global, cola máx. 8), dedup, disco < 2 GB, kill-switch, juego no GTA 6, sin permiso. |
| 4. En cola de corte | worker | Posición en su segmento (LIVE o VOD), prioridad y desglose, por qué está ahí y quién va antes. |
| 5. Cortando | worker | Progreso real (descargar segmento → cortar → guardar), no "en espera" sin más. |
| 6. Subtítulos | worker | Whisper tiny local, o "sin voz" si no hay habla. |
| 7. Paquete viral + scores | worker | viral_score con sus 7 componentes, SEO por plataforma, prioridad por plataforma, estado (publicable / override / bloqueado / descartado). |
| 8. Listo para revisión | panel | Queda en `ready_for_review`, con preview. Aprobación humana sigue vigente. |
| 9. Aprobado / rechazado | usuario | Quién, cuándo, y motivo del rechazo (falso positivo, mal layout, juego equivocado...). |
| 10. En cola de publicación | publisher | Una entrada por plataforma: hueco asignado (18:00 / 20:30 / 23:00), presupuesto diario restante, posición. |
| 11. Publicado / fallido | publisher | Hora, link, o motivo del fallo (con circuit breaker: 2 fallos seguidos pausan la plataforma). |

Fallos que el tracking debe mostrar con nombre propio: segmento expirado
del CDN, gap de chat (IRC caído), 429 de Kick/Twitch (stop, backoff),
stream caído, disco lleno, kill-switch activo.

## 3. Sección Tracking en el panel

- **Vista "Ahora"**: quién está en vivo ahora, qué ve el watcher
  (msgs/min, último pico, baseline), VODs nuevos hoy.
- **Lista de clips**: cada uno con su paso actual, tiempo en ese paso, y
  barra del pipeline (paso X de 11). Filtros: LIVE / VOD, streamer, estado.
- **Clic en un clip**: su línea de tiempo completa paso a paso, con horas
  y motivos. Nada de "se quedó pegado en descarga" sin decir dónde ni por qué.
- **Alertas**: atascado más de lo normal en un paso, expirado, rechazado.

## 4. Cola con 2 segmentos

La Cola se divide en dos segmentos visibles, LIVE arriba y VOD abajo,
cada uno con su contador:

- **Segmento LIVE**: clips de streams en vivo, ordenados por prioridad.
- **Segmento VOD**: clips de VODs terminados, ordenados por prioridad.
- El worker vacía el segmento LIVE primero y después el VOD.
- El watcher encola los triggers live antes que los VODs nuevos.
- Los 32 videos que hoy hay en cola son VOD: quedan en el segmento VOD.
  Esto se suma al arreglo en curso ("cola antes que buscar VODs nuevos").

## 5. Algoritmo — la fórmula NO se toca

La fórmula de `clipagent/priority.py` es INTOCABLE (MÁSTER PROMPT FINAL):
pesos, umbrales y filtros no cambian. La prioridad LIVE se aplica como
**nivel (tier) por encima de la fórmula**, no dentro de ella:

```
orden = (segmento LIVE primero, dentro del segmento por prioridad)
Prioridad = viralidad^0,5 × SEO^0,3 × chat_norm^0,2   (por plataforma)
```

Filtros duros iguales para live y VOD: viralidad < 50 descartado,
SEO < 90 no se publica salvo override registrado, SEO < 70 bloqueo duro,
> 7 días pierde 10 pts/día, no dos clips del mismo momento el mismo día
en la misma plataforma, 3 días sin subir por prioridad baja se archiva
solo. Aprendizaje igual: a los 30 clips publicados con datos a 7 días,
recalibra pesos máx. ±25% por ciclo.

Ejemplo real con la fórmula (SEO de YouTube, chat 8x → chat_norm ~73,
chat 10x → ~90):

- Clip LIVE: viralidad 62, SEO 92, chat 8x → prioridad ~72.
- Clip VOD: viralidad 90, SEO 98, chat 10x → prioridad ~92.
- Con prioridad LIVE absoluta: se corta/publica primero el live (~72),
  aunque el VOD (~92) sea mejor clip. Con impulso +15: el live queda en
  ~87 y el VOD (~92) sigue primero. Esa es la diferencia que decide §7.

Umbrales de disparo live (de los diseños del 16-nov, SIN validar aún con
datos reales de GTA 6 — hace falta la Fase 0 en sombra): z local ≥ 8,
adj ≥ 25, usuarios ≥ 12, risas+hype ≥ 8, penalty anti-spam ≥ 0,5,
subida ≥ 0,5, juego GTA 6 verificado, en lista blanca, confirmación en
la ventana siguiente, cooldown 8 min, 3/h por stream, 6/h global,
cola máx. 8.

## 6. Qué cambia en código (resumen)

- `clipagent/priority.py`: la fórmula NO se toca. `order_queue` acepta
  el segmento y ordena LIVE primero, prioridad después.
- `jobqueue.py`: `next_queued` ordena por (fuente LIVE primero, score
  efectivo después — score + bono por antigüedad 2 pts/día, máx. 14).
  Nueva columna `source` (live/vod) y tabla `job_events` para el historial.
- `watcher.py`: hoy solo encola VODs terminados; los triggers live
  crearán jobs (esto es el build del cutter live, §8, no este cambio).
- Panel: sección Tracking + Cola en 2 segmentos + tier en el orden.
- No cambia: filtros duros, topes diarios por plataforma (YT 3-5/día,
  TikTok/IG/FB 1/día en cuentas nuevas, huecos 2-3 h), aprobación humana
  por clip, fases de auto-publicación, Regla Nº 1 (nada se sube en el
  momento).

## 7. Decisión tomada (2026-11-17 12:59)

- **B) LIVE con impulso +15 (ELEGIDA)**: el live suma 15 puntos a su
  prioridad solo para ordenar. Un live bueno pasa a casi todos los VOD,
  pero un VOD excelente (~92) sigue por delante de un live flojo
  (~72 → 87). El score sigue mandando entre clips parecidos.
- A) LIVE siempre primero (absoluta): descartada por él.

## 9. No borrar el clip hasta terminar todas las plataformas (2026-11-17)

Problema que vio él: el flujo era aprobar → programar → publicar →
borrar, y si el clip se borra al subirse a la primera plataforma (p. ej.
YouTube), ya no se puede subir a las demás, porque cada plataforma tiene
su propio tope diario y su propio hueco. Un clip aprobado para 4
plataformas tarda 2-4 días en terminarlas todas.

Arreglo (ya existe la pieza clave en el agente: `publish_queue.json` y
`PublishQueue` en `clipagent/publish.py` ya llevan UNA entrada por
plataforma —mismo clip, estado propio, `scheduled_for`, `published_at`,
`platform_id` e historial—; lo que hay que arreglar es el borrado del
panel, que hoy es por clip):

- **El archivo del clip NO se borra cuando se publica en una
  plataforma.** Se borra solo cuando TODAS las plataformas aprobadas
  para ese clip están en estado terminal: publicado, fallido, expirado
  (TTL 72 h) o cancelado.
- **El panel muestra el estado por plataforma en cada clip**: p. ej.
  YouTube ✓ publicado 17 nov, TikTok ⏳ programado 18 nov 20:00,
  Instagram ⏳ programado 19 nov, Facebook ✗ fallido (motivo). Mientras
  falte una, el clip sigue visible en la Cola con su progreso por
  plataforma, no desaparece.
- **Borrado manual**: siempre disponible, pero con aviso que liste las
  plataformas pendientes ("TikTok e Instagram aún no se han subido — si
  borras, esas publicaciones se cancelan"). Sin ese aviso, no se borra.
- **Si una plataforma falla**: su entrada queda para reintento o para
  resolverla a mano; no bloquea a las demás ni borra el clip.
- Esto cambia el paso "borrar" del flujo de Fase 2: borrar es el final
  de TODAS las plataformas, no de la primera.

1. Panel (este cambio, tras el arreglo en curso): Tracking + Cola en 2
   segmentos + tier LIVE en el orden + **no borrar el clip hasta que
   todas sus plataformas terminen (§9)**. Funciona ya con los clips VOD.
2. Cutter live, aparte: Fase 0 en sombra (solo observa, no corta) →
   Fase 1 semi-manual el 19-nov (2 streams, el usuario dispara el
   corte) → Fase 2 auto-corte a cola de aprobación. Sin esto, el
   segmento LIVE se llena solo cuando el watcher live exista.
3. Al cerrar el VOD: re-puntuación del clip live contra el VOD completo,
   visible en su tracking.
