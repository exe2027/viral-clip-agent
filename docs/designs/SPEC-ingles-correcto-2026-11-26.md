# SPEC — Inglés correcto de punta a punta (v4)

**Estado:** v4 — reescritura tras la 3ª revisión goldfish (NO APROBADA, B-N1–B-N5,
I-1–I-12, M-1–M-6) + mediciones nuevas sobre el `layout_gate` que ya existe en el
árbol. **Sin implementar todavía: esta v4 necesita una 4ª revisión goldfish (EGM,
solo del delta v3→v4) antes de implementar.** Fecha: 2026-11-27 ~01:30 (Chile).

**Repo:** `~/workspace/gta6-clips/viral-clip-agent/`. **No borres clips. No cortes a mano.**
**No publiques nada a mano desde este trabajo** (la publicación desde el chat la lleva
otro agente; la salida de este trabajo son clips válidos en cola + VOD fluyendo).
**El repo es PÚBLICO: nunca van clips, fotogramas ni capturas del usuario a git**
(las imágenes viven en `~/workspace/gta6-clips/runs/layout-gate-calib/` y en
`runs/layout-fix-2026-11-26/frames/`, fuera del repo; los tests que las usan se
saltan si no están en el workspace).

## v3 → v4 (qué cambió y por qué)

La v3 recibió **NO APROBADA** en su 3ª revisión goldfish (agente da11e70f, 2026-11-27
00:15 UTC). La revisión **confirma la calibración v3** (la refutación ORB se sostiene:
es248_t1 ORB 9 y 183_t1 ORB 8 en fotogramas correctos; SIFTct02 separa a nivel clip;
197 se caza en la rejilla real) y bloquea por **cableado, contradicción con el árbol
y diseño**, no por calibración:

- **B-N1 — `layout_gate` YA EXISTE en el árbol** (sin commitear, no en HEAD) y la v3
  lo ignora. Decisión v4: **ALINEARSE con él, no reemplazarlo** (§5). El gate
  existente combina SIFT-RANSAC (contado en **celdas distintas**, no inliers solos)
  + geometría de la cara en 4 reglas, con fallo duro `"layout"` ya cableado en
  `validate_final_clip`. La v3 shippeaba SIFTct02 (conteo de buenos matches, umbral
  20): queda **retirado como método** (sus números quedan en §1/Referencias como
  historial de medición). La estadística de celdas es estrictamente mejor: arregla
  el colapso de inliers (170: 15 inliers en 4 celdas) y caza la captura 3 (37
  inliers en 21 celdas), la limitación documentada de la v3.
- **B-N2 — `_do_cut` descarta `facecam_box` top-level** (`workers.py:406-419`: solo
  pasa `cfg.get("layout")`). El pre-chequeo de la v3 (`cfg2["facecam_box"]`) nunca
  llegaba a `decide_layout`. Canal real en v4 (§4.2): `_do_cut` usa
  `get_layout_config(cfg)` (`config.py:1007-1011`, fusiona el `facecam_box`
  top-level) + copia profunda por clip. 6º call site no listado: `workers.py:955`.
- **B-N3 — la señal (b) de `cam_pura` no tiene cableado.** Decisión v4 (§5.2):
  **se elimina (b)**: la procedencia de la caja se controla en el render (§4.1),
  no se re-chequea en el validador. `cam_pura` = reglas de cara (ii)+(iii)+(iv) de
  `layout_gate`, con sus motivos propios.
- **B-N4 — `decide_template_for_segment` no puede decidir (facecam_box, hits)** con
  `find_facecam_box_in_source` (devuelve solo `{"box", "method"}`, descarta `hits`).
  Decisión v4 (§4.3): la función llama **directamente a `detect_vod_facecam`**
  (devuelve `hits` + caja en los rescates) y usa `find_facecam_box_in_source` solo
  como segundo escalón.
- **B-N5 — la reserva EN no es implementable como está escrita** (exige conocer el
  pre-chequeo EN antes de listar, pero el tope debe ir antes del listado). Decisión
  v4 (§8.1): **tope perezoso como regla única** (contadores por idioma; el tope de
  un idioma sube a `max_vods` si el otro no tiene entradas sin visitar en `order`),
  con relajación simétrica y `lang` calculado al inicio de la iteración.
- **I-1–I-12, M-1–M-6**: p7775 NO es estrategia de `box_strategies` (es 4ª estrategia
  nueva, solo Rubius); centro de p7775 corregido a (0,11, 0,56) @2560×1440 + tope de
  borde superior `y0 < 0,50` conservado; `validate_cam_box` gana
  `require_containment=False`; **avatar: NO hay primitiva de píxeles** — la varianza
  temporal está refutada por medición (244 es 81% estático, clip 12 solo 15-21%) y
  el `avatar_share` 0,20 de la v3 se **elimina**; el avatar se resuelve por caja del
  candidato + exención de cara en `layout_gate` (§4.3/§5.2); premisa del camino
  `"cara"` corregida (hoy va a revisión humana, no a sin_webcam); equivalente VOD de
  B-3.2; trigger `"layout"` en el rescate; `quality_gate` SÍ se reconstruye tras el
  re-render (matiz); gancho (b) en `:2559` y `:2601` (ambos a `"game"`); llamada
  completa a `_game_filter` (`game_crop` 9:16 + `game_zoom` + `game_h=1920`); umbral
  con hogar en config (`layout_dup_sift_cells`, ya existe); citas con baseline único
  (árbol, con HEAD donde difiere); `isWorkerRunning` con try/catch; test de Burn
  escribible (regla sobre cajas documentadas + detector con fixture explícito);
  gancho del escaneo nombrado (`decide_layout` `:2432-2444`); resto de menores.

**Mediciones nuevas en esta v4 (hechas por mí 2026-11-26/27, scripts en
`runs/layout-gate-calib/` — durable, fuera del repo; `/tmp` se limpió entre turnos):**
- **Calibración final con el gate ACTUAL (celdas) sobre el conjunto completo del
  gate** (t=1,3,5,…,20 fotogramas por clip, 14 clips + 170_t7 + 3 capturas): tabla
  en §5.1/§5.2. **186 PASA** (celdas 6, share 0,85, cx 0,475, área 0,167); **190
  PASA** (celdas 2, share 0,55); **248 PASA** (celdas 5 — las celdas arreglan el
  falso positivo de inliers: 9 inliers en solo 3-5 celdas); **183/184/189 FALLAN**
  por (i) (celdas 35/51/20) — la v3 los pasaba, queda **revocada**; **197 FALLA**
  por (iv) (área 0,106 + 6 celdas ≥5); **214** solo lo caza (i) (96 celdas, cara en
  rango); **244 FALLA** por (i) (10 celdas, sistemático 8-12 en los 20 fotogramas);
  **clip 12 FALLA** por (i) (42 celdas: t1 arena de juego junto al avatar, t37
  póster de esqueleto junto al avatar — caja demasiado ancha, no exención);
  **las 3 capturas FALLAN** por (i) (307/57/21 celdas — la limitación de la v3
  desaparece). Limpios máx 6 celdas; DUP mín 10 celdas (244); 197 se caza por (iv).
- **t7 a fotograma único: 15/15 coinciden con `ESPERADO_T7`** de
  `test_layout_gate.py` (ya existe, 311 líneas, raíz del repo). **186 t7 FALLA**
  (Haar no ve cara en ese único fotograma, share 0/1) pero **186 pasa en conjunto
  completo** — no es contradicción: son conjuntos de fotogramas distintos; la v4 lo
  deja explícito en un solo sitio (§5.3). La regla de corroboración funciona: 170
  pasa (cx 0,42, área 0,080, 4 celdas < 5); 189 y 197 caen por (iii) (cara de
  primer plano desplazada).
- **Recorte de capturas:** mi rectángulo (107,431)-(1182,2363), 1075×1932, aspecto
  0,556 ≈ 9:16; el de `test_layout_gate.py` (`CAPTURA_VIDEO`, filas 66-2376, cols
  95-1195, aspecto 0,476) incluye UI. **Las 3 capturas fallan con ambos recortes**
  (mi recorte: 307/57/21 celdas; recorte del test: 257/43/22). La v4 adopta mi
  recorte como canónico y ordena actualizar `CAPTURA_VIDEO`.
- **Avatar por varianza: refutado.** `static_share` (desviación típica < thr entre
  4 fotogramas, zona de cámara): clip 12 = 0,147-0,214; 244 = 0,808-0,838. El mapa
  del juego es más estático que el avatar animado. No se usa.
- **face_gate NO necesita cambio para avatares (medido):** el `quality_gate` de
  clip 12 en D1 tiene fallos `['hook', 'titulo', 'audio']` — sin fallo de cara.
- **Repo público, imágenes fuera:** el commit 30ba071 metió las 22 imágenes en el
  repo público; se sacaron en c6ff3e1 (movidas a
  `runs/layout-gate-calib/fixtures-duplicacion/`). Siguen en el historial de
  30ba071 — la limpieza del historial (BFG + force-push) queda pendiente de
  decisión (§11). CI en verde 2026-11-26 21:21 (GHA 2200 passed, CircleCI success).

## Alcance (5 piezas, en orden)

1. **Puerta en el validador = `layout_gate` (ya existe en el árbol)** — adoptarlo,
   corregir su calibración a la rejilla del gate, añadir el parámetro `avatar` y
   resolver 186 en su marco. La puerta caza la duplicación (SIFT en celdas) y exige
   que la zona de cámara sea mayoritariamente cámara (geometría de la cara), antes
   de encolar.
2. **Guardas de caja** en el corte (regla reconciliada única + `validate_cam_box` /
   `find_cam` / `_cam_box_valida`), para que no se vuelva a renderizar un duplicado.
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

## Acción inmediata (estado a 2026-11-27 ~01:30, verificado en D1)

- **247: sin job vivo.** `SELECT … FROM jobs WHERE clip_id=247` → 0 filas (verificado
  2026-11-26 ~23:20). El job 165 (247 → YouTube, 2026-11-27 00:30) fue eliminado. No se
  reprograma 247 hasta que su re-make pase la puerta nueva. Job 161 (248 → Instagram,
  2026-11-27 00:00) sigue `scheduled`.
- **Los 10 EN re-hechos aún no están en D1** (verificado 2026-11-26 ~23:50: los 10
  siguen en `en_carpeta`, `updated_at` 23–26 nov). El re-render del otro chat
  (c02d444d) no ha aterrizado; la verificación en vivo de §10.4 queda pendiente.
- **190 publicado y verificado LIMPIO en el Chrome del usuario** (YouTube Shorts
  https://www.youtube.com/shorts/FFw5ngKR0dc, job 164, 20:05; verificado por otro
  agente en solo lectura: cámara grande arriba, juego abajo, sin duplicado).
- **Opción A aterrizada** (commit 26267d5): Workers avc fe1b2268 / avc-kaez 4324312a,
  `/api/health` ok en ambos, schedules `['*/5 * * * *']` verificados por API en ambas
  cuentas (seba 27afc37225a02fd92557f901c05dabee, kaez 30953ea4f41053a53494bc50b2a9d4a4).
- **CI en verde** 2026-11-26 21:21 (GHA run 45914822949: 2200 passed; CircleCI success).
  HEAD c6ff3e1 (incluye 30ba071 spec v3 + c6ff3e1 sacar imágenes del repo público).

## §1. Evidencia: la duplicación es real, por fotograma

### §1.1 Cómo se midió (2026-11-26 ~20:00-20:20, ampliado 23:50-01:30)

MP4 finales descargados de R2 (API v4 Cloudflare + surrogate del vault; nunca por
`wrangler r2`, que ve otro bucket). Fotogramas extraídos con ffmpeg a la rejilla
real del gate (`extract_gate_frames`, `final_review.py:780`: t=1,3,5,7,… paso 2 s,
hasta 24 fotogramas) y a t=7 para la regresión a fotograma único. El método de la
puerta es el del `layout_gate` existente: SIFT nfeatures=2000 + Lowe 0,75 +
homografía RANSAC (5,0), contado en **celdas distintas de destino** (rejilla de
20 px en la zona de juego) + cara Haar mayor por fotograma (centro-x mediano, área
mediana, share). Zonas de la receta rubius efectiva (`config.yaml`, segundo bloque
`templates:`): cámara x 0-1080, y 0-840; juego y 840-1920. cv2 4.11.0 (SIFT en
opencv-python ≥4,4, sin dependencia nueva). Coste medido ~2-3 s por fotograma SIFT
+ Haar rápido; el gate evalúa SIFT sobre ≤8 fotogramas repartidos → ~20-30 s por
clip (acotado por `layout_sift_max_frames: 8`).

**Historia de la medición (para que nadie la repita):** la v1/v2 calibraron con
MAD/NCC/template/Haar (ninguno separa), luego celdas NCC (0 coincidencias en
todo: no separa), luego ORB (falso positivo en es248_t1 correcto, ORB 9 ≥ 7 —
refuta «ORB primario»), luego SIFTct02 (conteo de buenos matches, umbral 20:
CORRECTO máx 18, DUP mín por clip 29 — separa, pero el conteo colapsa: 170 da 15
inliers en 4 celdas por el texto del encabezado de Twitch). El `layout_gate`
existente resuelve el colapso contando **celdas distintas**: 170 pasa con 4 celdas.
La captura 3, limitación documentada de la v3 con SIFTct02 (17 < 20), se caza con
celdas (21). SIFTct02 queda como historial, no como método.

### §1.2 Tabla por clip (conjunto completo del gate, n=20 fotogramas, gate actual)

`cells` = máx. de celdas distintas SIFT sobre ≤8 fotogramas repartidos
(`layout_sift_max_frames: 8`); `share` = fotogramas con cara Haar / total;
`cx`/`area` = medianas de la cara mayor. Umbrales: celdas 8; share 0,5; cx
[0,45, 0,65]; área 0,15 (primer plano 0,12); corroboración 5 celdas.

| Clip | Streamer | Veredicto (puerta) | cells | share | cx | área | Regla que lo caza |
|---|---|---|---|---|---|---|---|
| 183 EN | Burn | RECHAZAR (sangrado: caja demasiado ancha) | 35 | 1,00 | 0,445 | 0,153 | (i) duplicado |
| 184 EN | Burn | RECHAZAR (sangrado) | 51 | 0,85 | 0,435 | 0,144 | (i) duplicado |
| 186 EN | Burn | **PASAR** (sangrado leve tolerado) | 6 | 0,85 | 0,475 | 0,167 | — |
| 189 EN | Burn | RECHAZAR (sangrado) | 20 | 0,95 | 0,448 | 0,139 | (i) duplicado |
| 190 EN | Burn | **PASAR** (correcto; publicado y verificado limpio) | 2 | 0,55 | 0,619 | 0,234 | — |
| 191 EN | Burn | RECHAZAR (duplicado) | 231 | 1,00 | 0,118 | 0,044 | (i) duplicado |
| 197 EN | Burn | RECHAZAR (duplicado t=1..5; correcto después) | 6 | 0,55 | 0,45 | 0,106 | (iv) cara + corroboración |
| 199 EN | Burn | RECHAZAR (duplicado) | 318 | 1,00 | 0,21 | 0,038 | (i) duplicado |
| 214 EN | TheBurntPeanut | RECHAZAR (cara del juego en banda de cámara) | 96 | 1,00 | 0,522 | 0,116 | (i) duplicado (la cara pasa) |
| 244 EN | TheBurntPeanut | RECHAZAR (avatar sobre mapa) | 10 | 0,05 | 0,412 | 0,036 | (i) duplicado |
| 12 EN | TheBurntPeanut | RECHAZAR (avatar, caja demasiado ancha) | 42 | 0,15 | 0,537 | 0,014 | (i) duplicado |
| 246 ES | IlloJuan | **PASAR** (correcto) | 5 | 1,00 | 0,514 | 0,184 | — |
| 247 ES | Rubius | RECHAZAR (duplicado) | 151 | 0,75 | 0,184 | 0,019 | (i) duplicado |
| 248 ES | IlloJuan | **PASAR** (correcto) | 5 | 1,00 | 0,503 | 0,167 | — |
| 170 ES | AuronPlay | **PASAR** (webcam ancha legítima; solo t7 medido) | 4 | 1,00 | 0,421 | 0,080 | — (iv) no dispara: 4 < 5 |
| Captura 1 | (usuario) | RECHAZAR (duplicado: caja = fotograma completo) | 307 | 1,00 | 0,219 | 0,035 | (i) duplicado |
| Captura 2 | (usuario) | RECHAZAR (duplicado: cara del juego) | 57 | 1,00 | 0,664 | 0,212 | (i) duplicado |
| Captura 3 | (usuario) | RECHAZAR (duplicado) | 21 | 1,00 | 0,659 | 0,370 | (i) duplicado |

Lecturas que importan:
- **186 PASA en ambos marcos** (v3 y `layout_gate` actual, medido en la rejilla del
  gate): celdas 6 < 8, share 0,85, cx 0,475 en rango, área 0,167 ≥ 0,15. Márgenes
  finos (celdas 6 vs 8; cx 0,475 vs 0,45; área 0,167 vs 0,15): cualquier
  recalibración debe re-verificar 186. El docstring de `layout_gate` («186 lo caza
  la cara», cx 0,41-0,44) es un artefacto de su rejilla t2/t7/t20; la v4 ordena
  re-anclar el docstring a la rejilla del gate (§5.5). **A t7 a fotograma único
  186 FALLA** (Haar no ve cara en ese único fotograma, share 0/1) — ver §5.3: son
  conjuntos de fotogramas distintos, no una contradicción.
- **183/184/189: la v3 los pasaba; la v4 los rechaza.** El sangrado del borde
  derecho (franja de juego ~25% de la zona de cámara) NO es tolerable: la zona
  deja de ser mayoritariamente cámara en la medida SIFT (35/51/20 celdas). Es el
  mismo defecto de caja demasiado ancha que §4.1 corrige con snap; el gate
  rechaza el render y el re-render con la caja ajustada es el que debe pasar.
  183/184/189 también fallarían (iv) (cx 0,435-0,448, área 0,139-0,153) si (i) no
  disparase primero.
- **248 PASA gracias a las celdas:** 9 inliers en solo 5 celdas (t17: 9 inliers en
  3 celdas — matches colapsados). Con el conteo de inliers (umbral 8) 248 era un
  falso positivo; con celdas (umbral 8) pasa con margen 3.
- **197 se caza por (iv), no por (i):** celdas 6 < 8, pero cara anómala (área 0,106
  < 0,15; cx 0,45 en el borde) + corroboración 6 ≥ 5. Su segmento duplicado dura
  t=1..5 (SIFTct02 21/29/26 en t=1,3,5); t=7/15/25 son correctos. Por eso la
  puerta NO salta los fotogramas de la ventana del banner: se evalúan.
- **214 es el caso que solo caza (i):** su cara (del juego) está centrada (cx
  0,522) y su área 0,116… pero (i) da 96 celdas. Sin (i), 214 pasaría (iv) solo si
  las celdas fueran ≥5 — lo son (96) — en realidad 214 fallaría (iv) también
  (área < 0,15 + 96 ≥ 5). La regla (i) es la que lo caza primero y con más margen.
- **Clip 12: el render actual FALLA (i) con 42 celdas — no necesita exención,
  necesita re-render con la caja ajustada.** En t1 la zona de cámara muestra
  arena de juego junto al avatar (90 inliers); en t37 un póster de esqueleto
  junto al avatar (71 inliers); en t7 el avatar está centrado sobre fondo oscuro
  (10 inliers, 3 celdas). La caja es demasiado ancha: el avatar se mueve dentro
  del fotograma fuente y la caja fija incluye juego a los lados. El arreglo es
  la caja del candidato vtuber (§4.3), no una exención del gate. 244 (10 celdas,
  sistemático 8-12 en los 20 fotogramas) tiene el mismo arreglo. El `avatar_share`
  0,20 de la v3 se **elimina**: no hay primitiva reproducible (varianza refutada)
  y no hace falta (la caja del candidato + el gate SIFT lo sustituyen).
- **Márgenes del gate (honestos):** limpios máx 6 celdas (186); DUP mín 10 celdas
  (244); umbral 8 → margen 2 a cada lado. Corroboración (iv): 197 con 6 vs 5
  (margen 1). Share de 190: 0,55 vs 0,5 (margen 0,05). Son márgenes finos pero
  reales sobre el corpus medido; §10 fija los tests que los protegen.

### §1.3 Corrección a la afirmación anterior (queda registrada)

Mi afirmación del 2026-11-26 19:42 («la cara no falla en los 10 EN») estaba mal:
miré las etiquetas del veredicto en D1, no los fotogramas. Las 3 capturas del
usuario lo demuestran (gameplay duplicado arriba y abajo; la cámara nunca ocupa
la zona superior como pide la receta rubius). Esta spec existe por esa corrección.

## §2. Causa raíz del duplicado (diagnóstico del padre, verificado por mí en código)

En `rubius.build_filter` (`clipagent/rubius.py:471-528`) el fotograma fuente se parte
en dos: arriba `crop={cover}` de `cam_box` escalado a 1080x840, abajo
`crop={game_crop}` (`GAME_CROP = "1440:1440:560:0"`, `rubius.py:95`, recorte 1:1
centrado) a 1080x1080 (`GAME_Y, GAME_H = 840, 1080`, `rubius.py:93`). **Arriba se
ve LO QUE CUBRA `cam_box`.** Si la caja es mala, arriba es gameplay y abajo también
es gameplay. Dos mecanismos verificados:

- (a) El detector se engancha a una **cara del juego** (Haar/YuNet/estabilidad en
  `decide_layout` `clipagent/render.py:2320` y `find_facecam_box_in_source`
  `final_review.py:1667`): en la captura 3 la cara del personaje GTA ocupa casi
  todo el cuadro; en la 2 la cara del personaje ocupa la banda de cámara.
- (b) Se usa el `DEFAULT_CAM_BOX = "561:365:0:0"` (`clipagent/rdjavi.py:114`) en un
  streamer cuya cámara es un inset pequeño en otro sitio: la caja cubre
  calle/gameplay y la camarita queda chiquita en una esquina. Esa caja es la de
  **p3220** (arriba a la izquierda, según el propio CLI de rubius.py `:948-953`),
  no «la de Rubius» en general: la caja de Rubius p7775 es `561:365:0:626`
  (abajo a la izquierda).

**El flujo vivo nunca llama a `rubius.py`:** `live_cutter.process_peak` →
`workers.cut_clip` (`workers.py:422`) → `_do_cut` (`:406`) →
`render.cut_clip_with_layout` (`render.py:3471`) → `decide_layout` (`:2320`) →
`segment_filter_graph` (`:2949`). `rubius.py` solo lo usan el driver de CircleCI
(`circle_render.py`) y el re-make (`remake_all.py:448`).

## §3. Por qué el validador no lo cazó

`face_gate` (`final_review.py:859-893`) exige una cara en ≥ `face_min_share` (0,5) de
las muestras, ancho mínimo 0,08×1080 = 86 px. La camarita pequeña cuenta, la cara
del juego cuenta, el avatar cuenta: nunca mide qué fracción de la zona es cámara
ni si arriba ≠ abajo. `template_gate` (`:1169-1182`) despacha solo por `subs_style`
(`fixed_yellow`/`karaoke`/`word_white`), no por nombre de plantilla.

**Desde la v3 el árbol tiene la respuesta parcial:** `layout_gate`
(`final_review.py:1914`) + `_sift_dup` (`:1843`) + `_layout_check_arrays`
(`:2004`) + fallo duro `"layout"` en `validate_final_clip` (`:2334`) + llaves
`layout_*` obligatorias en `load_validator_config` (`config.py:769-773`). La v4
**adopta ese gate** (§5) en vez de construir uno paralelo: la tarea es
re-anclarlo a la rejilla del gate, añadir el parámetro `avatar` y resolver los
puntos que el propio gate deja abiertos (§4/§5.5). **No re-implementar
`layout_gate`**: Fase 1 se construye encima del estado actual del árbol.

## §4. El arreglo (Fase 1: Python/config + 5 call sites + `_do_cut`)

### §4.1 Guardas de caja — una regla reconciliada, un solo sitio

Hoy existen **tres reglas distintas y contradictorias** sobre dónde puede estar la
caja (verificado por grep):
- `validate_cam_box` (`clipagent/yunet_facecam.py:365`): w 0,08-0,6; h 0,08-0,7;
  área <0,45; aspecto 0,5-2,0; **borde superior y > 0,35 → False**; cx debe ser
  <0,4 o >0,6 (no centrado); debe contener la cara completa (desempaquetar cara
  vacía → False).
- `_cam_box_valida` (`render.py:1923`): ancho `bw/w ≤ split_cam_max_w_frac` (0,45,
  `render.py:200`) Y área `(bw*bh)/(w*h) ≤ cam_max_area_frac` (0,35); usada en
  `:1989`, `:2371`, `:2559`, `:2601`.
- `_detect_corner_box` (`render.py:656`): ancho 0,15-0,42 (línea 684), alto ≤0,45
  (línea 687).
- `decide_layout` nunca llama a `validate_cam_box` (el escaneo usa
  `classify_frame`/`_detect_corner_box` y la corrección área <0,45).

**Regla reconciliada (normativa, única):** una caja es válida si y solo si cumple
las 4: (1) **posición:** el centro NO cae en el rectángulo central (x 30-70% Y
y 30-70% a la vez) **Y** centro-y < 0,70; (2) **borde superior:** `y0 < 0,50`
(cota conservada de `validate_cam_box`, relajada de 0,35 a 0,50: la caja de
Rubius p7775 tiene `y0 = 626/1440 = 0,435` — el 0,35 la rechaza, el 0,50 la
acepta; bloquea la zona de minimapa/HUD ≥0,50); (3) **tamaño:** ancho
`bw/W < split_cam_max_w_frac` (0,45) Y área < 0,35 (las dos de `_cam_box_valida`);
(4) **contención** (solo cuando hay cara detectada: ver I-3): la caja contiene la
cara completa.

Las fracciones se calculan con el tamaño real del segmento (base declarada:
2560×1440 para p7775 — su centro es **(0,11, 0,56)**, no (0,11, 0,435): 0,435 es
el borde superior). **Las cajas documentadas:** Rubius p7775 `561:365:0:626`
→ centro (0,11, 0,56) ✓ (pasa: no centrada, y0 0,435 < 0,50, ancho 0,22 < 0,45).
Rubius p3220 `561:365:0:0` → centro (0,11, 0,13) ✓. IlloJuan: caja ~400×408
@2560×1440 (docstring de `_detect_corner_box`), x0 no documentado, centro-x
≈ 0,08-0,13 (aproximado — marcar como tal, no como exacto). **Burn: sin
coordenadas documentadas en ningún sitio** — el test de Burn corre el detector
sobre segmentos fuente reales (§10.3) y exige detección no vacía, no coordenadas
fijadas.

**Cambios de código:**
- `validate_cam_box(box, face, require_containment=True)` (nuevo kwarg, I-3):
  con `require_containment=False` la cara se ignora por completo (la validación
  sin contención no la usa); con `True` (default) la contención se exige cuando
  hay cara. La ruta de caja explícita de config (`facecam_box` en `decide_layout`,
  `:2371`) llama con `require_containment=False`. Test: caja explícita válida sin
  cara detectada pasa; con cara presente pero fuera, la contención no aplica.
- `validate_cam_box` adopta la regla reconciliada (posición + `y0 < 0,50` +
  tamaño) en lugar de su `y ≤ 0,35` y su `cx < 0,4 o > 0,6` (sueltas, la regla
  reconciliada las sustituye; tamaño/aspecto/área se mantienen).
- `_cam_box_valida` (`render.py:1923`): mantiene su chequeo ancho <0,45 + área
  <0,35 y añade la regla de posición + `y0 < 0,50` (la misma función de posición,
  importada de `yunet_facecam` — un solo sitio, no dos copias).
- Gancho del escaneo (I-12): la regla de posición se aplica a la caja **antes de
  crear el pip** en la rama `s["cams"] == 1 and layout == "center" and s["boxes"]`
  de `decide_layout` (`render.py:2432-2444`, «corregido a pip por escaneo»,
  después de la corrección área <0,45) y en la rama vtuber inmediatamente posterior
  (`if layout == "center" and s.get("vtuber")` → pip con `vt["box"]`), con import
  perezoso de `validate_cam_box` (reconciliada). Una caja que no pasa la regla no
  se convierte en pip: el segmento cae a `"game"` (ver §4.1.4).
- Gancho (b) — pip→split y center→split (I-6): en el árbol la guarda está en
  `:2559` (pip→split) y `:2601` (center→split) y **ambas degradan la caja
  inválida a `"center"`, no a `"game"`** (la cita `:2448` de la v3 era de HEAD).
  **Cambiar ambas a `"game"`** y actualizar los docstrings de `_cam_box_valida`
  (`:1923`) y `_split_params` (`:1957`), que hoy dicen «cae a center».

**§4.1.4 Invariante (B6 del borrador, normativa):** caja inválida o ausente →
`sin_webcam` o hold, **NUNCA** el fotograma completo como cámara, **NUNCA**
duplicado. La rama `"game"` (`render.py:3083`, recorte 9:16 centrado a pantalla
completa, sin webcam) es la salida; `"center"` sigue la cara y no sirve como
salida de degradado.

**§4.1.5 `box_strategies` — p7775 es una estrategia NUEVA (I-1):** hoy
(`remake_all.py:271`) son detectada → AJUSTADA (`_tight_box`, `:248`, centrada
en la cara mediana; docstring: corrige la caja demasiado ancha del refinado de
borde en cámaras oscuras — «Burn: la cámara real es ~1,8× la cara, no 2,8×») →
default (p3220). **`561:365:0:626` no aparece como estrategia**: solo como
ejemplo CLI (`rdjavi.py:77`) y en comentarios (`render.py:2806`). **v4 añade la
4ª estrategia: p7775 (`561:365:0:626`), solo para segmentos de Rubius,
después del default p3220** (la función gana un parámetro `influencer`). Orden:
detectada → ajustada → default p3220 → p7775 (Rubius) → (si ninguna pasa las
guardas) sin_webcam por §4.1.4. La guarda filtra las cajas inválidas antes de
renderizar.

**§4.1.6 Trazabilidad (B7 parcial):** la caja usada, su fuente y sus guardas se
registran en el cerebro (`cam_box`, `cam_box_source`, `cam_box_guardas` en el
registro del clip). En Fase 1 viven solo en el cerebro: el Worker las descarta
hasta Fase 3 (§6.4) y **no son diagnosticables desde D1 hasta entonces**; la
puerta del §5 no depende de ellas.

### §4.2 El modo viaja por call site + `_do_cut` (B-N2, B9)

El mecanismo de la v2 (`cfg["layout"]["mode"]`) está **eliminado**: `cut_clip_with_layout`
(`render.py:3471`) hace `if layout_mode is not None: cfg["mode"] = layout_mode`
(`:3494-3496`) y los 5 call sites pasan `layout_mode="auto"` explícito, así que
cualquier valor en config queda pisado. **El modo viaja por call site.**

**Canal de la caja (B-N2 — el punto que la v3 no veía):** `workers._do_cut`
(`clipagent/workers.py:406-419`) hace `layout_cfg = (cfg.get("layout") or {}).copy()`
y pasa **solo** la subsección `layout` a `cut_clip_with_layout`. `config.yaml`
tiene `facecam_box` en el nivel superior (`:450`), fuera de `layout:` — así que
el pre-chequeo de la v3 (`cfg2["facecam_box"]`) y el código que la v3 tomó por
bueno (`_rerender_after_gate`, `live_cutter.py:910-916`, que hace
`cfg2["facecam_box"] = face_box`) **pierden la caja en `_do_cut`**. Solo el VOD
funciona (usa `get_layout_config` + `cut_clip_with_layout` directo,
`vod_fallback.py:724-734`).
**Fix (un solo sitio, beneficia a todos los call sites):** `_do_cut` usa
`get_layout_config(cfg)` (`config.py:1007-1011`, fusiona el `facecam_box`
top-level en la config de layout) en lugar de `cfg.get("layout")`. Y la copia
por clip debe ser **profunda** (`copy.deepcopy`): `dict(self.cfg)` es superficial
y `cfg2["layout"]` es el MISMO dict que `self.cfg["layout"]` — escribir ahí
contamina otros clips.

**Pre-chequeo (obligatorio, con copia profunda por clip):** antes del corte, la
decisión §4.3 corre sobre el segmento y escribe `cfg2["facecam_box"]` (o
`layout_mode="game"` si es sin_webcam) en una **copia profunda** de `self.cfg`.
La rama forzada `"game"` en `decide_layout` añade `"plantilla":
"rubius_sin_webcam"` a la decisión devuelta; el discriminador entre el `game`
forzado y el `game` de `game_voice` es `decision["mode"] == "game"`.

**Call sites (6, no 5):** live `live_cutter.py:1096`; re-render live `:916`;
re-render VOD `vod_fallback.py:734`; corte directo `youtube_cut.py:556`;
`facebook_cut.py:517`; **corte por job/payload `workers.py:955`**
(`layout_mode=payload.get("layout_mode", "auto")`, pasa `cfg=cfg` completo a
`cut_clip` `:422` → `_do_cut`) — **no listado en la v3**. Los 6 pasan el modo
(`"game"` cuando la decisión es sin_webcam, si no `"auto"`). `config.yaml:385`
(`mode: "auto"`) gana `game` en su comentario como valor forzado (no configurable).

### §4.3 Decisión de plantilla por segmento (B10, B-N4, I-4)

**Una sola función de decisión: `decide_template_for_segment(segment_path, duration,
cfg)` en `clipagent/final_review.py`** (la misma que usa el re-render §4.4).
Internamente llama **directamente a `detect_vod_facecam`** (`render.py:3384` /
`:3254` HEAD — **es la que devuelve `hits` y caja**; en los rescates devuelve
`hits: 1` CON caja, `:3290-3310`) y usa `find_facecam_box_in_source`
(`final_review.py:1667`) solo como segundo escalón (o se extiende su retorno con
`hits`/`samples` — una de las dos; no ambas a la vez). La decisión es conjunta
sobre `(facecam_box, hits)` (B10): con `None` no se distingue `hits==0` (→
sin_webcam) de `hits==1` (→ ambiguo → rubius).

Tabla de decisión (normativa):

| Entrada | Decisión |
|---|---|
| `facecam_box` presente (voto ≥2 o rescate) | rubius, con esa caja (§4.1) |
| caja ausente + `hits==1` (sin rescate) | ambiguo → rubius (nunca sin_webcam) |
| caja ausente + `hits==0` + sin avatar | **rubius_sin_webcam** |
| candidato avatar + sin cara humana | rubius, **con la caja del candidato** (ajustada; debe pasar §4.1) |
| caja presente pero inválida (§4.1) | sin_webcam o hold (§4.1.4), nunca duplicado |

**Avatar (I-4, diseño final — sin primitiva de píxeles):** la rama avatar es
«candidato existe + no hay cara humana → rubius con la caja del candidato».
`_detect_vtuber_candidates` (`render.py:973`) devuelve caja/`_cells`/`_states`
(presencia por fotograma), no una proporción de píxeles; el `avatar_share_min =
0,20` de la v3 se **elimina**. La varianza temporal como primitiva está
**refutada por medición** (§1.2): clip 12 estático 15-21%, 244 estático 81% — el
mapa del juego es más estático que el avatar animado. No hay medición de
«proporción de píxeles de avatar» que sea reproducible (la bbox de 244 da 0,42
por contaminación del mapa — la bbox NO sirve). La validación la hace el gate:
con `avatar=True` se saltan las reglas de cara (ii)/(iii)/(iv) — Haar no ve
avatares, son inaplicables por principio (mismo patrón que el «no aplica» de
plantillas no-rubius) — **pero la regla (i) SIFT sigue aplicando** (§5.2). El
clip 12 actual FALLA (i) y se re-renderiza con la caja ajustada del candidato;
244 igual. `face_gate` no cambia (clip 12 ya lo pasa en D1: fallos
`['hook','titulo','audio']`, sin fallo de cara).

**Orden interno (detect_vod_facecam):** voto → bordes → YuNet (rescate); el
externo (`find_facecam_box_in_source`) es voto → YuNet → bordes. La decisión
§4.3 usa el interno como fuente de verdad de `(facecam_box, hits)`.

### §4.4 Re-render tras la puerta (B-3, I-5)

`_rerender_after_gate` (`live_cutter.py:874`, llamado desde `:1422` cuando
`final_review.get("needs_rerender")`):
- Pasa `layout_mode="game"` en `:916` cuando la decisión §4.3 es sin_webcam
  (y la caja validada en `:910-916` via el canal §4.2, no por `cfg2["facecam_box"]`
  directo — ese era el bug B-N2).
- La re-validación (`:959`) usa `plantilla="rubius_sin_webcam"` cuando la decisión
  es sin_webcam.
- **`record["plantilla"]` (puesto una vez al construir el record, `:1322`) y el
  `quality_gate` derivado se actualizan desde `review2["plantilla"]`.** Corrección
  I-5(d): hoy `record["plantilla"]` NO se actualiza tras el re-render (solo
  `record["clip"]`, `package["clip"]`, `record["rerendered"]`), **pero el
  `quality_gate` derivado SÍ se reconstruye** (`live_cutter.py:1454`,
  `record["quality_gate"]` en `:1464`). El bug es `record["plantilla"]`, no el
  quality_gate.
- **Ambos caminos `hard` corren la decisión §4.3 completa** (incluida la
  heurística de avatar) antes de elegir sin_webcam: `"plantilla"` con motivo de
  layout **Y** `"cara"` con `find_facecam_box_in_source` → None. **Y el rescate
  también se dispara con `"layout"` en `hard`** (nuevo fallo duro de `layout_gate`
  — la v3 solo contemplaba `"plantilla"`/`"cara"`).
- Corrección de premisa (I-5a): **hoy el camino `"cara"` + sin cara en la fuente
  NO va directo a sin_webcam — va a REVISIÓN HUMANA** (`live_cutter.py:898-908`).
  La exigencia (correr la decisión §4.3 completa antes de elegir sin_webcam)
  sigue en pie; la premisa de la v3 era falsa.
- **Equivalente VOD (I-5b):** `_rerender_vod_clip` (`vod_fallback.py:658`) debe
  devolver la plantilla nueva desde `review2` y el llamador
  (`vod_fallback.py:917`) debe actualizar `clip["plantilla"]` (hoy conserva la
  del manifiesto, `:897`, y el POST VOD seguiría llevando `rubius` aunque el
  re-render sea `sin_webcam`).

### §4.5 Invariante del render (normativo)

Una sola medida de ancho: `bw / SAMPLE_W < split_cam_max_w_frac` (0,45;
`render.py:200`, aplicado en `_split_params` `:1996`). La corrección del escaneo
(`:2325` en la v2, área <0,45) se unifica a la misma regla de ancho. §4.1 añade
el área <35% de `_cam_box_valida` como **segunda** condición de render (no como
sustituto del ancho). La cita de la v2/v3 que decía «`split_cam_max_w_frac` es
área» era errónea: es ancho.

## §5. La puerta del validador = `layout_gate` (B-N1 — adopción, no reemplazo)

**Decisión v4: la puerta ES `layout_gate`, que ya existe en el árbol**
(`clipagent/final_review.py:1914`, `_sift_dup` `:1843`, `_layout_check_arrays`
`:2004`, `validate_final_clip` `:2160` con `layout_fn` inyectable `:2164`, fallo
duro `"layout"` `:2334`). No se construye un segundo gate. La v4: (a) adopta sus
4 reglas y sus llaves como normativa; (b) corrige su docstring de calibración a
la rejilla del gate; (c) añade el parámetro `avatar`; (d) fija las tablas de
calibración medidas (§5.1-§5.3); (e) resuelve los puntos que el gate deja
abiertos (186, 183/184/189, captura 3, muestreo SIFT).

**SIFTct02 (v3) queda retirado como método.** Sus números (CORRECTO máx 18,
DUP mín por clip 29, umbral 20) quedan en §1.1/Referencias como historial. La
estadística que shippea es **celdas distintas**, no buenos matches.

### §5.1 El método (código existente, normativa)

`_sift_dup(cam_gray, game_gray)` (`:1843`) devuelve `(inliers RANSAC, celdas
distintas de destino)` — celdas en rejilla de 20 px en la zona de juego.
Racional (docstring): los inliers solos engañan cuando muchos matches colapsan
en 1-2 puntos (clip 170: 15 inliers en 4 celdas, texto del encabezado de Twitch
vs barandilla del juego). La duplicación real reparte los inliers por la escena
(252: 62 inliers en 22 celdas; 199: 602 en 284). SIFT nfeatures=2000 (con 800,
252 daba 4-7 y se escapaba; con 2000 da 33-71 y los limpios se quedan ≤4),
Lowe 0,75, RANSAC 5,0. `_sift_dup_inliers` (`:1907`) es un wrapper de
compatibilidad que devuelve solo inliers («los tests sintéticos lo usan
directamente») — no es la estadística de la puerta.

`layout_gate(clip_path, vcfg, recipe, frames=None, out_dir=None,
template_name="rubius")` (`:1914`): aplica solo a rubius (otras plantillas →
`pasa=True`, «no aplica»). Usa `extract_gate_frames` (t=1,3,5,…). Devuelve
`pasa, motivo, duda, sift_max_inliers, sift_max_cells, face_share,
face_cx_mediana, face_area_mediana, frames_total, frames_con_cara`. Nunca
lanza excepción: sin fotogramas/cv2/SIFT/Haar FALLA ruidosamente, nunca con un
valor inventado. Llamada desde `validate_final_clip` (`:2305-2308`) con
`layout_fn` inyectable (mismo patrón que los detectores de cara y Gemini; stub
`_layout_stub` en `test_final_gate.py`).

`_layout_check_arrays(arrays, vcfg, recipe, template_name)` (`:2004`): el
núcleo, testeable directamente sobre fotogramas. Cuatro reglas, en orden:

- **(i) Duplicación:** `sift_cells_max >= layout_dup_sift_cells` (8,
  `config.yaml:675`) → FALLA «layout duplicado». SIFT sobre como mucho
  `layout_sift_max_frames` (8, `:699`) fotogramas repartidos; manda el máximo.
  Submuestreo para n=20: índices {0,3,5,8,11,14,16,19} → t ∈ {1,7,11,17,23,29,
  33,39} (reparto uniforme sobre los 20). El máximo se toma sobre el
  subconjunto, no sobre los 20.
- **(ii) Sin cara estable:** `share < face_min_share` (0,5) → FALLA «sin cara
  estable».
- **(iii) Cara de primer plano desplazada:** cx fuera de [0,45, 0,65] Y área ≥
  `layout_face_area_closeup_min` (0,12, `:693`) → FALLA «cámara contaminada»
  (sin corroboración SIFT: una cara grande desplazada basta).
- **(iv) Cara desplazada o pequeña CON corroboración:** `cara_anomala` (cx
  fuera de rango O área < `layout_face_area_min` 0,15, `:689`) Y
  `sift_cells_max >= layout_face_sift_cells_min` (5, `:680`) → FALLA.

Llaves en `config.yaml:675-699` (todas obligatorias en `load_validator_config`,
`config.py:769-773` y parseadas en `:857-864`): `layout_dup_sift_cells: 8`,
`layout_face_sift_cells_min: 5`, `layout_face_cx_min: 0,45`,
`layout_face_cx_max: 0,65`, `layout_face_area_min: 0,15`,
`layout_face_area_closeup_min: 0,12`, `layout_sift_max_frames: 8`. La llave
antigua `layout_dup_sift_inliers: 8` ya no existe en config (la sustituye la de
celdas). **I-8 resuelto: el umbral tiene hogar en config; no hay dos umbrales
SIFT con el mismo nombre conceptual** (SIFTct02 y su «umbral 20» quedan
retirados con el método).

### §5.2 `cam_pura` y `layout_duplicado` (B-N3 — señal (b) eliminada)

La v3 definía `cam_pura` como disyunción de (a) señal de similitud + (b)
procedencia de la caja. **(b) se elimina**: `validate_final_clip` no recibe
`cam_box` (ni por parámetro ni por `cfg`) y corre ANTES del registro donde
§4.1.6 registra la caja (`live_cutter.py:1322`); (b) no es evaluable por el
validador como estaba especificada, y ningún fixture mostraba un caso donde (b)
disparase y (a) no. La procedencia de la caja se controla **en el render**
(§4.1), no se re-chequea en el validador.

- `layout_duplicado` = regla (i) de `layout_gate` (motivo propio).
- `cam_pura` = reglas de cara (ii)+(iii)+(iv) de `layout_gate` (motivos
  propios). La zona de cámara es mayoritariamente cámara cuando la cara está
  estable, centrada y de tamaño suficiente, Y no hay juego en la zona (i).
- **Parámetro `avatar` (nuevo, default False):** `validate_final_clip` /
  `layout_gate` / `_layout_check_arrays` ganan `avatar: bool`. Cuando es True,
  se saltan las reglas de cara (ii)/(iii)/(iv) — Haar no ve avatares, son
  inaplicables por principio (mismo patrón que el «no aplica» de plantillas
  no-rubius) — **pero la regla (i) SIFT sigue aplicando**. El llamador pasa
  `avatar=True` cuando la decisión §4.3 es «rubius con caja de candidato
  avatar». `face_gate` no cambia (clip 12 ya lo pasa; §1.2).

### §5.3 Calibración (rejilla del gate, gate actual — tabla normativa)

**Conjunto completo (n=20 fotogramas por clip):** la tabla de §1.2 ES la tabla
de calibración. Resultado: 4 limpios pasan (186, 190, 246, 248), 10
duplicados/contaminados fallan (183, 184, 189, 191, 197, 199, 214, 244, 12, 247),
170 pasa (t7), las 3 capturas fallan. **Separación completa a nivel clip.**

**Fotograma único t7 (regresión de `test_layout_gate.py`, 15/15 coinciden con
`ESPERADO_T7`):**

| Clip | Pasa | inl | cells | share | cx | área | Regla |
|---|---|---|---|---|---|---|---|
| 170 | ✓ | 14 | 4 | 1,00 | 0,421 | 0,080 | — |
| 183 | ✗ | 34 | 19 | 1,00 | 0,435 | 0,161 | (i) |
| 184 | ✗ | 38 | 27 | 1,00 | 0,414 | 0,114 | (i) |
| 186 | ✗ | 6 | 3 | 0,00 | — | — | (ii) sin cara en ese fotograma |
| 189 | ✗ | 6 | 4 | 1,00 | 0,416 | 0,141 | (iii) primer plano desplazado |
| 190 | ✓ | 0 | 0 | 1,00 | 0,615 | 0,235 | — |
| 191 | ✗ | 46 | 23 | 1,00 | 0,115 | 0,048 | (i) |
| 197 | ✗ | 10 | 5 | 1,00 | 0,422 | 0,176 | (iii) |
| 199 | ✗ | 598 | 281 | 1,00 | 0,573 | 0,543 | (i) |
| 214 | ✗ | 73 | 52 | 1,00 | 0,565 | 0,188 | (i) |
| 244 | ✗ | 21 | 9 | 0,00 | — | — | (i) |
| 246 | ✓ | 4 | 3 | 1,00 | 0,483 | 0,183 | — |
| 247 | ✗ | 201 | 101 | 0,00 | — | — | (i) |
| 248 | ✓ | 2 | 2 | 1,00 | 0,508 | 0,188 | — |
| 252 | ✗ | 61 | 22 | 1,00 | 0,477 | 0,145 | (i) |

**186 en t7 vs en conjunto completo — no es contradicción (decisión en un solo
sitio):** a t7 Haar no ve cara en ese único fotograma (share 0/1 → (ii));
en los 20 fotogramas ve cara en 17 (share 0,85) con cx 0,475 y área 0,167.
La tabla t7 usa 1 fotograma; la tabla §1.2 usa 20. El test de regresión
(`test_layout_gate.py`) usa t7; el gate en producción usa el conjunto completo.
Ambas tablas son normativas para su conjunto de fotogramas. **La v2/v3
etiquetaban 186 de forma distinta según la sección; la v4 lo decide aquí y
solo aquí: 186 PASA en producción (conjunto completo), FALLA a t7 (un solo
fotograma sin cara).**

**Muestreo SIFT — limitación documentada (no un bug):** 186 tiene un fotograma
no muestreado con 14 celdas (t37) — si el reparto incluyese t37, 186 fallaría
(i). El submuestreo a 8 fotogramas es un compromiso de coste (~20-30 s/clip);
el test y la producción usan el mismo reparto, así que el test sí prueba el
gate. Si un día se sube `layout_sift_max_frames`, 186 debe re-verificarse (su
margen de celdas es 6 vs 8). Lo mismo para 244 (10 vs 8) y 197 (corroboración
6 vs 5) en el otro sentido.

**Capturas del usuario — recorte canónico:** recorte (107,431)-(1182,2363),
1075×1932, aspecto 0,556 ≈ 9:16 (detectado por filas/columnas oscuras). Celdas:
307/57/21. El recorte fijo de `test_layout_gate.py` (`CAPTURA_VIDEO`, filas
66-2376, cols 95-1195, aspecto 0,476, incluye UI) da 257/43/22 — **mismos
veredictos**. La v4 adopta mi recorte como canónico y ordena actualizar
`CAPTURA_VIDEO` en el test (§10.2). **La limitación de la v3 (captura 3 no se
cazaba, SIFTct02 17 < 20) desaparece: con celdas, captura 3 da 21 ≥ 8.**

**Corrección del docstring de `layout_gate` (§5.5):** su calibración (15 clips,
45 fotogramas, `runs/layout-fix-2026-11-26/frames/`, rejilla t2/t7/t20 +t35)
dice «186 sin cara» y «186 cara de área 0,19 a cx 0,43» — en la rejilla del
gate 186 es share 0,85, cx 0,475, área 0,167. **El docstring se re-ancla a la
rejilla del gate** (tablas §1.2/§5.3) en Fase 1. La t2 no se muestrea nunca en
producción (el gate empieza en t=1, paso 2 s) — los fotogramas t2 de esa
calibración no son representativos del camino real.

### §5.4 Qué NO se shippea (medido, no hipotético)

- **ORB como señal única:** refutado (es248_t1 ORB 9 ≥ 7 en fotograma correcto;
  183_t1 ORBcomp 8). Queda documentado como medido-pero-no-shippeado.
- **MAD / NCC global / template matching / fracción de cara Haar / NCC por
  celdas / histogramas:** ninguno separa (medido en v1/v2; NCC por celdas da 0
  coincidencias en todos los fotogramas).
- **Varianza temporal para avatar:** refutada (§1.2).
- **Saltar fotogramas de la ventana del banner (t 0,5-3,5 s):** rechazado — 197
  se caza precisamente en t=1,3,5; SIFT es robusto al banner.
- **SIFTct02 (conteo de buenos matches, umbral 20):** retirado; lo sustituye
  el conteo en celdas.

### §5.5 Cambios al gate existente (Fase 1, mínimos)

1. Parámetro `avatar` en `layout_gate` / `_layout_check_arrays` /
   `validate_final_clip` (§5.2).
2. Re-anclar el docstring de calibración a la rejilla del gate (§5.3).
3. Nada más: no se tocan los umbrales (8/5/0,45-0,65/0,15/0,12/8) — la
   calibración §5.3 los valida sobre el corpus medido.

## §6. Formato «rubius sin webcam» (adopta el borrador revisado, con B7–B11 inlinadas)

Se adopta el borrador `runs/formato-rubius-sin-webcam-2026-11-26/SPEC.md`
(REVISADA, 2ª ronda de corrección, 454 líneas) **con sus correcciones inlinadas
aquí** — no se depende del archivo del borrador (vive fuera del repo). Las
divergencias respecto al borrador son dos y están decididas aquí:

- **186 PASA** (no FAIL): el borrador lo etiquetaba FAIL por `cam_pura`. La
  decisión es §1.2/§5.3 (PASA en conjunto completo). El arreglo del sangrado es
  snap de caja (§4.1), no rechazo del render.
- **NCC por celdas NO se adopta** (medido: 0 coincidencias en todos los
  fotogramas). La señal de duplicación es SIFT en celdas (§5.1).

### §6.1 Receta y config (B11)

Receta `rubius_sin_webcam` en el segundo bloque `templates:` de `config.yaml`
(la efectiva): gameplay a pantalla completa vertical (1080×1920), sin banda de
cámara; título y subtítulos como el rubius (`word_white`, banner y subs en las
mismas posiciones relativas que la receta rubius — la posición del banner en
sin_webcam es pregunta abierta §11.1: y 811-1099 vs arriba ~100-388).
`RECIPE_TEXT` gana la entrada `rubius_sin_webcam` (`final_review.py:120`; hoy
cae al fallback rubius en `:1740-1742`).

**Limpieza de duplicados (B11):** `config.yaml` tiene `validator:` dos veces
(553 y 640), `templates:` dos veces (604 y 717) y **`sfx:` dos veces (366 y
379)**. `yaml.safe_load` (`config.py:551`) toma la última en silencio.
Efectivos: `validator:` 640, `templates:` 717. Borrar 553-603, 604-639 y
366-370 (bloque 366-369 + línea en blanco 370; el comentario Layout 371-378
se conserva). La primera `validator:` carece de `rubius_subs_outline_frac:
0,03` (solo en la segunda, :665). **Test guarda:** ninguna llave de nivel
superior duplicada en `config.yaml` (robusto al baseline — los rangos son del
árbol actual; en HEAD serían 538-588 y 589-624). `TEMPLATE_REQUIRED_KEYS`
definida dos veces en `config.py` (:378 y :664, idénticas; efectiva :664) —
consolidar en una.

### §6.2 Render sin webcam (B9, I-7, M-4, M-5)

- **Modo forzado `"game"` en `decide_layout`:** la v2 lo pasaba por
  `cfg["layout"]["mode"]` — **eliminado** (B9: `cut_clip_with_layout` pisa
  `cfg["mode"]` con `layout_mode="auto"`). El modo viaja por call site (§4.2).
  `decide_layout` gana el modo forzado `"game"`; la rama `game` existente
  (`render.py:3083`, recorte 9:16 centrado a pantalla completa, sin webcam) es
  la salida (no `center`, que sigue la cara). La decisión devuelta lleva
  `"plantilla": "rubius_sin_webcam"`.
- **Zoom del juego (I-7, M-4):** llamar a `rubius._game_filter` desde la rama
  `game` con **import perezoso dentro de `segment_filter_graph`** (patrón en
  `render.py:2977`, `from .rubius import _game_filter as _rubius_game_filter`;
  nunca a nivel de módulo) y con los **tres** argumentos que `_game_filter`
  (`rubius.py:443-467`) exige: `game_crop` (recorte 9:16 a pantalla completa,
  distinto del `1440:1440:560:0` de rubius — nuevo helper paralelo a
  `_rubius_game_crop` `:2934`, que devuelve el recorte 1:1), `game_zoom`
  (ventanas hype/18-24 s) y `game_h=1920` (enhebrado en los TRES sitios donde
  `_game_filter` usa `GAME_H`: escala, recorte final y `y_coef` — no solo
  `y_coef`). La llamada en `:3026` es `_rubius_game_filter(
  _rubius_game_crop(src_w, src_h), game_zoom)`; la rama `game` la llama con
  su propio recorte 9:16 + `game_h=1920`.
- **`_crop_rects_for_frame` tiene su propia rama `game` (`render.py:3609`):**
  si la rama `game` gana zoom, la hoja de contacto no lo refleja. No bloquea;
  se anota explícitamente o se ignora (M-5).
- **`_criterion` conserva `motivo` siempre** (`quality_gate.py:112-114`): hoy
  borra `motivo` cuando pasa. Verificado por grep: **ningún test existente
  afirma `motivo == ""`** (la instrucción de la v2 de actualizar esos tests
  era errónea — M-2 de la 2ª revisión).

### §6.3 Puerta para sin_webcam (diseño del borrador, adoptado)

`_cam_game_match` + chequeos duros `layout_duplicado` (rama rubius) y
`gameplay_puro` (rama nueva: prueba positiva de layout que condiciona la
exención de cara) + `cam_pura` (§5.2). `face_gate`: sentencia `pasa=None`
(cuando no aplica), el criterio webcam pasa solo si `gameplay_puro` pasó.
Gemini `webcam_visible` queda **anulado** por el gate (no es señal de layout).

### §6.4 Transporte de `plantilla` (B7 — Fase 3)

`parseViralInput` (`panel-backend/worker/src/db.ts:352`) es una whitelist: copia
SOLO llaves conocidas (`seo_scores`, `score_kind`, `viral_score`, `hype_score`,
`priority`, `viral_breakdown`, `quality_gate`, `platforms`, `seo_inputs`,
`seo_source`, `hold_reason`). **Cero ocurrencias de `plantilla` en todo
`panel-backend/worker/src/`.** La resolución del borrador citaba :344/:475/
:2034/:1452 — **verificado erróneo por grep** (:344 y :475 son llaves de cierre;
:2034 no es el INSERT; :1452 es `score = body.score;`). Líneas correctas
(verificadas): `ViralData` `db.ts:212`; `parseViralInput` `:352`;
`mergeViralJson` `:497`; INSERT `:1949` (HEAD; `:1968` en el árbol);
POST parse `index.ts:1412` → merge `:1491`; PATCH parse `:1241` → merge `:1242`.

Fix (Fase 3): `plantilla?: string` en `ViralData` (`db.ts:212`); nueva rama en
`parseViralInput` (string + nombre conocido: rdjavi, willyrex, rubius,
`rubius_sin_webcam`; desconocido → error, nunca guardado en silencio);
`mergeViralJson`: `if (patch.plantilla !== undefined) next.plantilla =
patch.plantilla;`. El mismo cambio lleva `cam_box`/`cam_box_source`/
`cam_box_guardas` (§4.1.6). Consecuencia: en Fase 1 esos campos viven solo en
el cerebro y NO son diagnosticables desde D1 hasta Fase 3; la puerta §5 no
depende de D1.

**`plantilla` en el JSON `viral` (Fase 1, sin archivos del Worker):** 3 caminos
— live `live_cutter.py:~1640`; `viral_source.py:~771` (hoy hardcodea
`plantilla="rubius"` en `:684-687`); VOD `vod_fallback.py:938-951` + PATCH
`:1023` (Fase 3, con el cambio B7). En Fase 1 la `plantilla` se envía en el JSON
`viral` y el Worker la descarta hasta Fase 3.

### §6.5 `template_gate` despacha por nombre de plantilla (B8)

`template_gate` (`final_review.py:1169-1182`) despacha hoy solo por
`subs_style`. **Rama POR NOMBRE DE PLANTILLA antes del despacho por
`subs_style`:** `template_name == "rubius_sin_webcam"` →
`_template_fullscreen_game(...)`; si no, el despacho actual.
`template_gate` gana `template_name` como parámetro (hoy no tiene ninguno;
`validate_final_clip` lo resuelve en `:1927` y llama en `:1981`). Alternativa
rechazada: un valor distinto de `subs_style` (mentiría sobre los subtítulos,
que son el mismo `word_white` compartido).

## §7. Fase 2 — cajas de cámara por influencer (después de Fase 1)

Tabla D1 `influencer_cam_boxes` (caja + `confianza` alta/media + fecha),
API del panel GET/PUT, `confianza` definida (alta = caja validada en ≥3 clips
distintos; media = 1-2 clips). **La semilla `facecam_boxes` en `config.yaml`
se ELIMINA** (M-5 de la 2ª revisión: sin procedimiento definido — solo tabla D1).
Procedimiento de medición con muestra etiquetada por humano (30-50 segmentos)
+ matriz de confusión (§11.3: medir la tasa real de sin-facecam antes de
decidir; <10% → aprobación con el coste escrito, no pre-decidido).

## §8. VOD EN + dispatch diario (Fase 3)

### §8.1 Reserva EN en el VOD-fallback — tope perezoso (B-N5, I-3 de la 2ª revisión)

Hechos verificados: la cuota vive en `run()` (`clipagent/vod_fallback.py:
1463-1465,1507`: `if enqueued >= max_clips or vods_tried >= max_vods: break`),
NO en `fallback_order` (`:193`, solo ordena ES primero y luego EN). Los saltos
por pre-chequeo (sin VOD / cooldown / claramente-no-GTA6) hacen `continue` SIN
incrementar `vods_tried` — los VODs saltados no consumen huecos. `lang` se
calcula DESPUÉS de `vods_tried += 1` (`:~1510`) y `latest_vod_fn` se llama
ANTES de cualquier tope por idioma — por eso la v3 no era implementable como
estaba escrita (B-N5): el predicado «existe al menos una entrada EN que PASE el
pre-chequeo» exige listar los VODs EN antes de decidir el tope, pero el tope
debe ir antes del listado para no gastar listados en entradas ES capadas.

**Regla única — tope perezoso (normativa):**
- Contadores por idioma: `intentados = {"es": 0, "en": 0}`; topes iniciales
  `tope = {"es": 1, "en": 1}` (con `max_vods = 2`).
- Al inicio de cada iteración, `lang` se calcula desde `entry.get("language")`
  **antes** de `latest_vod_fn`. El chequeo de tope va al inicio de la iteración,
  antes del listado.
- **Relajación:** si el otro idioma no tiene entradas sin visitar en `order`,
  el tope de este idioma sube a `max_vods` (2). Una entrada saltada por
  pre-chequeo cuenta como visitada y levanta la reserva del otro idioma.
- Simétrico: sin entradas ES → 2 EN; sin entradas EN → 2 ES; una entrada EN
  saltada por pre-chequeo → ES puede usar el 2º hueco.
- El corte VOD ya lleva `plantilla` (`vod_fallback.py:658` re-render, pasado a
  validación `:754-759`; `clip.get("plantilla")` `:897`; `layout_cfg` con
  `facecam_box` `:724-734`; `viral_payload` `:938-951`).

Los tests cubren «sin candidato ES → 2 EN» (el caso que la v3 no cubría).

### §8.2 Dispatch diario — mover al inicio, con guarda y log (I-1, I-2, I-10 de la 2ª revisión)

Hechos verificados: `maybeDispatchDaily` (`panel-backend/worker/src/index.ts:
1779`, dedupe por día en settings `brain_daily_dispatched_day`; si el dispatch
falla, el día NO se marca y reintenta en el siguiente ciclo mientras siga la
hora 9). `isDailyDispatchDue` (`cycle-gate.ts:211-220`, exportada): hora==9 +
marca ≠ día; devuelve true si la lectura de settings falla. Gate en
`cycle-gate.ts:293-304` (`cycleGate` devuelve `{run, …, dailyDue, …}`).
Llamada final en `index.ts:4624` condicionada a `r.ran || r.gate.dailyDue`.

**Tres hipótesis del fallo 2026-11-26 09:02** («Too many subrequests», sin
reintento, sin rastro en D1 `worker_events` 09:00-10:30 — solo 3 eventos
`auto_publish` del clip #57 a las 09:01, ningún `github_dispatch`):
(1) el dispatch se lanzó tarde en el ciclo y el Worker murió por subrequests
antes de llegar a `:4624`; (2) el camino `!ok` hace `if (!ok) return;` **sin
log** (solo el camino de excepción loguea «Dispatch diario falló»), así que un
`dispatchCerebro` que devuelve false (GitHub 4xx/5xx, token inválido) es
igualmente consistente con la ausencia; (3) `runWorkerCycle` (`jobs.ts:1974`)
no está envuelto en try/catch — si lanza, el handler aborta antes de `:4624`
sin rastro. **La corrección de la v2 («failure already logged in
`worker_events`») era falsa para el camino `!ok`.**

**Cambios (Fase 3):**
1. El dispatch se mueve al **inicio del handler, DENTRO del bucle por tenant,
   ANTES del `runWorkerCycle` de ese tenant** (I-2: la v2 lo ponía fuera del
   bucle y perdía la condición «Worker en marcha»).
2. **Guarda explícita: NO despachar con el Worker parado.** Con el Worker
   parado, `runWorkerCycle` devuelve `{ran: false, gate: idleCycleGate()}` —
   parado ⇒ sin dispatch diario (comentario en `index.ts:4595-4601`). La
   condición es `isWorkerRunning` (`jobs.ts:1874`, exportada; early-return en
   `:1980` árbol / `:1727` HEAD).
3. **try/catch alrededor de `isWorkerRunning`** (I-10): si la lectura D1 lanza,
   se loguea y se salta el dispatch temprano (peor que hoy sería abortar el
   handler para ese tenant). Precedente: `index.ts:2008` `.catch(() => null)`.
4. **Log en el camino `!ok`** (hoy silencioso).
5. Se **borra la llamada final** (`:4624`) — el dispatch vive solo al inicio.

**Ya desbloqueado:** la opción A aterrizó 2026-11-26 20:07 (commit 26267d5,
schedules `*/5 * * * *` verificados, health ok) — hay 12 ciclos en la hora 9.

## §9. Secuenciación y baseline

**Baseline único (I-9):** «árbol a 2026-11-27 ~01:30, HEAD `c6ff3e1` + trabajo sin
commitear». Las citas dan líneas del árbol, con líneas HEAD donde difieren.
Trabajo sin commitear que esta spec toca o sobre el que se construye:
`clipagent/final_review.py` (+293/+358 líneas, el `layout_gate`),
`clipagent/render.py` (+166), `clipagent/remake_all.py` (+84),
`clipagent/live_cutter.py` (+33), `clipagent/config.py` (+14),
`clipagent/config.yaml` (+37), `panel-backend/worker/src/*` (piso 3, otro
agente), `clipagent/compilation.py` (+197, compilación de vídeos largos, otro
agente), `test_layout_gate.py` (nuevo, raíz del repo), `test_final_gate.py`
(+23). `git diff --stat` a 2026-11-27 ~01:00: 27 archivos, 1791 inserciones,
115 borrados. **Fase 1 se implementa ENCIMA del estado actual del árbol
(layout-gate incluido; no se re-implementa `layout_gate`)**; si el árbol se
commitea antes, re-verificar las citas.

**Fase 1 — Python/config + 5 call sites + `_do_cut` (NO archivos del Worker):**
guardas §4.1 (regla reconciliada, `require_containment`, p7775 como 4ª
estrategia, ganchos `:2432-2444` / `:2559` / `:2601` → `"game"`); modo por call
site + `_do_cut` con `get_layout_config` + `deepcopy` (§4.2, 6 call sites);
`decide_template_for_segment` (§4.3, avatar por caja de candidato, sin
primitiva de píxeles); `_rerender_after_gate` (§4.4, trigger `"layout"` +
`record["plantilla"]` desde `review2` + parámetro `avatar`); parámetro
`avatar` en `layout_gate` + re-anclar su docstring (§5.5); `template_gate`
despacha por nombre de plantilla (§6.5); receta `rubius_sin_webcam` + borrar
los 3 bloques duplicados de `config.yaml` + test guarda (§6.1); `plantilla` en
el JSON `viral` en los caminos live y viral (NO Worker; el Worker la descarta
hasta Fase 3, §6.4). **Antes de implementar: 4ª revisión goldfish del delta
v3→v4 (EGM) + aprobación del usuario.**

**Fase 2 — cajas por influencer** (§7, después de Fase 1).

**Fase 3 — Worker + VOD + dispatch:** cambio B7 (`ViralData` +
`parseViralInput` + `mergeViralJson` para `plantilla`/`cam_box*`); camino
payload VOD; pill en el panel; `vod_fallback.py` `run()` con topes perezosos
(§8.1); `index.ts` mover-al-inicio + borrar llamada final + log `!ok` + guarda
Worker-en-marcha con try/catch (§8.2); `cerebro-live.yml` sin cambios de caps
(`--max-vods 2 --max-clips 2`).

**Regla de commit (orden del usuario 2026-11-26 20:02):** todo cambio terminado
y verificado se commitea y se sube (commit + push a main), nunca queda solo en
memoria. Antes de commitear, separar lo terminado y verificado de lo que otro
agente sigue editando en el mismo árbol: commitear solo lo terminado, `git add`
por ruta, nunca `git add -A`. **El repo es PÚBLICO: nunca commitear clips,
fotogramas ni capturas** (regla añadida tras 30ba071/c6ff3e1). Verificar
`/api/health` tras cada deploy (TENANT obligatorio, crons intactos); nunca
desplegar con config nocron sin reponer crons (PUT array JSON al endpoint de
schedules; GET devuelve `{"result": {"schedules": [...]}}`).

## §10. Tests y aceptación

### §10.1 Puerta (`test_layout_gate.py` — ya existe, raíz del repo; NO sobrescribir: extender)

- **Tabla t7 (`ESPERADO_T7`):** 15 casos de §5.3 (170 ✓; 183/184/186/189 ✗;
  190 ✓; 191/197/199/214/244 ✗; 246 ✓; 247 ✗; 248 ✓; 252 ✗). **186 t7 es
  ✗ (sin cara en ese fotograma) — es el comportamiento correcto a t7.**
- **Tabla de conjunto completo (nueva):** los 14 clips de §1.2 con sus 20
  fotogramas de la rejilla del gate (las extracciones viven en
  `runs/layout-gate-calib/`): 186/190/246/248 ✓; 183/184/189/191/197/199/214/
  244/12/247 ✗. **186 en conjunto completo es ✓** — las dos tablas coexisten
  porque usan conjuntos de fotogramas distintos (§5.3).
- **Capturas del usuario:** las 3 fallan por (i) con **ambos recortes** (canónico
  307/57/21 celdas; recorte del test 257/43/22). `CAPTURA_VIDEO` se actualiza
  al recorte canónico (107,431)-(1182,2363) y los valores esperados se
  re-verifican (§5.3).
- **Avatar:** clip 12 y 244 fallan (i) con `avatar=False`; con `avatar=True`
  las reglas de cara se saltan pero (i) sigue fallando (42/10 celdas) —
  el re-render con la caja ajustada del candidato es el que debe pasar.
- **170:** pasa con `avatar=False` (4 celdas < 5, regla (iv) no dispara) — el
  caso que protege la corroboración.
- **248:** pasa con celdas (5) aunque tenga 9 inliers — el caso que protege
  la estadística de celdas frente al conteo de inliers.
- **Sintéticos:** `_sift_dup_inliers` sigue usándose directamente en los tests
  sintéticos (wrapper de compatibilidad).

### §10.2 Guardas y render

- Regla reconciliada (§4.1) sobre las cajas documentadas: p7775 ✓
  (centro (0,11, 0,56), y0 0,435 < 0,50, ancho 0,22); p3220 ✓; caja centrada
  grande ✗; caja con y0 ≥ 0,50 ✗.
- `validate_cam_box(..., require_containment=False)`: caja explícita válida sin
  cara detectada pasa; con cara presente pero fuera, la contención no aplica.
- Los 6 call sites llegan a `decide_layout` con modo `game` cuando la decisión
  es sin_webcam (incluido `workers.py:955`).
- `record["plantilla"]` se actualiza desde `review2` tras el re-render (live y
  VOD); el POST VOD lleva la plantilla nueva.
- Invariante §4.1.4: caja inválida/ausente → `"game"` o hold, nunca el
  fotograma completo como cámara, nunca duplicado.
- `config.yaml`: ninguna llave de nivel superior duplicada (guarda).

### §10.3 Detector de Burn

**Burn no tiene coordenadas documentadas.** El test corre el detector sobre
segmentos fuente reales de Burn (fixtures fijados en el test) y afirma que las
cajas devueltas pasan la regla §4.1. **El test exige detección no vacía y falla
explícitamente si no encuentra ninguna** (I-11) — nunca se salta en silencio.
Si los fixtures no están en el workspace, el test falla con mensaje explícito
(«fixtures de Burn ausentes en runs/…»), no se marca como pasado.

### §10.4 Verificación en vivo (pendiente del re-render)

- Los 10 EN re-hechos pasan la puerta nueva (o se reporta cuáles no y por qué).
- Un VOD EN entra por el hueco reservado (§8.1).
- El dispatch diario del día siguiente tiene éxito (o reintenta dentro de la
  hora 9).
- 247 re-hecho pasa la puerta antes de cualquier re-programación.
- Clip 12 y 244 re-renderizados con la caja ajustada del candidato pasan la
  puerta.
- **Líneas base antes de afirmar verde:** suite Python, suite Worker, tsc,
  ruff (medir primero, no asumir).

## §11. Preguntas abiertas

1. **Posición del banner en sin_webcam:** y 811-1099 (como rubius) vs arriba
   ~100-388. No bloquea la implementación (la receta usa la de rubius hasta
   decidir).
2. **247: re-make vs descarte.** No hay job vivo; el re-make necesita decisión.
3. **Tasa real de sin-facecam:** medirla con muestra etiquetada (30-50
   segmentos) + matriz de confusión; si <10%, aprobación con el coste escrito,
   no pre-decidido.
4. **Piso 3/día:** spec aparte, aprobada 2026-11-26 20:46, en implementación por
   otro agente.
5. **Limpieza del historial del repo público:** las 22 imágenes siguen en el
   historial de 30ba071 (sacadas del árbol en c6ff3e1, no del historial).
   BFG + force-push necesita decisión explícita del usuario — no se hace
   unilateralmente.
6. **Recorte de capturas en el test:** `CAPTURA_VIDEO` de `test_layout_gate.py`
   se actualiza al recorte canónico (§5.3); los valores esperados se re-verifican.

## Referencias

- Repo: `~/workspace/gta6-clips/viral-clip-agent/` (HEAD `c6ff3e1` a 2026-11-27
  ~01:30; árbol con `layout_gate` + piso-3 + compilación sin commitear).
- Spec v3 (mismo archivo, commit `30ba071`): SIFTct02 (retirado), ORB refutado,
  avatar_share 0,20 (eliminado), 183/184/189 PASAR (revocado).
- Borrador sin_webcam: `~/workspace/gta6-clips/runs/formato-rubius-sin-webcam-2026-11-26/SPEC.md`
  (REVISADA, 454 líneas) + `REVISION-GOLDFISH-2.md` (NO APTA: B7-B11) +
  `REVISION-RESOLUCION.md` (sus 4 citas Worker son erróneas — ver §6.4).
- Fixtures README: `tests/fixtures/duplicacion/README.md` (solo texto en el
  repo; imágenes en `runs/layout-gate-calib/fixtures-duplicacion/`).
- Calibración: `runs/layout-gate-calib/` (fotogramas de la rejilla del gate,
  `calib_final.txt`, `perframe_cells.txt`, `t7_check.txt`); `runs/layout-fix-2026-11-26/frames/`
  (calibración t2/t7/t20 del `layout_gate` — rejilla no representativa, §5.3);
  `runs/review-en-2026-11-26/` (14 mp4 EN) y `runs/review-es-2026-11-26/`
  (246/247/248).
- Código (árbol; HEAD donde difiere): `clipagent/render.py` (decide_layout
  :2320 / :2221 HEAD; `cut_clip_with_layout` :3471 / :3341 HEAD; pisado
  `:3494-3496`; `_cam_box_valida` :1923; `_split_params` :1957; escaneo
  `:2432-2444`; guardas `:2559`/`:2601`; rama game :3083;
  `detect_vod_facecam` :3384 / :3254 HEAD; `_detect_vtuber_candidates` :973;
  `_scan_frames` def :2037, llamadas :2197/:2397);
  `clipagent/rubius.py` (`build_filter` :471-528; `GAME_CROP` :95; `GAME_Y/H`
  :93; `_game_filter` :443-467); `clipagent/rdjavi.py` (`DEFAULT_CAM_BOX` :114;
  ejemplo p7775 :77); `clipagent/final_review.py` (`face_gate` :859-893;
  `template_gate` :1169-1182; `find_facecam_box_in_source` :1667;
  `extract_gate_frames` :780; `_sift_dup` :1843; `_sift_dup_inliers` :1907;
  `layout_gate` :1914; `_layout_check_arrays` :2004; `validate_final_clip`
  :2160, `layout_fn` :2164, llamada :2305-2308, `"layout"` :2334;
  `RECIPE_TEXT` :120, fallback :1740-1742); `clipagent/quality_gate.py`
  (`_criterion` :112-114); `clipagent/live_cutter.py` (`_rerender_after_gate`
  :874, revisión humana :898-908, `cfg2["facecam_box"]` :910-916, re-validación
  :975-984, corte live :1096, plantilla :1104, record :1322, llamador :1422,
  `build_quality_gate` :1454, `record["quality_gate"]` :1464);
  `clipagent/workers.py` (`_do_cut` :406-419, `cut_clip` :422, job :955);
  `clipagent/remake_all.py` (`box_strategies` :271, `_tight_box` :248);
  `clipagent/yunet_facecam.py` (`validate_cam_box` :365, `find_cam` :395);
  `clipagent/vod_fallback.py` (`fallback_order` :193, cuota :1463-1507,
  re-render :658, corte :724-734/:754-759, `clip.get("plantilla")` :897,
  llamador :915-917, `viral_payload` :938-951, PATCH :1023-1035);
  `clipagent/viral_source.py` (hardcode `:684-687`, payload :740-772);
  `clipagent/config.py` (`load_config` :551, `get_layout_config` :1007-1011,
  `resolve_template` :967 / :953 HEAD, `layout_*` :769-773/:857-864);
  `config.yaml` (`sfx:` :366/:379, `layout:` :384, `mode` :385, `facecam_box`
  :450, `validator:` :553/:640, `templates:` :604/:717, `layout_*` :675-699);
  `panel-backend/worker/src/db.ts` (`ViralData` :212, `parseViralInput` :352,
  `mergeViralJson` :497, INSERT :1949 HEAD / :1968 árbol);
  `panel-backend/worker/src/index.ts` (PATCH :1241/:1242, POST :1412/:1491,
  `maybeDispatchDaily` :1779, comentario parado :4595-4601, llamada final
  :4624, `isWorkerRunning` import :152, usos :1966/:1984/:2008);
  `cycle-gate.ts` (`isDailyDispatchDue` :211, `cycleGate` def :292, :293-304);
  `jobs.ts` (`isWorkerRunning` :1874, `runWorkerCycle` :1974, early-return
  :1980 árbol / :1727 HEAD).
- D1 (seba `d1q.py`, db `clips-avc`; kaez `query_kaez_d1.py`): clips 12
  (en_carpeta, 51, fallos hook/titulo/audio), 244 (en_carpeta, 51), 252
  (en_carpeta, 57), 170 (publicado, 0); jobs 161 (248 IG 00:00, scheduled),
  164 (190 YT EN publicado 20:05), 165 (247 eliminado, 0 filas).
- Goldfish: v1 (5d857a24), v2 (bf83eb8c), v3 (da11e70f) — las tres NO APROBADA.
  4ª revisión (delta v3→v4) pendiente antes de implementar.
