# SPEC — Fix 3 del detector `subtitulos_una_palabra` (2026-11-24)

Elefante (spec) + Pez dorado (revisión) según EGM. NO implementar nada hasta
que el usuario apruebe esta spec.

## 1. Hechos verificados (cada uno comprobado en archivos antes de escribirlo)

### 1.1 Dónde vive el check
- `clipagent/final_review.py`, función `template_gate`, líneas ~1505-1530.
  El check solo corre con SRT (`if spans and active:`); sin SRT pasa débil.
- El medidor actual es `_subs_edge_coverages` (`final_review.py:548-567`):
  blanco = los 3 canales >= 200; una columna "tiene texto" si tiene >= 5
  píxeles blancos; cobertura = fracción de columnas con texto en cada
  franja de 150 px (`_RUBIUS_SUBS_EDGE_W = 150`,
  `_RUBIUS_SUBS_EDGE_COV = 0.10`). **No mira el contorno: cualquier
  contenido claro del juego en los bordes cuenta como "texto".**

### 1.2 La regla actual (Fix 2, commit 46cd99e, 2026-11-24 01:44)
- Falla solo si `both >= 2 and share > 0.10`, donde `both` = fotogramas
  con cobertura >= 0.10 en AMBOS bordes y `share = both / len(active)`.
- Fix 2 se motivó por IlloJuan (run 45299157326): 1/18 en t=39.0s, palabra
  "But", blanco de los bordes = cielo del juego. La línea completa real
  es sistemática (scooter 4/7, 57%).

### 1.3 El Fix 2 no bastó: run vivo kaez 45310466231
- Log `diagnostico-2026-11-23/run-45310466231.log`:
  - línea 2078: `- 179: validación final: plantilla: subtitulos_una_palabra`
    (thegrefg).
  - línea 4016: `- 119: validación final: plantilla: subtitulos_una_palabra`
    (burn).
- Es el MISMO falso positivo por atribución, ahora con 2 fotogramas por
  clip, así que la regla combinada (both>=2 AND share>10%) también cae.

### 1.4 Los 4 fotogramas FP, medidos por el Elefante (no citados de memoria)
Fotogramas `gate_*.jpg` de `diagnostico-2026-11-23/kaez-45310466231-*/`,
zona de subtítulos zy 900-1020, franjas de 150 px. Medición con
`.venv/bin/python` + OpenCV, función `_subs_edge_coverages` real del repo.
La columna "blanco-con-contorno" de esta tabla es POOLED (denominador
2x150x120); los valores por borde están en §1.6:

| clip | fotograma | left | right | blanco en bordes | blanco-con-contorno en bordes |
|---|---|---|---|---|---|
| burn 119 | gate_11_21.0s | 0.273 | 0.920 | 0.212 | **0.0074** |
| burn 119 | gate_20_39.0s | 0.647 | 0.453 | 0.102 | **0.0019** |
| thegrefg 179 | gate_04_7.0s | 1.000 | 1.000 | 0.147 | **0.0083** |
| thegrefg 179 | gate_05_9.0s | 1.000 | 1.000 | 0.154 | **0.0083** |

- El blanco de los bordes es juego claro: líneas blancas del mapa de GTA 6
  (thegrefg, cobertura 1.000 en ambos bordes) y suelo/paredes claras de un
  centro comercial (burn).
- Los subtítulos reales son una sola palabra centrada y correcta,
  verificado en los SRT del artefacto:
  - burn `clip_01.srt`: "probably **calling** the".
  - thegrefg `clip_01.srt`: "**tema**, eh. De verdad,".
- Los 40 fotogramas gate de ambos clips dan blanco-con-contorno en bordes
  <= 0.0142 (máximo en gate_01/02, t=1-3 s, ventana del banner, fuera de
  los instantes activos del check).

### 1.5 El positivo sintético, medido por el Elefante
- Línea completa sintética (DejaVuSans-Bold 72, texto que toca ambos
  bordes, contorno negro de 4 px, dibujada sobre un fotograma FP):
  blanco-con-contorno en bordes = **0.0818**, dentro del rango
  0.067-0.095 citado por el diagnosticador.
- Una sola palabra sintética centrada ("tema,") sobre el mismo fondo:
  0.0130, igual que el fondo sin palabra (la palabra centrada no toca
  las franjas; el 0.0130 es juego, no texto).

### 1.6 Hallazgo de calibración: la definición de la métrica decide todo
- **Definición franjas, por borde** (denominador 150x120 px por borde;
  ver Resolución Goldfish, hallazgo 1): los 4 FP que dispararon el fallo
  dan L/R = 0.0148/0.0000 (burn 21.0s), 0.0038/0.0000 (burn 39.0s),
  0.0083/0.0083 (thegrefg 7.0s y 9.0s). Peor borde activo: 0.0148 →
  margen real del umbral 0.03: **~2.0x**. Típico: ~3.6x. La línea
  sintética da 0.0818 → 2.7x sobre el umbral. El umbral 0.03 es mucho
  menos holgado de lo que esta sección afirmaba antes (corregido tras
  la revisión Goldfish; el "6-10x" era falso).
- El máximo sobre los 40 fotogramas (0.0142, burn gate_01/02, t=1-3s)
  NO fija el margen: esos instantes están en la ventana del banner y
  fuera de los activos del check.
- **Definición zona completa** (toda la zona 900-1020): los mismos 4 FP
  dan 0.0277-0.0432 → un umbral 0.03 NO separa. La spec fija la métrica
  como **franjas de borde únicamente, por borde**; cualquier otra
  definición invalida la calibración.

### 1.7 Tests que ya existen (`test_validator_fp.py`)
- `TestUnaPalabraTolerancia`: `test_un_fotograma_de_18_pasa_caso_illojuan`
  (1/18 "But"), `test_un_fotograma_de_4_pasa_denominador_pequeno`,
  `test_cuatro_de_6_sigue_fallando_caso_scooter` (4/7),
  `test_dos_de_19_sigue_fallando` (2/19, 10.5% > 10%).
- `_paint_both_edges` pinta bloques blancos LISOS (sin contorno) en los
  bordes. Bajo Fix 3 esos bloques son juego, no texto: el test 2/19
  cambiaría de resultado. **Los tests sintéticos hay que repintarlos con
  texto con contorno, no solo ajustar el umbral.**

### 1.8 [sin dato] — lo que NO se pudo verificar en archivos
- El 5.º fotograma FP (el caso IlloJuan "But" t=39.0s del run 45299157326):
  el artefacto `illojuan-artifact/.../clip_01/final_review/gate_*.jpg` no
  reproduce el 1/18 (ningún gate tiene ambos bordes >= 0.10) y
  `runs/calibrate-fp-2026-11-23/gate_20_39.0s.jpg` no tiene blanco en los
  bordes (0.00006). La identidad exacta del 5.º fotograma no está
  localizada en disco.
- Los casos "1/20" y "1/5" citados en el encargo como casos que hoy pasan
  bien: no se localizaron en archivos. [sin dato: confirmar con el
  diagnosticador a qué clips corresponden].
- El positivo real (scooter 4/7, 2026-11-21): sus fotogramas no están
  localizados en disco; la única medida positiva es la sintética (§1.5).

## 2. El problema exacto
`_subs_edge_coverages` no distingue "blanco que es texto" de "blanco que
es juego". El subtítulo rubius es blanco con contorno negro; el juego
claro (mapa, cielo, suelo) es blanco sin contorno negro pegado. Fix 2
subió el listón de conteo (both>=2 AND share>10%), pero el defecto es de
ATRIBUCIÓN, no de conteo: dos fotogramas con juego claro en ambos bordes
bastan para tumbar un clip bueno. Cada FP cuesta un clip validado que no
se publica (119 y 179 en el run 45310466231) y rompe el "no pasa un día
sin un clip nuevo en la Cola".

## 3. Propuesta recomendada: discriminador blanco-con-contorno

**Recomendación del Elefante: opción A (discriminador), no B (SRT-aware)
como sustituto.** B queda como guarda complementaria opcional (§3.3).

### 3.1 Opción A — discriminador blanco-con-contorno (recomendada)
- Nuevo helper (p. ej. `_subs_edge_outlined_coverages`) que, en las
  mismas franjas de 150 px de cada borde, mide **por borde** L y R: la
  fracción de píxeles **blancos con negro cerca** dentro de esa franja
  (denominador 150x120 px por borde). Blanco = 3 canales >= 200; negro
  = 3 canales < 60 (convención por canal, como el `_BLACK` existente);
  negro dilatado con kernel 5x5 **sobre el recorte zy 900-1020**
  (dominio de dilatación fijado: el negro fuera del recorte no cuenta);
  píxel válido = blanco AND negro-dilatado. (Corregido tras la revisión
  Goldfish: la versión anterior mezclaba pooled y por borde.)
- El check `subtitulos_una_palabra` falla solo si, en >= 2 fotogramas
  activos Y > 10% de los activos (se conserva la regla Fix 2 de conteo),
  **L > 0.03 AND R > 0.03** en ese fotograma.
- **Umbral: 0.03**, clave de config nombrada
  `validator.rubius_subs_outline_frac`, **requerida** (se añade a la
  tupla `required` de `load_validator_config`, al bloque `validator`
  efectivo de `config.yaml` — ojo: hay DOS bloques `validator:` (líneas
  467 y 554), gana el último —, a `VCFG_TEMPLATE` en
  `test_validator_fp.py` y a `_validator_cfg` en `render_gate.py`).
  **Sin fallback ni constante silenciosa**: el proyecto prohíbe los
  defaults silenciosos (`config.py:616-625`) y esta spec no pide
  excepción.
- Alcance: solo la plantilla rubius / estilo `word_white`
  (`template_gate` despacha por `subs_style`; el karaoke muestra la
  línea completa por diseño y no tiene este check). La frase "A detecta
  la línea completa venga del camino de render que venga" queda
  retirada: es falsa fuera de rubius.
- El espejo `render_gate.py:566` entra en el alcance: hoy tiene su
  propio check `subtitulos_una_palabra` con la métrica vieja y regla
  distinta (`both == 0`, tolerancia cero). Se actualiza con el mismo
  helper y la misma regla de conteo, o se declara por escrito por qué
  queda divergente.
- Por qué A y no B: A juzga el RENDER FINAL rubius, que es lo que ve el
  espectador. B juzga la entrada (SRT/ASS), no el render. (La
  desestimación de B queda condicionada a verificar el mecanismo del
  scooter — ver Resolución Goldfish, hallazgo 6.)
- Implementación **solo en numpy, sin rama cv2**: CI sí tiene cv2
  (`ci.yml` instala `.[dev,vision]`), pero los jobs vivos sin `vision`
  no; una rama dual duplicaría calibración y comportamiento en bordes
  sin beneficio. La dilatación 5x5 se implementa con desplazamientos
  numpy.

### 3.2 Opción B — SRT-aware / ASS-aware (no recomendada como sustituto)
- Idea: en vez de medir píxeles, mirar el SRT (o los eventos ASS que
  genera `make_word_ass`) del instante activo: si el evento cubre más de
  una palabra, el render es de línea completa.
- Por qué no sustituye a A (condicionado): el defecto es de RENDER, y
  hay >= 3 caminos de render (`burn_subtitles`: rubius con
  `make_word_ass`, karaoke/rdjavi con ASS, genérico que quema el cue
  SRT completo por diseño). `make_word_ass` no puede emitir dos
  palabras a la vez por construcción, así que el mecanismo del scooter
  tiene que ser el camino genérico o cues SRT solapados — y en el
  camino genérico B (mirar el cue) SÍ lo vería, porque el render ES el
  cue. Por eso B no queda descartada por un supuesto: queda pendiente
  la verificación barata del mecanismo del scooter (§8.2). Si el
  mecanismo es el camino genérico, B es una guarda válida (y barata)
  para ese camino; A sigue siendo el único que juzga el render final
  en rubius.
- B solo se acepta como guarda complementaria en `live_cutter` (fallo
  rápido antes de renderizar si un evento ASS lleva > 1 palabra), nunca
  como reemplazo del check sobre el render final.

### 3.3 Criterio de elección (si el usuario prefiere B)
- B solo si se demuestra primero, con el clip scooter real, qué evento
  ASS/SRT corresponde a su línea completa. Sin esa demostración, A.

## 4. Casos de regresión concretos (obligatorios, todos en `test_validator_fp.py`)

| # | Caso | Fuente | Resultado exigido con Fix 3 |
|---|---|---|---|
| R1 | burn 119, gate_11_21.0s + gate_20_39.0s | fotogramas reales del artefacto | PASA (eran FP; outlined 0.0074 / 0.0019) |
| R2 | thegrefg 179, gate_04_7.0s + gate_05_9.0s | fotogramas reales del artefacto | PASA (eran FP; outlined 0.0083 / 0.0083) |
| R3 | línea completa sintética con contorno (texto que toca ambos bordes) | render con el estilo de PRODUCCIÓN (Arial Black 100 px, contorno 10, vía libass/ruta rubius real) — no DejaVu 72/4 px, que no representa el render | FALLA (outlined > 0.03; medir y declarar el valor) |
| R4 | scooter 4/7 | test existente `test_cuatro_de_6_sigue_fallando_caso_scooter`, repintado con glifos reales (anillo negro primero, relleno blanco después, sobre fondo real) | FALLA (positivo real; si el repintado cambia el resultado, el test estaba mal modelado, no el detector) |
| R5 | 2/19 | test existente `test_dos_de_19_sigue_fallando`, repintado igual que R4 | FALLA |
| R6 | 1/18 IlloJuan "But" | test existente, repintado con glifos reales (una sola implementación; el fotograma real no está localizado, §1.8) | PASA |
| R8 | palabra centrada sintética "tema," / "calling" | sintético sobre fondo real, estilo de producción | PASA (no toca las franjas; outlined = fondo) |

- R1/R2 usan los fotogramas REALES del artefacto (no sintéticos): el
  test carga los 4 JPG y corre el check nuevo.
- R3/R4/R5/R6/R8: el repintado se especifica con precisión — glifos
  reales con PIL (anillo negro primero, relleno blanco después, sobre
  fondo real) — y cada fixture declara su valor de outlined MEDIDO
  (p. ej. fixture R4 > 0.05). Los bloques lisos de `_paint_both_edges`
  quedan prohibidos como modelo de "texto" (son juego); además un
  bloque sólido sobre fondo oscuro dispara el medidor nuevo por
  adyacencia bloque/fondo, no por texto — repintar mal cumpliría el
  "rojo antes de verde" de forma espuria.
- **R7 eliminada**: "1/20 y 1/5" no tienen origen ni archivos ni nombres
  de test, y pasan con el código actual, así que no pueden ser "rojas
  antes de verde".
- **R9 ya no es un caso de la tabla: es la puerta pre-despliegue**
  (ver §5): medir el positivo real del scooter (outlined por borde, y
  ancho de la banda de antialiasing gris 60-200 tras x264+JPEG) antes
  de desplegar. Sin R9, el umbral 0.03 descansa en n=1 sintético.

## 5. Criterio de éxito
- Tests: los casos R1-R6 y R8 en verde. Rojo-antes-de-verde solo para
  los casos de FALLA (R1/R2 fallan hoy; R3/R4/R5 con el repintado
  correcto — verificar rojo antes de verde). Los casos de PASA
  (R6/R8) se validan por mutación, no por rojo-antes-de-verde: con el
  medidor sin contorno (Fix 3 revertido) R1/R2 vuelven a fallar.
- **Puerta pre-despliegue R9 (go/no-go):** antes de desplegar, medir el
  positivo real del scooter (outlined por borde + ancho de la banda de
  antialiasing gris 60-200 tras x264+JPEG). Si el scooter real da
  < 0.03 por borde, NO se despliega con 0.03: plan B explícito — subir
  el kernel a 7x7 o el negro a < 100, re-medir TODO el set FP y volver
  a calibrar el umbral. Sin R9 en verde no hay deploy.
- Suite completa sin nuevos fallos; ruff en verde (cambio 100% Python).
- Verificación en vivo: en el próximo run vivo (kaez o seba), un clip
  equivalente a 119/179 (juego claro + una palabra) pasa
  `subtitulos_una_palabra`; y un clip con línea completa real, si lo
  hay en ese run, sigue siendo rechazado. Si en ese run no aparece
  ninguna línea completa real, la verificación en vivo queda
  INCOMPLETA (el negativo en vivo no se puede forzar sin un clip real).
- El veredicto del check debe citar la métrica nueva en el detalle
  (p. ej. "con contorno L=0.008 R=0.008, umbral 0.03"), para que
  el próximo FP se diagnostique sin adivinar.

## 6. Fuera de alcance
- No tocar la puerta de cara, ni SFX (`sfx_presente`), ni
  `titulo_banner`, ni `subtitulos_blancos`, ni caps/slots/reglas de
  publicación.
- No cambiar `subtitulos_blancos` ni `_GATE_SUB_WORD_PX` (50 px).
- No re-encolar ni re-validar los clips 119/179 en esta spec: eso es
  decisión del usuario (§8, punto 3) y trabajo del run, no del fix.
- No tocar avc-sebastian ni el Worker/panel. Solo `clipagent/` y sus
  tests.

## 7. Riesgos: qué clips reales podría dejar pasar el nuevo detector
1. **Línea completa sin contorno negro** (texto blanco liso, contorno
   fino < 5 px, contorno gris > 60, o contorno degradado por JPEG/x264):
   outlined bajo → el clip malo PASA. Es el falso negativo principal.
   **Se acepta sin mitigación real**: R9 solo lo mide, no lo evita. El
   contorno fino no es hipotético: los estilos vivos tienen
   `border_width` 2-4 (solo rubius tiene 10). Coste aceptado: un clip
   con línea completa y contorno fino pasaría la puerta.
2. **Línea completa de otro color** (amarilla, por cambio de estilo):
   el detector solo mira blanco; ya era así antes, Fix 3 no lo empeora
   pero tampoco lo arregla.
3. **Juego con negro pegado al blanco** (UI oscura junto a carretera
   clara, bordes de ventana del juego): outlined del juego sube. Medido:
   peor borde activo 0.0148 → margen ~2.0x al umbral. Además la métrica
   cuenta "blanco cerca de negro", condición necesaria pero no
   suficiente de "texto con contorno": un panel blanco de UI sobre
   fondo negro o una marca vial blanca sobre asfalto oscuro puntúan
   igual que un glifo. **Sin medición en escena oscura**: los 40
   fotogramas medidos son de juego claro; en juego oscuro el FP de
   outlined debería ser mayor. La calibración debe añadir al menos una
   medición FP en escena oscura.
4. **Umbral calibrado con n pequeño**: 4 FP reales + 1 sintético. Un
   juego más claro (desierto, nieve, cielo) no medido podría superar
   0.03. Mitigación: R9 y revisión del primer run vivo con el detalle
   de la métrica (§5).
5. **Regresión de tests existentes**: los tests sintéticos con bloques
   lisos cambian de resultado por diseño (eran juego, no texto). Si
   alguien los "arregla" repintando sin contorno, el test miente.
   El repintado con contorno es parte del fix, no un detalle.
6. **Falso negativo por muestreo (nuevo, hallazgo Goldfish):** con
   `both>=2 AND share>10%` + muestreo cada 2 s, una línea completa
   real de ~1.5 s entre dos muestras aparece en 1 solo fotograma activo
   y PASA; y 1 fotograma con outlined 0.20 (línea enorme) pasa mientras
   2 fotogramas con 0.031 (borde del juego) fallan: la binarización por
   fotograma descarta la magnitud. La premisa "la línea completa real
   es sistemática" descansa en n=1 (scooter, fotogramas perdidos). La
   spec no añade vía de severidad (p. ej. 1 fotograma con outlined >
   2x umbral también falla): queda como decisión del usuario (§8,
   punto 4) o se acepta el hueco por escrito.
7. **Ventana del banner y deriva vertical (alcance):** `active` excluye
   t=0.5-3.5 s; una línea completa en esos 3 s nunca se chequea (rubius
   suprime palabras ahí, el camino genérico no). Y la zona zy 900-1020
   es fija por receta, pero `burn_subtitles` ajusta `MarginV`; si
   fuente/margen cambian, el glifo puede quedar parcialmente fuera de
   zy y el detector lo sub-mide. Sin caso de regresión que los cubra:
   quedan como límites declarados del check.

## 8. Decisiones que debe tomar el usuario (antes de implementar)
1. **Umbral 0.03 ya, o calibrar primero con el positivo real (R9)?**
   Desplegar con 0.03 medido en 4 FP + 1 sintético (margen real ~2x al
   peor borde activo, ~3.6x al típico — no 6-10x como se afirmó antes),
   o esperar a medir el scooter real antes de congelar el umbral. R9
   es puerta pre-despliegue en cualquier caso: sin scooter medido no
   hay deploy.
2. **¿A, B, o A+B?** Recomendación: A (discriminador sobre el render
   rubius). B solo como guarda complementaria si se demuestra el
   mecanismo del scooter — antes de descartar B hay que verificar si
   el scooter se renderizó por el camino genérico (que quema el cue
   SRT completo por diseño) o con cues SRT solapados, porque en esos
   casos B sí lo vería. Esa verificación es trabajo previo, no
   posterior.
3. **¿Qué hacer con los clips 119 y 179 ya rechazados?** Re-encolarlos
   para re-validar con Fix 3 (volverían a cortarse/validarse en el
   próximo run) o dejarlos rechazados.
4. **¿Vía de severidad para 1 fotograma?** Un fotograma con outlined
   muy alto (p. ej. > 2x umbral) ¿también falla aunque sea 1 solo, o se
   acepta el hueco de muestreo (§7.6)?

## Resolución Goldfish (revisión 2026-11-24 03:02, veredicto: NO aprueba la spec tal cual)

El pez dorado reprodujo las mediciones (los 4 fotogramas y sus SRT
existen; §1.4 correcta como pooled; R1/R2 hoy son rojos de verdad:
burn 2/14 activos = 14.3% > 10%, thegrefg 2/7 = 28.6%). Sus 15
hallazgos se resuelven así; los cambios ya están aplicados en el cuerpo
de la spec donde se indica:

1. **Métrica ambigua pooled vs por borde — RESUELTO.** Verificado por
   el Elefante: gate_11 L=0.0148/R=0.0000 (pooled 0.0074). La spec ahora
   define L y R por borde (denominador 150x120), falla solo si
   L > 0.03 AND R > 0.03, dilatación sobre el recorte zy 900-1020,
   negro < 60 por canal.
2. **Márgenes falsos — RESUELTO.** §1.6 reescrita con números por
   borde: ~2.0x al peor activo, ~3.6x al típico, sintético 2.7x sobre
   el umbral. El "6-10x" queda retirado.
3. **Positivo sintético no representativo — RESUELTO.** R3 exige el
   estilo de producción (Arial Black 100 px, contorno 10, vía libass),
   no DejaVu 72/4 px.
4. **Kernel 5x5 y negro <60 sin calibrar — RESUELTO (parcial).** R9
   incluye medir el ancho de la banda de antialiasing y la fracción de
   borde real, con plan B explícito (kernel 7x7 o negro <100 +
   re-medición completa) si el scooter real da < 0.03.
5. **Falso negativo por muestreo — RECONOCIDO, DECISIÓN DEL USUARIO.**
   §7.6 lo documenta y §8.4 pregunta por la vía de severidad. No se
   añade por defecto: cambiaría la regla de conteo Fix 2.
6. **B no descartada con fundamento — RESUELTO (condicionado).** §3.2
   y §8.2: antes de descartar B hay que verificar el mecanismo del
   scooter (camino genérico en `transcribe.py:1382` que quema el cue
   completo, o cues solapados). El goldfish encontró >= 3 caminos de
   render (`live_cutter.py` → `burn_subtitles`): rubius con
   `make_word_ass`, karaoke/rdjavi con ASS, y genérico con el cue SRT
   completo. [No verificado por el Elefante: se apoya en la evidencia
   citada por el goldfish.]
7. **Alcance solo rubius — RESUELTO.** §3.1 y §6 declaran el alcance
   (`word_white` únicamente); el karaoke y otros estilos quedan fuera.
8. **Espejo `render_gate.py` — RESUELTO.** Verificado por el Elefante:
   `render_gate.py:566` tiene su propio check con `both == 0`. Entra
   en el alcance: mismo helper y misma regla de conteo, o divergencia
   declarada por escrito.
9. **Config inviable — RESUELTO.** Verificado por el Elefante:
   `config.yaml` tiene dos bloques `validator:` (467 y 554) y
   `load_validator_config` prohíbe defaults silenciosos. Clave nombrada
   `validator.rubius_subs_outline_frac`, requerida, sin fallback;
   añadir al bloque efectivo + `VCFG_TEMPLATE` + `_validator_cfg`.
10. **Riesgo 1 sin mitigación — RESUELTO (aceptado por escrito).** §7.1
    declara que se acepta sin mitigación real, con su coste.
11. **Premisa de entorno inexacta — RESUELTO.** CI sí tiene cv2; el
    riesgo es en jobs sin `vision`. Implementación solo numpy, sin rama
    cv2.
12. **FP/FN no cubiertos — RESUELTO (parcial).** §7.3/§7.7: escena
    oscura sin medir (se exige una medición FP en escena oscura),
    adyacencia ≠ contorno, ventana del banner y deriva vertical como
    límites declarados.
13. **R6/R7/R9 no ejecutables — RESUELTO.** R7 eliminada, R6 fijada a
    una sola implementación, R9 fuera de la tabla (puerta
    pre-despliegue), §5 corregido (casos de PASA por mutación).
14. **Trampa del repintado — RESUELTO.** §4 especifica el repintado
    (glifos reales, anillo negro + relleno blanco, valor de outlined
    medido declarado por fixture).
15. **Sin contradicción con reglas del usuario.** §6 respeta no tocar
    cara/SFX/caps ni publicar ni pedir secretos; §8.3 deja 119/179
    como decisión del usuario. (Minucia: se quitó la mención a `tsc`.)

**Veredicto Goldfish tras la resolución: la spec corregida queda lista
para aprobación del usuario. Implementación bloqueada hasta su "sí".**
