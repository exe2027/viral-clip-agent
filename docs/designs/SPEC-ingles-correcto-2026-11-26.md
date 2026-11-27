# SPEC — Inglés correcto de punta a punta (v6)

**Estado:** v6 — reescritura tras la 5ª revisión goldfish (NO APROBADA, B-N7/B-N5/B-N6
siguen abiertos parciales, B-N8 nuevo, I-13/I-18/I-23–I-26, M-10/M-11/M-16/M-17).
**Sin implementar todavía: esta v6 necesita una 6ª revisión goldfish (EGM, solo
del delta v5→v6) + aprobación del usuario antes de implementar.** Fecha:
2026-11-27 ~03:00 (Chile).

**Repo:** `~/workspace/gta6-clips/viral-clip-agent/`. **No borres clips. No cortes a mano.**
**No publiques nada a mano desde este trabajo** (la publicación desde el chat la lleva
otro agente; la salida de este trabajo son clips válidos en cola + VOD fluyendo).
**El repo es PÚBLICO: nunca van clips, fotogramas ni capturas del usuario a git**
(las imágenes viven en `~/workspace/gta6-clips/runs/layout-gate-calib/` y en
`runs/layout-fix-2026-11-26/frames/`, fuera del repo; los tests que las usan se
saltan si no están en el workspace).

## v5 → v6 (qué cambió y por qué)

La v5 recibió **NO APROBADA** en su 5ª revisión goldfish (agente 03e7602e,
2026-11-27 02:36 UTC). La revisión **confirma una parte importante por
re-ejecución propia** (I-14, I-15, I-16, I-17, I-20, I-21, M-2, M-7, M-8, M-12,
M-13, M-15; 252 conjunto completo 220/70 exacto; capturas 331/57/22 y 274/36/22;
t7 de 253–256 exacto) pero **los tres bloqueadores de la v4 siguen abiertos
(B-N7 con evidencia nueva más fuerte, B-N5 parcial, B-N6 parcial) y aparece
B-N8: el árbol ya contiene trabajo concurrente que la v5 presenta como
pendiente o ignora.** Verifiqué cada hallazgo en el código del árbol actual
antes de escribir esta v6 (no se acepta ninguno por autoridad):

- **B-N8 (nuevo) — la v5 prescribe contra un árbol que ya implementó otra
  cosa.** Verificado: `clipagent/template_decision.py` existe (sin seguimiento)
  con `decide_template_for_segment(segment_path, duration, cfg=None,
  detect_fn=None, avatar_fn=None)` y 8 call sites ya lo importan
  (`live_cutter.py:948-951` y `:1175-1177`, `workers.py:963-965`,
  `viral_source.py:678-679`, `vod_fallback.py:743`, `youtube_cut.py:1006-1014`,
  `facebook_cut.py:962-970`, `auto_clips.py:~1565`); `quality_gate.py` ya trae
  el criterio webcam sin_webcam (`:203-224`); `rubius.py` ya trae
  `game_h=GAME_H` (`:443`); `live_cutter.py` ya implementa el pre-chequeo §2.2A
  y `_rerender_after_gate` ya tiene rama sin_webcam; `workers.py:_h_corte` ya
  decide y propaga `plantilla_decidida`; `live_cutter.py:1757` ya escribe
  `_viral["plantilla"]`; `youtube_cut.py:582` / `facebook_cut.py:543`
  `_default_validate` ya aceptan `plantilla`. **Decisión v6: ADOPTAR el árbol,
  no re-prescribirlo.** La función de decisión canónica ES
  `decide_template_for_segment` en `clipagent/template_decision.py` (se queda
  ahí de forma permanente — moverla ahora removería 8 call sites sin ganancia
  conductual; la v5 ordenaba crearla nueva en `final_review.py`: retirado).
  `prepare_cut_layout` (§4.2 v5) **no se crea**: cada camino ya corre la
  decisión en su sitio; §4.2 v6 es la tabla del estado real + los huecos que
  quedan. `viral_source.py` **sigue descartando** sin_webcam/ambiguo (`:680-692`)
  — la orden v5 §4.2.7 de renderizar sin_webcam por `cut_clip_with_layout` en
  ese camino se RETIRA (ese camino solo re-hace a rubius; `rubius_render`
  exige `cam_box`; el sin_webcam fluye por live/VOD).
- **B-N7 sigue abierto (3 sub-hallazgos, todos verificados por mí).**
  (a) `face = {"pasa": None}` → `not None` es True → "cara" en `hard_failures`
  (`final_review.py:2331-2332`); además la justificación era falsa
  (`layout_gate` devuelve `pasa=True` para "no aplica", `:1914`). **Decisión
  v6: el centinela es `{"pasa": True, "method": "sin_webcam", ...}`** (§6.3)
  — el mismo patrón "no aplica → True" del gate. (b) Las claves §6.1 v5
  (`banner`, `subs`, `sfx`, `dimensiones`) contradicen el esquema real:
  la receta rubius usa (`cam`, `game`, `title_banner`, `subs_zone`,
  `subs_style`, `subs_over_game`, `cam_full_width`, `blurred_bg`) y
  `TEMPLATE_REQUIRED_KEYS["rubius_sin_webcam"]` **ya existe** en el árbol
  (`config.py:657`, definición ÚNICA — la v5 citaba `:378`/`:664`, que no
  existen) con (`game`, `title_banner`, `subs_zone`, `subs_style`,
  `subs_over_game`, `cam_full_width`, `blurred_bg`). **Decisión v6: la receta
  usa ESAS claves exactas** (§6.1); la "doble definición, consolidar en una"
  de la v5 se retira por falsa. (c) Falta `TEMPLATE_NAMES` (`config.py:639`):
  `load_validator_config` construye `vcfg["templates"]` solo para esos
  nombres y `resolve_template(vcfg, "rubius_sin_webcam")` levanta `ConfigError`
  (reproducido por el goldfish con el venv). **Decisión v6: el alta en
  `TEMPLATE_NAMES` va EN EL MISMO CAMBIO que la receta en `config.yaml`**
  (el comentario del árbol `:640-645` ya lo anticipa así); el loader ya
  salta el chequeo de `cam` cuando la receta no la lleva (`:824-827`).
- **B-N5 sigue abierto (parcial).** Verificado el bucle (`vod_fallback.py:
  run()` `:1436`, cuota `:1511`, `vods_tried += 1` `:1553`): (1) el caso
  simétrico "EN saltada por pre-chequeo → ES usa el 2º hueco" es inalcanzable
  en una sola pasada ES-primero (la relajación se evalúa antes de examinar
  EN); (2) la excepción de `latest_vod_fn`, `not vod` y `not vod_id` no
  cuentan como "visitadas", así que la relajación puede no disparar nunca.
  **Decisión v6: bucle en DOS FASES** (§8.1): fase 1 con tope 1/idioma y
  `continue`; fase 2 revisita solo las entradas saltadas por tope cuando el
  otro idioma ya no tiene entradas sin examinar; TODAS las vías de `continue`
  marcan la entrada como visitada.
- **B-N6 sigue abierto (parcial) — la exención de posición del avatar no tiene
  mecanismo.** Verificado: `_cam_box_valida` (`render.py:1923`) es el único
  guardián del camino de caja explícita (`:2387`) y de las guardas (`:2575`,
  `:2617`); la v5 le añade la posición incondicionalmente (§4.1), así que una
  caja avatar en esquina inferior sería rechazada y se perdería. **Decisión
  v6: `_cam_box_valida(..., avatar=False)`** — con `avatar=True` se salta
  SOLO la posición (ancho <0,45 + área <0,35 siguen); el flag viaja en
  `params["avatar"]` desde la rama vtuber (`:2458`) y en
  `cfg["facecam_box_avatar"]` para la caja explícita. Además la caja avatar
  **no la devuelve la decisión**: la decide el escaneo del render (rama
  vtuber, que ya existe) con la caja AJUSTADA (sin margen, desde `_cells`);
  la decisión devuelve `("rubius", None, "firme")` para avatar y el flag de
  validación sale de `hay_avatar` (público, §4.3.1).
- **I-23 (nuevo) — `face_gate` de 244 FALLA y la exención avatar no lo
  cubre.** Re-medido por el goldfish (20 fotogramas, `face_gate` real): clip
  12 PASA (`method=yunet`, share 1,0, 20/20); clip 244 FALLA (`method=
  facecrop`, share 0,05, 1/20). La exención `avatar=True` de la v5 cubre solo
  `layout_gate`. **Decisión v6: `face_gate` gana `avatar=False`; con True
  devuelve `pasa=True, method="avatar"`** (§5.2) — Haar no ve avatares por
  principio — y §10.4 EXIGE medir el 244 re-renderizado real con ambos gates,
  no afirmar su pase a ciegas.
- **I-13 sigue abierto (parcial).** Verificado: el punto 3 VOD de la v5 era
  el re-render, no el primer corte — el primer corte VOD vive en
  `auto_clips.py` (`:1578`, `--layout` ya admite `"game"`, `:705`) y YA decide
  la plantilla antes de cortar; los puntos 5 y 7 YA enhebran `plantilla`
  (`_default_validate(..., plantilla=None)`, `youtube_cut.py:582`,
  `facebook_cut.py:543`); lo que falta es `avatar` en `_default_validate` y
  en `validate_final_clip`, y el `duration` de cada punto ya lo aporta su
  call site (el helper v5 que lo dejaba sin especificar no existe). §4.2 v6
  lista los 7 puntos reales con sus líneas actuales.
- **I-18 sigue abierto (parcial) — el portador de p7775 está muerto.**
  Verificado: `load_blocked_clips` (`remake_all.py:681`) construye el dict
  con solo `"streamer"` (de `c.get("streamer") or c.get("influencer_name")
  or c.get("influencer")`, `:702-703` son campos de la fila cruda `c`, no del
  clip construido); `clip.get("influencer_name") or clip.get("influencer")`
  es siempre None → p7775 nunca dispara. **Decisión v6: el portador es
  `clip.get("streamer")`** (§4.1.5).
- **I-24 (nuevo) — §6.2 incompleta.** Verificado: la rama `game`
  (`render.py:3229`) es un recorte 9:16 PLANO (misma expresión que `center`),
  sin `_game_filter`; la llamada `_rubius_game_filter(...)` en `:3042` es del
  grafo split, no de la rama `game`; `game_zoom` no tiene fuente en la rama
  `game`. **Decisión v6: se RETIRA la llamada a `_game_filter` desde la rama
  `game`** (el zoom pertenece al camino split; sin calibración no se forkea
  un segundo zoom); `t_offset` es 0 (un solo segmento); el discriminador es
  `decision["mode"] == "game"` y `cut_clip_with_layout` devuelve la decisión
  (`render.py:3575` `decision["output"] = output_path; return decision`), así
  que `layout.get("plantilla")` llega a los llamadores. **Matiz verificado:**
  hoy `mode="game"` cae al auto-scan y el no-cam da `"center"` (`:2502`),
  cuyo filtro es EL MISMO recorte 9:16 que `"game"` — los píxeles coinciden
  hoy cuando el escaneo no encuentra cara; lo que falta es el FORZADO (una
  cara espuria del juego aún puede voltear el tramo a split) y la etiqueta
  `"plantilla"` en la decisión.
- **I-25 (nuevo) — orden avatar vs `hits==1` sin especificar.** **Decisión
  v6: avatar DESPUÉS de ambiguo** (§4.3): caja → rescates → `hits==1`
  ambiguo → avatar → sin_webcam. Un voto único + avatar es evidencia
  contradictoria → ambiguo (M5: nunca sin_webcam sobre señal débil). El flag
  `avatar` para la validación es independiente de la fila (sale de
  `hay_avatar`), así que el clip ambiguo con avatar igual se valida con
  `avatar=True`.
- **I-26 (nuevo) — §6.3 incompleto.** **Decisión v6: contratos completos**
  (§6.3): firma de `_template_fullscreen_game` modelada en
  `_template_word_white(check, arrays, spans, recipe, face_result,
  vcfg=None)` (`final_review.py:1434`); `gameplay_puro` con fuente de
  fotogramas (los mismos `frames` del gate), submuestreo ≤8 repartidos y
  mitades y 0–840 / 840–1920 en gris vía `_sift_dup`; SFX factorizado a
  `_check_sfx` (hoy vive SOLO en la rama `word_white`, `:1186-1200` — la v5
  no lo decía); Gemini `webcam_visible` se retira para sin_webcam con entrada
  en `overridden`.
- **M-10 sigue abierto (parcial) — se RETIRA la extensión.** La v5 ordenaba
  extender `find_facecam_box_in_source` a `{"box","method","hits","samples"}`
  sin definir el retorno `None` ni inicializar `res`. **Decisión v6: NO se
  extiende** — `template_decision.py` lee `hits` directamente de
  `detect_vod_facecam` y esa es la única fuente; `find_facecam_box_in_source`
  conserva su contrato `{"box","method"} | None` y sus llamadores
  (`live_cutter.py:922`, `vod_fallback.py:719`, `remake_all.py:291`) no
  cambian.
- **M-11 sigue abierto:** `_layout_stub` (`test_final_gate.py:311`) aún no
  acepta `avatar` (verificado). **M-16 (nuevo): 186 t7 inliers 6 vs 3.**
  Re-medido por mí con el procedimiento del test
  (`_layout_check_arrays` sobre `frames/186_t7.jpg`): **inl=3, cells=3,
  share=0,00, FALLA** — el goldfish tiene razón; las otras 6 filas t7
  reproducen exactas (170: 14; 252: 61; 253: 7; 254: 77; 255: 1; 256: 5).
  La tabla §5.3 corrige 186 a inl=3 (veredicto y celdas no cambian).
  **M-17 (nuevo): derivas de citas** — `render.py` −16 líneas en la región
  `:2950–3625` (ver Referencias v6 con las líneas actuales); `load_config`
  `:534` (v5 `:551`); `load_validator_config` `:691` (v5 `:695`);
  `resolve_template` `:975` (v5 `:973`); parse `face_min_share` `:843`
  (v5 `:841`); "cero ocurrencias de `plantilla`" literalmente falso
  (aparece en comentarios de `publish/native-engagement.ts:25`,
  `publish/zernio-engagement.ts:171-172`, `evolution.ts:14,66` — lo cierto en
  sustancia es que no existe el CAMPO en `ViralData`/`parseViralInput`).

## v4 → v5 (qué cambió y por qué)

La v4 recibió **NO APROBADA** en su 4ª revisión goldfish (agente 18678322,
2026-11-27 01:48 UTC). La revisión **confirma la calibración v4 por
re-ejecución independiente** (186 PASA 6/0,85/0,475/0,167; 183 FALLA 35; 248
PASA 5; 244 FALLA 10; 186-t7 FALLA por (ii); submuestreo SIFT; 186 t37 16/14
no muestreado; 244 sistemático 8–12; clip 12 t1 90 / t37 71 / t7 10 en 3
celdas; 248 t17 9 en 3 celdas) y **resuelve B-N1/B-N2/B-N3/B-N4 en sustancia**,
pero bloquea por tres puntos (dos nuevos) + 10 importantes + 9 menores.
Verifiqué cada hallazgo en código antes de escribir esta v5 (no se acepta
ninguno por autoridad):

- **B-N7 (nuevo) — Fase 1 no podía producir ni validar un sin_webcam.**
  Verificado: `decide_layout` solo acepta forzados `("stack","pip","center")`
  (`render.py:2339-2346`) — `"game"` es un no-op; `face_gate` hace
  `recipe["cam"]` sin condición (`final_review.py:893`) y la receta del
  borrador no tiene `cam` → `KeyError`. **Decisión v5: §6.2 y §6.3 ENTRAN en
  Fase 1** (con sus definiciones inlinadas en la spec, §6.2/§6.3), porque el
  camino sin_webcam es la salida del usuario, no un extra. Fase 1 ya no
  promete el camino a medias.
- **B-N6 (nuevo) — la rama avatar no tenía cableado.** Verificado: 8 call
  sites de `validate_final_clip` (`live_cutter.py:975` y `:1424`,
  `vod_fallback.py:754` y `:904`, `workers.py:1278`, `viral_source.py:684`,
  `youtube_cut.py:562`, `facebook_cut.py:523`) y ninguno recibía `avatar`.
  **Decisión v5: cableado completo especificado** (§4.3/§4.4/§5.2): helper
  de muestreo `_sample_vtuber_frames`, caja ajustada desde `_cells` (sin
  margen) + escalado ×sw/160 (el mismo de `find_facecam_box_in_source`
  `:1681-1688`), y tabla por call site con el portador de `avatar=True`.
- **B-N5 (v3, SIGUE ABIERTO) — el tope perezoso no decía `break` o
  `continue` ni qué es "visitada".** Verificado el bucle
  (`vod_fallback.py:1465` quota `break`, `:1507` `vods_tried += 1`, `:1512`
  `lang`). **Decisión v5: §8.1 reescrita con pseudocódigo normativo**
  (`continue`, "visitada" = examinada por cualquier vía, relajación por
  conjunto de visitadas) + tests simétricos + generalización a otros
  `--max-vods` (M-14).
- **I-13 — el pre-chequeo no tenía punto de inserción en el primer corte.**
  Verificado: el corte live usa `cfg=self.cfg` directo
  (`live_cutter.py:1119`); el único `cfg2` es `dict(self.cfg)`
  (`:933`, superficial). **Decisión v5: un solo helper
  `prepare_cut_layout` (§4.2) con sus 7 puntos de inserción nombrados, y
  la exigencia `deepcopy` sustituida por `copy.deepcopy` en el helper con
  justificación de propiedad por clip** (ningún escritor anidado
  identificado en el árbol: `get_layout_config` copia la subsección
  `layout`, `cut_clip_with_layout` escribe `mode` en esa copia fresca).
- **I-14 — `face_min_share` es 0,25, no 0,5.** Verificado:
  `config.yaml:556` y `:649` (ambos bloques), `config.py:841`,
  `final_review.py:2113`, y el comentario del propio test
  (`test_layout_gate.py:190`). Los veredictos NO cambian; el margen de
  190 es 0,30 (no 0,05). Corregido en §1.2/§3/§5.1.
- **I-15 — números de celdas de capturas no reproducibles.** Verificado por
  re-medición propia con el procedimiento declarado (recorte +
  `cv2.resize` a 1080×1920, como hace el test en
  `test_layout_gate.py:216`): **canónico 331/57/22; recorte del test
  274/36/22** (reproducen exactamente los del goldfish). Las 6 variantes
  fallan por (i). Los números v4 (307/57/21, 257/43/22) venían de un resize
  PIL LANCZOS no declarado. §5.3 y el README usan los nuevos.
- **I-16 — tabla t7 incompleta (faltaban 253/254/255/256).** Medido por mí:
  253_t7 FALLA (7 celdas, (iv)), 254_t7 FALLA (34, (i)), 255_t7 FALLA (1,
  (iii)), 256_t7 FALLA (5, (iv)). **ESPERADO_T7 tiene 19 entradas, no 18**
  (el goldfish contó 18: se dejó la 256). La tabla v5 lista las 19
  (19/19 coinciden). **252 gana fila de conjunto completo** (medido por
  mí: FALLA, 220 inliers, 70 celdas, share 0,85, cx 0,609, área 0,202,
  (i)). `test_layout_gate.py` tiene 338 líneas (no 311 ni 317).
- **I-17 — área 0,35 vs 0,45 + función de posición inexistente.**
  Decisión v5: `validate_cam_box` adopta **área < 0,35** (la reconciliada;
  su 0,45 queda sustituido) y se nombra el helper compartido
  `_caja_en_esquina` (nuevo, en `yunet_facecam.py`) con la posición +
  `y0 < 0,50`.
- **I-18 — p7775 incompleta.** Verificado: las estrategias son tuplas
  `(nombre, cam_box, cam_square)`; el cuadrado es `365:365:90:626`
  (`rdjavi.py:77`); el consumidor (`remake_all.py:516`) no pasa
  influencer y el clip trae `influencer_name`/`influencer`
  (`remake_all.py:702-703`; D1: id 2 = Rubius). Decisión v5: orden
  detectada → ajustada → p7775 (Rubius) → default; el fallback
  "→ sin_webcam" NO es una estrategia (es la decisión §4.3); alcance:
  `box_strategies` solo la usa el re-make.
- **I-19 — `_game_filter` toma 2 args; `GAME_H` se usa en 4 sitios.**
  Verificado: firma `(game_crop, game_zoom)`; `GAME_H` en `:454` (escala
  sin zoom), `:463` (`y_coef`), `:466` (escala con zoom), `:467`
  (recorte final). Decisión v5: `game_h=GAME_H` (keyword nuevo) enhebrado
  por los 4 sitios.
- **I-20 — no hay bucle por tenant.** Verificado: el handler `scheduled`
  (`index.ts:4659`) es de un solo tenant (comentario `:4660-4665`,
  `tenant = requireTenant(env)` `:4667`). §8.2 corregido: "al inicio del
  handler, antes de `runWorkerCycle` (`:4687`)".
- **I-21 — el test de Burn rompería CI.** Decisión v5: `skipUnless` cuando
  los fixtures no están (misma convención que `test_layout_gate.py`);
  falla explícita solo con fixtures presentes y detección vacía.
- **I-22 — deriva de citas.** Todas las citas re-verificadas contra el
  árbol en el momento de escribir la v5 (baseline §9). Corregidas las
  sistemáticas: `live_cutter.py` −22/23 líneas; `config.yaml`
  (`validator:` segundo `:646`, `templates:` `:610`/`:743`,
  `layout_*` `:687-711`); `render.py` rama `game` `:3213`;
  `segment_filter_graph` `:3079`; `detect_vod_facecam` rescates
  `:3434`/`:3442`; `validate_final_clip` árbol `:2248`/`:2299`/`:2312`/
  `:2315` (HEAD `:1927`/`:1978`/`:1981`); `maybeDispatchDaily` árbol
  `:1854`, llamada final `:4722` (HEAD `:1779`/`:4624`); INSERT `:1968`
  en HEAD y árbol (el `:1949` HEAD de la v4 no existe); early-return
  `:1980` en HEAD y árbol (el `:1727` HEAD de la v4 no existe);
  POST parse/merge `:1487`/`:1566`, PATCH `:1315`/`:1317` (la v4 citaba
  `:1412`/`:1491`/`:1241`/`:1242`, desactualizados).
- **M-7–M-15** resueltos en su sitio (ver §9): re-validación `:975`;
  rangos de borrado 553–609 / 610–645 / 366–370; `_criterion` `:120`;
  parse `:857-868`; `face_gate` `:859-1111`; `safe_load` `:566`;
  corrección área `:2431`; import rubius `:94`;
  `clipagent/circle_render.py`; **M-10: se elige extender
  `find_facecam_box_in_source`** (retorno con `hits`/`samples` — tiene
  `res` en scope); `_layout_stub` gana `avatar=False`; **197 SIFTct02
  re-medido duradero** (t1/t3/t5 = 23/30/27, t7/t15/t25 = 4/1/6,
  `runs/layout-gate-calib/siftct02_197.txt` — los 21/29/26 de la v3 no
  estaban en ningún archivo durable); gancho center espejado (M-13);
  tests VOD simétricos + generalización (M-14); **§2 corregido (M-15):
  el flujo vivo SÍ alcanza `_game_filter`** vía `_split_rubius_graph`
  (`render.py:2946`, import `:2977`, llamado desde
  `segment_filter_graph` `:3110`).

**Mediciones nuevas en esta v5 (hechas por mí 2026-11-27 ~02:00-02:30):**
capturas con `cv2.resize` (331/57/22 y 274/36/22); 253/254/255/256 t7;
252 conjunto completo (70 celdas); 197 SIFTct02 re-medido (23/30/27).
Todo lo demás reproduce la calibración v4 (re-ejecutada por el goldfish).

## Alcance (5 piezas, en orden)

1. **Puerta en el validador = `layout_gate` (ya existe en el árbol)** — adoptarlo,
   corregir su calibración a la rejilla del gate, añadir el parámetro `avatar` y
   resolver 186 en su marco. La puerta caza la duplicación (SIFT en celdas) y exige
   que la zona de cámara sea mayoritariamente cámara (geometría de la cara), antes
   de encolar.
2. **Guardas de caja** en el corte (regla reconciliada única + `validate_cam_box` /
   `find_cam` / `_cam_box_valida`), para que no se vuelva a renderizar un duplicado.
3. **Formato «rubius sin webcam»** (gameplay a pantalla completa) como salida cuando no
   hay cámara real — **ENTRA EN FASE 1** (§6.2 modo forzado + §6.3 puerta, con
   definiciones inlinadas en esta spec, no solo en el borrador).
4. **Cajas de cámara por influencer** (Fase 2, después).
5. **VOD EN**: reservar 1 de 2 huecos del VOD-fallback para EN + reintento del dispatch
   diario (Fase 3).

## Fuera de alcance

- Publicar (lo hace otro agente en paralelo desde el chat).
- Borrar clips (lo gestiona el otro chat).
- Watcher en vivo de YouTube para Speed (trabajo aparte; Speed sigue siendo solo VOD).
- Piso 3/día (tiene su propia spec `docs/designs/SPEC-piso-3-por-dia-v2-2026-11-26.md`,
  aprobada por el usuario 2026-11-26 20:46, en implementación por otro agente).

## Acción inmediata (estado a 2026-11-27 ~02:30, verificado en D1 el 2026-11-26)

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
  HEAD b3f3095 (spec v4 + README v4).

## §1. Evidencia: la duplicación es real, por fotograma

### §1.1 Cómo se midió (2026-11-26 ~20:00-20:20, ampliado 23:50-02:30)

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
celdas (22). SIFTct02 queda como historial, no como método.

### §1.2 Tabla por clip (conjunto completo del gate, n=20 fotogramas, gate actual)

`cells` = máx. de celdas distintas SIFT sobre ≤8 fotogramas repartidos
(`layout_sift_max_frames: 8`); `share` = fotogramas con cara Haar / total;
`cx`/`area` = medianas de la cara mayor. Umbrales: celdas 8; share **0,25**
(`face_min_share`, `config.yaml:556` y `:649` — la v3/v4 decían 0,5, erróneo);
cx [0,45, 0,65]; área 0,15 (primer plano 0,12); corroboración 5 celdas.

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
| 252 EN | — | RECHAZAR (duplicado; medido en v5) | 70 | 0,85 | 0,609 | 0,202 | (i) duplicado |
| 170 ES | AuronPlay | **PASAR** (webcam ancha legítima; solo t7 medido) | 4 | 1,00 | 0,421 | 0,080 | — (iv) no dispara: 4 < 5 |
| Captura 1 | (usuario) | RECHAZAR (duplicado: caja = fotograma completo) | 331 | 1,00 | 0,219 | 0,035 | (i) duplicado |
| Captura 2 | (usuario) | RECHAZAR (duplicado: cara del juego) | 57 | 1,00 | 0,664 | 0,212 | (i) duplicado |
| Captura 3 | (usuario) | RECHAZAR (duplicado) | 22 | 1,00 | 0,659 | 0,370 | (i) duplicado |

Lecturas que importan:
- **186 PASA en ambos marcos** (v3 y `layout_gate` actual, medido en la rejilla del
  gate): celdas 6 < 8, share 0,85, cx 0,475 en rango, área 0,167 ≥ 0,15. Márgenes
  finos (celdas 6 vs 8; cx 0,475 vs 0,45; área 0,167 vs 0,15): cualquier
  recalibración debe re-verificar 186. El docstring de `layout_gate` («186 lo caza
  la cara», cx 0,41-0,44) es un artefacto de su rejilla t2/t7/t20; la v5 ordena
  re-anclar el docstring a la rejilla del gate (§5.5). **A t7 a fotograma único
  186 FALLA** (Haar no ve cara en ese único fotograma, share 0/1) — ver §5.3: son
  conjuntos de fotogramas distintos, no una contradicción.
- **183/184/189: la v3 los pasaba; la v4/v5 los rechazan.** El sangrado del borde
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
  t=1..5 (SIFTct02 re-medido en v5: 23/30/27 en t=1,3,5; t=7/15/25 correctos:
  4/1/6 — `runs/layout-gate-calib/siftct02_197.txt`); t=7/15/25 son correctos.
  Por eso la puerta NO salta los fotogramas de la ventana del banner: se evalúan.
  A fotograma único, t1/t3/t5 pasan (i)/(iv) (3/4/4 celdas; Haar ve un artefacto
  de juego a cx 0,116, área 0,012) — el clip se caza **a nivel clip** por (iv).
- **214 es el caso que solo caza (i) con margen:** su cara (del juego) está
  centrada (cx 0,522) y su área 0,116 < 0,15, así que (iv) también dispararía
  (96 ≥ 5) — pero (i) lo caza primero con 96 celdas.
- **252 (nuevo en v5):** FALLA por (i) con 70 celdas (220 inliers) en conjunto
  completo; su cara pasa (cx 0,609, área 0,202). Duplicado cinematográfico a
  otra escala, mismo defecto.
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
  **Caveat honesto (§4.3):** si el detector no devuelve candidato para la
  fuente de clip 12/244 (avatar no en esquina), la decisión es sin_webcam, no
  rubius — y el re-render sin_webcam es el que debe pasar la puerta (§6.3).
- **Márgenes del gate (honestos, con el umbral correcto 0,25):** limpios máx 6
  celdas (186); DUP mín 10 celdas (244); umbral 8 → margen 2 a cada lado.
  Corroboración (iv): 197 con 6 vs 5 (margen 1). Share de 190: 0,55 vs **0,25**
  (margen 0,30, no 0,05 como decía la v4). Son márgenes finos pero reales sobre
  el corpus medido; §10 fija los tests que los protegen.
- **Capturas (procedimiento canónico, v5):** recorte + `cv2.resize` a 1080×1920
  (como hace el test, `test_layout_gate.py:216`). Canónico: 331/57/22 celdas;
  recorte del test: 274/36/22. Las 6 variantes fallan por (i). Los números v4
  (307/57/21 y 257/43/22) venían de un resize PIL LANCZOS no declarado. Los
  fixtures pre-recortados dan 330/57/23 (±1 celda por re-codificación JPEG).

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

**El flujo vivo nunca llama a `rubius.build_filter`** (el entry de `rubius.py`,
usado por el driver de CircleCI `clipagent/circle_render.py` y el re-make
`remake_all.py:94`): `live_cutter.process_peak` → `workers.cut_clip`
(`workers.py:422`) → `_do_cut` (`:406`) → `render.cut_clip_with_layout`
(`render.py:3471`) → `decide_layout` (`:2320`) → `segment_filter_graph`
(`:3079`). **Pero el flujo vivo SÍ alcanza `_game_filter` de `rubius.py`**
(M-15: la afirmación de la v4 era falsa): el camino vivo llega a
`_split_rubius_graph` (`render.py:2946`, vía `segment_filter_graph` variante
rubius `:3110`) que importa `from .rubius import _game_filter`
(`render.py:2977`). El diagnóstico del duplicado no depende de esto, pero la
afirmación es falsa y §6.2 se apoya en ese import perezoso en el camino vivo.

## §3. Por qué el validador no lo cazó

`face_gate` (`final_review.py:859-1111`) exige una cara en ≥ `face_min_share`
(**0,25**, `config.yaml:556` y `:649` — la v3/v4 decían 0,5, erróneo) de las
muestras, ancho mínimo 0,08×1080 = 86 px. La camarita pequeña cuenta, la cara
del juego cuenta, el avatar cuenta: nunca mide qué fracción de la zona es cámara
ni si arriba ≠ abajo. `template_gate` (`:1112`) despacha solo por `subs_style`
(`fixed_yellow`/`karaoke`/`word_white`), no por nombre de plantilla.

**El árbol tiene la respuesta parcial:** `layout_gate`
(`final_review.py:1914`) + `_sift_dup` (`:1843`) + `_layout_check_arrays`
(`:2004`) + fallo duro `"layout"` en `validate_final_clip` (`:2334`) + llaves
`layout_*` obligatorias en `load_validator_config` (`config.py:769-773`). La v5
**adopta ese gate** (§5) en vez de construir uno paralelo: la tarea es
re-anclarlo a la rejilla del gate, añadir el parámetro `avatar` y resolver los
puntos que el propio gate deja abiertos (§4/§5.5). **No re-implementar
`layout_gate`**: Fase 1 se construye encima del estado actual del árbol.

## §4. El arreglo (Fase 1: Python/config + deltas sobre el árbol — §4.2/§4.3 adoptan lo existente)

### §4.1 Guardas de caja — una regla reconciliada, un solo sitio

Hoy existen **tres reglas distintas y contradictorias** sobre dónde puede estar la
caja (verificado por grep):
- `validate_cam_box` (`clipagent/yunet_facecam.py:365`): w 0,08-0,6; h 0,08-0,7;
  área <0,45; aspecto 0,5-2,0; **borde superior y > 0,35 → False**; cx debe ser
  <0,4 o >0,6 (no centrado); debe contener la cara completa (desempaquetar cara
  vacía → False).
- `_cam_box_valida` (`render.py:1923`): ancho `bw/w ≤ split_cam_max_w_frac` (0,45,
  `render.py:200`) Y área `(bw*bh)/(w*h) ≤ cam_max_area_frac` (0,35); usada en
  `:2387` (caja explícita), `:2575` (pip→split), `:2617` (center→split).
- `_detect_corner_box` (`render.py:656`): ancho 0,15-0,42 (línea 684), alto ≤0,45
  (línea 687).
- `decide_layout` nunca llama a `validate_cam_box` (el escaneo usa
  `classify_frame`/`_detect_corner_box` y la corrección área <0,45 en `:2443`).

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
- **Helper compartido de posición (I-17, nuevo):** `_caja_en_esquina(box_frac)`
  en `clipagent/yunet_facecam.py` — toma la caja en fracciones `[x0, y0, w, h]`
  (0..1) y devuelve bool con SOLO la posición + borde superior: centro fuera
  del rectángulo central (x 30-70% Y y 30-70% a la vez) Y centro-y < 0,70 Y
  `y0 < 0,50`. Es la única implementación de la regla de posición (un solo
  sitio, no dos copias).
- `validate_cam_box(box, face, require_containment=True)` (nuevo kwarg, I-3):
  con `require_containment=False` la cara se ignora por completo (se salta el
  desempaquetado — `validate_cam_box` desempaqueta `face` incondicionalmente
  hoy, `:365-375`); con `True` (default) la contención se exige cuando hay
  cara. La ruta de caja explícita de config (`facecam_box` en `decide_layout`,
  `:2347`) llama con `require_containment=False`. Test: caja explícita válida
  sin cara detectada pasa; con cara presente pero fuera, la contención no aplica.
- `validate_cam_box` adopta la regla reconciliada: posición vía
  `_caja_en_esquina` (sustituye su `y ≤ 0,35` y su `cx < 0,4 o > 0,6`);
  tamaño w 0,08-0,6 / h 0,08-0,7 / aspecto 0,5-2,0 (se mantienen); **área
  < 0,35 (cambia: su 0,45 queda sustituido por la reconciliada — I-17)**.
- `_cam_box_valida` (`render.py:1923`): mantiene su chequeo ancho <0,45 + área
  <0,35 y añade la posición vía `_caja_en_esquina` (import perezoso desde
  `yunet_facecam` — precedente viable: `render.py:3339` importa
  `yunet_facecam` perezosamente dentro de `_yunet_facecam_box`; no hay ciclo
  porque `yunet_facecam.py` no importa `render`). **Antes de llamar a
  `_caja_en_esquina`, la caja se convierte a fracciones** `[x0/w, y0/h, bw/w,
  bh/h]` (I-17 resquicio: el helper toma fracciones 0..1, `_cam_box_valida`
  recibe px). **Y gana el parámetro `avatar=False` (B-N6 — el mecanismo de la
  exención): con `avatar=True` se salta SOLO la posición** (ancho <0,45 +
  área <0,35 siguen aplicando). El flag llega por dos vías: (a) caja
  explícita — `decide_layout` lee `cfg.get("facecam_box_avatar", False)` en
  la rama `:2387`; (b) guardas pip→split / center→split (`:2575` / `:2617`) —
  `avatar=params.get("avatar", False)`, que la rama vtuber pone en `params`
  (§4.1 gancho del escaneo). Sin el flag, una caja avatar en esquina inferior
  sería rechazada por `y0 < 0,50` o centro-y ≥ 0,70 y se perdería.
- Gancho del escaneo (I-12): la regla de posición se aplica a la caja **antes de
  crear el pip** en la rama `s["cams"] == 1 and layout == "center" and s["boxes"]`
  de `decide_layout` (`render.py:2443-2452`, «corregido a pip por escaneo»,
  después de la corrección área <0,45 en `:2443`) y en la rama vtuber
  inmediatamente posterior (`if layout == "center" and s.get("vtuber")` → pip
  con `vt["box"]`, `:2458-2465`). Una caja humana que no pasa la regla no se
  convierte en pip: el segmento cae a `"game"` (ver §4.1.4). **La rama vtuber
  usa la caja AJUSTADA del candidato (bounding box de `_cells` sin el margen
  de 1 celda, §4.3.1), no `vt["box"]` (parche + margen), y pone
  `params["avatar"] = True`** — es el portador del flag hasta la guarda
  `:2575`.
- Gancho (b) — pip→split y center→split (I-6, M-13): en el árbol la guarda está
  en `:2575` (pip→split) y `:2617` (center→split) y **ambas degradan la caja
  inválida a `"center"`, no a `"game"`**. **Cambiar ambas a `"game"`** con
  `params={}` + motivo: la rama pip (`:2575`) hace hoy `r["layout"] =
  "center"; r["params"] = {}; r["reason"] += ...` — se cambia `"center"` por
  `"game"`; la rama center (`:2617`) hace hoy `continue` manteniendo
  `"center"` — se espeja: `r["layout"] = "game"; r["params"] = {};
  r["reason"] += (motivo); continue`. La guarda pip→split pasa
  `avatar=params.get("avatar", False)` a `_cam_box_valida` (la center→split
  no necesita el flag: su caja viene de `_face_cam_box`, cara humana). Y se
  actualizan los docstrings de `_cam_box_valida` (`:1923`) y `_split_params`
  (`:1957`), que hoy dicen «cae a center», y los comentarios de guarda de
  ambas ramas («el tramo queda en "center" (sin webcam) y el validador lo
  retiene»).

**§4.1.4 Invariante (B6 del borrador, normativa):** caja inválida o ausente →
`sin_webcam` o hold, **NUNCA** el fotograma completo como cámara, **NUNCA**
duplicado. La rama `"game"` (`render.py:3229`, recorte 9:16 centrado a pantalla
completa, sin webcam) es la salida; `"center"` sigue la cara y no sirve como
salida de degradado. (Matiz: el filtro de `"game"` y el de `"center"` son el
mismo recorte 9:16 — ver §6.2 —; la diferencia es la etiqueta: un tramo
degradado a `"game"` declara gameplay puro, uno en `"center"` declara que el
render siguió buscando cara.)

**§4.1.5 `box_strategies` — p7775 es una estrategia NUEVA (I-1, I-18):** hoy
(`remake_all.py:271`) son detectada → AJUSTADA (`_tight_box`, `:248`, centrada
en la cara mediana; docstring: corrige la caja demasiado ancha del refinado de
borde en cámaras oscuras — «Burn: la cámara real es ~1,8× la cara, no 2,8×») →
default (p3220). **`561:365:0:626` no aparece como estrategia**: solo como
ejemplo CLI (`rdjavi.py:77`) y en comentarios (`render.py:2806`). **v6 añade
la 4ª estrategia: p7775 (`561:365:0:626`, cuadrado `365:365:90:626` —
`rdjavi.py:77`), solo para segmentos de Rubius, en el orden detectada →
ajustada → p7775 → default** (p7775 ANTES del default para que se pruebe
dentro de `max_intentos=3`: el consumidor hace `strategies[:max_intentos]`,
`remake_all.py:516`). La función gana el parámetro `influencer_name=None`;
el consumidor pasa **`clip.get("streamer")`** — I-18: `load_blocked_clips`
(`remake_all.py:681`) construye el dict con solo `"streamer"` (de
`c.get("streamer") or c.get("influencer_name") or c.get("influencer")`,
`:702-703` son campos de la fila cruda `c`, no del clip construido, y el
backfill no las copia); `clip.get("influencer_name") or
clip.get("influencer")` es siempre None y p7775 nunca dispararía con ese
portador (la v5 lo citaba como si fueran campos del clip — erróneo). La
condición es nombre en minúsculas == `"rubius"` (D1: id 2, nombre "Rubius").
**El fallback "→ sin_webcam" NO es una estrategia:** `box_strategies`
siempre devuelve al menos el default (append incondicional, `:345`) y el
default p3220 siempre pasa las guardas (ancho 0,22 < 0,45; área 0,056 < 0,35),
así que "ninguna pasa las guardas" no se da nunca por esa vía; la salida
sin_webcam es la decisión §4.3 (sin caja válida → sin_webcam), tomada ANTES
del render, no tras agotar estrategias. **Alcance:** `box_strategies` solo
la usa el re-make (`remake_all.py`); los caminos live/VOD nunca la llaman,
así que p7775 no afecta a los cortes en vivo (el vivo usa el escaneo de
`decide_layout` + config explícita + el pre-chequeo §4.3).

**§4.1.6 Trazabilidad (B7 parcial):** la caja usada, su fuente y sus guardas se
registran en el cerebro (`cam_box`, `cam_box_source`, `cam_box_guardas` en el
registro del clip). En Fase 1 viven solo en el cerebro: el Worker las descarta
hasta Fase 3 (§6.4) y **no son diagnosticables desde D1 hasta entonces**; la
puerta del §5 no depende de ellas.

### §4.2 El modo viaja por call site + `_do_cut` (B-N2, B9, I-13, B-N8)

El mecanismo de la v2 (`cfg["layout"]["mode"]`) está **eliminado**: `cut_clip_with_layout`
(`render.py:3487`) hace `if layout_mode is not None: cfg["mode"] = layout_mode`
(`:3510-3512`) y los call sites pasan `layout_mode="auto"` explícito, así que
cualquier valor en config queda pisado. **El modo viaja por call site.**

**`prepare_cut_layout` NO se crea (B-N8 — la v5 lo prescribía, el árbol ya
resolvió el problema de otra forma).** El pre-chequeo existe hoy en cada camino
vía `decide_template_for_segment` (§4.3, `clipagent/template_decision.py`), y
cada call site ya aporta su `duration` (live `CLIP_DURATION_S`, VOD la duración
del segmento, youtube/facebook la del candidato, workers `payload["duration"]`,
auto_clips `_seg_dur`): el "duration sin especificar" de I-13 se disuelve
porque no hay helper común que lo necesite. Lo que queda por hacer en Fase 1
es (a) el canal de la caja en `_do_cut` (B-N2, sigue abierto) y (b) el flag
`avatar` en `_default_validate` (§4.3.2); el resto de I-13 está hecho en el
árbol.

**Canal de la caja (B-N2 — sigue abierto):** `workers._do_cut`
(`clipagent/workers.py:406-419`) hace `layout_cfg = (cfg.get("layout") or
{}).copy()` y pasa **solo** la subsección `layout` a `cut_clip_with_layout` —
el `facecam_box` top-level de `config.yaml` (`:450`) se pierde. El pre-chequeo
(`cfg2["facecam_box"]` en `_rerender_after_gate`, `live_cutter.py:935`) y el
corte por job (`_h_corte`, `workers.py:934`, corte `:972`) **pierden la caja
en `_do_cut`** — solo el VOD funciona (usa `get_layout_config` +
`cut_clip_with_layout` directo, `vod_fallback.py:756-765` y
`auto_clips.py:1600`). **Fix (un solo sitio, beneficia a todos los call
sites):** `_do_cut` usa `get_layout_config(cfg)` (`config.py:997`, fusiona el
`facecam_box` top-level en la config de layout en `:1010-1011`) en lugar de
`cfg.get("layout")`, y copia la caja por valor
(`layout_cfg["facecam_box"] = list(cfg["facecam_box"])` cuando existe: la
copia profunda vive en este borde — `cfg2` se pasa a `cut_clip`/
`subtitle_clip`/`apply_sfx` y su comportamiento de mutación no está
verificado; `get_layout_config` devuelve un dict fresco por llamada, y la
única escritura documentada es `cfg2["facecam_box"]` de nivel superior.
No hay escritor anidado identificado en el árbol; la copia es defensiva y
barata, no una exigencia derivada de un escritor concreto — I-13 mitad
deepcopy, resuelta como en la v5).

**Los 7 puntos reales (estado del árbol a 2026-11-27 ~03:00 — lo hecho se
adopta, lo que falta se marca):**
1. **Corte live** (`live_cutter.py:1044` `process_peak`): HECHO — la decisión
   corre en `:1173-1181` sobre `video_for_cut` antes del corte (`:1184`,
   `layout_mode="game"` si sin_webcam); `plantilla`/`plantilla_decidida` en
   locales para la validación `:1490`. Falta: nada en este punto (el flag
   `avatar` de la validación sale de `hay_avatar`, §4.3.2).
2. **Re-render live** (`_rerender_after_gate`, `live_cutter.py:897`): HECHO en
   lo estructural — decisión en `:948-951`, re-corte con
   `layout_mode="game" if sin_webcam else "auto"`, re-validación `:1021` con
   `plantilla=plantilla`. Falta: parámetro `avatar` en `_rerender_after_gate`
   y en la re-validación (§4.4), y la actualización de `record["plantilla"]`
   / `record["avatar"]` en el llamador (`:1511-1520` hoy solo actualiza
   `record["clip"]`, `package["clip"]`, `record["rerendered"]`).
3. **Corte VOD** (`auto_clips.py:1573-1578`, en el primer corte, NO en
   `vod_fallback.py:724-734` — I-13: esa es el re-render): HECHO — la
   decisión corre sobre el segmento descargado antes del corte (`:1600`,
   `cut_clip_with_layout`); `args.layout = "game"` y `plantilla_decidida`
   van al manifiesto. `--layout` ya admite `"game"` (`auto_clips.py:705`).
   Falta: nada en este punto.
4. **Re-render VOD** (`_rerender_vod_clip`, `vod_fallback.py:658`): HECHO en
   lo estructural — decisión en `:743-746`, re-corte con `layout_mode="game"
   if sin_webcam else "auto"`, re-validación `:785`, `review2["plantilla"]`
   en `:801`. Falta: parámetro `avatar` + actualización de `clip["avatar"]`
   en el llamador (`:950`) — ver §4.4.
5. **Cortes directos** (`youtube_cut.py:567` `_default_render`,
   `facebook_cut.py:528` `_default_render`): HECHO en lo estructural —
   `run_youtube_clips` (`:660`) decide en `:1006-1014` y
   `run_facebook_clips` (`:603`) en `:962-970`; `layout_mode` se enhebra en
   `_default_render`; `_default_validate` YA acepta `plantilla`
   (`youtube_cut.py:582`, `facebook_cut.py:543`) y la validación la pasa
   cuando el inyectable la acepta (`_validate_kw`, youtube `:1077`,
   facebook `:1033`). Falta: parámetro `avatar=False` en `_default_validate`
   y su enhebrado desde la decisión (mismo patrón `_validate_kw` — la guarda
   pasa a comprobar los dos kwargs, `plantilla` y `avatar`, y solo los pasa
   si el inyectable los acepta). El contrato inyectable no cambia:
   `render(seg_path, clip_path, dur, cfg)` y `validate(clip_path, title,
   seg_text, cfg)` se invocan posicionalmente; `plantilla`/`avatar` viajan
   solo por kwargs opcionales que el inyectable puede no aceptar.
6. **Corte por job** (`workers.py:934` `_h_corte`): HECHO en lo estructural —
   decisión en `:963-969`, corte `:972`, `plantilla_decidida` propagada en el
   resultado. Falta: `payload["avatar"]` para la validación `:1297` y el
   canal `_do_cut` (B-N2, arriba).
7. **Fuente viral** (`viral_source.py:678-679`): HECHO como DESCARTE — la
   decisión corre y sin_webcam/ambiguo retornan `None` con motivo
   (`:680-692`): este camino solo re-hace a rubius (`rubius_render` exige
   `cam_box`) y el formato sin webcam va por live/VOD. **La orden v5 §4.2.7
   (renderizar sin_webcam por `cut_clip_with_layout` en este camino) se
   RETIRA** — forkaría el renderer en el camino de re-make sin ningún
   consumidor que lo pida. La validación `:702` valida rubius, coherente con
   el descarte. El POST ya lleva la plantilla (`payload["viral"] =
   {"plantilla": plantilla}`, `:790`).
`config.yaml:385` (`mode: "auto"`) gana `game` en su comentario como valor
forzado (no configurable). `DEFAULTS["mode"]` (`render.py:187`) gana `"game"`
en su lista de forzados (ver §6.2).

### §4.3 Decisión de plantilla por segmento (B10, B-N4, B-N6, M-10, B-N8, I-25)

**Una sola función de decisión, y ya existe: `decide_template_for_segment(
segment_path, duration, cfg=None, detect_fn=None, avatar_fn=None)` en
`clipagent/template_decision.py`** — la prescribe la v5 como función NUEVA en
`final_review.py`; el árbol ya la tiene en `template_decision.py` con 8 call
sites (B-N8). **Decisión v6: se queda en `template_decision.py` de forma
permanente** (moverla removería 8 call sites sin ganancia conductual) y
`final_review.py` NO la re-exporta. Lo que cambia es su CONTRATO DE RETORNO
y el orden avatar/ambiguo; la lógica de detección no cambia.

**Retorno normalizado — siempre una 4-tupla `(plantilla, caja, estado,
avatar)`:**
- `plantilla`: `"rubius"` o `"rubius_sin_webcam"`.
- `caja`: la caja en px del vídeo fuente, o None.
- `estado`: `"firme"` o `"ambiguo"`.
- `avatar`: bool — True si se encontró candidato avatar en este segmento
  (independiente de la fila: también se calcula en la fila ambigua, I-25).
- Las tuplas de aridad variable de hoy — `("rubius", caja)`,
  `("rubius_sin_webcam", None)`, `("rubius", None, "ambiguo")` — se
  RETIRAN: todos los call sites desempaquetan 4 valores
  (`live_cutter.py:948-951` y `:1175-1177`, `workers.py:963-965`,
  `viral_source.py:678-679`, `vod_fallback.py:743`, `youtube_cut.py:1006-1014`,
  `facebook_cut.py:962-970`, `auto_clips.py:1573-1578`) y los chequeos
  `len(_dec) == 3 and _dec[2] == "ambiguo"` se sustituyen por
  `_dec[2] == "ambiguo"`. `test_template_decision.py` se actualiza al
  contrato nuevo. Razón del 4º elemento: el flag `avatar` sale de UN solo
  escaneo dentro de la decisión; con una 3-tupla + llamada separada a
  `hay_avatar` cada clip escanearía dos veces lo mismo.

**Fuente de verdad de `(facecam_box, hits)` (M-10 — RETIRADA la extensión de
la v5):** la decisión llama a `detect_vod_facecam` directamente
(`render.py:3400`) y lee `hits`/`facecam_box` de su retorno — como hace hoy
`template_decision.py`. `find_facecam_box_in_source`
(`final_review.py:1667`) **NO se extiende**: conserva su contrato
`{"box", "method"} | None` y sus llamadores (`live_cutter.py:922`,
`vod_fallback.py:719`, `remake_all.py:291`) no cambian. La v5 ordenaba
extenderla sin definir el retorno `None` ni inicializar `res` (M-10); con la
decisión viviendo en `template_decision.py`, la extensión no hace falta.
El orden interno de `detect_vod_facecam` es voto → bordes → YuNet (rescates
con `hits: 1` CON caja); el externo de `find_facecam_box_in_source` es
voto → YuNet → bordes; los rescates externos de la decisión (YuNet directo
→ bordes fijos) siguen el orden externo.

Tabla de decisión (normativa — el orden de filas ES el orden de evaluación):

| Entrada | Decisión |
|---|---|
| `facecam_box` presente (voto ≥2 o rescate) | `("rubius", caja, "firme", False)` (§4.1) |
| caja ausente + `hits==1` (sin rescate) | `("rubius", None, "ambiguo", avatar)` — nunca sin_webcam; sin caja forzada, auto-scan (el flag `avatar` se calcula igual: si hay avatar, la validación lo sabe) |
| caja ausente + `hits==0` + sin avatar | **`("rubius_sin_webcam", None, "firme", False)`** |
| candidato avatar + sin cara humana | `("rubius", None, "firme", True)` — la caja la pone el render (§4.3.1), no la decisión |
| caja presente pero inválida (§4.1) | sin_webcam o hold (§4.1.4), nunca duplicado |

**I-25 (orden avatar vs `hits==1`): avatar DESPUÉS de ambiguo.** Un voto
único + candidato avatar es evidencia contradictoria (¿camarita pequeña o
avatar?): ambiguo, nunca sin_webcam (M5). El flag `avatar` independiente
garantiza que el clip ambiguo con avatar se valide con `avatar=True` aunque
la decisión no fuerce caja.

**§4.3.1 Avatar (B-N6 — cableado completo, sin caja en la decisión):**
- **Muestreo:** NO hay helper nuevo `_sample_vtuber_frames` (la v5 lo
  prescribía): la decisión usa `_scan_frames` (`render.py:2053`) vía
  `hay_avatar` — la primitiva existente `_hay_avatar` de
  `template_decision.py` se hace PÚBLICA con ese nombre
  (`hay_avatar(segment_path, duration, cfg=None)`), misma implementación
  (anotación `vtuber` por muestra del escaneo). Nunca levanta.
- **Detección:** `_detect_vtuber_candidates(frames, cfg)` (`render.py:973`)
  sobre esos fotogramas. Se toma el candidato de mayor `present_frac` (la
  función ya filtra por `present_frac ≥ vtuber_present_frac` (0,20),
  racha ≥ 4, textura y esquina). La rama avatar corre **solo cuando no hay
  caja** (sin cara humana en ninguna vía: voto, YuNet, bordes).
- **La caja avatar la pone EL RENDER, no la decisión:** la rama vtuber de
  `decide_layout` (`render.py:2458-2465`) convierte el candidato en pip
  (luego split) y usa la **caja ajustada** — bounding box del cluster activo
  SIN el margen de 1 celda, desde `_cells` (celdas del cluster, en el dict
  devuelto): `[min(cs)*CELL, min(rs)*CELL, (max(cs)-min(cs)+1)*CELL,
  (max(rs)-min(rs)+1)*CELL]` en px de muestra 160×90, escalada ×sw/160,
  ×sh/90 (el mismo escalado que `find_facecam_box_in_source` hace para la
  caja detectada, `final_review.py:1681-1688`). Hoy esa rama usa `vt["box"]`
  (parche + margen, `render.py:1001-1009`): **cambiarla a la caja ajustada**
  y poner `params["avatar"] = True` (portador del flag hasta la guarda
  `:2575`, §4.1). Es el análogo exacto de `_tight_box` para caras (que toma
  una CARA, no una caja de candidato, y por eso no es aplicable tal cual).
- **Guardas para la caja avatar:** la regla de posición §4.1 **NO se aplica
  a las cajas de candidato avatar** (vía `avatar=True` en `_cam_box_valida`,
  §4.1): su validez la da el detector (esquina obligatoria + área
  0,03–0,35 + `present_frac` + racha + textura). Solo se aplican los topes
  de tamaño: ancho < 0,45 (`split_cam_max_w_frac`) y área < 0,35. Un avatar
  en esquina inferior NO lo tumba el tope `y0 < 0,50` (que es para
  facecams humanas: minimapa/HUD).
- **Si el detector no devuelve candidato:** la tabla aplica sin avatar
  (`hits==0` → sin_webcam). **Caveat para clip 12/244:** si su fuente no
  da candidato (avatar no en esquina), la decisión es sin_webcam y el
  re-render sin_webcam es el que debe pasar la puerta (§6.3) — no se
  inventa una caja.
- **`avatar_share_min = 0,20` eliminado** (v4): no hay primitiva de píxeles
  reproducible; la varianza temporal está refutada (§1.2).

**§4.3.2 Transporte de `avatar=True` (B-N6(3) — tabla por call site):**
`validate_final_clip` gana el parámetro `avatar=False` y lo enhebra en
`face_gate` (I-23, §5.2) y en la llamada a `layout_fn` / `layout_gate`
(`final_review.py:2312`); `_layout_stub` (`test_final_gate.py:311`) gana
`avatar=False` (M-11). Cada call site pasa el flag desde la 4-tupla de la
decisión que produjo el clip (4º elemento):

| Call site | Portador de `avatar` |
|---|---|
| `live_cutter.py:1490` (primera validación live) | locales de `process_peak` (la decisión corrió en `:1173`) |
| `live_cutter.py:1021` (re-validación live) | parámetro `avatar` nuevo de `_rerender_after_gate`; el llamador (`:1511`) pasa el local de `process_peak`; la decisión §4.4 puede actualizarlo |
| `vod_fallback.py:939` (primera validación VOD) | `clip.get("avatar", False)`; el corte VOD lo fija en `clip["avatar"]` |
| `vod_fallback.py:785` (re-validación VOD) | `clip.get("avatar", False)`; `_rerender_vod_clip` actualiza `clip["avatar"]` desde su decisión |
| `workers.py:1297` (validación del job) | `payload.get("avatar", False)`; `_h_corte` lo fija en `payload["avatar"]` |
| `viral_source.py:702` (validación viral) | local de la decisión (`:678`); siempre False en la práctica (el camino descarta sin_webcam/ambiguo y la caja humana no es avatar) |
| `youtube_cut.py:1077` / `facebook_cut.py:1033` (`_default_validate`) | parámetro nuevo `avatar=False`; `run_youtube_clips` (`:660`) / `run_facebook_clips` (`:603`) lo enhebran desde la 4-tupla de la decisión |

Sin `avatar=True`, un clip 12/244 re-renderizado con la caja del candidato se
validaría con `avatar=False` y `face_gate` lo tumbaría (244: share 0,05 < 0,25,
I-23) y la regla (ii) del layout igual — por eso la aceptación §10.4 exige el
flag en la re-validación Y medir el re-render real, no afirmar su pase.

### §4.4 Re-render tras la puerta (B-3, I-5)

`_rerender_after_gate` (`live_cutter.py:897`, llamado desde `:1511` cuando
`final_review.get("needs_rerender")`). **Estado actual verificado (la v5 lo
describía con el árbol viejo — el árbol ya cambió):** hoy TIENE la rama de
rescate de layout: si `"cara"` está en `hard`, busca `face_box` con
`find_facecam_box_in_source` (`:922`); si no hay caja y hay fallo de layout,
corre `decide_template_for_segment` (`:948-951`): sin_webcam → `sin_webcam =
True`, `plantilla = "rubius_sin_webcam"`; ambiguo → revisión humana; con caja
→ `face_box`. El re-corte pasa `layout_mode="game" if sin_webcam else "auto"`
(la v5 decía que siempre pasaba `"auto"` — falso en el árbol actual); la
re-validación en `:1021` pasa `plantilla=plantilla` (la decidida) pero NO
pasa `avatar` (no existe el parámetro); `cfg2 = dict(self.cfg or {})` en
`:933` con la única escritura `cfg2["facecam_box"] = face_box` en `:935`.

**Cambios (normativos — solo los deltas que quedan):**
- La función gana el parámetro `avatar=False` y la re-validación `:1021`
  pasa `avatar=avatar`. El avatar sale de la 4-tupla de la decisión §4.3
  (4º elemento); cuando la rama es rubius-con-caja-avatar, `avatar=True`.
- **"cara" + sin cara en la fuente:** la decisión §4.3 manda TAMBIÉN cuando
  NO hay fallo de layout — hoy, sin `layout_fallo`, `"cara"` + sin caja va
  directo a revisión humana sin decidir. **Cambio:** si `"cara"` está en
  `hard` y `find_facecam_box_in_source` no devuelve caja, se corre la
  decisión §4.3 siempre (no solo con `layout_fallo`): si es sin_webcam
  (`hits==0`, sin avatar) → rama sin_webcam (re-corte `"game"`); si es
  ambiguo (`hits==1`) → revisión humana sin re-corte (como hoy); si trae
  caja → rubius con caja (como hoy). Esto CAMBIA el comportamiento actual
  para el caso `hits==0` sin fallo de layout: hoy va directo a revisión
  humana sin re-cortar.
- **`record["plantilla"]` se actualiza desde `review2["plantilla"]`:**
  el llamador (`:1511-1520`) hoy actualiza solo `record["clip"]`,
  `package["clip"]`, `record["rerendered"]`. Tras el re-render:
  `plantilla = final_review.get("plantilla", plantilla)` y
  `record["plantilla"] = plantilla`; además `record["avatar"] =
  <avatar usado en la re-validación>` (solo registro, no va al Worker).
  `validate_final_clip` devuelve `"plantilla"` (la receta validada).
  El `quality_gate` derivado SÍ se reconstruye tras el re-render
  (existente) — el bug es `record["plantilla"]`, no el quality_gate.
- **Equivalente VOD (I-5b):** `_rerender_vod_clip`
  (`vod_fallback.py:658`, re-validación `:785`) gana el parámetro `avatar`
  y la misma lógica de decisión; el llamador (`:950`) actualiza
  `clip["plantilla"] = final_review.get("plantilla", plantilla)` y
  `clip["avatar"]` tras la llamada (hoy `review2["plantilla"]` se fija en
  `:801` pero el llamador no actualiza `clip["avatar"]` — no existe esa
  clave en el manifiesto — y el POST VOD (`viral_payload`, `:973`) usa
  `clip["plantilla"]`). El payload viral del POST usa `clip["plantilla"]`
  — por eso el llamador debe actualizarlo.
- El rescate se dispara también con `"layout"` en `hard` (nuevo fallo duro
  de `layout_gate` — ya implementado en el árbol: `layout_fallo` en
  `:944-946`).

### §4.5 Invariante del render (normativo)

Una sola medida de ancho: `bw / SAMPLE_W < split_cam_max_w_frac` (0,45;
`render.py:200`, aplicado en `_split_params` `:1957`). La corrección del escaneo
(`render.py:2443`, área <0,45) se unifica a la misma regla de ancho. §4.1 añade
el área <35% de `_cam_box_valida` como **segunda** condición de render (no como
sustituto del ancho). La cita de la v2/v3 que decía «`split_cam_max_w_frac` es
área» era errónea: es ancho.

## §5. La puerta del validador = `layout_gate` (B-N1 — adopción, no reemplazo)

**Decisión v4/v5: la puerta ES `layout_gate`, que ya existe en el árbol**
(`clipagent/final_review.py:1914`, `_sift_dup` `:1843`, `_layout_check_arrays`
`:2004`, `validate_final_clip` `:2160` con `layout_fn` inyectable `:2164`, fallo
duro `"layout"` `:2334`). No se construye un segundo gate. La v5: (a) adopta sus
4 reglas y sus llaves como normativa; (b) corrige su docstring de calibración a
la rejilla del gate; (c) añade el parámetro `avatar` con su cableado completo
(§4.3.2); (d) fija las tablas de calibración medidas (§5.1-§5.3); (e) resuelve
los puntos que el gate deja abiertos (186, 183/184/189, captura 3, muestreo SIFT).

**SIFTct02 (v3) queda retirado como método.** Sus números (CORRECTO máx 18,
DUP mín por clip 29, umbral 20; 197 re-medido en v5: 23/30/27 en t=1,3,5 y
4/1/6 en t=7,15,25) quedan en §1.1/Referencias como historial. La
estadística que shippea es **celdas distintas**, no buenos matches.

### §5.1 El método (código existente, normativa)

`_sift_dup(cam_gray, game_gray)` (`:1843`) devuelve `(inliers RANSAC, celdas
distintas de destino)` — celdas en rejilla de 20 px en la zona de juego.
Racional (docstring): los inliers solos engañan cuando muchos matches colapsan
en 1-2 puntos (clip 170: 15 inliers en 4 celdas, texto del encabezado de Twitch
vs barandilla del juego). La duplicación real reparte los inliers por la escena
(252: 62 inliers en 22 celdas a t7 — 70 en conjunto completo; 199: 602 en 284).
SIFT nfeatures=2000 (con 800, 252 daba 4-7 y se escapaba; con 2000 da 33-71 y
los limpios se quedan ≤4), Lowe 0,75, RANSAC 5,0. `_sift_dup_inliers` (`:1907`)
es un wrapper de compatibilidad que devuelve solo inliers («los tests
sintéticos lo usan directamente») — no es la estadística de la puerta.

`layout_gate(clip_path, vcfg, recipe, frames=None, out_dir=None,
template_name="rubius")` (`:1914`): aplica solo a rubius (otras plantillas →
`pasa=True`, «no aplica»). Usa `extract_gate_frames` (t=1,3,5,…). Devuelve
`pasa, motivo, duda, sift_max_inliers, sift_max_cells, face_share,
face_cx_mediana, face_area_mediana, frames_total, frames_con_cara`. Nunca
lanza excepción: sin fotogramas/cv2/SIFT/Haar FALLA ruidosamente, nunca con un
valor inventado. Llamada desde `validate_final_clip` (`:2312`) con
`layout_fn` inyectable (mismo patrón que los detectores de cara y Gemini; stub
`_layout_stub` en `test_final_gate.py:311`).

`_layout_check_arrays(arrays, vcfg, recipe, template_name)` (`:2004`): el
núcleo, testeable directamente sobre fotogramas. Cuatro reglas, en orden:

- **(i) Duplicación:** `sift_cells_max >= layout_dup_sift_cells` (8,
  `config.yaml:687`) → FALLA «layout duplicado». SIFT sobre como mucho
  `layout_sift_max_frames` (8, `:711`) fotogramas repartidos; manda el máximo.
  Submuestreo para n=20: índices {0,3,5,8,11,14,16,19} → t ∈ {1,7,11,17,23,29,
  33,39} (reparto uniforme sobre los 20). El máximo se toma sobre el
  subconjunto, no sobre los 20.
- **(ii) Sin cara estable:** `share < face_min_share` (**0,25**,
  `config.yaml:556` y `:649` — la v3/v4 decían 0,5, erróneo) → FALLA «sin cara
  estable».
- **(iii) Cara de primer plano desplazada:** cx fuera de [0,45, 0,65] Y área ≥
  `layout_face_area_closeup_min` (0,12, `:705`) → FALLA «cámara contaminada»
  (sin corroboración SIFT: una cara grande desplazada basta).
- **(iv) Cara desplazada o pequeña CON corroboración:** `cara_anomala` (cx
  fuera de rango O área < `layout_face_area_min` 0,15, `:701`) Y
  `sift_cells_max >= layout_face_sift_cells_min` (5, `:692`) → FALLA.

Llaves en `config.yaml:687-711` (todas obligatorias en `load_validator_config`,
`config.py:769-773` y parseadas en `:857-868`): `layout_dup_sift_cells: 8`,
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
§4.1.6 registra la caja (`live_cutter.py:1345`); (b) no es evaluable por el
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
  no-rubius) — **pero la regla (i) SIFT sigue aplicando**. El transporte de
  `avatar=True` hasta cada `validate_final_clip` está en la tabla §4.3.2 (los
  7 call sites). **`face_gate` TAMBIÉN gana `avatar: bool` (I-23): con True
  devuelve `{"pasa": True, "method": "avatar", "motivo": "avatar reactivo por
  decisión §4.3: Haar no aplica", "share": None, ...}` sin leer
  `recipe["cam"]` para la cara** — medido: clip 244 FALLA `face_gate` real
  (`method=facecrop`, share 0,05, 1/20 fotogramas) mientras clip 12 lo PASA
  (`method=yunet`, share 1,0, 20/20); sin esta exención el 244 re-renderizado
  seguiría fallando la puerta dura "cara" aunque el layout fuese correcto.
  Los dos centinelas con `share: None` exigen blindar el formato
  `f"{face.get('share', 0):.0%}"` de la rama de override de Gemini en
  `validate_final_clip` (`:2354-2360`): usar `(face.get("share") or 0)` —
  con la clave presente y `None`, `.get(..., 0)` devuelve `None` y el
  formateo levanta `TypeError`.

### §5.3 Calibración (rejilla del gate, gate actual — tabla normativa)

**Conjunto completo (n=20 fotogramas por clip):** la tabla de §1.2 ES la tabla
de calibración. Resultado: 5 limpios pasan (186, 190, 246, 248), 11
duplicados/contaminados fallan (183, 184, 189, 191, 197, 199, 214, 244, 12,
247, 252), 170 pasa (t7), las 3 capturas fallan. **Separación completa a nivel
clip.**

**Fotograma único t7 (regresión de `test_layout_gate.py`, 19/19 coinciden con
`ESPERADO_T7` — la v4 listaba 15; el mapa tiene 19):**

| Clip | Pasa | inl | cells | share | cx | área | Regla |
|---|---|---|---|---|---|---|---|
| 170 | ✓ | 14 | 4 | 1,00 | 0,421 | 0,080 | — |
| 183 | ✗ | 34 | 19 | 1,00 | 0,435 | 0,161 | (i) |
| 184 | ✗ | 38 | 27 | 1,00 | 0,414 | 0,114 | (i) |
| 186 | ✗ | 3 | 3 | 0,00 | — | — | (ii) sin cara en ese fotograma |
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
| 253 | ✗ | 7 | 7 | 1,00 | 0,469 | 0,098 | (iv) cara + corroboración |
| 254 | ✗ | 77 | 34 | 0,00 | — | — | (i) |
| 255 | ✗ | 1 | 1 | 1,00 | 0,405 | 0,182 | (iii) primer plano desplazado |
| 256 | ✗ | 5 | 5 | 1,00 | 0,301 | 0,057 | (iv) cara + corroboración |

Notas t7: 253 es TEMPORAL (el bug solo está en los primeros ~5 s; a t7 lo caza
(iv) con 7 celdas; su máximo entre fotogramas es 69). 255 es TEMPORAL (8 celdas
solo a t4, justo en el umbral; a t7 lo caza (iii) con 1 celda — la cara de
primer plano desplazada no necesita corroboración). 189 a t7 tiene 4 celdas y
su máximo es 9 a t20. El test `test_t7_cada_clip` (`test_layout_gate.py:143`)
itera TODAS las entradas del mapa y exige `{clip}_t7.jpg` para cada una.

**186 en t7 vs en conjunto completo — no es contradicción (decisión en un solo
sitio):** a t7 Haar no ve cara en ese único fotograma (share 0/1 → (ii));
en los 20 fotogramas ve cara en 17 (share 0,85) con cx 0,475 y área 0,167.
La tabla t7 usa 1 fotograma; la tabla §1.2 usa 20. El test de regresión
(`test_layout_gate.py`) usa t7; el gate en producción usa el conjunto completo.
Ambas tablas son normativas para su conjunto de fotogramas. **La v2/v3
etiquetaban 186 de forma distinta según la sección; la v4/v5 lo deciden aquí
y solo aquí: 186 PASA en producción (conjunto completo), FALLA a t7 (un solo
fotograma sin cara).**

**Muestreo SIFT — limitación documentada (no un bug):** 186 tiene un fotograma
no muestreado con 14 celdas (t37) — si el reparto incluyese t37, 186 fallaría
(i). El submuestreo a 8 fotogramas es un compromiso de coste (~20-30 s/clip);
el test y la producción usan el mismo reparto, así que el test sí prueba el
gate. Si un día se sube `layout_sift_max_frames`, 186 debe re-verificarse (su
margen de celdas es 6 vs 8). Lo mismo para 244 (10 vs 8) y 197 (corroboración
6 vs 5) en el otro sentido.

**Capturas del usuario — procedimiento canónico (I-15, declarado por fin):**
screenshot crudo → recorte → **`cv2.resize` a (1080, 1920)** (exactamente lo
que hace el test, `test_layout_gate.py:216`). Recorte canónico
(107,431)-(1182,2363): **331/57/22 celdas**. Recorte del test (`CAPTURA_VIDEO`,
filas 66-2376, cols 95-1195): **274/36/22 celdas**. Las 6 variantes fallan por
(i). (Los fixtures pre-recortados dan 330/57/23 — ±1 celda por re-codificación
JPEG; el procedimiento canónico es desde el screenshot crudo.) **La limitación
de la v3 (captura 3 no se cazaba, SIFTct02 17 < 20) desaparece: con celdas,
captura 3 da 22 ≥ 8.**

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
   `validate_final_clip` (§5.2 + tabla §4.3.2) **y en `face_gate`** (I-23:
   con `avatar=True` devuelve `pasa=True, method="avatar"` sin medir cara),
   con el blindaje del formato de `share` en la rama de override de Gemini.
2. Re-anclar el docstring de calibración a la rejilla del gate (§5.3).
3. Nada más: no se tocan los umbrales (8/5/0,45-0,65/0,15/0,12/8) — la
   calibración §5.3 los valida sobre el corpus medido. **Corrección M-16:**
   186 t7 es inl=3 (no 6): re-medido por mí y por el goldfish con
   `_layout_check_arrays` sobre `frames/186_t7.jpg` (veredicto y celdas no
   cambian).

## §6. Formato «rubius sin webcam» (adopta el borrador revisado, con B7–B11 inlinadas — ENTRA EN FASE 1)

Se adopta el borrador `runs/formato-rubius-sin-webcam-2026-11-26/SPEC.md`
(REVISADA, 2ª ronda de corrección, 454 líneas) **con sus correcciones inlinadas
aquí** — no se depende del archivo del borrador (vive fuera del repo). Las
divergencias respecto al borrador son dos y están decididas aquí:

- **186 PASA** (no FAIL): el borrador lo etiquetaba FAIL por `cam_pura`. La
  decisión es §1.2/§5.3 (PASA en conjunto completo). El arreglo del sangrado es
  snap de caja (§4.1), no rechazo del render.
- **NCC por celdas NO se adopta** (medido: 0 coincidencias en todos los
  fotogramas). La señal de duplicación es SIFT en celdas (§5.1).

**B-N7: §6.2 y §6.3 ENTRAN en Fase 1** (en la v4 quedaban fuera y Fase 1 no
podía producir ni validar un sin_webcam). Sus definiciones están inlinadas
abajo — un implementador de Fase 1 no necesita salir de esta spec.

### §6.1 Receta y config (B11)

Receta `rubius_sin_webcam` en el segundo bloque `templates:` de `config.yaml`
(la efectiva). **Claves exactas (normativo — la receta NO tiene `cam`):**
`game: {y0: 0, y1: 1920}` (gameplay a pantalla completa vertical 1080×1920);
`title_banner: {x0: 103, x1: 967, y0: 811, y1: 1099}` (misma geometría que
rubius — la posición del banner en sin_webcam es pregunta abierta §11.1:
y 811-1099 vs arriba ~100-388); `subs_zone: {y0: 900, y1: 1020}` (misma que
rubius); `subs_style: word_white`; `subs_over_game: true`;
`cam_full_width: true`; `blurred_bg: false`. Sin `cam`, sin `facecam_box`.
Son EXACTAMENTE las claves de `TEMPLATE_REQUIRED_KEYS["rubius_sin_webcam"]`,
que **ya existe en el árbol** (`config.py:657`): (`game`, `title_banner`,
`subs_zone`, `subs_style`, `subs_over_game`, `cam_full_width`,
`blurred_bg`). La v5 prescribía claves (`banner`, `subs`, `sfx`,
`dimensiones`) que contradicen el esquema real y no pasan
`load_validator_config` — retiradas. **Corrección B-N7b:** la
`TEMPLATE_REQUIRED_KEYS` de la v5 ("definida dos veces `:378`/`:664`,
consolidar en una") era falsa: hay UNA sola definición (`:657`; las líneas
`:378`/`:664` no existen). No se consolida nada.
**`TEMPLATE_NAMES` (B-N7c):** `config.py:639` gana `"rubius_sin_webcam"` **EN
EL MISMO CAMBIO que la receta en `config.yaml`** — `load_validator_config`
construye `vcfg["templates"]` solo para esos nombres (`:807-813`) y sin el
alta `resolve_template(vcfg, "rubius_sin_webcam")` levanta `ConfigError`
(reproducido por el goldfish). El comentario del árbol (`:640-645`) ya dice
que el alta va con la receta. El loader ya salta el chequeo de `cam` cuando
la receta no la lleva (`:824-827`: la autoridad es `TEMPLATE_REQUIRED_KEYS`,
no el nombre) — no hay que tocar el loader.
`RECIPE_TEXT` gana la entrada `rubius_sin_webcam` (`final_review.py:120`; hoy
cae al fallback rubius en `:1740-1742`). El docstring de `validate_final_clip`
(`final_review.py:2183`, «una de TRES recetas») pasa a «CUATRO recetas».

**Limpieza de duplicados (B11, M-8):** `config.yaml` tiene `validator:` dos veces
(553 y 646), `templates:` dos veces (610 y 743) y **`sfx:` dos veces (366 y
379)**. `yaml.safe_load` (`config.py:566`, dentro de `load_config` `:534`)
toma la última en silencio. Efectivos: `validator:` 646, `templates:` 743.
**Borrar 553–609, 610–645 y 366–370** (bloques reales verificados: el primer
`validator:` va de 553 a 609, el primer `templates:` de 610 a 645 — las líneas
600–609 y 640–645 son solo comentarios; el bloque `sfx:` 366–369 + línea en
blanco 370; el comentario Layout 371–378 se conserva). La primera `validator:`
carece de `rubius_subs_outline_frac: 0,03` (solo en la segunda, `:677`).
**Test guarda:** ninguna llave de nivel superior duplicada en `config.yaml`
(robusto al baseline — los rangos son del árbol actual).

### §6.2 Render sin webcam (B9, I-7, I-19, M-4, M-5) — EN FASE 1

- **Modo forzado `"game"` en `decide_layout` (B-N7(1)):** `decide_layout`
  (`render.py:2336`) hoy solo acepta forzados `("stack", "pip", "center")`
  (`:2355-2360`) y `"game"` es un no-op. **Cambio:** la tupla pasa a
  `("stack", "pip", "center", "game")`; cuando `forced == "game"`, los
  segmentos son `[{"start": 0.0, "end": duration, "layout": "game",
  "params": {}, "reason": "forzado sin_webcam"}]` para toda la duración
  (con `apply_game_voice` igual que los demás forzados cuando hay
  `game_voice`); la decisión devuelta gana `"plantilla":
  "rubius_sin_webcam"` (el dict devuelto hoy es `{"mode", "segments",
  "samples"}` y `cut_clip_with_layout` lo devuelve tal cual —
  `render.py:3573` `decision["output"] = output_path; return decision` —
  así que `layout.get("plantilla")` llega a los llamadores:
  `live_cutter.py:1193`, `workers.py` `_h_corte`). `DEFAULTS["mode"]`
  (`render.py:187`) y el comentario de `config.yaml:385` ganan `"game"` en
  su lista de valores forzados. La rama `game` existente (`render.py:3229`,
  recorte 9:16 centrado a pantalla completa, sin webcam) es la salida (no
  `center`, que sigue la cara). El discriminador entre el `game` forzado y
  el `game` de `game_voice` es `decision["mode"] == "game"` (forzado) frente
  a `mode == "auto"` con segmentos `"game"` (voz del juego).
  El mecanismo de la v2 (`cfg["layout"]["mode"]`) sigue **eliminado** (B9:
  `cut_clip_with_layout` pisa `cfg["mode"]` con `layout_mode` — `:3512`).
  El modo viaja por call site (§4.2).
  **Matiz verificado (I-24):** hoy `mode="game"` cae al auto-scan y el
  no-cam da `"center"` (`:2502`), cuyo filtro es EL MISMO recorte 9:16 que
  `"game"` (`:3229` vs la rama `center` que sigue) — los píxeles coinciden
  hoy cuando el escaneo no encuentra cara. El forzado sigue siendo
  necesario por dos razones: (1) una cara espuria del juego aún puede
  voltear un tramo a split en el auto-scan; (2) la decisión no lleva
  `"plantilla"`, así que `layout.get("plantilla")` es None y la plantilla
  depende solo de `plantilla_decidida` en cada call site. El forzado es un
  fix de forzado + etiqueta, no de píxeles.
- **Zoom del juego (I-7, I-19, M-4 — RETIRADO el cambio de la v5):** la v5
  ordenaba llamar a `rubius._game_filter` desde la rama `game` con un
  helper de recorte 9:16 nuevo y `game_h=1920`. **No se hace:** la rama
  `game` (`:3229`) es un recorte 9:16 PLANO, sin zoom; `_game_filter`
  pertenece al grafo split/rubius (su llamada en `:3042` es de
  `_split_rubius_graph`, `:2962`, no de la rama `game`) y no hay fuente de
  `game_zoom` en la rama `game` (I-24: ni ventanas hype ni 18-24 s — esas
  pertenecen al split). Añadir zoom aquí forkaría un segundo zoom sin
  calibración. `t_offset` es 0 (un solo segmento: `cut_clip_with_layout`
  pasa `t_offset=float(seg["start"])` = 0,0). **I-19 está HECHO en el
  árbol:** `_game_filter(game_crop, game_zoom, game_h=GAME_H)`
  (`rubius.py:443`), `game_h` usado en `:460` (escala sin zoom), `:469`
  (`y_coef`), `:472` (escala con zoom), `:473` (recorte final). Nada que
  enhebrar.
- **`_crop_rects_for_frame` tiene su propia rama `game` (`render.py:3625`,
  def `:3616`):** sin zoom en la rama `game`, la hoja de contacto no tiene
  nada que reflejar (M-5 cerrado por la retirada del zoom).
- **`_criterion` conserva `motivo` siempre** (`quality_gate.py:120-121`): hoy
  borra `motivo` cuando pasa. Verificado por grep: **ningún test existente
  afirma `motivo == ""`** (la instrucción de la v2 de actualizar esos tests
  era errónea — M-2 de la 2ª revisión; el goldfish de la v4 no lo
  re-comprobó, así que la v5 lo re-verifica en §10.5 antes de afirmar verde).

### §6.3 Puerta para sin_webcam (B-N7(2) — definiciones inlinadas, EN FASE 1)

**`validate_final_clip` salta `face_gate` para sin_webcam:** en la llamada a
`face_gate` (`final_review.py:2299`), si `template_name ==
"rubius_sin_webcam"` → `face = {"pasa": True, "method": "sin_webcam",
"motivo": "sin webcam por plantilla: no aplica", "share": None, "frames": 0,
"frames_total": len(frames), "face_widths": [], "detail": "sin_webcam"}`.
**El centinela es `pasa=True`, NO `pasa=None` (B-N7a):** con `None`, el chequeo
duro `if not face.get("pasa"): hard_failures.append("cara")` (`:2331-2332`)
rechaza el sin_webcam siempre — y la justificación de la v5 era falsa:
`layout_gate` devuelve `pasa=True` para "no aplica" (`:1914`), no `None`.
El `share: None` exige el blindaje del formato en la rama de override de
Gemini (§5.2).

**`layout_gate` NO se llama para sin_webcam** (devuelve «no aplica» para
plantillas distintas de rubius — `final_review.py:1914`): en su lugar
`layout = {"pasa": True, "motivo": "sin_webcam: layout verificado por
gameplay_puro en la plantilla", "duda": False, "sift_max_inliers": None,
"sift_max_cells": None, "face_share": None, "face_cx_mediana": None,
"face_area_mediana": None, "frames_total": len(frames), "frames_con_cara":
0}`. La prueba de layout de sin_webcam es `gameplay_puro`, dentro de
`_template_fullscreen_game`.

**`_template_fullscreen_game(check, arrays, spans, recipe, face_result,
vcfg=None)` (función nueva en `final_review.py`, junto a
`_template_word_white` `:1434`, misma firma):** NO reutiliza
`_template_word_white` tal cual (esa función lee `recipe["cam"]` en su
primera línea, `:1444`, y termina con `_check_full_width` (`:1600`) y
`_check_cam_live` (`:1615`), que no existen sin cámara). Checks:
- `dimensiones`: 1080x1920 — ya está en el cuerpo de `template_gate`
  (`:1165`, común a las tres plantillas); la rama nueva no lo repite.
- `gameplay_puro` (duro, **la prueba positiva de layout que condiciona la
  exención de cara**): celdas SIFT distintas de destino entre la mitad
  superior (y 0–840) y la inferior (y 840–1920) del render a pantalla
  completa, máximo sobre ≤ `layout_sift_max_frames` (8) fotogramas
  repartidos. **Fuente de fotogramas (I-26): los mismos `arrays` que recibe
  `template_gate`** (fotogramas de `extract_gate_frames`, t=1,3,5,… — los
  mismos del gate); **submuestreo: la misma regla que §5.1** (si hay más de
  8, índices `round(i*(n-1)/(8-1))` para i en 0..7); **recorte y gris: por
  fotograma, `top = arr[0:840, :]`, `bottom = arr[840:1920, :]`, ambos a
  gris con `cv2.cvtColor(..., COLOR_RGB2GRAY)`** (igual que
  `_layout_check_arrays`); `inl, cells = _sift_dup(top_gray, bottom_gray)`;
  **falla si el máximo de celdas ≥ `layout_dup_sift_cells` (8)**. La
  implementación usa `_sift_dup` directamente (NO `_sift_dup_inliers`, que
  devuelve solo inliers — la lección del gate adoptado es justo la
  contraria: entre mitades de un render a pantalla completa, los inliers
  solos también pueden colapsar por texturas repetidas del mismo juego) y
  NO llama a `layout_gate` ni a `_layout_check_arrays` (este último lee
  `recipe["cam"]` y la receta nueva no tiene `cam` → `KeyError`).
  **Un rubius duplicado mal etiquetado como `sin_webcam` falla aquí:**
  arriba y abajo son la misma imagen → celdas altas → rechazado, no
  publicado.
- Banner y subtítulos: se factorizan de `_template_word_white` a helpers
  compartidos con nombre — `_check_banner(check, arrays, recipe)` (banda
  del título, `recipe["title_banner"]`) y `_check_subs_word_white(check,
  arrays, spans, recipe, vcfg=None)` (subtítulos blancos palabra por palabra
  sobre el gameplay, `recipe["subs_zone"]`) — misma medida, mismos umbrales
  que rubius. La rama nueva **no ejecuta** `_check_full_width` ni
  `_check_cam_live`.
- SFX: el chequeo `sfx_presente` hoy vive SOLO dentro de la rama
  `word_white` de `template_gate` (`:1186-1200`, "solo rubius") — la v5 no
  lo decía (I-26). Se factoriza a `_check_sfx(check, clip_path, vcfg,
  sfx_events)` (misma lógica: `sfx_events == []` → pasa; None → se exige
  presencia; si no, `detect_sfx_in_clip` con `sfx_min_ncc`) y se llama
  desde AMBAS ramas, `word_white` y `sin_webcam` (el SFX se mezcla con
  `apply_sfx` con independencia de la plantilla). `fuente_ariblk` ya está
  en el cuerpo de `template_gate` (común) y aplica igual.
- **Estado de calibración (honesto):** la separación SIFT-entre-mitades NO
  está calibrada todavía sobre un render `sin_webcam` real (las dos mitades
  son regiones distintas del mismo juego, no la misma imagen dos veces). El
  umbral 8 se adopta por analogía con `layout_gate` y la aceptación §10.4
  lo mide sobre los primeros renders sin_webcam reales; si no separa, se
  dice por escrito y la defensa primaria queda en §4.1.4 + revisión humana
  — no se entrega el check como probado sin medirlo.
- `face_gate`: centinela `pasa=True` (arriba, nunca `None`); el criterio
  webcam en `build_quality_gate` (`quality_gate.py:128`) pasa solo si
  `gameplay_puro` pasó (ya implementado, `:203-224`). **Gemini
  `webcam_visible`:** para sin_webcam se RETIRA de `gemini_fallos` sin
  condiciones en `validate_final_clip` (con entrada en `overridden`:
  "webcam_visible: sin webcam por plantilla (correcto: no hay cámara)") y
  la rama de override `:2354-2360` NO se ejecuta cuando
  `face.get("method") == "sin_webcam"` — esa rama formatea `share` y el
  centinela lleva `share: None` (§5.2); el juez diciendo "no hay cámara"
  en un sin_webcam tiene razón, no es un error a anular.

### §6.4 Transporte de `plantilla` (B7 — Fase 3)

`parseViralInput` (`panel-backend/worker/src/db.ts:352`) es una whitelist: copia
SOLO llaves conocidas (`seo_scores`, `score_kind`, `viral_score`, `hype_score`,
`priority`, `viral_breakdown`, `quality_gate`, `platforms`, `seo_inputs`,
`seo_source`, `hold_reason`). **No existe el CAMPO `plantilla` en
`ViralData`/`parseViralInput`** (corrección M-17: la v5 decía "cero ocurrencias
de `plantilla` en todo `panel-backend/worker/src/`" — literalmente falso:
aparece en comentarios de `publish/native-engagement.ts:25`,
`publish/zernio-engagement.ts:171-172`, `evolution.ts:14,66`; lo cierto en
sustancia es que no existe el campo). La resolución del borrador citaba :344/:475/
:2034/:1452 — **verificado erróneo por grep** (:344 y :475 son llaves de cierre;
:2034 no es el INSERT; :1452 es `score = body.score;`). Líneas correctas
(verificadas en el árbol actual): `ViralData` `db.ts:212`; `parseViralInput`
`:352`; `mergeViralJson` `:497`; INSERT `:1968` (árbol Y HEAD — el `:1949`
HEAD de la v4 no existe); POST parse `index.ts:1487` → merge `:1566`; PATCH
parse `:1315` → merge `:1317` (la v4 citaba `:1412`/`:1491`/`:1241`/`:1242`,
desactualizados por el movimiento del árbol).

Fix (Fase 3): `plantilla?: string` en `ViralData` (`db.ts:212`); nueva rama en
`parseViralInput` (string + nombre conocido: rdjavi, willyrex, rubius,
`rubius_sin_webcam`; desconocido → error, nunca guardado en silencio);
`mergeViralJson`: `if (patch.plantilla !== undefined) next.plantilla =
patch.plantilla;`. El mismo cambio lleva `cam_box`/`cam_box_source`/
`cam_box_guardas` (§4.1.6). Consecuencia: en Fase 1 esos campos viven solo en
el cerebro y NO son diagnosticables desde D1 hasta Fase 3; la puerta §5 no
depende de D1.

**`plantilla` en el JSON `viral` (Fase 1, sin archivos del Worker):** 3 caminos
— live **HECHO** (`live_cutter.py:1744-1757`: `_viral["plantilla"] =
record["plantilla"]`); `viral_source.py` **HECHO** (payload del POST:
`payload["viral"] = {"plantilla": plantilla}`, `:790`); VOD
`vod_fallback.py:973` (`viral_payload`: añadir `plantilla`, Fase 3 junto con
el cambio B7 del Worker — el POST VOD usa `clip["plantilla"]`, actualizado
por el llamador §4.4). En Fase 1 la `plantilla` se envía en el JSON `viral`
y el Worker la descarta hasta Fase 3.

### §6.5 `template_gate` despacha por nombre de plantilla (B8)

`template_gate` (`final_review.py:1112`) despacha hoy solo por
`subs_style` (`:1167`). **Rama POR NOMBRE DE PLANTILLA antes del despacho por
`subs_style`:** `template_name == "rubius_sin_webcam"` →
`_template_fullscreen_game(_check, arrays, spans, recipe, face_result,
vcfg=vcfg)` + `_check_sfx(_check, clip_path, vcfg, sfx_events)`; si no, el
despacho actual. `template_gate` gana `template_name` como parámetro (hoy no
lo tiene; `validate_final_clip` lo resuelve en `:2248` y llama en `:2315`).
Alternativa rechazada: un valor distinto de `subs_style` (mentiría sobre los
subtítulos, que son el mismo `word_white` compartido).

## §7. Fase 2 — cajas de cámara por influencer (después de Fase 1)

Tabla D1 `influencer_cam_boxes` (caja + `confianza` alta/media + fecha),
API del panel GET/PUT, `confianza` definida (alta = caja validada en ≥3 clips
distintos; media = 1-2 clips). **La semilla `facecam_boxes` en `config.yaml`
se ELIMINA** (M-5 de la 2ª revisión: sin procedimiento definido — solo tabla D1).
Procedimiento de medición con muestra etiquetada por humano (30-50 segmentos)
+ matriz de confusión (§11.3: medir la tasa real de sin-facecam antes de
decidir; <10% → aprobación con el coste escrito, no pre-decidido).

## §8. VOD EN + dispatch diario (Fase 3)

### §8.1 Reserva EN en el VOD-fallback — tope perezoso en dos fases (B-N5, I-3 de la 2ª revisión, M-14)

Hechos verificados: la cuota vive en `run()` (`clipagent/vod_fallback.py:
run()` `:1436`; `if enqueued >= max_clips or vods_tried >= max_vods: break`
en `:1511`, al inicio de la iteración del `for entry in order:`), NO en
`fallback_order` (`:193`, solo ordena ES primero y luego EN). Los saltos por
pre-chequeo (sin VOD / cooldown / claramente-no-GTA6) hacen `continue` SIN
incrementar `vods_tried` — los VODs saltados no consumen huecos.
`vods_tried += 1` en `:1553`; `lang` se calcula DESPUÉS, en `:1558`;
`latest_vod_fn` se llama ANTES de cualquier tope por idioma. Los saltos por
excepción de `latest_vod_fn`, `not vod` y `not vod_id` también hacen
`continue` sin marcar nada.

**Por qué la v5 no bastaba (B-N5, verificado por el goldfish):** (1) el caso
simétrico "EN saltada por pre-chequeo → ES usa el 2º hueco" es inalcanzable
en una sola pasada ES-primero: la relajación se evalúa al inicio de cada
iteración, antes de examinar EN, así que cuando se examina ES2, EN1 aún no
se ha visitado y el tope ES nunca se relaja; (2) la excepción de
`latest_vod_fn`, `not vod` y `not vod_id` no cuentan como "visitadas", así
que si las ES se saltan por esas vías, la relajación no dispara nunca y la
corrida queda en 1 VOD en vez de 2. **Decisión v6: dos fases.** La fase 1 es
la pasada única de la v5; la fase 2 revisita SOLO las entradas saltadas por
tope, cuando el otro idioma ya no tiene nada por examinar.

**Regla única — tope perezoso en dos fases (normativa, con pseudocódigo):**
```
intentados = {"es": 0, "en": 0}          # VODs intentados por idioma
tope = {"es": 1, "en": 1}                # tope inicial por idioma (cualquier max_vods ≥ 1)
visitadas = set()                        # nombres de entradas examinadas en esta corrida
saltadas_por_tope = []                   # entradas saltadas por tope en fase 1, en orden
def _lang(entry):
    lang = (entry.get("language") or "es").lower()
    return lang if lang in ("es", "en") else "es"
def _otro_sin_examinar(otro):
    # True si el otro idioma aún tiene entradas sin examinar en order.
    return any(e["name"] not in visitadas and _lang(e) == otro
               for e in order)
# FASE 1: una pasada en fallback_order, tope 1/idioma.
for entry in order:
    if enqueued >= max_clips or vods_tried >= max_vods:
        break                            # cuota global: manda siempre
    lang = _lang(entry)                  # ANTES de latest_vod_fn (hoy se calcula después)
    otro = "en" if lang == "es" else "es"
    # Relajación inmediata: si el otro idioma no tiene entradas SIN VISITAR,
    # el tope de este idioma sube a max_vods (cubre "sin entradas del otro
    # idioma" y órdenes no-ES-primero).
    if not _otro_sin_examinar(otro):
        tope[lang] = max_vods
    if intentados[lang] >= tope[lang]:
        visitadas.add(entry["name"])     # saltada por tope: CUENTA como visitada
        saltadas_por_tope.append(entry)
        continue                         # <-- CONTINUE, no break: se sigue barriendo
    # ... listado (latest_vod_fn) y pre-chequeo, como hoy, con UNA regla nueva:
    # CADA continue marca la entrada como visitada ANTES de continuar:
    # excepción de latest_vod_fn, sin VOD, sin vod_id, cooldown,
    # claramente-no-GTA6 (las tres últimas ya eran continue; ahora marcan).
    vods_tried += 1
    intentados[lang] += 1
    visitadas.add(entry["name"])
    # ... procesar el VOD (incluida la rama dry-run, como hoy) ...
# FASE 2: revisitar solo las saltadas por tope, con el tope relajado.
for entry in saltadas_por_tope:
    if enqueued >= max_clips or vods_tried >= max_vods:
        break                            # cuota global: manda siempre
    lang = _lang(entry)
    otro = "en" if lang == "es" else "es"
    # Relajación por conjunto de visitadas: si el otro idioma aún tiene
    # entradas sin examinar, la reserva sigue y esta entrada NO se reintenta.
    if _otro_sin_examinar(otro):
        continue
    tope[lang] = max_vods
    # ... listado + pre-chequeo + procesado, idénticos a la fase 1
    # (cada continue marca visitada; solo las entradas de esta lista se
    # re-examinan: nunca se re-lista una entrada ya intentada) ...
```
Definiciones normativas (B-N5):
- **El chequeo de tope hace `continue`, NUNCA `break`.** Con `break` (el
  patrón adyacente del bucle), con `order` ES-primero el segundo streamer ES
  dispararía `break` y las entradas EN nunca se visitarían — la reserva EN
  moriría y la corrida quedaría en 1 VOD en vez de 2. Con `continue` se salta
  esa entrada y se sigue barriendo.
- **"Visitada" = la entrada fue examinada en esta corrida por cualquier vía:**
  intentada (`vods_tried += 1`), saltada por pre-chequeo (sin VOD, cooldown,
  claramente-no-GTA6), saltada por tope, **o saltada por excepción de
  `latest_vod_fn` / `not vod` / `not vod_id` (NUEVO en v6)**. Todas cuentan
  para la relajación. Sin esta definición, la relajación no dispararía nunca
  en el caso normal ni en el de excepción.
- **La relajación es por conjunto de visitadas, no por intento:** se evalúa
  al inicio de cada iteración (fase 1) y al inicio de cada re-visita
  (fase 2), antes del listado. En la corrida normal (ES con entradas
  restantes saltadas por tope) la relajación sube el tope EN a `max_vods` —
  inofensiva porque la cuota global (`vods_tried >= max_vods: break`) capa
  el total en 2. En el caso que importa (todas las ES saltadas por
  pre-chequeo/excepción, o sin entradas ES) la relajación deja 2 EN; y en el
  simétrico (EN saltada por pre-chequeo) la fase 2 deja que ES use el 2º
  hueco — traza `[ES1, ES2, EN1]`: ES1 intentado, ES2 saltado por tope
  (visitado, guardado), EN1 saltado por pre-chequeo (visitado); fase 2:
  ES2, otro idioma EN sin entradas sin examinar → tope relajado → ES2
  intentado. Total 2 VODs.
- **Generalización (M-14):** tope inicial 1 por idioma para cualquier
  `max_vods ≥ 1`; la relajación lo sube a `max_vods`. Con `max_vods=2`:
  tope {es:1, en:1} (el caso actual).
- Simétrico: sin entradas ES → 2 EN; sin entradas EN → 2 ES; una entrada EN
  saltada por pre-chequeo → ES puede usar el 2º hueco (fase 2).
- El corte VOD ya lleva `plantilla` (`vod_fallback.py:658` re-render, pasado
  a validación `:785`; `clip.get("plantilla")` `:932`; `layout_cfg` con
  `facecam_box` `:756-758`, corte `:762-765`; `viral_payload` `:973`).

Tests (§10.4): «sin candidato ES → 2 EN» **y** «sin EN → 2 ES» (el simétrico);
«EN saltada por pre-chequeo → ES usa el 2º hueco» (fase 2); «tope ES alcanzado
→ `continue`, no `break` (las EN se visitan)»; «excepción en `latest_vod_fn` /
sin VOD / sin `vod_id` cuentan como visitadas (la relajación dispara)»;
«fase 2 no re-lista entradas ya intentadas».

### §8.2 Dispatch diario — mover al inicio, con guarda y log (I-1, I-2, I-10 de la 2ª revisión, I-20)

Hechos verificados: `maybeDispatchDaily` (`panel-backend/worker/src/index.ts:
1854`, dedupe por día en settings `brain_daily_dispatched_day`; si el dispatch
falla, el día NO se marca y reintenta en el siguiente ciclo mientras siga la
hora 9). `isDailyDispatchDue` (`cycle-gate.ts:211`, exportada): hora==9 +
marca ≠ día; devuelve true si la lectura de settings falla. Gate en
`cycle-gate.ts:292-304` (`cycleGate` devuelve `{run, …, dailyDue, …}`).
Llamada final en `index.ts:4722` condicionada a `r.ran || r.gate.dailyDue`.
`runWorkerCycle` en `:4687`.

**Tres hipótesis del fallo 2026-11-26 09:02** («Too many subrequests», sin
reintento, sin rastro en D1 `worker_events` 09:00-10:30 — solo 3 eventos
`auto_publish` del clip #57 a las 09:01, ningún `github_dispatch`):
(1) el dispatch se lanzó tarde en el ciclo y el Worker murió por subrequests
antes de llegar a `:4722`; (2) el camino `!ok` hace `if (!ok) return;`
(`:1861`) **sin log** (solo el camino de excepción loguea «Dispatch diario
falló», `:1867`), así que un `dispatchCerebro` que devuelve false (GitHub
4xx/5xx, token inválido) es igualmente consistente con la ausencia; (3)
`runWorkerCycle` (`jobs.ts:1974`) no está envuelto en try/catch — si lanza,
el handler aborta antes de `:4722` sin rastro. **La corrección de la v2
(«failure already logged in `worker_events`») era falsa para el camino `!ok`.**

**Cambios (Fase 3):**
1. El dispatch se mueve al **inicio del handler `scheduled` (`:4659`), ANTES
   de `runWorkerCycle` (`:4687`)**. (I-20: la v4 decía "DENTRO del bucle por
   tenant" — **ese bucle no existe**: el handler es de un solo tenant,
   comentario `:4660-4665` «Un Worker = un tenant: no hay loop por tenants»,
   `const tenant = requireTenant(env)` en `:4667`. La justificación de la v4
   para I-2 presuponía ese bucle; el movimiento sigue siendo definible sin
   él.)
2. **Guarda explícita: NO despachar con el Worker parado.** Con el Worker
   parado, `runWorkerCycle` devuelve `{ran: false, gate: idleCycleGate()}` —
   parado ⇒ sin dispatch diario (comentario en `index.ts:4697-4702`). La
   condición es `isWorkerRunning` (`jobs.ts:1874`, exportada; early-return en
   `:1980` árbol y HEAD).
3. **try/catch alrededor de `isWorkerRunning`** (I-10): si la lectura D1 lanza,
   se loguea y se salta el dispatch temprano (peor que hoy sería abortar el
   handler para ese tenant). Precedente: `index.ts:2008` `.catch(() => null)`.
4. **Log en el camino `!ok`** (`:1861`, hoy silencioso; también `:1888`,
   el segundo dispatch — revalidate).
5. Se **borra la llamada final** (`:4722`) — el dispatch vive solo al inicio.

**Ya desbloqueado:** la opción A aterrizó 2026-11-26 20:07 (commit 26267d5,
schedules `*/5 * * * *` verificados, health ok) — hay 12 ciclos en la hora 9.

## §9. Secuenciación y baseline

**Baseline único (I-9, I-22, B-N8):** «árbol a 2026-11-27 ~03:00, HEAD
`dee18af` + trabajo sin commitear». Las citas dan líneas del árbol (sin
líneas HEAD: el árbol se mueve por ediciones concurrentes y la v5 arrastró
números de tres estados distintos — M-17). **Trabajo concurrente ADOPTADO
por esta spec (no pendiente, no a re-implementar):**
`clipagent/template_decision.py` (nuevo, sin seguimiento) +
`test_template_decision.py` + `test_config_sin_webcam.py` +
`test_quality_gate_sin_webcam.py` + `test_live_cutter_sin_webcam.py` +
`test_vod_sin_webcam.py` + `test_auto_clips_layout.py` +
`test_game_filter.py`; decisión en 8 call sites (`live_cutter.py:948-951` y
`:1173-1181`, `workers.py:963-969`, `viral_source.py:678-679`,
`vod_fallback.py:743`, `youtube_cut.py:1006-1014`, `facebook_cut.py:962-970`,
`auto_clips.py:1573-1578`); criterio webcam sin_webcam en `quality_gate.py`
(`:203-224`, con `gameplay_puro`); `game_h=GAME_H` en `rubius.py` (`:443`);
`--layout` con `"game"` en `auto_clips.py` (`:705`); `_viral["plantilla"]`
en `live_cutter.py` (`:1757`); `plantilla` en el POST viral
(`viral_source.py:790`); `_default_validate(..., plantilla=None)` en
`youtube_cut.py:582` / `facebook_cut.py:543`; ramas sin_webcam en los
re-renders live (`live_cutter.py:948-960`) y VOD (`vod_fallback.py:743-765`);
`clipagent/final_review.py` (el `layout_gate`), `clipagent/render.py`,
`clipagent/remake_all.py`, `clipagent/config.py`, `config.yaml`,
`panel-backend/worker/src/*` (piso 3, otro agente),
`clipagent/compilation.py` (compilación de vídeos largos, otro agente),
`test_layout_gate.py` (raíz del repo, 338 líneas), `test_final_gate.py`.
**Fase 1 se implementa ENCIMA del estado actual del árbol**; si el árbol se
commitea antes, re-verificar las citas.

**Fase 1 — deltas que quedan (NO archivos del Worker):**
- §4.1: `_caja_en_esquina` + `validate_cam_box` (área 0,35,
  `require_containment`) + `_cam_box_valida(..., avatar=False)` con
  conversión a fracciones + ganchos `:2443-2452` (posición antes del pip),
  `:2458-2465` (vtuber: caja ajustada + `params["avatar"]`), `:2575` /
  `:2617` (degradar a `"game"`, avatar en la guarda pip→split) + p7775 como
  4ª estrategia con portador `clip.get("streamer")`.
- §4.2: `_do_cut` vía `get_layout_config` + copia de la caja (B-N2).
- §4.3: `decide_template_for_segment` con retorno 4-tupla
  `(plantilla, caja, estado, avatar)` + orden avatar-después-de-ambiguo +
  `hay_avatar` público; actualizar los 8 call sites y
  `test_template_decision.py`.
- §4.4: parámetro `avatar` en `_rerender_after_gate` /
  `_rerender_vod_clip` + re-validaciones; decisión §4.3 también sin
  `layout_fallo` en el caso `"cara"`; `record["plantilla"]` /
  `record["avatar"]` y `clip["plantilla"]` / `clip["avatar"]` desde el
  re-render.
- §5.5: `avatar` en `layout_gate` / `_layout_check_arrays` /
  `validate_final_clip` **y en `face_gate`** (I-23) + `_layout_stub` +
  blindaje del formato de `share` + re-anclar el docstring del gate.
- §6.1: receta `rubius_sin_webcam` en `config.yaml` (claves reales §6.1) +
  alta en `TEMPLATE_NAMES` (mismo cambio) + entrada en `RECIPE_TEXT` +
  docstring «CUATRO recetas» + borrar los 3 bloques duplicados + test
  guarda.
- §6.2: modo forzado `"game"` en `decide_layout` + `"plantilla"` en la
  decisión + `DEFAULTS` / `config.yaml:385`.
- §6.3: centinelas `face`/`layout` (`pasa=True`) + `_template_fullscreen_game`
  + `_check_banner` / `_check_subs_word_white` / `_check_sfx` + retirada de
  `webcam_visible` de Gemini para sin_webcam.
- §6.5: `template_gate` despacha por nombre de plantilla.
- §4.3.2: `avatar` en `_default_validate` (youtube/facebook) + en las 7
  validaciones de la tabla.
- **Antes de implementar: 6ª revisión goldfish del delta v5→v6 (EGM) +
  aprobación del usuario.**

**Fase 2 — cajas por influencer** (§7, después de Fase 1).

**Fase 3 — Worker + VOD + dispatch:** cambio B7 (`ViralData` +
`parseViralInput` + `mergeViralJson` para `plantilla`/`cam_box*`); camino
payload VOD; pill en el panel; `vod_fallback.py` `run()` con topes perezosos
en dos fases (§8.1); `index.ts` mover-al-inicio + borrar llamada final + log
`!ok` + guarda Worker-en-marcha con try/catch (§8.2); `cerebro-live.yml` sin
cambios de caps (`--max-vods 2 --max-clips 2`).

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

### §10.1 Puerta (`test_layout_gate.py` — ya existe, raíz del repo, 338 líneas; NO sobrescribir: extender)

- **Tabla t7 (`ESPERADO_T7`, 19 entradas):** §5.3 (170 ✓; 183/184/186/189 ✗;
  190 ✓; 191/197/199/214/244 ✗; 246 ✓; 247 ✗; 248 ✓; 252 ✗; 253/254/255/256
  ✗). **186 t7 es ✗ (sin cara en ese fotograma) — es el comportamiento
  correcto a t7.**
- **Tabla de conjunto completo (nueva):** los 15 clips de §1.2 con sus 20
  fotogramas de la rejilla del gate (las extracciones viven en
  `runs/layout-gate-calib/`, 252 en `frames252/`): 186/190/246/248 ✓;
  183/184/189/191/197/199/214/244/12/247/252 ✗. **186 en conjunto completo
  es ✓** — las dos tablas coexisten porque usan conjuntos de fotogramas
  distintos (§5.3).
- **Capturas del usuario:** las 3 fallan por (i) con **ambos recortes** y el
  procedimiento declarado (recorte + `cv2.resize` a 1080×1920): canónico
  331/57/22; recorte del test 274/36/22. `CAPTURA_VIDEO` se actualiza al
  recorte canónico (107,431)-(1182,2363) y los valores esperados se
  re-verifican (§5.3). Los fixtures pre-recortados dan 330/57/23 (±1 por
  JPEG).
- **Avatar:** clip 12 y 244 fallan (i) con `avatar=False`; con `avatar=True`
  las reglas de cara del layout se saltan pero (i) sigue fallando (42/10
  celdas) — el re-render con la caja ajustada del candidato es el que debe
  pasar. **Y `face_gate` con `avatar=True` se salta entero (I-23):** sin el
  flag, 244 falla `face_gate` real (share 0,05, 1/20) aunque el layout fuese
  correcto; 12 lo pasa sin flag (`method=yunet`, share 1,0, 20/20).
- **170:** pasa con `avatar=False` (4 celdas < 5, regla (iv) no dispara) — el
  caso que protege la corroboración.
- **248:** pasa con celdas (5) aunque tenga 9 inliers — el caso que protege
  la estadística de celdas frente al conteo de inliers.
- **Sintéticos:** `_sift_dup_inliers` sigue usándose directamente en los tests
  sintéticos (wrapper de compatibilidad).

### §10.2 Guardas y render

- Regla reconciliada (§4.1) sobre las cajas documentadas: p7775 ✓
  (centro (0,11, 0,56), y0 0,435 < 0,50, ancho 0,22); p3220 ✓; caja centrada
  grande ✗; caja con y0 ≥ 0,50 ✗. `_caja_en_esquina` testeada sola.
- `validate_cam_box(..., require_containment=False)`: caja explícita válida sin
  cara detectada pasa; con cara presente pero fuera, la contención no aplica.
  Área < 0,35 (no 0,45).
- Los 7 puntos reales (§4.2) llegan a `decide_layout` con modo `game` cuando la
  decisión es sin_webcam (incluidos `workers.py:972` y los dos `run_*`); la
  decisión es 4-tupla en los 8 call sites y `test_template_decision.py` cubre
  el contrato nuevo (aridad fija, `hits==1` → ambiguo con flag avatar,
  avatar → `("rubius", None, "firme", True)`).
- `record["plantilla"]` se actualiza desde `review2` tras el re-render (live y
  VOD); el POST VOD lleva la plantilla nueva; `clip["avatar"]` /
  `record["avatar"]` se actualizan igual.
- Invariante §4.1.4: caja inválida/ausente → `"game"` o hold, nunca el
  fotograma completo como cámara, nunca duplicado.
- `config.yaml`: ninguna llave de nivel superior duplicada (guarda).
- Modo forzado `"game"`: `decide_layout` con `mode="game"` devuelve un
  segmento `game` para toda la duración + `"plantilla":
  "rubius_sin_webcam"` en la decisión.
- `_cam_box_valida(..., avatar=True)` salta solo la posición (ancho/área
  siguen); `params["avatar"]` llega desde la rama vtuber; p7775 dispara con
  `clip.get("streamer") == "rubius"` (no con `influencer_name`).
- `_template_fullscreen_game`: receta sin `cam` no levanta `KeyError`;
  `gameplay_puro` medido sobre los primeros renders sin_webcam reales
  (umbral 8 por analogía hasta calibrar — §6.3); `resolve_template(vcfg,
  "rubius_sin_webcam")` ya no levanta `ConfigError` (receta + `TEMPLATE_NAMES`
  en el mismo cambio); `face = {"pasa": True, ...}` para sin_webcam (nunca
  `None`).
- `_do_cut` pasa el `facecam_box` top-level (test: caja forzada en un job
  llega a `cut_clip_with_layout`).

### §10.3 Detector de Burn

**Burn no tiene coordenadas documentadas.** El test corre el detector sobre
segmentos fuente reales de Burn (fixtures fijados en el test) y afirma que las
cajas devueltas pasan la regla §4.1. **El test se salta (`skipUnless`) si los
fixtures no están en el workspace** (en CI nunca están: viven en `runs/`,
fuera del repo público — I-21); **con fixtures presentes, falla
explícitamente si la detección es vacía** (I-11). Si los fixtures no están en
el workspace, el test se salta con mensaje explícito, no se marca como pasado
ni como fallido.

### §10.4 Verificación en vivo (pendiente del re-render)

- Los 10 EN re-hechos pasan la puerta nueva (o se reporta cuáles no y por qué).
- Un VOD EN entra por el hueco reservado (§8.1).
- El dispatch diario del día siguiente tiene éxito (o reintenta dentro de la
  hora 9).
- 247 re-hecho pasa la puerta antes de cualquier re-programación.
- Clip 12 y 244 re-renderizados con la caja ajustada del candidato pasan la
  puerta **medida con `avatar=True` en `face_gate` y en `layout_gate`**
  (I-23): el pase de 244 NO se afirma a ciegas — hoy 244 FALLA `face_gate`
  real (share 0,05, 1/20) y su aceptación exige el re-render medido; si el
  detector no devuelve candidato, el re-render sin_webcam es el que debe
  pasar la puerta (§6.3).
- **VOD (tests, §8.1):** «sin candidato ES → 2 EN», «sin EN → 2 ES», «EN
  saltada por pre-chequeo → ES usa el 2º hueco» (fase 2), «tope ES alcanzado →
  `continue` (las EN se visitan)», «excepción en `latest_vod_fn` / sin VOD /
  sin `vod_id` cuentan como visitadas (la relajación dispara)», «fase 2 no
  re-lista entradas ya intentadas».

### §10.5 Líneas base antes de afirmar verde

Suite Python, suite Worker, tsc, ruff (medir primero, no asumir). Re-verificar
por grep que ningún test afirma `motivo == ""` (M-2: verificado por el goldfish
de la v5 — grep vacío). **Medido en v6 (2026-11-27 ~03:00–03:20, árbol actual):**
`test_template_decision.py` + `test_quality_gate_sin_webcam.py` +
`test_config_sin_webcam.py` + `test_game_filter.py`: **24 passed**; y la
corrida de referencia de los 9 archivos de la puerta/sin_webcam
(`test_layout_gate.py`, `test_final_gate.py`,
`test_template_decision.py`, `test_live_cutter_sin_webcam.py`,
`test_vod_sin_webcam.py`, `test_auto_clips_layout.py`,
`test_config_sin_webcam.py`, `test_quality_gate_sin_webcam.py`,
`test_game_filter.py`): **92 passed, 22 subtests passed** (1181,50 s, VM
cargada). La suite Python completa, la suite Worker, tsc y ruff se miden
antes de implementar; si el árbol se commitea antes, se re-mide.

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

- Repo: `~/workspace/gta6-clips/viral-clip-agent/` (HEAD `dee18af` a 2026-11-27
  ~03:00; árbol con `template_decision.py` + 8 call sites + piso-3 +
  compilación sin commitear).
- Spec v5 (mismo archivo, commit `dee18af`): adopta `layout_gate`, 183/184/189
  RECHAZAR, 186 PASAR, avatar sin primitiva, tope perezoso con verbo (B-N5
  parcial), §6.2/§6.3 en Fase 1 (B-N7 con centinela `pasa=None` erróneo y
  claves de receta contradictorias), avatar cableado con helper
  `prepare_cut_layout` (no existe en el árbol) y decisión en `final_review.py`
  (el árbol la tiene en `template_decision.py`).
- 5ª revisión goldfish: `~/workspace/gta6-clips/runs/revision-goldfish-v5-2026-11-27.md`
  (NO APROBADA, B-N7/B-N5/B-N6 parciales, B-N8 nuevo, I-13/I-18/I-23–I-26,
  M-10/M-11/M-16/M-17). 6ª revisión (delta v5→v6) pendiente antes de implementar.
- Borrador sin_webcam: `~/workspace/gta6-clips/runs/formato-rubius-sin-webcam-2026-11-26/SPEC.md`
  (REVISADA, 454 líneas) + `REVISION-GOLDFISH-2.md` (NO APTA: B7-B11) +
  `REVISION-RESOLUCION.md` (sus 4 citas Worker son erróneas — ver §6.4).
- Fixtures README: `tests/fixtures/duplicacion/README.md` (solo texto en el
  repo; imágenes en `runs/layout-gate-calib/fixtures-duplicacion/`).
- Calibración: `runs/layout-gate-calib/` (fotogramas de la rejilla del gate,
  `calib_final.txt`, `perframe_cells.txt`, `t7_check.txt`,
  `siftct02_197.txt`, `frames252/`); `runs/layout-fix-2026-11-26/frames/`
  (calibración t2/t7/t20 del `layout_gate` — rejilla no representativa, §5.3);
  `runs/review-en-2026-11-26/` (14 mp4 EN) y `runs/review-es-2026-11-26/`
  (246/247/248).
- Código (árbol a 2026-11-27 ~03:00, todas las líneas re-verificadas en esta
  v6; sin líneas HEAD — el árbol se mueve):
  `clipagent/template_decision.py` (nuevo; `decide_template_for_segment`,
  `_hay_avatar`, `RUBIUS`/`SIN_WEBCAM`/`AMBIGUO`);
  `clipagent/render.py` (decide_layout `:2336`; forzados `:2356-2360`; caja
  explícita `:2387`; escaneo pip `:2443-2452`; vtuber `:2458-2465`; guardas
  `:2575-2590` / `:2617`; `segment_filter_graph` `:3095`; rama `game`
  `:3229`; `_crop_rects_for_frame` def `:3616`, game `:3625`;
  `_split_rubius_graph` `:2962`; lazy import `:2993`; llamada `:3042`;
  `_rubius_game_crop` `:2950`; `detect_vod_facecam` `:3400`, rescates
  `hits: 1` CON caja `:3450`/`:3458`; `cut_clip_with_layout` `:3487`,
  pisado `:3510-3512`, retorno decisión `:3573`; `_cam_box_valida` `:1923`;
  `_split_params` `:1957`; `_scan_frames` def `:2053`;
  `_detect_vtuber_candidates` `:973`; `CELL=10` `:757`;
  `_detect_corner_box` `:656`; `_face_cam_box` `:1458`; `DEFAULTS` `:187`,
  `:200`; lazy import yunet `:3339`);
  `clipagent/rubius.py` (`build_filter` `:471-528`; `GAME_CROP` `:95`;
  `GAME_Y/H` `:93`; `_game_filter` `:443`, `game_h` en `:460`/`:469`/
  `:472`/`:473`); `clipagent/rdjavi.py` (`DEFAULT_CAM_BOX` `:114`; ejemplo
  p7775 `:77`); `clipagent/final_review.py` (`face_gate` `:859-1111`;
  `template_gate` `:1112`, despacho `subs_style` `:1167`, SFX solo rubius
  `:1186-1200`; `_template_word_white` `:1434`, cam `:1444`;
  `_check_full_width` `:1600`; `_check_cam_live` `:1615`;
  `find_facecam_box_in_source` `:1667`, escalado `:1681-1688`;
  `extract_gate_frames` `:780`; `_GATE_MAX_FRAMES` `:413`; `_sift_dup`
  `:1843`; `_sift_dup_inliers` `:1907`; `layout_gate` `:1914`;
  `_layout_check_arrays` `:2004`; `validate_final_clip` `:2160`,
  resolve `:2248`, face call `:2299`, layout call `:2312`, template call
  `:2315`, hard `"cara"` `:2331-2332`, `"layout"` `:2334`, `"plantilla"`
  `:2345`, override Gemini `:2354-2360`; `RECIPE_TEXT` `:120`, fallback
  `:1740-1742`, «TRES recetas» `:2183`);
  `clipagent/quality_gate.py` (`_template_check` `:111`, `_criterion`
  `:120`, `build_quality_gate` `:128`, criterio webcam `:203-224`);
  `clipagent/live_cutter.py` (`_rerender_after_gate` `:897`, `"cara"`
  `:922`, `layout_fallo` `:928`, decisión `:948-951`, sin_webcam `:957`,
  `cfg2` `:933`, `facecam_box` `:935`, re-corte `:984`, re-validación
  `:1021`, `process_peak` `:1044`, decisión `:1173-1181`, corte live
  `:1184`, plantilla `:1193`, primera validación `:1490`, llamador `:1511`,
  `_viral` `:1744-1757`); `clipagent/workers.py` (`_do_cut` `:406-419`,
  `cut_clip` `:422`, `_h_corte` `:934`, decisión `:963-969`, corte `:972`,
  validación `:1297`);
  `clipagent/remake_all.py` (`box_strategies` `:271`, `_tight_box` `:248`,
  consumidor `:516`, `load_blocked_clips` `:681`, `streamer` `:702-703`);
  `clipagent/yunet_facecam.py` (`validate_cam_box` `:365`, `find_cam`
  `:395`); `clipagent/vod_fallback.py` (`fallback_order` `:193`, `run()`
  `:1436`, cuota `:1511`, `vods_tried` `:1553`, `lang` `:1558`, re-render
  `:658`, decisión `:743`, `layout_cfg` `:756-758`, corte `:762-765`,
  re-validación `:785`, `review2["plantilla"]` `:801`,
  `clip.get("plantilla")` `:932`, primera validación `:939`, llamador
  `:950`, `viral_payload` `:973`); `clipagent/viral_source.py` (decisión
  `:678-679`, descarte `:680-692`, `plantilla="rubius"` `:694`, validación
  `:702`, POST `:790`);
  `clipagent/youtube_cut.py` (`_default_render` `:567`,
  `_default_validate` `:582`, `run_youtube_clips` `:660`, decisión
  `:1006-1014`, validación `:1077`); `clipagent/facebook_cut.py`
  (`_default_render` `:528`, `_default_validate` `:543`,
  `run_facebook_clips` `:603`, decisión `:962-970`, validación `:1033`);
  `auto_clips.py` (`--layout` `:705`, decisión `:1573-1578`, corte
  `:1600`); `clipagent/config.py` (`TEMPLATE_NAMES` `:639`, comentario
  `:640-645`, `TEMPLATE_REQUIRED_KEYS` `:657` (única), `load_config`
  `:534`, `safe_load` `:566`, `load_validator_config` def `:691`, lista
  `:769-773`, parse `:857-868`, `face_min_share` parse `:843`,
  `get_layout_config` `:997`, fusión `:1010-1011`, `resolve_template`
  `:975`); `config.yaml` (`sfx:` `:366`/`:379`, `layout:` `:384`, `mode`
  `:385`, `facecam_box` `:450`, `validator:` `:553`/`:646`, `templates:`
  `:610`/`:743`, `rubius_subs_outline_frac` `:677`, `layout_*`
  `:687-711`, `face_min_share` `:556`/`:649` = 0,25);
  `panel-backend/worker/src/db.ts` (`ViralData` `:212`, `parseViralInput`
  `:352`, `mergeViralJson` `:497`, INSERT `:1968`);
  `panel-backend/worker/src/index.ts` (PATCH parse `:1315` / merge
  `:1317`, POST parse `:1487` / merge `:1566`, `maybeDispatchDaily`
  `:1854`, `!ok` sin log `:1861`, log excepción `:1867`, segundo `!ok`
  `:1888`, `isWorkerRunning` uso `:2008`, `scheduled` `:4659`,
  comentario single-tenant `:4660-4665`, tenant `:4667`,
  `runWorkerCycle` `:4687`, llamada final `:4722`);
  `cycle-gate.ts` (`isDailyDispatchDue` `:211`, `cycleGate` def `:292`,
  `:293-304`); `jobs.ts` (`isWorkerRunning` `:1874`, `runWorkerCycle`
  `:1974`, early-return `:1980`).
- Tests: `test_layout_gate.py` (338 líneas, raíz del repo;
  `ESPERADO_T7` 19 entradas, `CAPTURA_VIDEO` `:54` (recorte del test,
  sin actualizar al canónico — §11.6), resize `:216`,
  `test_t7_cada_clip` `:143`, comentario `face_min_share` 0,25 `:190`);
  `test_final_gate.py` (804 líneas, `_layout_stub` `:311`, sin `avatar`);
  `test_template_decision.py`, `test_config_sin_webcam.py`,
  `test_quality_gate_sin_webcam.py`, `test_game_filter.py` (24 passed en
  v6, 2026-11-27 ~03:00), `test_live_cutter_sin_webcam.py`,
  `test_vod_sin_webcam.py`, `test_auto_clips_layout.py`.
- D1 (seba `d1q.py`, db `clips-avc`; kaez `query_kaez_d1.py`): influencers
  id 2 = Rubius; clips 12 (en_carpeta, 51, fallos hook/titulo/audio;
  `face_gate` PASA yunet 20/20), 244 (en_carpeta, 51; `face_gate` FALLA
  facecrop 1/20), 252 (en_carpeta, 57), 170 (publicado, 0); jobs 161 (248
  IG 00:00, scheduled), 164 (190 YT EN publicado 20:05), 165 (247 eliminado,
  0 filas).
- Goldfish: v1 (5d857a24), v2 (bf83eb8c), v3 (da11e70f), v4 (18678322),
  v5 (03e7602e) — las cinco NO APROBADA. 6ª revisión (delta v5→v6)
  pendiente antes de implementar.
