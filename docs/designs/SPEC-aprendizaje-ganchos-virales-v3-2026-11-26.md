# SPEC v3 — Aprendizaje de ganchos virales para los clips GTA 6
Fecha: 2026-11-26. Estado: APROBADA PARA IMPLEMENTAR por el usuario ("Aprende aprende aprende", 2026-11-26 11:43).
Reemplaza a v2. v1 y v2 quedan como historial, no se borran.
Decisiones del usuario recogidas: viral = más de 800.000 vistas; umbrales viral_source 800.000 vistas / 20.000 likes / 1.000 comentarios (confirmados por widget 2026-11-26); "son dos cosas en conjunto" = Instagram Y YouTube juntos, y los tres umbrales juntos.

## 0. Recibo de cambios v2 → v3 (resoluciones de la re-revisión Goldfish de v2)
- **H1 (viral del usuario ausente):** resuelto. Viral = vistas > 800.000 Y likes ≥ 20.000 Y comentarios ≥ 1.000, los tres juntos, en ambas fuentes. El outlier ≥ 3x queda como filtro adicional solo donde existe mediana del autor (YouTube). "Scrollear y quedarse con los mejores" = YouTube automático diario (viral_source) + Instagram por sesión en el Chrome del usuario (ver §3.1).
- **H2 (pack sin constructor):** resuelto. `build_pattern_pack(report_dict) -> dict` en `reel_study.py` (§3.2).
- **H3 (cargador en vivo sin dueño):** resuelto. `load_pattern_pack(lang, base_dir)` en `reel_study.py`; lo llama `live_cutter._definitive_package` con caché por proceso (dict a nivel de módulo); aplica versión, `sample_n` y caducidad (§3.2).
- **H4 (idioma sin detección):** resuelto. Sin auto-detección: el idioma lo asigna el curador por URL en la lista de estudio y se guarda por reel (`lang` en cada análisis); la agregación es por archivo de idioma (§3.1).
- **H5 (exclusiones sin regla):** resuelto. Follow-bait = `cta_type == "follow"` O `hook_text` con regex `(?i)(s[ií]gueme|follow|comenta .* (para|y te|recibir)|link en bio)`; oficial = username en `reel_study.official_accounts` (inicial: `rockstargames`) (§3.1).
- **H6 (mediana del autor sin fuente en IG):** resuelto. En IG no hay mediana → mandan los pisos absolutos (likes ≥ 20k, comentarios ≥ 1k); el outlier ≥ 3x solo aplica donde hay mediana (YouTube, `channel_shorts_median`). Corrección: lo que falta sin mediana es `outlier_multiple`, no `engagement_rate` (§3.1).
- **H7 (guardia sin sitio ni interacción con retry):** resuelto. La guardia corre dentro de `_gemini_first_package`, después de `validate_title_final`; la regeneración es UN reintento sin `pattern_pack` (prompt plano) y luego retención. Limitación conocida: ciega entre idiomas (título ES traducido de `hook_text` EN no puntúa) (§3.3).
- **H8 (protocolo N=20 no medible):** resuelto. Días alternos (pares con pack, impares sin pack) durante 4 semanas, mín. 20 por brazo, medianas por plataforma + tasas de paso; el umbral de 5 puntos se reporta con su N (§7).
- **H9 (imprecisiones):** resuelto. `call_package_clip` reenvía todos los kwargs (no selectivo); el VOD recibe `reel_patterns=None` por defecto y lo pasa cuando está disponible; `generate()` dentro de `make_seo_package` debe reenviar `pattern_pack` al prompt.

## 1. Objetivo
Igual que v2 §1. El cerebro usa patrones de gancho/título de reels virales de GTA 6 para generar mejores títulos (ES primero), sin copiar vídeo, audio, título ni caption de nadie, $0, sin que el usuario configure nada.

## 2. Qué existe hoy
Igual que v2 §2, más: `viral_source` con umbrales viejos (100k vistas, 5k likes, sin comentarios) — esta spec los cambia (§3.4). `reel_study.py` ya tiene `build` pendiente: hoy no existe `build_pattern_pack` ni `load_pattern_pack` (se crean aquí).

## 3. Alcance
### 3.1 Estudiar patrones — dos fuentes en conjunto
- **YouTube (automático):** `viral_source` diario 10:00 Chile. Candidatos: Short 15–60 s, ≤ 30 días, vistas > 800.000, likes ≥ 20.000, comentarios ≥ 1.000, outlier ≥ 3x la mediana del canal. Máx. 5 candidatos y 2 re-makes por corrida, cooldown 7 días, un vídeo → un tenant.
- **Instagram (por sesión, en el Chrome del usuario):** scrolleo de reels de GTA 6 (búsqueda/explore o URLs que pasa el usuario). Filtro: likes ≥ 20.000 Y comentarios ≥ 1.000 (las vistas de reels ajenos no las devuelve `instagram-cli`; se leen en la página al scrollear y se guardan con la URL). Solo metadatos/patrones, nunca descarga del vídeo para republicar. La lista curada lleva `lang` por URL (asignada por el curador).
- Criterios comunes: GTA 6 verificado; exclusiones de los conteos (con motivo en el informe): follow-bait (H5), comment-bait (`engagement_rate > 1,0`), cuentas oficiales (`official_accounts`, inicial `rockstargames`). `sample_n ≥ 5` por idioma para alimentar el prompt; con menos, aviso y sin alimentación.
- Salida: `reel_patterns_es.json` / `reel_patterns_en.json`, esquema plano v2 (ver v2 §3.1), + `"official_accounts"` y `"excluded"` con motivo. Caducidad 30 días.

### 3.2 Alimentar el título Gemini
- `build_pattern_pack(report_dict)` en `reel_study.py`: devuelve `{version, generated_at, lang, sample_n, top_hook_types: [máx. 2 con plantillas genéricas], top_hook_words: [máx. 10], median_caption_chars}`. Si `sample_n < 5` o el informe es obsoleto, devuelve `None`.
- `load_pattern_pack(lang, base_dir)` en `reel_study.py`: prueba `reel_patterns_{lang}.json`, cae a `reel_patterns.json` (solo para ES), valida `version == 2`, `sample_n ≥ 5`, edad ≤ 30 días; si algo falla, devuelve `None` (sin excepción).
- `live_cutter._definitive_package` llama a `load_pattern_pack` con caché por proceso (dict de módulo) y pasa el pack a `_call_package_clip(..., reel_patterns=pack)`; `workers.call_package_clip` lo reenvía tal cual a `metadata.package_clip(reel_patterns=...)`. `package_clip` pasa el pack a `_gemini_first_package(..., pattern_pack=...)` → `make_seo_package(client, lang, pattern_pack)` → su cierre `generate(card, visible)` reenvía el pack a `_seo_package_prompt(card, visible, lang, pattern_pack)`. El prompt lo marca como guía de estilo ("la ficha del momento manda; no copies estos textos").
- `vod_package_from_transcript(..., reel_patterns=None)`: recibe el pack cuando está disponible. Pre-corte (sin transcripción): sin pack.
- `enrich_package` post-hoc se mantiene como anotación. `lang="both"`: pack ES al paquete ES, pack EN al EN; si falta el EN, ese va sin pack. Sin Gemini: sin pack, retención como hoy.
- Trazabilidad: `pattern_pack_id` + `title_pattern_types` en `seo_inputs` (vía `seo_inputs_from_package`).

### 3.3 Guardia anti-copia
- Corpus: `hook_text` guardadas en el informe del idioma. Métrica: `difflib.SequenceMatcher.ratio ≥ 0,8` sobre título normalizado, o coincidencia exacta → rechazo. Distinta de `is_duplicate_title` / `is_pattern_repeat` (títulos propios recientes).
- Sitio: dentro de `_gemini_first_package`, después de `validate_title_final`. Regeneración: UN reintento sin `pattern_pack`; si también se rechaza, retención para revisión humana. Nunca bucle, nunca publicación.
- Limitación conocida: ciega entre idiomas; umbral 0,8 solo caza casi-copias en títulos cortos.

### 3.4 Umbrales viral_source (cambio aprobado con números confirmados)
- `config.yaml` § `viral_source`: `min_views: 300000`, `min_likes: 0`, `min_comments: 0`.
- Enmienda 2026-11-26 11:48 (usuario): los umbrales iniciales 800.000 / 20.000 / 1.000 dieron CERO candidatos en la corrida en seco (los 2 únicos candidatos de Speed cayeron por outlier, no por vistas). Se baja el piso a 300.000 vistas y likes/comentarios dejan de filtrar (varían mucho; las llaves se mantienen en 0 para no romper `load_viral_config` y para seguir registrando los valores). El filtro outlier ≥ 3x se mantiene por ahora; la corrida en seco con 300k dirá si también hay que tocarlo (la mediana de Speed es 6,05M: 3x = 18M).
- `clipagent/viral_source.py`: añadir `"min_comments"` a `_VIRAL_REQUIRED`, parseo `int(section["min_comments"])`, validación `>= 0`, y filtro en `enrich_candidate` donde se verifican los likes (línea ~417): sin metadata completa no hay verificación; si `comments < min_comments`, candidato rechazado con motivo. Actualizar el docstring del módulo y los tests de `load_viral_config` (falta la llave → error fuerte, como las demás).
- Consecuencia aceptada por el usuario: menos candidatos, días con cero. No se toca `max_per_run` (2), `cooldown_days` (7) ni el outlier (3x).

### 3.5 Fuera de alcance
Igual que v2 §3.4 (aprendizaje propio → Fase 2; sin voz clonada, sin publicar solo, sin tocar slots/gates, sin pagar Hazlo Viral, sin resucitar `suggest_titles`, sin editar shims). Más: no se toca el workflow diario (sigue 10:00 Chile) ni `viral-source-claims.json`.

## 4. Configuración
- Solo: `viral_source.min_views/min_likes/min_comments` y `reel_study.report_file_en` + `reel_study.official_accounts: ["rockstargames"]` (verificar bloque único antes de editar). Sin nada nuevo en el panel.

## 5. Límite duro
Igual que v2 §5. `viral_source` es el re-make viral implementado; reels de IG solo estudio. Sin relajar por omisión.

## 6. Supuestos
Igual que v2 §6, más: el idioma por reel lo pone el curador; la mediana de autor en IG no existe; el scrolleo de IG es por sesión en el Chrome del usuario (su regla de navegador), no automático.

## 7. Medición
- Días alternos 4 semanas (pares con pack, impares sin pack), mín. 20 paquetes definitivos por brazo, ES. Se registran antes de activar: medianas de `gemini_seo` por plataforma + tasa de paso del check, por brazo.
- Éxito: mediana `gemini_seo` por plataforma sube vs. brazo sin pack y la tasa de paso no baja más de 5 puntos (reportada con N). Puerta de referencia SEO ≥ 80; retry-90 sin cambio.
- `reel_patterns_es.json` v2 con `sample_n ≥ 5`; cero títulos aceptados con ratio ≥ 0,8 contra una `hook_text`; cero clips de terceros descargados para republicar.

## 8. Testing
- Regresión: muestra < 5 avisa; engagement sin seguidores = None; `load_viral_config` falla fuerte sin una llave requerida (incluida `min_comments`).
- Nuevos: `build_pattern_pack` (None con `sample_n < 5` u obsoleto; pack correcto con ≥ 5); `load_pattern_pack` (nuevo nombre, fallback viejo, obsoleto → None); exclusiones (follow-bait por regex y por `cta_type`, oficial por denylist, comment-bait > 1,0); guardia (idéntico rechaza, plantilla+momento pasa, 2º rechazo → retención); `live_cutter` sin pack = comportamiento actual; `min_comments` rechaza candidato con comentarios bajos y acepta con suficientes.
- Suite completa en verde antes de dar por terminado; corrida en seco de `viral_source` con los umbrales nuevos sin encolar nada.

## 9. Preguntas ya respondidas (no re-preguntar)
1. Lista de reels: ambas vías en conjunto (usuario pasa URLs + Muse propone/scrollea; YouTube automático, IG por sesión).
2. Límite duro §5: confirmado por el usuario al pedir los umbrales de `viral_source` (lo usa como camino de re-make).
3. Aprendizaje propio: diferido a Fase 2 (v2, sin objeción).

## 10. Registro de decisiones
- 2026-11-26: v3 aprobada para implementar por el usuario ("Aprende aprende aprende"). Umbrales 800k/20k/1k confirmados por widget. Re-revisión Goldfish de v2 (H1–H9) resuelta en §0.
- 2026-11-26 11:48: enmienda del usuario — piso a 300.000 vistas, likes/comentarios dejan de filtrar (varían mucho). Corrida en seco con 800k: 0 candidatos (2 de Speed fuera por outlier 1,19x/0,93x vs mediana 6,05M).
- 2026-11-26 13:10: enmienda del usuario ("si tú te sabes la lógica hazlo tú") — excepción absoluta al outlier: `absolute_viral_views: 3000000` en `viral_source`; con >=3M vistas el candidato pasa sin outlier (`outlier_status` = "absoluto"). El outlier sigue mandando por debajo de 3M.
