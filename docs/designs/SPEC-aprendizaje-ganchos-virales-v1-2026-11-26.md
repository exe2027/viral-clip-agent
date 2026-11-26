# SPEC v1 — Aprendizaje de ganchos virales para los clips GTA 6
Fecha: 2026-11-26. Estado: BORRADOR (sin aprobar, sin implementar).
Origen: reel de David Madrazo @davidmadrazo.ia `DfjSvf6A8q8` (2026-11-09), evaluado 2026-11-26: su método Hazlo Viral NO busca clips GTA, NO corta y NO publica; solo se toma la idea de aprender de reels virales. El usuario pidió además "que pueda descargar clips que ya son virales y acomodarlos a nuestro estilo" — ver §5, límite duro.

## 1. Objetivo
Que el cerebro aprenda patrones de gancho/título de reels virales de GTA 6 (y del rendimiento real de nuestros propios clips) y los use para generar mejores títulos en español primero, sin copiar vídeo, audio, título ni caption de nadie, sin gastar nada ($0) y sin que el usuario configure nada.

Éxito = títulos que pasan el check promesa-payoff y el SEO ≥ 80 con más frecuencia que hoy, sin ningún título copiado y sin ningún clip republicado de terceros.

## 2. Qué existe hoy (verificado en vivo 2026-11-26)
- `reel_study.py`: estudia reels por URL (máx. 10 por corrida, pasada manual, no scrapeo masivo), descompone hook/cuerpo/CTA/hashtags/engagement relativo al autor y devuelve plantillas genéricas con huecos (`{what}`, `{game}`), NUNCA el caption copiado. NO clona voces, NO genera vídeo, NO publica solo. `reel_patterns.json` actual: `sample_n: 4` (bajo el mínimo orientativo 5), patrones orientativos, incluye un outlier (Rockstar, 33M seguidores).
- `config.yaml` § `reel_study`: `enabled: true`, `report_file: "reel_patterns.json"`, `max_urls_per_run: 10`, `min_sample: 5`.
- `auto_clips.py::suggest_titles` (línea 558): plantillas legacy ELIMINADAS por la enmienda títulos-verdad 2026-11-26; devuelve `(None, None)` como retención. NO es el camino de títulos. El camino en vivo es `package_clip` con Gemini (`clipagent/metadata.py:2379`, `clipagent/workers.py:817 package_clip_step`). Cualquier arreglo de títulos debe alimentar ESE camino, no resucitar `suggest_titles` ni crear un generador paralelo.
- Auto-SEO: puntúa al cortar, puerta ≥ 80; loop de revalidación cada 6 h. Medido 2026-11-26 en un paquete: SEO 51–54 con títulos legacy — esa es la línea base a mejorar.
- Plantilla rubius ya es la por defecto (cámara arriba, gameplay abajo, banner amarillo, subtítulos del audio real). El corte lo hace solo el programa, nunca Muse a mano.

## 3. Alcance (lo que SÍ hace esta spec)
1. **Estudiar patrones, no vídeos.** Entrada: metadatos de reels virales de GTA 6 (URL, autor, caption, likes, comentarios, seguidores, fecha) leídos con `instagram-cli post` o `social.search`, máx. 10 URLs por corrida, lista curada a mano (el usuario pasa URLs como hizo con este reel, o Muse propone la lista y el usuario la ve antes). Salida: `reel_patterns.json` v2 con patrones agregados por idioma (ES y EN separados, ES primero), `sample_n`, fecha, URLs fuente, y nota de muestra pequeña si `sample_n < 5`.
2. **Alimentar el título Gemini.** `package_clip` recibe los 3 patrones principales vigentes (tipo de hook, fórmula de título con huecos, palabras de hook frecuentes) como guía en el prompt, junto a la ficha del momento (transcripción real + chat). El título final sigue saliendo de la ficha del momento, y sigue pasando por `check_title` (promesa-payoff) y `seo_score.py` (≥ 80). Si falla, queda retenido/revisado, no se publica.
3. **Guardia anti-copia.** Antes de aceptar un título generado: similitud contra los captions/títulos fuente estudiados; si es copia o casi-copia, se descarta y se regenera sin ese patrón. Se registra qué patrón se usó por clip (trazabilidad en el manifiesto/D1).
4. **Aprender de lo propio.** Segunda fuente, con menor peso al inicio: rendimiento real (vistas 24–48 h) de nuestros clips publicados, por patrón usado. Solo datos reales, nunca inventados; sin vistas reales no hay peso.
5. **Sin configuración del usuario.** Sin secciones nuevas del panel, sin pestañas, sin nada que tenga que tocar él. Solo avisarle el resultado.

## 4. Fuera de alcance
- Voz clonada, generación de vídeo, edición automática de guiones con voz sintética.
- Publicar o programar solo desde este módulo; no toca slots, límites por plataforma ni auto-publicación.
- Cambiar la plantilla rubius, la viralidad, los gates de aceptación (viralidad ≥ 45, SEO ≥ 80) o las reglas de publicación.
- Pagar Hazlo Viral, comentar "REEL", o usar el método/curso de Madrazo.
- Nuevo generador de títulos paralelo a Gemini/`package_clip`.

## 5. Límite duro — descargar clips virales ajenos y resubirlos: NO
La petición literal ("descargar clips que ya son virales y acomodarlos a nuestro estilo") NO se implementa, por tres razones que ya son reglas del proyecto:
- **Duplicado / no original:** cambiar solo el estilo, el efecto o los subtítulos NO hace un clip distinto (regla de campañas 2026-11-23). Las plataformas y las campañas lo rechazan por duplicado; hace falta otro corte (otro inicio/fin, otro hook, otro texto) desde la fuente.
- **Riesgo de ban / copyright:** republicar el vídeo de otro creador, aunque se reestile a rubius, es contenido de terceros sin permiso. Regla dura del proyecto: nada que arriesgue un ban.
- **Permiso:** nuestros clips usan audio real del directo con permiso del streamer en lista blanca (20 influencers). Un reel viral ajeno no tiene ese permiso.
- **Equivalente seguro que SÍ se implementa:** descargar/cortar SOLO desde fuentes con permiso — directos/VOD de streamers autorizados, o material que una campaña entrega como permitido — y re-cortar ESE material en estilo rubius con un momento distinto (otro inicio/fin, hook y título nuevos desde la transcripción/chat reales). Estudiar un reel viral ajeno = solo sus metadatos/patrón, nunca su vídeo para republicar. Si el usuario quiere relajar este límite, debe decirlo explícito y la spec se reescribe; el Goldfish y el usuario lo ven como decisión, no como detalle.

## 6. Supuestos (a confirmar o corregir)
1. La fuente de reels virales es curada a mano por URL (máx. 10/corrida), no un scraper automático masivo. Si el usuario quiere automático después, es otra spec.
2. ES primero; los patrones EN se guardan separados y no se mezclan con los ES.
3. El engagement se lee siempre junto a los seguidores del autor; un reel de Rockstar (33M) no pesa igual que uno de una cuenta pequeña. Sin seguidores, engagement = None, no inventado.
4. $0: solo `instagram-cli` / `social.search` (ya conectados), Gemini por el conector existente, nada de pago.
5. La mejora se mide contra la línea base SEO 51–54 (2026-11-26), no contra una promesa.

## 7. Criterios de éxito (medibles)
- `reel_patterns.json` v2 con `sample_n ≥ 5` en ES, con fecha, fuentes y patrones por idioma.
- Títulos generados con patrones: mediana SEO sube respecto a 51–54 y más títulos superan ≥ 80, sin bajar la tasa de paso del check promesa-payoff.
- Cero títulos aceptados con similitud de copia contra las fuentes; la guardia tiene test que lo demuestra.
- Cero clips de terceros descargados para republicar; el código no tiene ruta de descarga de IG/TikTok a publicación.
- Suite completa en verde, incluido `test_reel_study.py`; sin configuración nueva para el usuario.

## 8. Testing
- Unit: `aggregate_patterns` con muestra < 5 avisa; engagement sin seguidores = None; outlier de seguidores gigantes no distorsiona el top.
- Guardia anti-copia: título idéntico/casi-idéntico a un caption fuente se rechaza.
- Integración: `package_clip` con patrones ES genera título que pasa `check_title` + SEO en un clip de prueba; sin `reel_patterns.json` el comportamiento es el actual.
- No se toca nada de publicación en los tests; no se publica nada en pruebas.

## 9. Preguntas abiertas para el usuario
1. ¿La lista de reels virales a estudiar la pasas tú por URL (como este reel), o quieres que yo proponga una lista y la veas antes de estudiarla?
2. ¿Confirmas el límite duro del §5 (nada de resubir vídeo ajeno, solo patrones + re-corte de fuentes con permiso)?
3. ¿Aprendemos también de nuestros propios clips publicados (vistas 24–48 h) desde el inicio, o primero solo de reels externos?

## 10. Registro de decisiones
- 2026-11-26: borrador v1 creado por el Elefante tras la evaluación del reel `DfjSvf6A8q8`. Pendiente: revisión Goldfish y aprobación del usuario. Sin código escrito.
