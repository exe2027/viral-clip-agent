# SPEC v2 — Aprendizaje de ganchos virales para los clips GTA 6
Fecha: 2026-11-26. Estado: BORRADOR v2 (sin aprobar, sin implementar).
Reemplaza a v1 (`SPEC-aprendizaje-ganchos-virales-v1-2026-11-26.md`, que queda como historial, no se borra).
Origen: reel David Madrazo `DfjSvf6A8q8` + `DfxS2WNCdzZ`, evaluados 2026-11-26; orden del usuario 2026-11-21 12:30 (fuente viral permanente).

## 0. Recibo de cambios v1 → v2 (qué cambió y por qué)
- **Reconciliado `viral_source` (hallazgo Goldfish 1 §b.6 / R7):** v1 no lo mencionaba. v2 declara que `viral_source` ES la versión acotada ya implementada de "descargar clips virales y re-hacerlos a rubius" (ver §5). Esta spec NO la duplica: solo añade aprendizaje de patrones para títulos.
- **Camino de títulos corregido (Goldfish 2 H1/H2):** v1 fundía `metadata.package_clip` (genera con Gemini) con `workers.package_clip_step` (solo puntúa un título ya elegido). v2 nombra el camino real: `live_cutter._definitive_package` → `workers.call_package_clip` → `metadata.package_clip` → `_gemini_first_package` → `llm.make_seo_package` → `llm._seo_package_prompt`. Y reconoce lo verificado: hoy los patrones NO llegan al prompt (solo `enrich_package` post-hoc, `best_title` no cambia) y el flujo en vivo ni siquiera pasa `reel_patterns` a `package_clip`.
- **Esquema v2 definido (Goldfish 1 §b.1, Goldfish 2 H6):** un archivo por idioma, esquema plano compatible (ver §3.1). Nada de anidado `{"es": {...}}` que rompería `enrich_package`.
- **"3 patrones" definido (Goldfish 1 §a.3, Goldfish 2 H6):** es un pack concreto (ver §3.2), no "top 3" ambiguo. `hook_templates` emite solo los 2 tipos más frecuentes; v2 lo asume así.
- **Guardia anti-copia definida (Goldfish 1 §b.3, Goldfish 2 H7):** métrica, umbral, corpus, tope de regeneraciones y fallback (ver §3.3). Se distingue de `is_duplicate_title` / `is_pattern_repeat` (corpus propio, no externo).
- **Aprender de lo propio DIFERIDO a Fase 2 (Goldfish 1 §b.4, Goldfish 2 H8):** v1 lo prometía sin fuente. La fuente real es `metrics.db`/`learning.py` (`views_7d`, BETA < 30 clips, recalibra exponentes ±25%/ciclo, nunca filtros duros) y es de prioridad, no de títulos. v2 no lo implementa.
- **Medición definida (Goldfish 1 §b.5, Goldfish 2 H9):** protocolo control vs. tratamiento con N=20 (ver §7). La cifra 51–54 de v1 era UN paquete, no una mediana: v2 no la usa como línea base.
- **Puertas aclaradas (Goldfish 2 H9):** publicar = SEO ≥ 80 / viralidad ≥ 45 (`priority.py`, `policy.py` por defecto 80/45). El "90" es el retry pre-corte de Gemini (`metadata.py`, `gemini_seo` < 90 → reintento o retención); no cambia.
- **Hook = caption, no vídeo (Goldfish 1 §a.2, Goldfish 2 H4):** v2 lo dice explícito. `classify_hook` clasifica solo la primera línea del caption; no hay transcripción de reels.
- **Exclusiones de muestra (Goldfish 2 H5/H12):** follow-bait, comment-bait y tráilers oficiales quedan fuera de los conteos (ver §3.1).
- **`viral_package.py` y `seo_score.py` son shims (Goldfish 2 H3, Goldfish 1 §b.8):** no se editan. El código real vive en `clipagent/metadata.py` y `clipagent/seo_score.py`.

## 1. Objetivo
Que el cerebro use patrones de gancho/título de reels virales de GTA 6 para generar mejores títulos en español primero, sin copiar vídeo, audio, título ni caption de nadie, sin gastar nada ($0) y sin que el usuario configure nada. Éxito = más títulos que pasan el check promesa-payoff y el SEO ≥ 80 que hoy, medido con el protocolo del §7.

## 2. Qué existe hoy (verificado en vivo 2026-11-26)
- `reel_study.py`: estudia reels por URL (máx. 10/corrida, manual, no scrapeo), descompone la primera línea del caption en hook, cuenta líneas del cuerpo, CTA, hashtags y engagement `(likes+comments)/followers`. Devuelve plantillas genéricas con huecos. Muestra actual `reel_patterns.json`: `sample_n: 4` (< mínimo 5), top hook `statement` (2/4) con dos casos de follow-bait, top CTA `none`, outlier Rockstar (33.768.252 seguidores) y el reel de Madrazo con engagement 3,181 (318 %, comment-bait).
- `config.yaml` § `reel_study`: `enabled: true`, `report_file: "reel_patterns.json"`, `max_urls_per_run: 10`, `min_sample: 5`.
- `auto_clips.py::suggest_titles` (l. 558): eliminado, devuelve `(None, None)`. No se resucita.
- `metadata.package_clip(n, ..., reel_patterns=None, ...)` (l. 2379) acepta patrones, pero hoy solo los usa post-hoc en `enrich_package` (l. 2631-2640, después de que Gemini ya generó el título; `best_title` no cambia). `_gemini_first_package(check_card, visible, lang, llm_client, recent_titles)` (l. 2272) y `llm._seo_package_prompt(card, visible, lang)` (l. 199) no aceptan patrones. `live_cutter._definitive_package` (l. 462) llama a `package_clip` SIN `reel_patterns`. Solo el camino batch `auto_clips.py` carga el informe.
- `viral_source` (`clipagent/viral_source.py`, `config.yaml` § `viral_source`, workflow `.github/workflows/viral-source.yml` diario 10:00 Chile): descubre Shorts virales de GTA 6 en los canales de YouTube de los streamers de la lista, los re-hace a rubius real, los valida fotograma a fotograma y los encola. Criterios: ≥ 100k vistas, ≥ 5k likes, outlier ≥ 3x la mediana del canal, ≤ 30 días, Short 15–60 s, máx. 5 candidatos y 2 re-makes por corrida, cooldown 7 días, un vídeo → UN solo tenant (claims en `viral-source-claims.json`). Solo YouTube con yt-dlp ($0); Instagram no corre en Actions porque necesita la sesión del usuario.

## 3. Alcance
### 3.1 Estudiar patrones, no vídeos (fuente: reels IG + Shorts YT, solo metadatos)
- Entrada: URLs curadas a mano (el usuario las pasa, o Muse propone la lista y el usuario la ve antes), máx. 10 por corrida. Lectura con `instagram-cli post` / `social.search` (IG) o metadata de YouTube (Shorts). Nunca se descarga el vídeo de un reel de IG para republicar.
- Criterios de inclusión (por idioma, ES y EN separados): GTA 6 verificado, ≤ 30 días, outlier ≥ 3x la mediana del autor (si no hay mediana del autor, engagement = None y el reel no cuenta para los tops). Mínimo `sample_n ≥ 5` por idioma para que los patrones alimenten el prompt; con menos, el informe avisa "muestra pequeña" y NO alimenta nada.
- Exclusiones de los conteos (quedan en el informe marcadas con motivo): follow-bait (hook que pide seguir/comentar para recibir algo), comment-bait (engagement_rate > 1,0), tráilers/cuentas oficiales (p. ej. Rockstar: su "gancho" es un anuncio, no un momento de streamer).
- Salida: UN archivo por idioma, esquema plano compatible con los consumidores actuales: `reel_patterns_es.json` y `reel_patterns_en.json`. Cada uno conserva las claves actuales (`sample_n`, `patterns` con `top_hook_type`, `hook_types`, `top_cta_type`, `top_hook_words`, `median_caption_chars`, `notes`, `reels`) y añade: `"version": 2`, `"lang": "es"|"en"`, `"generated_at"`, `"sources": [{url, username, followers}]`, `"excluded": [{url, reason}]`. El `reel_patterns.json` actual se conserva como ES hasta regenerarse (compatibilidad); el cargador prueba el nombre nuevo primero y cae al viejo.
- Caducidad: patrones con `generated_at` de más de 30 días se tratan como obsoletos: el cargador avisa y NO los pasa al prompt. Dueño de la corrida: Muse, manual, cuando el usuario pasa URLs o lo pide; sin corrida nueva no hay patrones nuevos.

### 3.2 Alimentar el título Gemini (el trabajo nuevo real)
- Contrato del prompt: `_seo_package_prompt(card, visible, lang, pattern_pack=None)` y `make_seo_package(client, lang, pattern_pack=None)` aceptan un `pattern_pack` opcional: `{version, generated_at, lang, sample_n, top_hook_types: [máx. 2, con sus plantillas genéricas], top_hook_words: [máx. 10], median_caption_chars}`. `_gemini_first_package` lo recibe y lo pasa a `make_seo_package`. El prompt lo presenta como guía ("sugerencias de estilo, la ficha del momento manda; no copies estos textos"), nunca como fuente de hechos: el título sigue saliendo de la ficha (transcripción real + chat).
- Cableado: `live_cutter._definitive_package` carga el informe del idioma del clip (con caché por corrida; si falta, está obsoleto o `sample_n < 5`, pasa `reel_patterns=None` y sigue como hoy) y lo pasa a `package_clip`. `workers.call_package_clip` ya reenvía kwargs conocidos por inspección; `package_clip` ya acepta `reel_patterns`. El camino VOD (`vod_package_from_transcript`) lo pasa cuando esté disponible. El paquete pre-corte (sin transcripción) NO recibe patrones.
- `enrich_package` post-hoc se mantiene solo como anotación (`reel_study` en el paquete, `best_title` no cambia por él).
- `lang="both"`: el paquete ES recibe el pack ES y el EN el pack EN; si el pack EN falta o está obsoleto, el EN va sin pack (nunca con el pack ES).
- Sin Gemini disponible: sin pack y retención como hoy; los patrones nunca bloquean un clip por sí solos.
- Trazabilidad: se registra `pattern_pack_id` (versión + `generated_at` + idioma) y `title_pattern_types` en `seo_inputs` del manifiesto/D1.

### 3.3 Guardia anti-copia (externa; distinta de la de títulos propios)
- Corpus: las `hook_text` (primeras líneas) guardadas en el informe del idioma. El caption completo NO se persiste (no se guarda texto verbatim de terceros más allá de la primera línea que ya guarda `reel_study`).
- Métrica y umbral: título normalizado (minúsculas, sin emojis/puntuación) contra cada `hook_text` normalizada: `difflib.SequenceMatcher.ratio ≥ 0,8` o coincidencia exacta → rechazo. Además, coincidencia exacta contra `is_duplicate_title` y repetición de patrón contra `is_pattern_repeat` (corpus propio: títulos recientes del panel) siguen como hoy y son una guardia distinta.
- Tope: máx. 2 regeneraciones con el pack; si las 2 se rechazan, el clip queda retenido para revisión humana (nunca bucle, nunca publicación).
- Tests: título idéntico a una `hook_text` se rechaza; título derivado de plantilla genérica con el momento real pasa; 2 rechazos seguidos → retención, no tercer intento.

### 3.4 Fuera de alcance (incluye lo diferido)
- Aprender de nuestros propios clips (vistas 24–48 h) → **Fase 2, diferido explícito.** La fuente real es `metrics.db`/`learning.py` (`views_7d`, BETA < 30 clips, recalibra exponentes ±25%/ciclo, nunca filtros duros) y es de prioridad, no de títulos. Definir ese contrato es otra spec.
- Voz clonada, generación de vídeo, publicar/programar solo desde este módulo, tocar slots/límites, cambiar la plantilla rubius, los gates (viralidad ≥ 45, SEO ≥ 80) o el retry-90 de Gemini.
- Pagar Hazlo Viral, comentar "REEL"/"PANEL", o usar el método de Madrazo.
- Resucitar `suggest_titles` o crear un generador de títulos paralelo a Gemini/`package_clip`.
- Editar `viral_package.py` o `seo_score.py` (shims deprecados): el código real está en `clipagent/`.

## 4. Configuración
- Solo se añade `report_file_en: "reel_patterns_en.json"` bajo el bloque `reel_study` existente (verificar antes que no haya un segundo bloque `reel_study`). Los bloques duplicados `validator:`/`templates:`/`sfx:` observados en `config.yaml` NO se tocan en esta spec; se dejan anotados como deuda para no romper nada editando el bloque equivocado.
- Sin secciones nuevas del panel, sin nada que configure el usuario.

## 5. Límite duro — re-make viral: lo que ya existe y lo que sigue prohibido
- **Lo que ya existe y NO se duplica:** `viral_source` (§2) es la implementación acotada de "descargar clips que ya son virales y acomodarlos a nuestro estilo": Shorts de YouTube de streamers de la lista, con umbrales, re-make real a rubius, validación fotograma a fotograma, un vídeo → un tenant, máx. 2/corrida. Esta spec no lo toca.
- **Lo que sigue prohibido:** bajar un reel viral de Instagram (o cualquier vídeo de un creador fuera de la lista) y resubirlo solo reestilizado. Sin permiso, sin umbrales, sin re-corte real validado: duplicado y riesgo de ban. Los reels de IG son solo fuente de estudio (metadatos/patrones), nunca fuente de vídeo. Si el usuario quiere Shorts de IG como fuente de re-make, es otra spec (necesita sesión + revisión).
- Si el usuario quiere relajar este límite, debe decirlo explícito y la spec se reescribe; no se relaja por omisión.

## 6. Supuestos (a confirmar o corregir)
1. La lista de reels es curada a mano por URL (máx. 10/corrida); no hay scraper masivo.
2. Frecuencia ≠ eficacia: sin datos propios (Fase 2), los patrones son guía de estilo, no prueba de que rindan. El prompt lo dice así.
3. El hook estudiado es el del caption (primera línea), no el hook audiovisual del vídeo (primeros 1–3 s). No se transcribe ningún reel.
4. $0: solo `instagram-cli`/`social.search` y Gemini por el conector existente. Ampliar el prompt en cada `package_clip` no tiene coste medido aparte; si Gemini no está disponible, el §3.2 es inerte y el clip sigue el camino actual.
5. Cada `fetch_reel` es una llamada a `instagram-cli` con timeout de 60 s; sin reintentos declarados más allá de los del CLI.

## 7. Criterios de éxito (protocolo de medida, no promesas)
- Control: los próximos 20 paquetes definitivos SIN pack (código actual) miden `gemini_seo` por plataforma y tasa de paso del check promesa-payoff; esas cifras se registran antes de activar el §3.2. Tratamiento: los 20 siguientes CON pack, misma medición.
- Éxito: la mediana de `gemini_seo` por plataforma sube respecto al control, y la tasa de paso del check no baja más de 5 puntos. La puerta de referencia es SEO ≥ 80 (publicar); el retry-90 no cambia.
- `reel_patterns_es.json` v2 con `sample_n ≥ 5` en ES, con fecha, fuentes y excluidos con motivo.
- Cero títulos aceptados con ratio ≥ 0,8 contra una `hook_text` fuente (la guardia tiene test que lo demuestra); cero clips de terceros descargados para republicar.
- Suite completa en verde, incluidos `test_reel_study.py` y los tests nuevos de la guardia y del cableado en vivo (con `reel_patterns=None` el comportamiento es el actual).

## 8. Testing
- Regresión (ya existen, no se cuentan como nuevos): muestra < 5 avisa; engagement sin seguidores = None.
- Nuevos: exclusiones (follow-bait, comment-bait > 1,0, tráiler oficial) fuera de los tops; esquema v2 por idioma con fallback al informe viejo; pack obsoleto (> 30 días) no alimenta el prompt; `lang="both"` no mezcla packs; guardia anti-copia (idéntico rechaza, plantilla+momento pasa, 2 rechazos → retención); pre-corte sin patrones.
- Integración: `package_clip` con pack ES genera título que pasa `check_title` + SEO en un clip de prueba; sin informe, comportamiento actual.

## 9. Preguntas abiertas para el usuario
1. ¿La lista de reels virales a estudiar la pasas tú por URL (como estos dos de Madrazo), o quieres que yo proponga una lista y la veas antes de estudiarla?
2. ¿Confirmas el límite duro del §5 (re-make solo vía `viral_source`; reels de IG solo estudio de patrones)?
3. ¿De acuerdo con diferir a Fase 2 el aprendizaje de nuestros propios clips?

## 10. Registro de decisiones
- 2026-11-26: v1 creada (Elefante). Revisión Goldfish doble: 2 informes (corrección/supuestos + ajuste con código vivo). Verificación del Elefante de los puntos clave en el código (firma `package_clip`, `_definitive_package` sin `reel_patterns`, `learning.py`, `policy.py` 80/45, `viral_source` y workflow diario 10:00).
- 2026-11-26: v2 reescribe v1 con las resoluciones del §0. Pendiente: re-revisión Goldfish de los cambios y aprobación del usuario. Sin código escrito.
