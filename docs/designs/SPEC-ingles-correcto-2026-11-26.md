# SPEC — Inglés correcto de punta a punta (v3)

**Estado:** v3 — reescritura tras la 2ª revisión goldfish (NO APROBADA) + la 2ª revisión
goldfish del borrador sin_webcam (NO APROBADA, B7–B11) + calibración completa en la
rejilla real del gate. **Sin implementar todavía: esta v3 necesita una 3ª revisión
goldfish (EGM) antes de implementar.** Fecha: 2026-11-26 ~23:55 (Chile).

**Repo:** `~/workspace/gta6-clips/viral-clip-agent/`. **No borres clips. No cortes a mano.**
**No publiques nada a mano desde este trabajo** (la publicación desde el chat la lleva
otro agente; la salida de este trabajo son clips válidos en cola + VOD fluyendo).

## v2 → v3 (qué cambió y por qué)

La v2 recibió **NO APROBADA** en su 2ª revisión goldfish (agente bf83eb8c, veredicto
completo recuperado de `runtime.messages` 2026-11-26 23:39 UTC): B-1 (método por celdas
no separa; ORB debe ser primario; salto por banner contradice 197; corpus 49 no 52;
**rejilla de calibración ≠ rejilla del gate**), B-2 (contradicción 186 sin resolver;
`cam_pura` sin definición; README contradice la spec), B-3 (cableado del re-render roto
en 3 puntos + pre-chequeo sin mecanismo + conflicto avatar §4.2 vs §4.3), B-4 (nuevo:
el JSON `viral` NO transporta `plantilla`/`cam_box` — `parseViralInput` es whitelist),
B5 parcial (Burn sin coordenadas, clip 12 sin números, regla y≤0,35 perdida); I-1–I-8;
M-1–M-6. Además la 2ª revisión del borrador sin_webcam (REVISION-GOLDFISH-2.md, NO
APTA) añade B7–B11 + I1–I4 + M1–M5, de los que 4 golpean a esta spec (B7 transporte,
B8 despacho de `template_gate`, B9 pisado de `cfg["mode"]`, B10 `(facecam_box, hits)`,
B11 tercer duplicado `sfx:`).

**Mediciones nuevas en esta v3 (hechas por mí 2026-11-26, no por la revisión):**
- **Rejilla real del gate:** `extract_gate_frames` (`final_review.py:780`) muestrea
  desde **t=1,0 con paso 2 s** (`face_sample_every_s: 2`, `config.yaml:641`) → t=1,3,5,7…
  Calibración rehecha sobre t=1,3,5 para los 13 clips (39 fotogramas) + t=7,15,25.
- **197 en la rejilla real: SÍ se caza.** 197_t1/t3/t5 son todos duplicados (la cámara
  no aparece hasta ~t=7): SIFT 21/29/26, ORB-completo 16/21/7. El gate muestrea t=1,3,5.
- **SIFT (nfeatures=2000, contrastThreshold=0,02) umbral 20 es el método que separa a
  nivel clip:** CORRECTO máx 18 (es248_t15), DUP mín por clip 29 (197). **ORB queda
  descartado como señal única: es248_t1 (correcto, verificado visualmente) da ORB
  9 ≥ 7** — falso positivo. La corrección B-1 del goldfish («ORB primario, umbral 7»)
  queda refutada por ese fotograma; la v3 shippea SIFTct02 como primario único.
- **Clip 12 (avatar peanut):** medido. Avatar bbox x 0,27-0,77, y 0,01-0,82; píxeles
  avatar/zona = **0,34**; centro (0,52, 0,42). 244: píxeles 0,08-0,10. Umbral
  `avatar_share_min = 0,20` los separa (0,34 vs 0,10).
- **Citas re-verificadas con grep contra el árbol actual** (varias estaban
  desactualizadas en v2 Y en el borrador): `decide_layout` es `render.py:2320` (no
  :2221); `cut_clip_with_layout` es `:3471` (no :3341); el pisado de `layout_mode` es
  `:3494-3496` (no :3367-3368); `_cam_box_valida` existe en `:1923` (ancho <45% +
  área <35%); `_split_params` es `:1957`, ancho en `:1996`; `extract_gate_frames`
  es `final_review.py:780` (no :413 — ahí está la constante `_GATE_MAX_FRAMES = 24`);
  `_criterion` es `quality_gate.py:112` (no :128-130); `config.yaml`: `validator:`
  553/640, `templates:` 604/717, `sfx:` 366/379 (no 538/625 ni 589/682);
  `rubius_subs_outline_frac` en :665; `parseViralInput` es `db.ts:352`,
  `mergeViralJson` `:497`, INSERT `:1949`, POST merge `index.ts:1491` (la revisión
  tenía razón en las 4; la resolución del borrador citaba :344/:475/:2034/:1452 —
  erróneas, verificadas por grep). `config.py: load_config` es `:551` (no :566).

## Alcance (5 piezas, en orden)

1. **Puerta en el validador** que caza la duplicación (mitad superior = mitad inferior)
   y exige que la zona de cámara sea mayoritariamente cámara, antes de encolar.
2. **Guardas de caja** en el corte (reutilizar `validate_cam_box`/`find_cam` y la
   existente `_cam_box_valida`), para que no se vuelva a renderizar un duplicado.
3. **Formato «rubius sin webcam»** (gameplay a pantalla completa) como salida cuando no
   hay cámara real — adopta el borrador revisado **con sus correcciones de ronda 2
   inlinadas** (§6), no el archivo del borrador (que vive fuera del repo y está
   pendiente de su propia 3ª revisión + aprobación del usuario).
4. **Cajas de cámara por influencer** (Fase 2, después).
5. **VOD EN**: reservar 1 de 2 huecos del VOD-fallback para EN + reintento del dispatch
   diario (Fase 3).

## Fuera de alcance

- Publicar (lo hace otro agente en paralelo desde el chat).
- Borrar clips (lo gestiona el otro chat).
- Watcher en vivo de YouTube para Speed (trabajo aparte; Speed sigue siendo solo VOD).
- Piso 3/día (tiene su propia spec `docs/designs/SPEC-piso-3-por-dia-v2-2026-11-26.md`,
  aprobada por el usuario 2026-11-26 20:46, en implementación por otro agente).

## Acción inmediata (estado a 2026-11-26 ~23:55, verificado en D1)

- **247: sin job vivo.** `SELECT … FROM jobs WHERE clip_id=247` → 0 filas (verificado
  ~23:20). El job 165 (247 → YouTube, 2026-11-27 00:30) fue eliminado. No se reprograma
  247 hasta que su re-make pase la puerta nueva. Job 161 (248 → Instagram,
  2026-11-27 00:00) sigue `scheduled`.
- **Los 10 EN re-hechos aún no están en D1** (verificado ~23:50: los 10 siguen en
  `en_carpeta`, `updated_at` 23–26 nov). El re-render del otro chat (c02d444d) no ha
  aterrizado; la verificación en vivo de §10.4 queda pendiente de eso.
- **190 publicado y verificado LIMPIO en el Chrome del usuario** (YouTube Shorts
  https://www.youtube.com/shorts/FFw5ngKR0dc, job 164, 20:05; verificado por otro
  agente en solo lectura: cámara grande arriba, juego abajo, sin duplicado).
- **Opción A aterrizada** (commit 26267d5): Workers avc fe1b2268 / avc-kaez 4324312a,
  `/api/health` ok en ambos, schedules `['*/5 * * * *']` verificados por API en ambas
  cuentas (seba 27afc37225a02fd92557f901c05dabee, kaez 30953ea4f41053a53494bc50b2a9d4a4).

## §1. Evidencia: la duplicación es real, por fotograma

### §1.1 Cómo se midió (2026-11-26 ~20:00-20:20, ampliado 23:50-23:55)

- MP4 finales descargados de R2 (`~/workspace/gta6-clips/runs/review-en-2026-11-26/`
  y `runs/review-es-2026-11-26/`); fotogramas con ffmpeg en t=2,7,15,25 (calibración
  inicial) y **t=1,3,5 (rejilla real del gate)**; las 3 capturas del usuario recortadas
  al área de vídeo (1290×2796 → recorte `(107,431)-(1182,2363)` → 1075×1932) y
  guardadas en `tests/fixtures/duplicacion/usuario_*.jpg` (coordenadas registradas en
  el README de fixtures).
- **Corpus real: 49 fotogramas** (13 clips × t=2,7,15,25; 183, 190 y 244 no tienen t=25)
  **+ 39 fotogramas de rejilla** (13 clips × t=1,3,5) + 3 capturas. La v2 decía 52:
  el número correcto es 49 para el corpus inicial.
- Métodos probados y descartados (todos medidos, sin separación): MAD, NCC global y
  grueso (16×16, 8×8), template matching, Haar, histogramas (gris/tono), NCC de bordes
  Canny 32×32, comparación por celdas (0 coincidencias en todos los fotogramas).
- **Método que separa: SIFT** (nfeatures=2000, contrastThreshold=0,02, BFMatcher L2,
  knn k=2, ratio 0,75; zonas: cámara y 0-840 x 0-810, juego y 840-1920; cv2 4.11.0).
  Umbral **20**. CORRECTO máx 18 (es248_t15); cada clip DUPLICADO tiene ≥1 fotograma
  ≥21 (mín por clip 29 en 197, máx 471 en 199). A nivel fotograma hay solape
  (199_t3=15 por desenfoque de transición, es248_t15=18); **a nivel clip la
  separación es perfecta** (6/6 DUP rechazados, 7/7 CORRECTO pasan) y el gate es a
  nivel clip (1 fotograma que falle → clip rechazado).
- **ORB queda descartado como señal única:** con exclusión de franja y umbral 7,
  es248_t1 (correcto, verificado visualmente: cara de IlloJuan arriba, calle abajo)
  da **9** — falso positivo. El OR (ORB≥7 O SIFT≥20) hereda ese falso positivo.
  ORB se documenta como medido-pero-no-shippeado.

### §1.2 Tabla por clip (t=7 salvo indicación; rejilla t=1,3,5 medida 23:52)

| Clip | Streamer | Veredicto visual |
|---|---|---|
| 183 EN | Burn | CORRECTO. Cara grande arriba; sangrado leve (franja de juego a la derecha). |
| 184 EN | Burn | CORRECTO con sangrado leve. |
| 186 EN | Burn | **CORRECTO (decisión de esta spec, §5.2).** Cara ~75% de ancho, juego ~25% a la derecha. La zona es mayoría cámara; el mismo sangrado existe en 183/184/189, correctos sin discusión. Rechazar 186 obligaría a rechazar los renders correctos normales del canal. El borrador adoptado lo etiqueta FALLA; esta spec adopta el borrador **excepto** en 186, con la excepción escrita aquí explícitamente (resuelve B-2: una sola expectativa por fixture, en este sitio). El sangrado se corrige ajustando la caja (`find_cam` + snap al borde real), no rechazando el clip. |
| 189 EN | Burn | CORRECTO (cuarto oscuro + escena nocturna). |
| 190 EN | Burn | CORRECTO. Publicado 20:05 YT EN, verificado LIMPIO en el Chrome del usuario. |
| 191 EN | Burn | DUPLICADO. Arriba = fotograma completo (camarita pequeña + cara grande del juego); abajo = mismo personaje. |
| 197 EN | Burn | VARIABLE por tramo, **cazado en la rejilla real**: t=1,3,5 duplicados (banda de cámara = juego nocturno oscuro, sin cara; SIFT 21/29/26); t=7,15,25 correctos (SIFT 4/1/2). El CLIP se rechaza (≥1 fotograma muestreado falla). |
| 199 EN | Burn | DUPLICADO. Arriba = fotograma completo (calle + camarita + overlays); abajo = misma calle. t=3 con desenfoque de transición (SIFT 15, por debajo del umbral — el clip se caza por t=1/t=5). |
| 214 EN | TheBurntPeanut | DUPLICADO. Arriba = cara del personaje (club, magenta), sin streamer; abajo = mismo personaje; avatar peanut diminuto abajo-izquierda en la banda de juego. |
| 244 EN | TheBurntPeanut | DUPLICADO. Avatar centrado sobre el mapa arriba; mismo mapa abajo. Avatar ~8-10% de píxeles de la zona (medido). |
| 246 ES | IlloJuan | CORRECTO. |
| 247 ES | Rubius | DUPLICADO. Arriba = fotograma completo (calle + Rubius pequeño centro-izquierda); abajo = misma calle. |
| 248 ES | IlloJuan | CORRECTO. |
| 12 EN | TheBurntPeanut | **CORRECTO (avatar).** El avatar llena la zona de cámara (píxeles avatar/zona = 0,34; bbox x 0,27-0,77, y 0,01-0,82; centro (0,52, 0,42)); abajo, juego desenfocado (transición). El avatar ES la cámara. Caso de regresión: sigue siendo rubius (§4.3, rama avatar). |

### §1.3 Corrección a la afirmación anterior (queda registrada)

Mi afirmación inicial («la cara no falla en los 10 EN») estaba mal: miré las etiquetas
del veredicto en D1, no los fotogramas. Las 3 capturas del usuario muestran la
duplicación; el diagnóstico por fotograma de §1.2 la confirma. La puerta dura
cara/formato no los frenó porque `face_gate` solo exige UNA cara visible en la zona
(la del inset pequeño o la del juego cuentan) y nunca mide qué fracción de la zona es
cámara ni si arriba ≠ abajo (§3).

## §2. Causa raíz (verificada en código)

- En `rubius.build_filter` (`clipagent/rubius.py:471-528`) el fotograma fuente se parte
  en dos: arriba `crop={cover}` de `cam_box` escalado a 1080x840 (`cam_cover_crop`,
  :505); abajo `crop={game_crop}` (`GAME_CROP = "1440:1440:560:0"`, `rubius.py:95`) a
  1080x1080. Arriba se ve LO QUE CUBRA `cam_box`. Si la caja es mala, arriba es
  gameplay y abajo también es gameplay.
- Dos formas en que sale mala: (a) el detector se engancha a una CARA DEL JUEGO
  (Haar/YuNet/estabilidad en `decide_layout` `clipagent/render.py:2320` y
  `find_facecam_box_in_source` `clipagent/final_review.py:1667`); (b) se usa el
  `DEFAULT_CAM_BOX = "561:365:0:0"` (`clipagent/rdjavi.py:114`) — la caja de p3220
  (arriba-izquierda; el CLI de `rubius.py:948-953` lo dice: «usando los de p3220») —
  en un streamer cuya cámara es un inset pequeño en otro sitio.
- El camino en vivo NO usa `rubius.py`: `live_cutter.process_peak` →
  `workers.cut_clip` (`clipagent/workers.py:422`) → `_do_cut` (`:406`) →
  `render.cut_clip_with_layout` (`clipagent/render.py:3471`) → `decide_layout`
  (`:2320`) → `segment_filter_graph` (`:2949`). `rubius.py` solo lo usan CircleCI
  (`circle_render.py`) y el re-make (`remake_all.py:448`).
- **El pisado (B9):** `cut_clip_with_layout` (`render.py:3494-3496`) hace
  `if layout_mode is not None: cfg["mode"] = layout_mode` — los 5 call sites pasan
  `layout_mode="auto"` explícito (live `live_cutter.py:1096`, re-render live `:916`,
  re-render VOD `vod_fallback.py:732`, corte directo `youtube_cut.py:555` /
  `facebook_cut.py:516`; el corte inicial VOD pasa `args.layout` desde
  `auto_clips.py:1578-1581`, `--layout` en `:705`, default None). Cualquier modo
  puesto en `cfg["layout"]["mode"]` se borra antes de `decide_layout`. El mecanismo
  por `cfg` de la v2 NO funciona; la v3 lo elimina y pasa el modo por call site (§4.2).
- **Guardas que ya existen y la v2 no nombraba:** `_cam_box_valida`
  (`render.py:1923`): ancho `bw/w ≤ split_cam_max_w_frac` (0,45, `render.py:200`) Y
  área `(bw*bh)/(w*h) ≤ cam_max_area_frac` (0,35); usada en `:1989`, `:2371`
  (caja explícita: inválida → se ignora y se sigue al auto), `:2559`, `:2601`.
  Su docstring: «una caja inválida no se renderiza como rubius; el tramo cae a
  "center" (sin webcam) y el validador lo retiene» — el «center» sigue la cara
  (Arreglo 6), por eso §4.2 lo sustituye por `"game"`.

## §3. Por qué el validador no lo cazó

- `face_gate` (`final_review.py:859-893`) exige una cara en ≥ `face_min_share` (0,5)
  de las muestras, min ancho 0,08×1080 = 86 px — el inset pequeño cuenta, la cara del
  juego cuenta, el avatar cuenta. Nunca mide qué fracción de la zona es cámara ni si
  arriba ≠ abajo.
- `template_gate` (`final_review.py:1169-1182`) despacha SOLO por `subs_style`
  (`fixed_yellow` / `karaoke` / `word_white`); no hay rama por nombre de plantilla.
  La receta rubius tiene `subs_style: word_white`, así que un duplicado pasa el
  template_gate si tiene banner/subs/gameplay (los tiene).
- `validate_final_clip` resuelve la plantilla en `:1927` y llama a `template_gate` en
  `:1981`; `face_gate` se ejecuta sin condición en `:1978`. `hard_failures` son solo
  `"cara"` / `"plantilla"` (`:1996-2009`); `needs_rerender = True` si `not hard_pasa`
  (`:2043-2049`).
- El muestreo del gate: `extract_gate_frames` (`final_review.py:780`) desde t=1,0 con
  paso `face_sample_every_s: 2` (`config.yaml:641`), tope `_GATE_MAX_FRAMES = 24`
  (constante en `final_review.py:413`).

## §4. El arreglo — Fase 1: parar la sangría (Python/config + 5 call sites; el cambio del Worker es Fase 3, §6/§9)

### §4.1 Guardas de caja en el corte: reutilizar lo que existe, hacerlo obligatorio

Existen DOS validadores de caja con reglas distintas. La v3 los distingue y usa cada
uno donde corresponde:

- **`_cam_box_valida(box, w, h, cfg)`** (`render.py:1923`): aceptación en el RENDER.
  Ancho `bw/w ≤ split_cam_max_w_frac` (0,45, `render.py:200`) Y área
  `(bw*bh)/(w*h) ≤ cam_max_area_frac` (0,35). Ya cableada en `:1989`, `:2371`
  (caja explícita), `:2559`, `:2601`. Es la guarda que decide si una caja se
  renderiza como cámara.
- **`validate_cam_box(box, face)`** (`clipagent/yunet_facecam.py:365`): validación en
  la DETECCIÓN. Ancho 0,08-0,6, alto 0,08-0,7, área <0,45, aspecto 0,5-2,0,
  borde superior y ≤0,35, centro fuera de 0,4-0,6, la caja contiene la cara entera.
  **`find_cam`** (`:395-512`): YuNet en ~12 fotogramas, cara estable en ≥50%
  (las caras del juego se mueven con la cámara), crece con mapa de bordes y valida
  con `validate_cam_box`.

**Regla de posición reconciliada (una sola, sustituye a las dos):** el centro de la
caja NO puede caer en el rectángulo central (x 30-70% Y y 30-70% a la vez), **Y se
conserva un límite superior: centro y < 0,70** (una caja abajo-izquierda, centro
(11%, 80%), pasaba la regla v2 en zona de minimapa/HUD; con el límite no pasa).
`validate_cam_box` se actualiza a esta regla (conservando área, aspecto y contención
de la cara). Casos reales: Rubius p3220 (arriba-izquierda) pasa; Rubius p7775
(`561:365:0:626`, centro x 11%, y 0,435) pasa; IlloJuan (centro x ~13%) pasa; avatar
centrado de 244 (centro ~50%, ~50%) NO pasa → sin_webcam (§4.3).

**Parámetro `face` por camino** (I-6): `validate_cam_box(box, face)` exige contención
de la cara; con `face` vacío devuelve False. Reglas por camino:
- Detección (Haar/YuNet/escaneo): `face` = la cara detectada en el segmento. Sin cara
  detectada no hay caja por ese camino (no se llama a `validate_cam_box` con cara vacía).
- `facecam_box` explícito de config: se valida SIN contención (la caja la puso un
  humano; la contención no aplica) pero CON todo lo demás (ancho/alto/área/aspecto/
  posición). Una caja explícita que no pase se ignora con aviso y se cae al escaneo
  automático (gancho existente en `decide_layout`, `:2371`, vía `_cam_box_valida`).
- `DEFAULT_CAM_BOX`: solo se aplica cuando el influencer es el dueño documentado de
  esa caja (p3220) Y pasa las guardas contra el segmento real. En cualquier otro
  streamer, sin detección válida no hay caja por defecto: no se inventa.
- **p7775 como segunda estrategia:** p3220 y p7775 son ambas de Rubius
  (arriba-izquierda vs abajo-izquierda). `box_strategies` (`remake_all.py:262-267`)
  prueba p3220 y después p7775 (en ese orden) antes de rendirse a sin_webcam; si
  ninguna valida, el resultado aceptado es sin_webcam (no se inventa una tercera).

**Test de Burn sin coordenadas hardcodeadas:** de Burn no hay caja documentada en
ningún sitio. El test de §10 NO hardcodea sus coordenadas: corre el detector sobre
segmentos reales de Burn (y de Rubius p3220/p7775 e IlloJuan, con sus cajas
documentadas) y afirma que las cajas devueltas pasan la regla reconciliada.

**Caminos que producen una caja (lista completa):**

| Camino | Guarda hoy | Cambio exigido |
|---|---|---|
| Escaneo por regiones estables (`decide_layout`, `render.py:2319-2325`) | área < 0,45 | Además `_cam_box_valida` (ancho+área) y la regla de posición reconciliada |
| `_face_cam_box` Haar, center→split (`render.py:2461`) | NINGUNA (la unión de caras no tiene tope de área) | `_cam_box_valida` obligatorio sobre la caja resultante; si no pasa → no hay split (sigue §4.2) |
| `classify_frame` / `_detect_corner_box` (`render.py:656`; ancho 0,15-0,42 en `:684`, alto ≤0,45 en `:687`) | Acotado por construcción | `_cam_box_valida` (no puede devolver fotograma completo, pero sí una caja mal centrada) |
| `facecam_box` explícito de config (`decide_layout`, `:2371`) | `_cam_box_valida` (ya existe: inválida → se ignora) | Se conserva; además la regla de posición reconciliada (sin contención de cara) |
| Fallback `DEFAULT_CAM_BOX` (`remake_all.py:262-267,379`; `rubius.py:611/699/948`) | NINGUNA | Solo p3220 + guardas; si no, no hay caja por defecto (§4.2) |
| Rama YuNet (`find_facecam_box_in_source` → `detect_vod_facecam` → `_yunet_facecam_box` → `yunet_facecam_box` → `find_cam` → `validate_cam_box`; eslabón YuNet de `face_gate`, `final_review.py:1064-1065`) | Ya pasa por `validate_cam_box` (regla vieja) | Actualizar a la regla reconciliada (posición + centro y <0,70) |

#### §4.1.5 Trazabilidad (obligatorio, con el hueco del Worker declarado)

Cada render registra en el record del clip `cam_box` (x,y,w,h en px de la fuente),
`cam_box_source` (`detectada` | `guardada` | `default_p3220` | `ninguna`) y
`cam_box_guardas` (pasa/falla por guarda). **Transporte: JSON `viral` del record**
(el mismo que ya lleva `plantilla` y `quality_gate`). **Hueco declarado (B-4): el
Worker descarta `plantilla`/`cam_box*` en `parseViralInput` (`db.ts:352`, whitelist
sin esas claves; cero ocurrencias de `plantilla` en `panel-backend/worker/src/`), así
que esos campos viven en el cerebro (record local + JSON enviado) y NO son
diagnosticables desde D1 hasta la Fase 3, que añade las claves al Worker (§6/§9).
La puerta del validador (§5) no depende de D1: funciona sobre el render.

### §4.2 Sin caja fiable no hay rubius — enganche real en el flujo live

- Si la decisión §4.3 da sin_webcam, el momento se renderiza como
  **`rubius_sin_webcam`** (§6), nunca como rubius con una caja inventada, nunca como
  `center` con la cara perdida.
- **El modo viaja por call site, no por `cfg["layout"]["mode"]`** (B9: el mecanismo
  por `cfg` de la v2 se elimina — `cut_clip_with_layout` lo pisa en
  `render.py:3494-3496`). Los CINCO call sites pasan el modo; `cut_clip_with_layout`
  mantiene su contrato (CLI > config):
  1. Live: `live_cutter.process_peak` → `cut_clip(..., layout_mode=..., cfg=cfg2, ...)`
     (`live_cutter.py:1096`). **Pre-chequeo OBLIGATORIO (B-3.5):** antes del corte,
     con una COPIA por clip de `self.cfg` (el `self.cfg` es compartido: poner
     `mode="game"` ahí contaminaría los demás clips), se corre la decisión §4.3
     sobre el segmento fuente; si da sin_webcam → `layout_mode="game"`; si da rubius
     con caja → `layout_mode="auto"` + `cfg2["facecam_box"] = caja` (validada §4.1).
  2. Re-render live: `_rerender_after_gate` → `cut_clip(..., layout_mode=..., ...)`
     (`live_cutter.py:916`): `layout_mode="game"` cuando la decisión §4.3 da
     sin_webcam, si no `"auto"` (o `None` para que mande `cfg`).
  3. Re-render VOD: `vod_fallback.py:732`: igual que (2).
  4. Corte inicial VOD: `auto_clips.py:1578-1581` (`layout_mode=args.layout`): se
     añade `"game"` a los choices de `--layout` (`auto_clips.py:705`, hoy
     `["auto","stack","pip","center"]`); la decisión §4.3 se corre dentro de
     `auto_clips.py` antes del corte y la entrada del manifiesto lleva `"plantilla"`.
  5. Corte directo por URL: `youtube_cut.py:555-557` / `facebook_cut.py:516-518`:
     `_default_render` gana parámetro `layout_mode` (default `"auto"`);
     `_default_validate` (`:560-566` / `:520-526`) gana `plantilla=None`.
- **Modo forzado `"game"` en `decide_layout`** (`render.py:2320`): la lista de
  forzados hoy es solo `("stack", "pip", "center")` (`:2340`); `"game"` solo llega
  vía tramos `game_voice`. Se añade `"game"`: con `cfg["mode"] == "game"`,
  `decide_layout` devuelve todo el clip como un solo segmento
  `{layout: "game", params: {}, reason: "forzado: sin webcam (rubius_sin_webcam)"}`.
  **La decisión lleva la plantilla (B-3.3):** la rama forzada añade
  `"plantilla": "rubius_sin_webcam"` al dict que devuelve (hoy devuelve solo
  `{"mode", "segments", "samples"}`); `cut_clip_with_layout` (`:3471`) la devuelve
  tal cual. Discriminador entre `game` forzado y `game` por `game_voice`:
  `decision["mode"] == "game"` (el forzado pone `mode="game"`; `game_voice` no
  cambia `mode`). `live_cutter.py:1104` ya lee `layout.get("plantilla")` primero.
  `config.yaml:385` (`mode: "auto"`) gana `game` como valor forzado en el comentario
  (no configurable).
- **Rama `game` en `segment_filter_graph` (`render.py:3083`):** ya existe (recorte
  9:16 centrado a pantalla completa). Se le añade el zoom de gameplay llamando a
  `rubius._game_filter` con `game_h=1920` (import perezoso dentro de
  `segment_filter_graph`, patrón existente en `render.py:2847`; nunca a nivel de
  módulo; `_game_filter` parametrizado por `game_h`, default 1080 — M4). NO se usa
  `center` (sigue la cara, Arreglo 6).
- **`resolve_split_variant` (`render.py:377`):** el formato nuevo NO pasa por aquí.
  Un valor explícito desconocido levanta `ConfigError` (falla en voz alta, como
  `resolve_template`). `"auto"` sigue resolviendo al default.
- **Gancho de rechazo en el render (I2):** `decide_layout` (`:2320`), dos puntos:
  (a) caja explícita `facecam_box` (`:2371`): inválida → se ignora y se cae al
  escaneo automático (ya existe, vía `_cam_box_valida`); (b) conversión pip → split
  (`:2448`): inválida → el tramo se degrada a layout `"game"` con params `{}`.
  Comportamiento del llamador: nada dentro del render; el tramo se valida como
  rubius, la puerta de cara falla y el rescate §4.4 enruta a `sin_webcam` (o
  revisión humana si ambiguo). `classify_frame` no es el gancho (clasificador, no
  punto de decisión).
- **Nunca:** si hay cara detectable, nunca se usa sin_webcam para «esconder» una cara
  mal encuadrada. Para cara mal puesta sigue el re-render con `facecam_box` forzado.

### §4.3 Decisión cara/no-cara — UNA sola función: `decide_template_for_segment`

Nueva función **`decide_template_for_segment(segment_path, duration, cfg)`** en
`clipagent/final_review.py` (I-5: §4.3 y §6 apuntaban a funciones distintas con
órdenes distintos; esta es la única). Se apoya en `find_facecam_box_in_source`
(`:1667`). Órdenes (distintos, ambos correctos en su sitio):
- Interno de `detect_vod_facecam` (`render.py:3384`): voto (5 muestras) → rescate
  por bordes (`_edge_rescue`, solo conf `high`) → rescate YuNet (`_yunet_rescue`).
  Éxito de un rescate → `hits: 1` **CON** caja.
- Externo de `find_facecam_box_in_source` (`:1667`): `detect_vod_facecam` → YuNet
  (`yunet_facecam_box`) → bordes. El docstring de `:1667` dice otro orden: la
  implementación lo corrige.
- **Tabla de decisión (sobre `(facecam_box, hits)` conjuntamente, B10):**

| Caso | Decisión |
|---|---|
| `facecam_box` presente (voto ≥2 o rescate) | rubius con caja (validada §4.1) |
| Caja ausente + `hits == 1` | ambiguo → rubius (nunca sin_webcam). Si la puerta de cara falla después, revisión humana |
| Caja ausente + `hits == 0` + sin avatar | `sin_webcam` |
| Avatar detectado (ver abajo) | según rama avatar |

- **Rama avatar (primero que la posición):** la decisión consulta la heurística de
  avatar del escaneo (`_detect_vtuber_candidates`, `render.py:973`, la misma que usa
  `decide_layout` en `_scan_frames` `:1938`, llamada en `:1963`). Si hay avatar, la
  regla de posición/tamaño de `validate_cam_box` NO se aplica a su caja (un avatar
  no es una cámara PiP: puede ser centrado y grande — ES la presencia del streamer).
  La validez del avatar es su **proporción de píxeles en la zona de cámara**:
  `avatar_share_min = 0,20` (config `validator:` efectivo):
  - share ≥ 0,20 → **rubius** (el avatar ES la cámara). Medido: clip 12 = 0,34
    (bbox x 0,27-0,77, y 0,01-0,82; centro (0,52, 0,42)).
  - share < 0,20 → **sin_webcam** (avatar pequeño sobre juego; la zona sería
    mayoría gameplay; el avatar sigue visible dentro del gameplay a pantalla
    completa). Medido: 244 = 0,08-0,10 (bbox 0,10; centro (0,47, 0,63)).
  - La proporción se mide por segmentación de píxeles del avatar (no por área de
    bbox: en 244_t2 el bbox da 0,42 por contaminación del mapa — el bbox NO vale).
- Segmento mixto: se decide por `(facecam_box, hits)`, no por
  `validator.face_min_share` (0,5 es umbral de la puerta de cara, no de la detección).

### §4.4 Re-render tras fallo de plantilla (cableado exacto, B-3)

- `validate_final_clip` pone `needs_rerender = True` cuando `not hard_pasa`
  (`final_review.py:2043-2049`); `_rerender_after_gate` (`live_cutter.py:874`) se
  llama desde `:1422` cuando `needs_rerender` es True, con `plantilla=plantilla`
  (la ORIGINAL).
- **(B-3.1)** En `_rerender_after_gate`, el corte (`:916`) pasa
  `layout_mode="game"` cuando la decisión §4.3 da sin_webcam (hoy pasa
  `layout_mode="auto"` y `_do_cut`/`cut_clip_with_layout` lo pisan).
- **(B-3.2)** La re-validación (`:959`) usa `plantilla="rubius_sin_webcam"` cuando
  la decisión es sin_webcam (hoy usa la `plantilla` original → se validaría contra
  la receta rubius, con `face_gate` corriendo). Después del re-render,
  `record["plantilla"]` (fijada una sola vez en la construcción del record,
  `:1322`) y el `quality_gate` derivado se actualizan desde
  `review2["plantilla"]` (hoy nada los actualiza: el re-render seguiría viajando
  como `plantilla="rubius"` al panel).
- **(B-3.4)** AMBOS caminos de `hard` corren la decisión §4.3 completa (incluida la
  heurística de avatar) antes de elegir sin_webcam: `"plantilla" in hard` con motivo
  de layout Y `"cara" in hard` con `find_facecam_box_in_source` → None (hoy el
  segundo iba directo a sin_webcam y un clip avatar como el 12 caería ahí).
- Un rubius que falla «plantilla» por layout → se corre §4.3. Un sin_webcam que
  falla (banner/subs) → re-render normal, misma regla, 1 intento
  (`max_rerenders: 1`). Si sigue fallando → revisión humana, como hoy.
- **En Fase 1 no hay caja guardada** (Fase 2, §7): el re-render usa la caja
  re-detectada (con guardas) o sin_webcam.

### §4.5 Invariante de fallback

Recuadro inválido o ausente → sin webcam o retención, **jamás el fotograma completo
como cámara, jamás duplicar**. Validez del recuadro = `_cam_box_valida`
(`render.py:1923`): ancho < 45% Y área < 35% (I-8: la v2 citaba
`split_cam_max_w_frac` como si fuera de área; es de ancho — la regla de área vive
en `_cam_box_valida` vía `cam_max_area_frac` 0,35 y en `validate_cam_box` vía área
<0,45). Aplicada **en el render** (§4.1) y como **red de seguridad en el validador**
(§5). Test de la invariante en §10 (recuadro ≥45% de ancho o ≥35% de área → sin
webcam o retención).

## §5. Puerta en el validador (reescrita; prioridad 1)

Dos criterios nuevos, **duros** (fallo = `rechazado`, no se encola, también frena el
encolado a mano). Se evalúan en `final_review.py` sobre el **render final** (no sobre
la fuente), con el muestreo existente del validador: `extract_gate_frames`
(`final_review.py:780`) desde t=1,0 con paso `face_sample_every_s: 2`
(`config.yaml:641`), tope `_GATE_MAX_FRAMES = 24`. **Basta 1 fotograma muestreado
que falle para rechazar el clip.** Los fotogramas del banner (t 0,5-3,5 s) **se
evalúan** (no se saltan): el método SIFT es robusto al banner (197_t1/t3 lo
demuestran); saltarlos dejaría fuera justo los fotogramas que cazan 197.

### §5.1 `layout_duplicado`: arriba ≠ abajo — método SIFTct02 (primario único)

**Procedimiento exacto (fijado; sin él los números no son reproducibles):**
función nueva `_cam_game_match` en `final_review.py`. Por fotograma muestreado:
zona de cámara = y 0-840, x 0-810 (sin el 25% derecho, la franja de sangrado);
zona de juego = y 840-1920, x 0-1080. SIFT (`cv2.SIFT_create(nfeatures=2000,
contrastThreshold=0.02)`, BFMatcher L2, knnMatch k=2, ratio de Lowe 0,75);
`matches` = buenos matches cámara→juego. **El fotograma falla si `matches ≥ 20`.**
cv2 4.11.0 (ya en el venv del validador; SIFT está en opencv-python ≥4.4, no es
dependencia nueva). Coste medido: ~3 s por fotograma (hasta ~72 s por clip con 24
muestras); si el presupuesto de tiempo del validador lo exige, la implementación
puede evaluar 1 de cada 2 fotogramas muestreados (t=1,5,9,…) — la separación del
corpus se conserva (t=1 caza los 6 clips DUP por sí solo); el test usa el muestreo
completo.

**Calibración (medida 2026-11-26, scripts en `/tmp/en_diag/calib52.py`,
`calib_grid.txt`, `siftct02_full.txt`):** corpus 49 fotogramas (t=2,7,15,25) + 39 de
rejilla (t=1,3,5) + 3 capturas. SIFTct02: CORRECTO máx 18 (es248_t15); cada clip
DUPLICADO tiene ≥1 fotograma ≥21 (mín por clip 29 en 197; 199_t3=15 por desenfoque
de transición — el clip se caza por t=1/t=5). **A nivel clip: 6/6 DUP rechazados,
7/7 CORRECTO pasan. Umbral 20** (entre 18 y 29; el umbral 17 cazaría la captura 3
pero rechazaría falsamente es248_t15=18).

**Por qué no ORB (refuta la corrección B-1 del goldfish con evidencia):** ORB con
exclusión de franja y umbral 7 da **9 en es248_t1, un fotograma CORRECTO verificado
visualmente** (cara de IlloJuan arriba, calle abajo) — falso positivo a nivel clip
(es248 se rechazaría por t=1). El OR (ORB≥7 O SIFT≥20) hereda ese falso positivo.
ORB queda documentado como medido-pero-no-shippeado. El goldfish no vio SIFTct02
(terminó antes de esa medición); la v3 lo incorpora y registra la discrepancia aquí.

**Por qué no celdas (B-1):** implementado `_cam_game_match` por celdas tal como lo
describía la v2 (rejilla 32×32, celdas 4×2, búsqueda ±4 px, NCC 0,7 y 0,8): **0
celdas coincidentes en TODOS los fotogramas, pureza 1,0 en CORRECTO y DUPLICADO.**
Causa: las dos zonas son recortes a distinta escala (cover crop ~2× arriba, recorte
1440×1440 a 0,75× abajo); un parche de tamaño fijo no casa aunque el contenido sea
el mismo. Las celdas se eliminan como método (también la «pureza por celdas» de la
v2 y la fijación del borrador en su I3 — el borrador manda per-cell NCC como método
único; esta medición lo refuta y la v3 no lo adopta en ese punto).

**Segunda señal — procedencia de la caja (determinista):** si el `cam_box`
registrado del render (§4.1.5) no pasa las guardas §4.1, el clip se rechaza aunque
el píxel no lo cace.

**Check duro en la rama rubius:** `layout_duplicado`: SIFTct02 ≥ 20 → **falla**.
Caza 191/199/214/244/247 (+ 197 en t=1,3,5).

### §5.2 `cam_pura`: la zona de cámara es mayoritariamente cámara (B-2 resuelto)

**Definición (no fracción de caja Haar — la v1 estaba mal: Haar da 0 caras en 186
CORRECTO y 0,543 de falso positivo en 199 DUPLICADO):** `cam_pura` es el check que
exige la segunda propiedad de la regla del usuario (19:59): la zona de cámara es
mayoritariamente cámara, no gameplay. Se implementa como **la disyunción de las dos
señales que ya existen**: (a) el test de similitud de §5.1 (si la zona de cámara
coincide con la zona de juego, es gameplay → falla); (b) la procedencia de la caja
(si el `cam_box` registrado no pasa las guardas §4.1, la zona no es cámara
verificada → falla). Es un check independiente con su propio motivo («la zona de
cámara es mayoritariamente gameplay»), que comparte el instrumento de §5.1 más la
procedencia — no un segundo instrumento sin definir. **186 PASA `cam_pura`**
(SIFT 3; su caja pasa las guardas; el sangrado es 25% de la zona). **244 FALLA**
(SIFT 35/43; avatar centrado sobre mapa).

### §5.3 Test de regresión (obligatorio, sobre la rejilla real del gate)

- **Fixtures:** las 3 capturas del usuario (recortadas, coordenadas en el README) +
  los fotogramas de la rejilla del gate (t=1,3,5,7), en
  `tests/fixtures/duplicacion/` con `README.md` de etiquetas. **Etiquetas finales:**

| Fixture | Esperado |
|---|---|
| usuario_captura1_calle.jpg | RECHAZAR (SIFT 465) |
| usuario_captura2_banner_chat_said_it.jpg | RECHAZAR (SIFT 110) |
| usuario_captura3_007.jpg | **Limitación documentada (ver abajo; SIFT 17)** |
| en183_t7, en184_t7, en186_t7, en189_t7, en190_t7 | PASAR |
| en191_t7, en199_t7, en214_t7, en247_t7 (es247_t7) | RECHAZAR |
| en197_t1, en197_t3, en197_t5 | RECHAZAR (SIFT 21/29/26; rejilla real del gate) |
| en197_t7 | PASAR por fotograma (el CLIP 197 se rechaza por t=1,3,5) |
| en244_t1, en244_t3 | RECHAZAR (SIFT 54/64; rejilla real) |
| en244_t7 | RECHAZAR (SIFT 35) |
| es246_t7, es248_t7 | PASAR |
| clip12_t7 | PASAR (avatar rubius; share 0,34) |

- **197_t2 y 244_t2 se retiran del test** (t=2 no se muestrea nunca: la rejilla del
  gate es t=1,3,5,7…; quedan en `/tmp/en_diag/` como referencia de medición, no
  como fixtures).
- **Limitación conocida — captura 3:** SIFT 17, por debajo del máximo CORRECTO (18).
  Ningún método probado la caza a nivel fotograma sin rechazar falsamente un render
  correcto (es248_t15 = 18). El CLIP del que viene (214) sí se rechaza por sus
  otros fotogramas (SIFT 96-246). El test fija la captura 3 como limitación
  documentada, no como RECHAZAR falso: el test afirma su valor medido (17) y que
  el clip 214 se rechaza por sus fotogramas de render.
- **El test es a nivel clip para el validador** (un clip con 1 fotograma muestreado
  que falle → rechazado; un clip con todos sus fotogramas por debajo del umbral →
  pasa) **y a nivel fotograma para la función `_cam_game_match`** (cada fixture
  etiquetado tiene su valor SIFT de referencia; los fotogramas límite —199_t3=15,
  es248_t15=18— se afirman por su valor, no por un pasa/falla individual).
- **Dónde se ejecuta:** en CI, con los fixtures en el repo. Sin dependencias nuevas
  más allá de cv2/numpy (ya en el venv del validador).

### §5.4 Registro en el veredicto

- Los dos checks nuevos aparecen en `template.checks` con `pasa`/`motivo` como los
  demás (`layout_duplicado`, `cam_pura`). Cuando fallan, `template.pasa = False` →
  `hard_failures` incluye `"plantilla"` (`final_review.py:1996-2009`) →
  `needs_rerender = True` (`:2043-2049`) → `_rerender_after_gate` (§4.4). Motivos:
  `layout_duplicado`: «la mitad superior y la inferior son la misma imagen»;
  `cam_pura`: «la zona de cámara es mayoritariamente gameplay».
- En `build_quality_gate` (`quality_gate.py:117`): el criterio `webcam` se resuelve
  como hoy (cadena determinista manda; `face.get("pasa") is True` → pasa). La puerta
  nueva no cambia `build_quality_gate` salvo §6.4 (plantilla sin_webcam).
- `_criterion` (`quality_gate.py:112-114`): hoy guarda `"motivo": motivo if not pasa
  else ""`. Cambio: **`"motivo": motivo` siempre** (se guarda el motivo aunque
  pase). **Verificado 2026-11-26 (M-2): ningún test existente afirma
  `motivo == ""`** (grep en `tests/` y `clipagent/` sin resultados); la instrucción
  de la v2 de «actualizar esos tests» era errónea — la implementación verifica de
  nuevo al tocar el código y no actualiza nada que no exista.

## §6. Formato «rubius sin webcam» (adopta el borrador revisado, con sus correcciones de ronda 2 inlinadas)

Se adopta el diseño del borrador revisado
(`runs/formato-rubius-sin-webcam-2026-11-26/SPEC.md`, 454 líneas tras su 2ª ronda de
corrección) en sus §§2 (decisión), 3 (layout), 4 (miniatura), 5 (paquete), 6
(validador), 8 (panel), 9 (flujo) y 10 (tests). **Esta spec inlina los mecanismos
que necesita** (no depende de que el archivo del borrador esté en el repo — vive
fuera, `~/workspace/gta6-clips/runs/…`, y está pendiente de su propia 3ª revisión +
aprobación del usuario; I-7). Las correcciones de su ronda 2 que afectan a esta spec:

- **B7 — Transporte `plantilla` (Worker):** `parseViralInput` (`db.ts:352`) es
  whitelist sin `plantilla` (verificado por grep: cero ocurrencias de `plantilla`
  en `panel-backend/worker/src/`). Cambio exigido (Fase 3, §9): `plantilla?: string`
  en `ViralData` (`db.ts:212`); rama nueva en `parseViralInput` (string + nombre
  conocido: rdjavi, willyrex, rubius, `rubius_sin_webcam`; desconocido → error,
  nunca guardado en silencio); `mergeViralJson` (`db.ts:497`):
  `if (patch.plantilla !== undefined) next.plantilla = patch.plantilla;`
  (reemplazo, como `quality_gate`/`seo_source`; una `plantilla` en base sobrevive a
  un PATCH que no la menciona). Cadena POST verificada: `index.ts:1412` (parse) →
  `:1491` (`viralJson = mergeViralJson("{}", viralParsed)`) → INSERT `db.ts:1949`
  (sin columna `plantilla`; viaja dentro del JSON `viral`). PATCH: `index.ts:1240`
  (parse) → `:1242` (merge). **Corrección a la resolución del borrador:** sus citas
  `:344`/`:475`/`:2034`/`:1452` son erróneas (verificadas por grep: `:344` y `:475`
  son llaves de cierre; `:2034` no es el INSERT; `:1452` es `score = body.score;`) —
  esta spec usa las líneas de la revisión, verificadas. Los campos de trazabilidad
  `cam_box`/`cam_box_source`/`cam_box_guardas` (§4.1.5) entran por el mismo cambio.
  Las tres líneas del cerebro se mantienen (live `live_cutter.py:1647` — construcción
  de `_viral`, a la que se añade `_viral["plantilla"] = record["plantilla"]`; viral
  `viral_source.py:740-772`, hardcodeo `plantilla="rubius"` en `:687` que pasa a ser
  la plantilla decidida; VOD `viral_payload` `vod_fallback.py:938-951` + PATCH
  `:1023-1035`), ahora como «el valor que el cambio del Worker acepta y guarda».
- **B8 — Despacho de `template_gate`:** ramificación POR NOMBRE DE PLANTILLA ANTES
  del despacho por `subs_style`: `template_name == "rubius_sin_webcam"` →
  `_template_fullscreen_game(...)`; si no, el despacho actual
  (`final_review.py:1169-1182`). `template_gate` recibe `template_name` como
  parámetro (hoy no lo tiene; `validate_final_clip` lo resuelve en `:1927` y llama
  en `:1981`). Alternativa rechazada: un valor distinto de `subs_style` (mentiría
  sobre los subtítulos, que son `word_white` compartidos; la diferencia es el
  layout). Test: receta sin `cam` por `template_gate` no llama a
  `_template_word_white` (sin `KeyError`) y ejecuta la rama nueva.
- **B9 — Modo por call site:** ver §4.2 (los 5 call sites; `cut_clip_with_layout`
  mantiene su contrato).
- **B10 — Decisión `(facecam_box, hits)`:** ver §4.3.
- **B11 — Tres duplicados en `config.yaml`:** `validator:` 553/640, `templates:`
  604/717, **`sfx:` 366/379** (idénticos). Limpieza: borrar 553–603, 604–639 y
  366–370 (bloque `sfx:` 366–369 + blanco 370; el comentario de Layout de 371–378 se
  conserva). Test de guardia: ninguna clave de nivel superior duplicada (mejor que
  contar solo las tres).
- **I1 — Una medida (ancho):** validez del recuadro por ancho
  (`bw / SAMPLE_W < split_cam_max_w_frac`, 0,45; `render.py:200`, aplicada en
  `_split_params` `:1996`). La corrección por escaneo (`:2325`, área <0,45) se unifica
  a la misma regla de ancho. Ver §4.5 (que añade el área <35% de `_cam_box_valida`
  como segunda condición en el render).
- **I2 — Gancho nombrado:** ver §4.2 (decide_layout, puntos (a) `:2371` y (b) `:2448`).
- **M4 — Zoom:** se LLAMA a `rubius._game_filter` desde la rama `game` (import
  perezoso, patrón `render.py:2847`; nunca a nivel de módulo), parametrizado por
  `game_h` (default 1080; la rama `game` lo llama con 1920).
- **Geometría (§3 del borrador):** gameplay y 0-1920 (recorte 9:16 centrado; fuente
  2560x1440 → 810x1440 en x=875); banner igual que rubius (x 103-967, y 811-1099,
  `#FDD227`, t 0.5-3.5 s); subtítulos iguales (blancas Arial Black 100 px, MarginV
  900, zona y 900-1020); punch-in desactivado; zoom gameplay 1.15x; sin whoosh.
- **Validador (§6.1-6.4 del borrador, con B8):** receta nueva SOLO en el bloque
  `templates:` efectivo (línea 717); `TEMPLATE_REQUIRED_KEYS`: añadir la entrada en
  AMBAS definiciones (`config.py:378` y `:664`, contenido idéntico; gana la segunda)
  o deduplicarlas. `TEMPLATE_BY_VARIANT` (`config.py:681`): sin entrada nueva.
  `resolve_template` (`config.py:953`): 4º nombre aceptado, 5º desconocido sigue
  fallando fuerte (`ConfigError`). Rama nueva `_template_fullscreen_game` (no
  reutilizar `_template_word_white` tal cual: lee `recipe["cam"]` en su primera
  línea, `final_review.py:1444`): checks de banner/subs/SFX extraídos a helpers
  compartidos; **no ejecuta** `_check_full_width` (`:1463`) ni `_check_cam_live`
  (`:1597`). **`gameplay_puro` (duro, la prueba positiva de layout que condiciona
  la exención de cara):** similitud entre mitad superior (y 0-840) e inferior
  (y 840-1920) BAJA (< umbral SIFTct02: un sin_webcam correcto tiene dos regiones
  distintas del mismo juego, no la misma imagen dos veces) y sin costura de split.
  Un rubius duplicado mal etiquetado como sin_webcam FALLA aquí. Accesos a
  `recipe["cam"]` a guardar: `_band_video` (`:842`, cubierto al guardar `face_gate`),
  `face_gate` (`:893`), `_template_word_white` (`:1444`). `validate_final_clip`
  resuelve la plantilla antes (`:1927`) y **no ejecuta `face_gate`** para
  `rubius_sin_webcam`: `face` queda como centinela `{"pasa": None, ...}` («no
  aplica»). `build_quality_gate` recibe la plantilla sin cambiar su firma
  (`validate_final_clip` ya devuelve `"plantilla"`): criterio `webcam` pasa con
  motivo «sin webcam por plantilla: layout gameplay puro verificado» solo si
  `gameplay_puro` pasó. **Un rubius normal sin cara SIGUE fallando.** Entrada
  `RECIPE_TEXT["rubius_sin_webcam"]` (`final_review.py:120`); `webcam_visible` de
  Gemini en `overridden` para esta plantilla (nunca duro). Docstring «una de TRES
  recetas» (`:1862`) → «CUATRO».
- **Panel (§8 del borrador):** la plantilla viaja en el JSON `viral` (con el cambio
  B7). La píldora solo-lectura en la Cola/Editor es cambio de frontend; con B7 el
  valor está en D1 dentro de `viral`; lo que se verifica es solo si el endpoint de
  lista/detalle lo serializa. **La píldora va a Fase 3** (§9).
- **Flujo (§9 del borrador):** live (§4.2 de esta spec); VOD: la función de corte ya
  recibe `plantilla` (`vod_fallback.py:658`, la pasa a la validación `:754-759`); el
  render usa `cut_clip_with_layout` con `layout_cfg` (`:724-734`); **no existe
  parámetro `plantilla` en el job de VOD hoy** (0 ocurrencias en `youtube_cut.py`/
  `facebook_cut.py` salvo docstrings): en v1 decide la detección. Re-make:
  `remake_all.py` hardcodea `plantilla="rubius"` (`:481`, `:752`, `:821`): esos tres
  puntos pasan a usar la plantilla decidida.
- **§5.1/§5.2 no aplican a sin_webcam como «duplicado»:** no hay dos zonas que
  comparar. `layout_duplicado`/`cam_pura` se evalúan solo cuando la plantilla tiene
  `cam`. Un sin_webcam correcto no es rechazado por `layout_duplicado` (el test lo
  fija). **Divergencia con el borrador (I3):** el borrador fija per-cell NCC como
  método único de similitud; esta spec no lo adopta (medido: 0 coincidencias en
  todo el corpus) y shippea SIFTct02 (§5.1). El borrador necesitará su propia
  corrección en ese punto en su 3ª revisión.

## §7. Fase 2 — Cajas de cámara por influencer (después de la Fase 1)

- **Idea:** detectar la caja de cámara **una vez por influencer** y guardarla; el
  corte reutiliza la guardada en vez de re-detectar a ciegas en cada clip. Si la
  detección es débil, no se inventa: sin caja fiable no hay rubius (§4.2).
- **Almacén:** tabla D1 nueva `influencer_cam_boxes` (propiedad del panel; migración
  siguiente a la 0045): `influencer_id`, `box_json` ([x,y,w,h] en px de la fuente de
  referencia), `src_w`, `src_h`, `confianza` (`alta` | `media`), `muestras` (nº de
  segmentos que votaron la misma esquina), `detectado_en`, `actualizado_en`. El
  cerebro la lee y escribe vía la API del panel (ya lista influencers por
  `GET /api/influencers`, `panel_bridge.py:348`).
- **API (a implementar en Fase 2):** `GET /api/influencers/:id/cam-box` (lectura por
  influencer; 404 si no hay caja) y `PUT /api/influencers/:id/cam-box` (escritura con
  sesión del panel; body `{box, src_w, src_h, confianza, muestras}`; idempotente por
  `influencer_id`: re-escrituras con la misma caja no duplican; si dos segmentos
  votan cajas distintas, NO se guarda nada hasta que haya acuerdo). Sin configuración
  nueva para el usuario: la caja se aprende sola.
- **Aprendizaje:** en los primeros 3 segmentos de un influencer, la detección vota la
  esquina; si ≥2 segmentos coinciden (±10 px) con confianza alta, la caja se guarda.
  Si no hay acuerdo, no se guarda nada.
- **`confianza`:** `alta` = ≥3 segmentos de acuerdo + borde confirmado (snap al borde
  real); `media` = 2 de acuerdo, sin confirmación de borde. Una caja `media` se usa
  pero se re-valida en cada clip hasta confirmarse `alta`.
- **Re-aprendizaje:** si un clip con caja guardada falla la puerta §5.2 dos veces
  seguidas, la caja se marca sospechosa y se re-detecta en el siguiente segmento (el
  streamer puede haber movido la cámara). Nunca se re-detecta en silencio dentro del
  mismo clip.
- **Sin semilla en `config.yaml` (M-5):** la v2 proponía sembrar `facecam_boxes`
  con un dry-run sin procedimiento definido. Se elimina la semilla: solo la tabla D1.
- **Alcance:** trabajo aparte de la Fase 1; la Fase 1 no la espera. La puerta del
  validador (§5) es la que garantiza «nunca más», con o sin cajas guardadas.

## §8. Arreglos VOD EN (Fase 3; desbloqueada: la opción A aterrizó)

Contexto verificado 2026-11-26 19:42 + actualizado 23:20: el respaldo VOD EN está
bloqueado en dos puntos. (a) El VOD-fallback de cerebro-live solo corre si NADIE está
en directo en ningún idioma y prioriza ES (`clipagent/vod_fallback.py`,
`fallback_order`, `FALLBACK_PRIORITY=["rubius","illojuan"]`), máx 2 VODs/corrida
(`cerebro-live.yml --max-vods 2 --max-clips 2`; `--youtube` ya activado 2026-11-21).
(b) La pasada diaria de VODs (panel-daily 09:00) muerta desde 21-nov. La opción A
aterrizó (commit 26267d5) y los schedules son `['*/5 * * * *']` verificados por API
en ambas cuentas: hay 12 ciclos en la hora 9, no uno.

### §8.1 Reservar 1 de 2 huecos del VOD-fallback para EN (punto del cambio: `run()`, no `fallback_order`)

- `fallback_order` (`vod_fallback.py:193`) solo ordena (ES primero, EN al final); la
  cuota vive en `run()` (`:1463-1465,1507`: `if enqueued >= max_clips or vods_tried
  >= max_vods: break`). Con 2 VODs ES disponibles, el bucle nunca llega a ningún EN.
  El cambio es en `run()`: contadores por idioma (`vods_tried_es`, `vods_tried_en`)
  en un solo pase por `order`: tope ES = `max_vods - 1` **si existe al menos una
  entrada EN que PASA el pre-chequeo**, tope EN = 1; si no hay entradas EN que pasen
  el pre-chequeo, tope ES = `max_vods` (no se desperdicia capacidad). **(I-3):**
  la v2 calculaba la reserva sobre entradas EN en `order` aunque estuvieran en
  enfriamiento o sin VOD — el hueco reservado no se usaba nunca y ES solo intentaba
  `max_vods - 1` (1 de 2 huecos perdido). Alternativa aceptada: tope perezoso (ES
  rellena el hueco EN si ningún EN fue intentado). Las entradas que superan el tope
  de su idioma se saltan (`continue`, sin contar).
- **Colocación del chequeo (I-3):** el chequeo de tope por idioma va ANTES de
  `latest_vod_fn` en el cuerpo del bucle (hoy la llamada a `latest_vod_fn` está
  antes de los `continue` de pre-chequeo): si va después, las entradas ES capadas
  siguen costando llamadas de listado.
- **Política de consumo (ya existe, se conserva):** los VODs saltados por pre-chequeo
  (sin VOD, en enfriamiento, `vod_clearly_not_gta6`) hacen `continue` ANTES de
  `vods_tried += 1`: no consumen hueco. Solo los VODs realmente intentados cuentan.
  Un VOD EN intentado que no deja clips válidos SÍ consume su hueco (fue un intento;
  reintentarlo en la siguiente corrida es desperdicio).
- Test: corrida simulada con candidatos ES+EN → 1 VOD EN intentado; EN solo en
  enfriamiento → 2 ES (el hueco no se pierde); sin candidato EN → 2 ES; VOD ES en
  enfriamiento no consume hueco.

### §8.2 Dispatch diario: atender antes del trabajo pesado (el reintento ya existe)

**Hechos verificados en código (corrigen la v2):**

- `maybeDispatchDaily` (`panel-backend/worker/src/index.ts:1779`) ya tiene el
  reintento por diseño (docstring): «Dedupe por día en settings (clave
  `brain_daily_dispatched_day`)… si el dispatch falla, NO se marca el día y se
  reintenta en el próximo ciclo mientras sigan siendo las 9». La marca de día solo
  se pone si `dispatchCerebro` devuelve ok. **(I-1): el camino `!ok` NO deja
  rastro:** `const ok = await dispatchCerebro(env, "panel-daily", tenant); if (!ok)
  return;` — sin log. Solo el camino de excepción loguea («Dispatch diario
  falló»). La v2 afirmaba «el fallo ya se registra en `worker_events`»: falso para
  el camino `!ok`.
- **Tres hipótesis para el 26-nov** (la v2 solo consideraba una): (a) el ciclo pesado
  agotó el presupuesto de subrequests antes de la llamada final (sin rastro porque
  el `logWorkerEvent` del catch también es un subrequest); (b) `dispatchCerebro`
  devolvió `false` (GitHub 4xx/5xx, token inválido) — igualmente consistente con la
  ausencia total de filas `github_dispatch` en D1 entre las 09:00 y las 10:30 Chile;
  (c) `runWorkerCycle` lanzó una excepción y el handler abortó antes de la llamada
  final (`:4624`) sin dejar rastro (la llamada a `runWorkerCycle` no está envuelta
  en try/catch visible). El diseño de mover el dispatch al principio + reintento por
  no-marcar el día arregla los tres casos.
- `isDailyDispatchDue` (`panel-backend/worker/src/cycle-gate.ts:211`, exportada):
  `hour == 9` y día no marcado; devuelve True si la lectura de settings falla. La
  puerta se evalúa en `cycleGate` (`:293-304`, que devuelve `{run, …, dailyDue, …}`).
- **(I-2) Condición «Worker en marcha»:** hoy la llamada final está condicionada a
  `r.ran || r.gate.dailyDue` (`index.ts:4624`), y con el Worker parado
  `runWorkerCycle` (`jobs.ts:1974`) devuelve la puerta vacía:
  `if (!(await isWorkerRunning(db, tenant))) return { ran: false, …,
  gate: idleCycleGate() };` — parado ⇒ no hay dispatch diario (comentario en
  `index.ts:4595-4601`). La v2 llamaba `isDailyDispatchDue` al principio sin
  comprobar el estado: panel-daily se dispararía también con el Worker parado.
  **Decisión explícita: NO se dispara con el Worker parado** (se preserva el
  comportamiento actual).

**Cambio exigido:**

1. DENTRO del bucle por tenant del handler `scheduled`, ANTES de `runWorkerCycle`
   de ese tenant: `if (await isWorkerRunning(env.DB, tenant)) await
   maybeDispatchDaily(env, tenant, event.scheduledTime)` (la función misma vuelve
   a comprobar hora==9 y día no marcado, así que fuera de la hora 9 o con el día ya
   marcado es no-op). Si la lectura de `isWorkerRunning` falla, se salta el dispatch
   temprano (falla cerrada: el día sigue sin marcar y el ciclo siguiente reintenta).
2. Eliminar la llamada del final (`index.ts:4624`): con la llamada inicial, la final
   es redundante.
3. Añadir log en el camino `!ok` de `maybeDispatchDaily` (hoy `if (!ok) return;` en
   silencio): `logWorkerEvent(…, "github_dispatch", "Dispatch diario devolvió
   ok=false …")` dentro de try/catch (si el log falla, no tumba nada).
4. El reintento es automático por el diseño existente: con crons `*/5`, hay 12 ciclos
   en la hora 9; un fallo no marca el día y el ciclo siguiente (5 min después)
   reintenta. Sin cron nuevo, sin marca nueva.
5. Test: `maybeDispatchDaily` con día no marcado y fallo de `dispatchCerebro` → día
   sigue sin marcar (reintento posible) + fila de log `!ok`; con éxito → día marcado
   (sin segundo dispatch); llamada al principio del handler dentro del bucle por
   tenant en un test de `scheduled` (con Worker parado no se dispara).
6. La verificación del arreglo es el dispatch exitoso del día siguiente en
   `worker_events`, no re-litigar la cadena «Too many subrequests» (que viene de los
   logs del workflow/Worker, no de D1).

### §8.3 Fuera de esta spec

- Watcher en vivo de YouTube para Speed (`clipagent/live_scan.py` solo mira
  Twitch/Kick). Queda como trabajo aparte; el VOD es el respaldo de Speed.

## §9. Secuenciación y regla de commit

- **Fase 1 (primero):** guardas de caja (§4.1) + puerta del validador (§5) + test de
  regresión (§5.3) + formato sin_webcam (§6, sin el cambio del Worker) + invariante
  (§4.5) + payload `plantilla` en los caminos live (`live_cutter.py`) y viral
  (`viral_source.py`). Solo Python/config + los 5 call sites de §4.2. **No toca
  ningún archivo del Worker.** Los campos `plantilla`/`cam_box*` se mandan en el JSON
  `viral` y el Worker los descarta hasta la Fase 3 (hueco declarado en §4.1.5).
  Archivos previstos: `clipagent/render.py`, `clipagent/final_review.py`,
  `clipagent/rubius.py` (parametrizar `_game_filter`), `clipagent/live_cutter.py`,
  `clipagent/workers.py`, `clipagent/remake_all.py`, `clipagent/config.py`,
  `clipagent/quality_gate.py`, `clipagent/yunet_facecam.py` (regla reconciliada),
  `clipagent/viral_source.py`, `clipagent/youtube_cut.py`,
  `clipagent/facebook_cut.py`, `auto_clips.py` (raíz),
  `config.yaml` (receta + limpieza de duplicados), `tests/fixtures/duplicacion/*`,
  tests nuevos. (La lista exacta la fija la implementación; lo que NO se toca en
  Fase 1: nada de `panel-backend/`.)
- **Fase 2:** cajas por influencer (§7). Migración D1 + endpoints del panel + lectura
  en el cerebro. Después de la Fase 1.
- **Fase 3 (desbloqueada):** cambio del Worker B7 (`ViralData` + `parseViralInput` +
  `mergeViralJson`, con tests) + camino VOD del payload (`vod_fallback.py`) + píldora
  solo-lectura del panel (§6, frontend; Worker solo si el endpoint de lista no
  devuelve ya `viral`) + VOD EN (§8). **La opción A ya aterrizó** (commit 26267d5,
  Workers avc fe1b2268 / avc-kaez 4324312a, `/api/health` ok en ambos, schedules
  `['*/5 * * * *']` verificados por API en ambas cuentas): no hay que esperar a nada
  para tocar `panel-backend/worker/src/index.ts`.
- **Regla de commit (usuario, 2026-11-26 20:02):** todo cambio terminado y verificado
  se commitea y se sube a main (commit + push), no queda solo en memoria. Antes de
  commitear, separar lo terminado y verificado de lo que otro agente sigue editando
  en el mismo árbol: **commitear solo los archivos de esta spec** (`git add` por
  ruta, nunca `git add -A`). **Estado del árbol a 2026-11-26 ~23:55 (M-3):** además
  de esta spec + sus fixtures, hay sin commitear `?? docs/designs/SPEC-piso-3-por-dia-2026-11-26.md`,
  `?? docs/designs/SPEC-piso-3-por-dia-v2-2026-11-26.md`,
  `?? docs/designs/SPEC-arreglo-5-tests-ci-2026-11-26.md` y
  `M test_fanout.py` (de otros agentes) — re-verificar `git status` antes del commit.
  HEAD a 23:55: e0964e9 (3 commits de arreglo de CI encima de 26267d5).
- **Verificación tras cada deploy:** `/api/health` en ambos Workers (TENANT
  obligatorio; crons intactos — verificar schedules por API en ambas cuentas, no
  solo en el toml; nunca desplegar con config nocron sin reponer crons — PUT array
  JSON al endpoint schedules). Kaez SIEMPRE con `wrangler.kaez.toml` SIN [triggers]
  (preserva crons); el deploy de kaez usa su propia cuenta
  (30953ea4f41053a53494bc50b2a9d4a4, conector custom.cloudflare-kaez). El deploy del
  Worker (Fase 3) se hace fuera de la ventana de publicaciones programadas de la
  noche (22:14-23:01 Chile) o verificando que ningún job en vuelo se ve afectado.

## §10. Tests y aceptación

- **Tests nuevos (Fase 1):** guardas de caja (caja de fotograma completo rechazada;
  cara del juego centrada rechazada; caja PiP válida aceptada; `DEFAULT_CAM_BOX` en
  no-p3220 rechazada sin detección; `facecam_box` explícito inválido ignorado con
  aviso; cajas reales p3220/p7775/IlloJuan pasan la regla reconciliada — Burn se
  prueba corriendo el detector sobre segmentos reales de Burn, sin coordenadas
  hardcodeadas; p7775 como segunda estrategia); invariante §4.5 (recuadro ≥45% de
  ancho o ≥35% de área → sin webcam o retención); decisión §4.3 (caja presente →
  rubius; `hits==1` sin caja → ambiguo → rubius; avatar share 0,34 (clip 12) →
  rubius; avatar share 0,08 (244) → sin_webcam; clip 12 sigue rubius); modo forzado
  `game` (los 5 call sites llegan a `decide_layout` con modo `game`; decisión con
  `plantilla=rubius_sin_webcam`; `decision["mode"]=="game"` discrimina del `game`
  por `game_voice`; sin seguimiento de cara; zoom con `game_h` 1920 en pantalla
  completa y 1080 en rubius); puerta §5 (las 22 etiquetas de §5.3; SIFTct02 ≥20
  FALLA 191/199/214/244/247/197 (t=1,3,5) y PASA 183/184/186/189/190/246/248;
  **186 PASAR fijado aquí**; un rubius duplicado mal etiquetado como sin_webcam
  FALLA `gameplay_puro`; captura 3 como limitación documentada con su valor 17);
  sin_webcam (rama `template_gate` nueva por nombre antes de `subs_style`;
  `quality_gate` salta la cara solo para esta plantilla; rubius normal sin cara
  sigue fallando; `resolve_template` acepta el 4º nombre y rechaza un 5º;
  `resolve_split_variant` con nombre explícito desconocido levanta `ConfigError`);
  `config.yaml` sin claves duplicadas de nivel superior (validator/templates/sfx);
  payload con `plantilla` en el JSON `viral` (live y viral: dos tests — el VOD va a
  Fase 3); `record["plantilla"]` se actualiza desde `review2["plantilla"]` tras el
  re-render; `_criterion` conserva el motivo cuando pasa (verificado: ningún test
  existente afirma `motivo == ""`).
- **Tests nuevos (Fase 3):** Worker (`parseViralInput` acepta `plantilla` conocida y
  rechaza desconocida; `mergeViralJson` la conserva/reemplaza; round-trip POST →
  GET); payload VOD con `plantilla`; VOD (§8.1: reserva EN sobre entradas que pasan
  pre-chequeo, hueco no perdido; §8.2: día no marcado tras fallo + log `!ok`,
  marcado tras éxito, sin dispatch con Worker parado).
- **Suites completas en verde vs baselines RE-MEDIDAS al implementar** (las citadas
  en el borrador — Python CI 2011 pass / 0 fail; Worker 1023 pass / 13 fallos
  pre-existentes — son afirmación del borrador, no verificadas por ninguna revisión:
  hay otros agentes commiteando a la vez; la implementación re-comprueba la baseline
  antes de afirmar verde). `tsc` limpio; Ruff limpio.
- **Aceptación en vivo (verificación final del trabajo):** (1) los 10 EN re-hechos por
  c02d444d pasan la puerta nueva (o se reporta cuáles no y por qué); (2) un VOD EN
  entra por el hueco reservado (§8.1); (3) el dispatch diario del día siguiente
  aparece exitoso en `worker_events` (§8.2); (4) el clip 247 re-hecho pasa la puerta
  y solo entonces se reprograma su publicación. Sin inventar nada: ids, veredictos y
  links en el reporte.
- **Criterio de «correcto»:** ningún clip con layout duplicado se encola ni se
  publica; un render futuro que duplique es rechazado por el validador antes de
  encolar, sin depender de que nadie lo mire.

## §11. Preguntas abiertas para el usuario + trabajo relacionado pendiente

1. **Banner en sin_webcam:** ¿se queda en y 811-1099 (marca, igual que rubius, tapa
   gameplay 3 s) o se sube arriba del todo (y ~100-388)? La spec propone quedarlo
   donde está. Si lo quiere arriba, cambia §6 (coordenadas del banner en la receta y
   en la rama del validador). **No bloquea la implementación del resto.**
2. **Clip 247:** ¿se re-hace con el arreglo y se reprograma, o se descarta? (Sin job
   vivo desde ~23:20; la spec no decide por él.)
3. **Medición de la tasa real sin-facecam:** dry-run con muestra etiquetada por humano
   (30-50 segmentos) + matriz de confusión. Si sale <10%, el valor del formato va a
   la aprobación con el coste escrito (no pre-decidido).
4. **PISO 3 POR DÍA (orden suya 2026-11-26 20:23, fuera de esta spec):** cada
   plataforma (YT/TT/IG/FB) publica como MÍNIMO 3 vídeos al día en cada cuenta (seba
   y kaez); excepción real: cuentas en calentamiento con tope de etapa 2/día. Esta
   noche solo 4 de 8 canales llegan a 3 (seba YT, seba TT, kaez YT, kaez TT); los 4
   de Meta NO llegan por un bug (la fila Meta resuelve su plan a resto_plan=2 en vez
   de 3) — corrección 20:37 en MEMORY.md. Tiene su propia spec
   (`docs/designs/SPEC-piso-3-por-dia-v2-2026-11-26.md`), aprobada por el usuario
   2026-11-26 20:46 («Sí, impleméntalo así»), en implementación por otro agente.
   Esta spec no lo implementa ni lo diseña.

## Referencias verificadas (código y datos, 2026-11-26)

- `clipagent/rubius.py:93` (`GAME_Y, GAME_H = 840, 1080`), `:95` (`GAME_CROP =
  "1440:1440:560:0"`), `:443-467` (`_game_filter`, `y_coef = GAME_H × 0,15`),
  `:471-528` (`build_filter`), `:611/699/948` (defaults `cam_box=DEFAULT_CAM_BOX`;
  CLI `:948-953`: «usando los de p3220»).
- `clipagent/rdjavi.py:114` (`DEFAULT_CAM_BOX = "561:365:0:0"`).
- `clipagent/remake_all.py:262-267` (`box_strategies`), `:379`
  (`cam_box=cam_box or DEFAULT_CAM_BOX`), `:481/:752/:821` (hardcodean
  `plantilla="rubius"`).
- `clipagent/render.py:187` (`DEFAULTS`, `mode: auto`), `:1923` (`_cam_box_valida`:
  ancho ≤0,45 Y área ≤0,35), `:1957` (`_split_params`), `:1996` (ancho en
  `_split_params`), `:200` (`split_cam_max_w_frac: 0.45` en DEFAULTS), `:2320`
  (`decide_layout`), `:2340` (forzados `("stack","pip","center")`), `:2371`
  (caja explícita + `_cam_box_valida`), `:2448` (pip→split), `:656`
  (`_detect_corner_box`), `:684` (ancho 0,15-0,42), `:687` (alto ≤0,45), `:973`
  (`_detect_vtuber_candidates`), `:1938` (`_scan_frames` def; llamada `:1963`),
  `:3083` (rama `game`), `:3384` (`detect_vod_facecam`: voto → bordes → YuNet;
  rescates `_edge_rescue` → `_yunet_rescue`, éxito → `hits: 1` CON caja), `:3471`
  (`cut_clip_with_layout`), `:3494-3496` (pisado `cfg["mode"] = layout_mode`),
  `:377` (`resolve_split_variant`).
- `clipagent/yunet_facecam.py:365` (`validate_cam_box`), `:395` (`find_cam`, cara
  estable ≥50% de 12 fotogramas), `:507-512` (aplica `validate_cam_box`).
- `clipagent/final_review.py:120` (`RECIPE_TEXT`, 3 entradas), `:780`
  (`extract_gate_frames`: t=1,0, paso `every_s`), `:842` (`_band_video`), `:859-893`
  (`face_gate`), `:1064-1065` (eslabón YuNet), `:1169-1182` (`template_gate` por
  `subs_style`), `:1444` (`_template_word_white`, `cam = recipe["cam"]`), `:1463`
  (`_check_full_width`), `:1597` (`_check_cam_live`), `:1667`
  (`find_facecam_box_in_source`: `detect_vod_facecam` → YuNet → bordes), `:1740-1742`
  (caída a texto rubius), `:1862` (docstring «TRES recetas»), `:1927` (resuelve
  plantilla), `:1981` (llama a `template_gate`), `:1978` (`face_gate` sin
  condición), `:1996-2009` (`hard_failures`: solo `cara`/`plantilla`),
  `:2043-2049` (`needs_rerender = True` si `not hard_pasa`), `:413`
  (`_GATE_MAX_FRAMES = 24`, constante).
- `clipagent/live_cutter.py:874` (`_rerender_after_gate`), `:916` (corte re-render,
  `layout_mode="auto"`), `:959` (re-validación con `plantilla` original), `:1096`
  (corte live, `layout_mode="auto"`), `:1104` (`plantilla = layout.get("plantilla")
  or …`), `:1322` (`"plantilla": plantilla` en la construcción del record), `:1422`
  (llamada si `needs_rerender`, con `plantilla=plantilla`), `:1454`
  (`build_quality_gate(final_review, ...)` sin `plantilla`), `:1647` (construcción
  de `_viral`).
- `clipagent/workers.py:406` (`_do_cut`, pasa `layout_mode`), `:422` (`cut_clip`).
- `clipagent/quality_gate.py:112-114` (`_criterion`), `:117`
  (`build_quality_gate`), `:167-179` (rama `webcam`).
- `clipagent/config.py:378` y `:664` (`TEMPLATE_REQUIRED_KEYS`, duplicado; efectiva
  `:664`), `:681` (`TEMPLATE_BY_VARIANT`), `:953` (`resolve_template`), `:551`
  (`load_config`, `yaml.safe_load`).
- `config.yaml`: `sfx:` 366/379; `layout:` 384, `mode: "auto"` :385; `facecam_box:
  null` :450; `validator:` 553/640; `templates:` 604/717 (efectivos: 640 y 717);
  `rubius_subs_outline_frac: 0.03` en 665 (solo en el segundo `validator:`);
  `face_sample_every_s: 2` en 554 y 641; `signals:` 747 (en el segundo bloque
  `templates:`); receta rubius efectiva en 717+ (cam 0,0,1080,840; game 840–1920;
  banner 103–967/811–1099; subs 900–1020).
- `clipagent/vod_fallback.py:193` (`fallback_order`), `:658`
  (`_rerender_vod_clip`), `:724-734` (`cut_clip_with_layout` con `layout_cfg`),
  `:732` (re-render VOD, `layout_mode="auto"`), `:754-759` (plantilla a validación),
  `:897` (`clip.get("plantilla")`), `:938-951` (`viral_payload`), `:1023-1035`
  (PATCH), `:1463-1465,1507` (cuota en `run()`).
- `clipagent/viral_source.py:687` (hardcodeo `plantilla="rubius"`), `:740-772`
  (construcción del payload).
- `auto_clips.py` (raíz): `:705` (`--layout` choices `["auto","stack","pip","center"]`,
  default None), `:1578-1581` (corte, `layout_mode=args.layout`).
- `clipagent/youtube_cut.py:555-557` (`_default_render`, `layout_mode="auto"`),
  `:560-566` (`_default_validate`); `clipagent/facebook_cut.py:516-518`
  (`_default_render`), `:520-526` (`_default_validate`).
- `panel-backend/worker/src/db.ts:212` (`ViralData`, sin `plantilla`), `:352`
  (`parseViralInput`, whitelist sin `plantilla`), `:497` (`mergeViralJson`), `:1949`
  (INSERT INTO clips).
- `panel-backend/worker/src/index.ts:1240` (PATCH parse), `:1242` (PATCH merge),
  `:1412` (POST parse), `:1491` (POST merge), `:1779` (`maybeDispatchDaily`),
  `:4595-4601` (comentario Worker parado → puerta vacía), `:4624` (llamada final
  `r.ran || r.gate.dailyDue`).
- `panel-backend/worker/src/cycle-gate.ts:211` (`isDailyDispatchDue`), `:293-304`
  (`cycleGate`, devuelve `{run, …, dailyDue, …}`); `panel-backend/worker/src/jobs.ts:1974`
  (`runWorkerCycle`: parado → `{ran: false, gate: idleCycleGate()}`).
- `panel-backend/worker/src/quality-gate.ts:152` (`qualityGateForClip`); `gate.ts:230`
  (llamada real).
- `panel-backend/wrangler.toml:37` (`crons = ["*/5 * * * *"]`); schedules verificados
  por API 2026-11-26 ~23:20: seba (cuenta 27afc37225a02fd92557f901c05dabee, script
  `avc`) `[{"cron": "*/5 * * * *"}]`; kaez (cuenta 30953ea4f41053a53494bc50b2a9d4a4,
  script `avc-kaez`) `[{"cron": "*/5 * * * *"}]`. `/api/health` ok en ambos (tenant
  sebastian/kaez, `worker_running: true`).
- Opción A aterrizada: commit 26267d5 («quitar franjas, publicar aleatorio 1:30–3 h,
  crons 24/7»), Workers avc fe1b2268 / avc-kaez 4324312a. HEAD a 23:55: e0964e9.
- D1 `clips-avc` (tenant sebastian): `SELECT id, kind, clip_id, platform, state,
  next_run_at, deleted_at FROM jobs WHERE clip_id=247` → 0 filas (2026-11-26 ~23:20);
  job 161 (248 → instagram_reels, `scheduled`, 2026-11-27 00:00). Los 10 EN siguen en
  `en_carpeta` (~23:50). `SELECT id, kind, message, datetime(at/1000,'unixepoch','-3
  hours') FROM worker_events WHERE at BETWEEN <2026-11-26 12:00 UTC> AND <13:30 UTC>`
  → sin filas `github_dispatch` de panel-daily (ni éxito ni fallo); últimas filas:
  auto_publish clip #57 09:01. Clip 12: `video_r2_key`
  `clips/sebastian/12/67109e8d6d650e39_clip_12.mp4`, `en_carpeta`, influencer 15.
- `runs/formato-rubius-sin-webcam-2026-11-26/SPEC.md` (REVISADA 2ª ronda, 454 líneas)
  + `REVISION-GOLDFISH-2.md` (NO APTA: B7–B11, I1–I4, M1–M5) +
  `REVISION-RESOLUCION.md` (mapeo de las dos rondas; esta spec corrige sus 4 citas
  Worker erróneas con grep).
- `~/workspace/gta6-clips/runs/review-en-2026-11-26/clip_{183,184,186,189,190,191,197,199,214,244}.mp4`,
  `runs/review-es-2026-11-26/clip_{246,247,248}.mp4`, `/tmp/en_diag/clip_12.mp4`;
  fotogramas en `tests/fixtures/duplicacion/` (22 imágenes + README.md) y
  `/tmp/en_diag/` (`{clip}_t{1,3,5,7,15,25}.jpg`, `calib52.py`, `calib_grid.txt`,
  `siftct02_full.txt`).
- Capturas del usuario: `~/workspace/user/files/779DA4C6-…-B10FF8C1…jpg`,
  `~/workspace/user/files/90C42C8E-…-D3D0DB27…jpg`,
  `~/workspace/user/files/A9AD4C7F-…-D9D1B64B…jpg`.
- Mediciones propias de calibración (2026-11-26): SIFTct02 (nfeatures=2000,
  contrastThreshold=0,02) CORRECTO máx 18 / DUP por clip mín 29, umbral 20; ORB
  (franja excluida, thr 7) CORRECTO 0/29 FP en t=2,7,15,25 pero **9 en es248_t1**
  (rejilla real) — falso positivo; celdas NCC 0 en todo; NCC/MAD/Haar/histogramas/
  bordes sin separación. Avatar: clip 12 píxeles 0,34 / bbox 0,40, centro
  (0,52, 0,42); 244 píxeles 0,08-0,10 / bbox 0,10, centro (0,47, 0,63);
  `avatar_share_min = 0,20`.
