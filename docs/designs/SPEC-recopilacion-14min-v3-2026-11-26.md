# SPEC — Recopilación 14 min solo YouTube (v3) — 2026-11-26

Reescribe v2 tras la revisión Goldfish DELTA (2026-11-26 14:09, veredicto: NO
implementable tal cual). Cambios respecto a v2: dedupe por ventana solapada
(N1/N3), fórmula de duración completa (N2), teaser de 12 s (N7), id de kaez
corregido (N4), contrato de validación de `publish_longform` (N6), pre-chequeo
definido (N5), punto de entrada nombrado (N8). Todo lo demás de v2 se mantiene.

## 0. Decisiones del usuario
- 2026-11-26 13:49 (widget): **14 min, sin verificar cuentas**.
- 2026-11-26 13:54 (delegación total): efectos de animación + investigar cómo hacer viral un vídeo. Aplicado en §3/§4: cold open, countdown rankeado, etiquetas `#N`, pattern interrupts, capítulos, título <60, audio normalizado.
- 2026-11-26 13:58 ("Así que empieza"): orden de construir. Miniatura personalizada FUERA de v1: `thumbnails.set` exige canal verificado, la misma verificación que la decisión 13:49 quiso evitar.
- **Enmienda 2026-11-26 14:24 (decisión del usuario, widget "Sumar los ya publicados y re-medir"): la fuente incluye TAMBIÉN los clips ya publicados en ES (status='publicado'), con los mismos filtros (MP4, no bloqueado, no archivado, idioma ES) y el mismo dedupe. Motivo: el pre-chequeo v3 quedó corto en ambos tenants (seba 11 clips/448 s, kaez 14/568 s) y hay más material en cola. Si con publicados sigue sin llegar a 13:30, se para e informa, no se construye un vídeo corto en silencio.**
- **Enmienda 2026-11-26 14:40 (orden del usuario: "Haz dos vídeos grandes entre las colas de seba y las colas de kaez pero no sean los mismos"): pool COMBINADO de ambas colas para DOS vídeos DISJUNTOS (ver §2b). Un mismo clip o momento va a UN solo vídeo.**

## 1. Objetivo
Subir a cada canal de YouTube (ES) una recopilación de ~14 min con los mejores momentos de GTA 6. Un vídeo por tenant (sebastián y kaez), cada uno con sus propios clips.

## 2. Selección (qué entra)
- Fuente: borradores validados de la Cola + clips ya publicados en ES (enmienda 14:24). Con solo publicados es imposible (seba 7:52, kaez 4:47); con solo borradores validados el pre-chequeo quedó corto (seba 11/448 s, kaez 14/568 s).
- Filtro duro por clip: `status IN ('en_carpeta', 'publicado')` + veredicto de calidad válido (`viral.quality_gate.pasa=1`; los publicados pasaron las puertas al publicarse: si su `pasa` no es 1 en D1, se aceptan igual por haber sido publicados, y se anota cuántos entraron por esta vía) + `video_r2_key <> ''` + `publish_blocked=0` + `archived=0` + `language='es'`.
- Orden: `score` DESC, desempate por `created_at` más reciente.
- Dedupe (reescrito en v3): mismo momento no entra dos veces si coincide **(influencer_id + ventana start_sec–end_sec solapada)**. Si `influencer_id` no está poblado en el pool, usar **(título normalizado + ventana solapada)**. PROHIBIDO dedupar por (título + duración) sola: los títulos son genéricos de la IA y esa regla dejaba a kaez en 800 s < 810 s. Y PROHIBIDO usar `source_id`/`source_key`: valen 0/'' en todo el pool real, no distinguen nada.
- Duración (fórmula completa, v3): `total = Σ duraciones ffprobe + 12 s teaser − 0,3 s × nº transiciones`. Corte: añadir clips en orden mientras `total < 13:30`; si el siguiente empuja el total por encima de 14:00, intentar sustituirlo por el siguiente más corto que quepa; tope duro 14:30 medido por ffprobe del archivo final.
- Pre-chequeo (v3): corre ANTES de descargar, sobre metadata D1 (`end_sec - start_sec`) y DESPUÉS del dedupe, con la misma fórmula (teaser 12 s incluido, transiciones estimadas como nº clips). Si el pool suma < 13:30, parar ese tenant e informar (no construir un vídeo corto en silencio). La verificación final usa ffprobe del archivo.
- Acceso: selección vía `GET /api/clips` + Cola (`PanelBridge.list_clips`), orden por score y filtro de idioma en cliente.
- La lista final (clip_id, título, duración real, score) queda en el informe antes de subir nada. Los clips cuya descarga falla se descartan y se toma el siguiente por score; el informe lista los descartados.

## 2b. Pool combinado y dos vídeos disjuntos (enmienda 14:40)
- Pool = seba + kaez juntos: en ambas D1, mismos filtros de §2 (`status IN ('en_carpeta','publicado')`, `pasa=1` en borradores, publicados aceptados, MP4, no bloqueado/archivado, ES). La descarga de cada MP4 se hace desde la D1/Worker de SU tenant (el clip conserva su tenant de origen en el informe).
- Dedupe COMBINADO por (influencer_id + ventana solapada), fallback (título normalizado + ventana), sobre el pool combinado: un mismo momento va a UN solo vídeo. PROHIBIDO (título + duración) y PROHIBIDO `source_id`/`source_key`, como en §2.
- Reparto: orden global por score DESC; reparto alternado (vídeo A = rangos impares 1, 3, 5…; vídeo B = rangos pares 2, 4, 6…) para que ambos tengan mezcla de ambas colas y calidad parecida. Cada vídeo se corta con la misma fórmula y pre-chequeo por separado (≥13:30 cada uno). Si el pool combinado no da para dos vídeos ≥13:30, parar e informar cuántos segundos/clips faltan para el segundo; no construir un solo vídeo en silencio.
- Destino: vídeo A → canal ES de YouTube de seba; vídeo B → canal ES de YouTube de kaez. Título igual en ambos (`Los momentos más divertidos de GTA 6 | 26 nov`): son canales distintos.
- Subida: cada vídeo solo a su tenant. Si el token de un tenant no está disponible, guardar su MP4 + informe y marcarlo bloqueado por credenciales; el otro vídeo sigue su curso. PROHIBIDO subir el vídeo de un tenant a la cuenta del otro.
- Informe: `report.json` por vídeo con su lista de clips (clip_id, tenant de origen, título, duración, score) y el video_id/URL si se subió.

## 3. Ensamblado (lo hace el programa, nunca a mano)
- Descarga de cada MP4 por `GET /api/clips/:id/video` (`PanelBridge.download_video`). PROHIBIDO: `/api/public-video`, `wrangler r2`, API directa de R2.
- Estructura viral:
  1. Cold open: primeros **12 s** del clip #1 (teaser). 12 s y no 10 s: el primer capítulo queda por encima del mínimo de 10 s de YouTube aunque el re-encode desvíe algo.
  2. Countdown: resto de clips ordenados de peor a mejor por score, con etiqueta en pantalla `#N · <título>` (ffmpeg drawtext, fade in/out 0,5 s).
  3. Revelación: el clip #1 completo al final.
  4. Transiciones: xfade 0,3 s entre clips. Pattern interrupt: cada etiqueta + cada cambio de clip; sin zooms artificiales en v1.
  5. Audio: normalizado entre clips (loudnorm). Audio original, sin música añadida en v1.
- Capítulos en la descripción: el teaser es el capítulo 0:00 (`0:00 El mejor momento`), y el #1 se lista dos veces (teaser y revelación con su timestamp real). Todos los capítulos duran ≥10 s.
- Normalización: re-encode a formato único (H.264 + AAC, 1080x1920 vertical, mismo fps).
- Verificación antes de subir: duración total 13:30–14:30 por ffprobe del archivo final, archivo reproducible. Si falla, se para y se informa.

## 4. Metadatos
- Título (≤60 caracteres): `Los momentos más divertidos de GTA 6 | 26 nov` (45 caracteres).
- Descripción: texto corto + capítulos con timestamps generados de la lista final (≥3 capítulos, primero 0:00, cada uno ≥10 s).
- Tags: ≤15. Categoría: Gaming (`categoryId "20"`). Idioma: `defaultLanguage "es"`. Estado: público.
- Miniatura v1: sin miniatura personalizada (YouTube elige una). Ver §0.

## 5. Subida
- Función NUEVA `publish_longform(path, title, description, tags, privacy)` en `clipagent/`, con contrato de validación propio antes de llamar a la API: archivo existe y ffprobe lo lee; título ≤60; descripción ≤5000; tags ≤15; token OAuth presente en `tokens/youtube_shorts.json` (el que materializa `panel_credentials.py`). Reutiliza solo: `Credentials.from_authorized_user_file`, `build("youtube", "v3")`, `MediaFileUpload(..., resumable=True)`, `videos().insert(part="snippet,status")`. PROHIBIDO: `YouTubeShortsPublisher.publish()`, `dry_run()`, `validate_package()`, `check_limits()`.
- Devuelve URL `https://www.youtube.com/watch?v={id}`.
- Pre-chequeo de credenciales ANTES de descargar y renderizar: tokeninfo + scope `youtube.upload` válido para el tenant. Si falla: se salta ESE tenant con motivo y el otro sigue (no se aborta todo).
- Punto de entrada: `python -m clipagent.compilation --tenant <sebastian|kaez>` (con `--dry-run` para construir sin subir). Una corrida por tenant en esta VM (`PANEL_TENANT` + `PANEL_BRAIN_TOKEN`). Autenticación del bridge: token del cerebro canjeado; si no hay, login de servicio como fallback (lo contempla `panel_credentials.py`).
- Cuentas: seba ES = id 2 en `clips-avc`; kaez ES = **id 5 en `clips-avc-kaez`** (v2 citaba id 8 por error: la fila id 8 es una fila con `tenant_id='kaez'` guardada en la DB de sebastián, resto de migración; inofensiva porque `resolveBrainCredentials` filtra por `tenant_id`, pero merece limpieza futura).
- Guarda de re-ejecución: si ya existe `runs/compilation-2026-11-26/<tenant>/report.json` con `video_id`, no subir de nuevo sin `--force` explícito.
- Registro: informe JSON en `runs/compilation-2026-11-26/<tenant>/report.json`. La recopilación NO aparece en la Cola/tracking del panel: el informe es el único registro.

## 6. Seguridad / anti-ban
- Vídeo largo por subida directa API, fuera de los gates del Worker: no consume cupo de Shorts, ni espaciamiento, ni rolling. No se suben 2 recopilaciones el mismo día al mismo canal.
- Contenido reutilizado: clips propios ya validados, seleccionados y ordenados. No es material de terceros. YouTube puede limitar la monetización de contenido reutilizado; esto es para visitas, no se promete monetización.

## 7. Verificación (antes de darlo por hecho)
1. El pool filtrado suma ≥13:30 (pre-chequeo, metadata D1 tras dedupe).
2. El archivo final dura 13:30–14:30 (ffprobe) y se reproduce.
3. La subida devuelve video_id y URL pública por tenant.
4. Se informa: clips usados (títulos), duración exacta, URLs de YouTube.

## 8. Fuera de alcance (v1)
- Recopilación EN, miniatura personalizada (hasta verificar el canal), intros con voz, música de fondo, pipeline diario/semanal de recopilaciones, Shorts a partir de la recopilación.
