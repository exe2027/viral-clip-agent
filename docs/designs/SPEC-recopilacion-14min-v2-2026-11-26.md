# SPEC — Recopilación 14 min solo YouTube (v2) — 2026-11-26

Reescribe v1 tras la revisión Goldfish (2026-11-26 13:58, veredicto: NO implementable tal cual).
Cambios respecto a v1: fuente ampliada a borradores validados (H1), descarga por el
camino real del cerebro (H3), uploader nuevo `publish_longform` (H2), título ≤60 (H10),
decisiones de viralidad del usuario 13:54 incorporadas (H11), pre-chequeo de
credenciales y guarda de re-ejecución (H13/H14). Corrección del principal a la
revisión: H4 era falso — verificado en vivo en D1, Sebastián tiene UNA sola cuenta
ES de YouTube (id 2, 15 publicados), kaez una (id 8); no hay ambigüedad de canal.

## 0. Decisiones del usuario
- 2026-11-26 13:49 (widget): **14 min, sin verificar cuentas** (20 min descartado: >15 min exige verificación por teléfono).
- 2026-11-26 13:54 (delegación total): efectos de animación + investigar cómo hacer viral un vídeo. Hallazgos web 2026 aplicados en §3/§4: cold open con el mejor momento en los primeros 10 s, countdown rankeado (tease del #1 al inicio, revelación al final), etiqueta en pantalla por clip (#N + título), pattern interrupts cada 30-60 s, capítulos, título <60 caracteres, audio normalizado.
- 2026-11-26 13:58 ("Así que empieza"): orden de construir. La miniatura personalizada queda FUERA de v1 por decisión del principal: `thumbnails.set` exige canal verificado, la misma verificación que la decisión 13:49 quiso evitar. Si el canal se verifica después, la miniatura pasa a v2 (y entonces el vídeo podría ser de 20 min).

## 1. Objetivo
Subir a cada canal de YouTube (ES) una recopilación de ~14 min con los mejores momentos de GTA 6, para sumar visitas de formato largo (el canal hoy solo tiene Shorts). Un vídeo por tenant (sebastián y kaez), cada uno con sus propios clips.

## 2. Selección (qué entra)
- Fuente: borradores validados de la Cola, NO solo publicados. Con solo `status='publicado'` es matemáticamente imposible: seba 13 clips con MP4 = 7:52, kaez 6 = 4:47 (verificado en D1 por la revisión).
- Filtro duro por clip: `status='en_carpeta'` + veredicto de calidad válido (`viral.quality_gate.pasa=1`) + `video_r2_key <> ''` (la columna es `TEXT NOT NULL DEFAULT ''`: el filtro `IS NOT NULL` nunca filtra) + `publish_blocked=0` + `archived=0` + `language='es'`.
- Orden: `score` DESC, desempate por `created_at` más reciente.
- Dedupe: mismo momento no entra dos veces — excluir si coincide `(source_id + ventana start_sec–end_sec solapada)` o (título normalizado + duración).
- Corte por duraciones REALES (ffprobe de cada MP4 descargado, no metadata D1): añadir clips en orden mientras el total < 13:30; si el siguiente empuja el total por encima de 14:00, intentar sustituirlo por el siguiente más corto que quepa; tope duro 14:30 medido por ffprobe del archivo final.
- Pre-chequeo: si el pool filtrado suma < 13:30, parar e informar (no construir un vídeo corto en silencio).
- Acceso real: el cerebro no consulta D1 directamente; selección vía `GET /api/clips` + Cola (`PanelBridge.list_clips`), orden por score y filtro de idioma en cliente. El endpoint de publicados tiene `LIMIT 50` y ordena por recencia: no usarlo para esto.
- La lista final (clip_id, título, duración real, score) queda en el informe antes de subir nada. Los clips cuya descarga falla se descartan y se toma el siguiente por score; el informe lista los descartados.

## 3. Ensamblado (lo hace el programa, nunca a mano)
- Descarga de cada MP4 por `GET /api/clips/:id/video` (`PanelBridge.download_video`, el camino que usa el cerebro en Actions). PROHIBIDO: `/api/public-video` (no existe para el cerebro: firmarla exige `SESSION_SECRET`, que el cerebro no tiene), `wrangler r2` (trampa del split-bucket) y la API directa de R2.
- Estructura viral (decisión 13:54):
  1. Cold open: primeros 10 s del clip #1 (teaser), cortados del mismo clip.
  2. Countdown: resto de clips ordenados de peor a mejor por score, con etiqueta en pantalla `#N · <título>` (ffmpeg drawtext, fade in/out 0,5 s).
  3. Revelación: el clip #1 completo al final.
  4. Transiciones: xfade 0,3 s entre clips (sin efectos cursis). Pattern interrupt: cada etiqueta + cada cambio de clip ya es un cambio visual; sin zooms artificiales en v1.
  5. Audio: normalizado entre clips (loudnorm) para que no haya saltos de volumen. Audio original de cada clip, sin música añadida en v1.
- Normalización: re-encode a formato único (H.264 + AAC, 1080x1920 vertical, mismo fps) para concatenar sin fallos. Se mantiene el formato vertical de los clips.
- Verificación antes de subir: duración total 13:30–14:30 por ffprobe del archivo final, archivo reproducible. Si falla, se para y se informa; no se sube nada a medias.

## 4. Metadatos
- Título (≤60 caracteres, regla del proyecto): `Los momentos más divertidos de GTA 6 | 26 nov` (45 caracteres).
- Descripción: texto corto + capítulos con timestamps (`0:00 <título del momento 1>`, ...) generados de la lista final. YouTube exige ≥3 capítulos, el primero en 0:00, cada uno ≥10 s (los clips del top duran 11 s o más).
- Tags: ≤15 en total.
- Categoría: Gaming (`categoryId "20"`). Idioma: `defaultLanguage "es"`. Estado: público.
- Miniatura v1: sin miniatura personalizada (YouTube elige una). Ver §0.

## 5. Subida
- Función NUEVA `publish_longform(path, title, description, tags, privacy)` en `clipagent/`. Reutiliza SOLO las piezas de bajo nivel: `Credentials.from_authorized_user_file(TOKEN_FILE, [SCOPE])`, `build("youtube", "v3")`, `MediaFileUpload(..., resumable=True)`, `videos().insert(part="snippet,status")`. PROHIBIDO usar para este vídeo: `YouTubeShortsPublisher.publish()`, `dry_run()`, `validate_package()` (exige 55–65 s y paquete viral) y `check_limits()` (topes de Shorts: la compilación NO consume cupo de Shorts, ni espaciamiento, ni rolling).
- Devuelve URL `https://www.youtube.com/watch?v={id}` (no `/shorts/`).
- Pre-chequeo de credenciales ANTES de descargar y renderizar (el render es lo caro): tokeninfo + scope `youtube.upload` válido para el tenant. `status='conectada'` en D1 no basta: es estado guardado, no verificación en vivo.
- Ejecución: en esta VM con el entorno del cerebro, UNA corrida por tenant (`PANEL_TENANT` + `PANEL_BRAIN_TOKEN`, como exige `panel_credentials.py`). Dos ejecuciones en total.
- Guarda de re-ejecución: si ya existe `runs/compilation-2026-11-26/<tenant>/report.json` con `video_id`, no subir de nuevo sin `--force` explícito.
- Registro: informe JSON en `runs/compilation-2026-11-26/<tenant>/report.json` (lista de clips, descartados, duración final, video_id, URL). La recopilación NO aparece en la Cola/tracking del panel (la subida directa por API no pasa por el Worker): el informe es el único registro.

## 6. Seguridad / anti-ban
- Un vídeo largo no compite con los Shorts del día (otro formato, subida directa por API, fuera de los gates del Worker). No se suben 2 recopilaciones el mismo día al mismo canal.
- Contenido reutilizado: son clips propios ya validados, seleccionados y ordenados (compilación). No es material de terceros. Advertencia honesta: YouTube puede limitar la monetización de contenido reutilizado; esto es para visitas, no se promete monetización de este vídeo.

## 7. Verificación (antes de darlo por hecho)
1. El pool filtrado suma ≥13:30 y cada clip existe con MP4 real.
2. El archivo final dura 13:30–14:30 (ffprobe) y se reproduce.
3. La subida devuelve video_id y URL pública por tenant.
4. Se informa: clips usados (títulos), duración exacta, URLs de YouTube.

## 8. Fuera de alcance (v1)
- Recopilación EN, miniatura personalizada (hasta verificar el canal), intros con voz, música de fondo, pipeline diario/semanal de recopilaciones, Shorts a partir de la recopilación.
