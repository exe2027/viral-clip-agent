# SPEC — Recopilación 14 min solo YouTube (v1) — 2026-11-26

## 0. Decisión del usuario
- 2026-11-26 13:42: "¿Que no tengamos un video largo ni kaez ni yo... una recopilación de los momentos más divertidos? ¿Te parece?" → "Se podrá hacer eso?" → "Podrías subir un vídeo a kaez y a mi de 20 minutos, de diferentes momentos en clips, una recopilación" → "Eso solo hablando de YouTube".
- 2026-11-26 13:49 (widget): **"Mejor 14 min, sin verificar cuentas"** — se descarta 20 min para no depender de la verificación de cuenta de YouTube (>15 min exige cuenta verificada; 14 min no).
- Alcance v1: UN vídeo por tenant (sebastián y kaez), solo YouTube, solo español, con los mejores momentos ya publicados. Sin tocar Shorts/Reels ni los topes de publicación diaria de clips.

## 1. Objetivo
Subir a cada canal de YouTube (ES) una recopilación de ~14 min con los momentos más divertidos de GTA 6 ya publicados como clips, para sumar visitas de formato largo (el canal hoy solo tiene Shorts).

## 2. Selección (qué entra)
- Fuente: tabla `clips` de la D1 de cada tenant (status='publicado'), idioma ES.
- Orden: `score` (viralidad al corte) DESC, desempate por `created_at` más reciente.
- Filtro duro: solo clips con `video_r2_key` no nulo (sin MP4 en R2 no hay material). Sin filtro de fecha en v1 (vale material de cualquier día).
- Corte: se van sumando clips en ese orden hasta llegar a 13:30–14:00 de duración total. NUNCA superar 14:30 (margen de seguridad bajo los 15 min).
- Exclusiones: clips archivados (`archived=1`) y clips con `publish_blocked=1` quedan fuera.
- La lista final (video_id, título, duración, score) queda en el informe de la corrida antes de subir nada.

## 3. Ensamblado (lo hace el programa, nunca a mano)
- Descarga de cada MP4 desde R2 **solo a través del Worker** (URL firmada `/api/public-video`, como hace el cerebro). NUNCA `wrangler r2` (trampa del split-bucket: wrangler ve otro bucket que el Worker — AGENTS.md 2026-11-20).
- Normalización: re-encode a un formato único (H.264 + AAC, 1080x1920 vertical, mismo fps) para poder concatenar sin fallos. Se mantiene el formato vertical de los clips (un vídeo vertical de 14 min es un vídeo normal en YouTube, no un Short).
- Concatenación con ffmpeg, cortes duros, sin transiciones ni intro en v1. Audio original de cada clip.
- Verificación antes de subir: duración total ≤14:30, archivo reproducible (ffprobe), tamaño razonable. Si falla, se para y se informa; no se sube nada a medias.

## 4. Metadatos
- Título: `Los momentos más divertidos de GTA 6 | Recopilación` + fecha (ej. `... | 26 nov 2026`). Un título por tenant (mismo texto, cada uno en su canal).
- Descripción: texto corto + capítulos con timestamps (`0:00 <título del momento 1>`, ...) generados de la lista final. Sin inventar momentos: cada capítulo es un clip real con su título real.
- Tags: `GTA 6, GTA VI, momentos divertidos, recopilación, gameplay español` (+ tags del top clip si aportan).
- Miniatura v1: sin miniatura personalizada (YouTube elige una). Mejora v2: generar desde el mejor momento.
- Categoría: Gaming. Idioma: es. Estado: público.

## 5. Subida
- Vía YouTube Data API v3 `videos.insert` con subida reanudable, reutilizando el camino existente (`clipagent/publish.py`, scope `youtube.upload`, 1 unidad de cuota por insert — despreciable).
- Credenciales por tenant: las de cada tenant en `panel_credentials` (canal ES de sebastián y canal ES de kaez). Si las credenciales de un tenant no son válidas, ese tenant se salta con motivo y el otro sigue.
- Un solo vídeo largo por canal en v1 (no es un pipeline diario; es una acción puntual).
- Registro: informe JSON en `runs/compilation-2026-11-26/<tenant>/report.json` (lista de clips, duración, video_id de YouTube, URL). Sin cambios de esquema D1 en v1.

## 6. Seguridad / anti-ban
- Un vídeo largo no compite con los Shorts del día (no consume cupo de Shorts ni espaciamiento de clips; es otro formato). No se suben 2 recopilaciones el mismo día al mismo canal.
- Contenido reutilizado: son clips propios ya publicados, seleccionados y ordenados (compilación). No es material de terceros. Advertencia honesta: YouTube puede limitar la monetización de contenido reutilizado; esto es para visitas, no se promete monetización de este vídeo.
- El corte/ensamblado lo hace el programa (regla del usuario 2026-11-24: nunca Muse a mano).

## 7. Verificación (antes de darlo por hecho)
1. La lista de clips seleccionados es real (existen en D1 y tienen MP4 en R2).
2. El archivo final dura 13:30–14:30 y se reproduce.
3. La subida devuelve video_id y URL pública por tenant.
4. Se informa: clips usados (títulos), duración exacta, URLs de YouTube.

## 8. Fuera de alcance (v1)
- Recopilación EN, intros/outros, transiciones, miniatura personalizada, capítulos manuales, pipeline diario/semanal de recopilaciones, Shorts a partir de la recopilación.
