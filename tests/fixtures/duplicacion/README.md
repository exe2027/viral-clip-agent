# Fixtures de regresión — duplicación de layout (SPEC-ingles-correcto-2026-11-26 v3, §5.3)

Fotogramas extraídos de los renders finales (MP4 de R2). **Todos en la rejilla real
del gate** (`extract_gate_frames`, `final_review.py:780`: t=1,3,5,7,… con paso 2 s).
Etiquetas según §5.3 de la spec v3 (verificadas visualmente 2026-11-26).

## Las 3 capturas del usuario (recortadas al área de vídeo)

Capturas de teléfono 1290×2796; el área de vídeo se detectó por filas/columnas
oscuras (>50% píxeles <200) y se recortó con inset de 12 px:
recorte `(107,431)-(1182,2363)` → **1075×1932** (aspecto 0,556 ≈ 1080/1920).
Archivos ya recortados en este directorio. La captura 3 lleva chrome del
reproductor encima del vídeo (icono pausa, barra 0:07, silencio): es parte del
fixture, no del render.

## Tabla (22 imágenes + README)

Columna SIFT: buenos matches SIFT (nfeatures=2000, contrastThreshold=0,02, ratio
0,75) entre zona de cámara (y 0-840, x 0-810) y zona de juego (y 840-1920). Medido
2026-11-26. Umbral de referencia: **20** (ver Calibración).

| Archivo | Etiqueta | SIFT |
|---|---|---|
| usuario_captura1_calle.jpg | RECHAZAR (duplicado: caja = fotograma completo) | 465 |
| usuario_captura2_banner_chat_said_it.jpg | RECHAZAR (duplicado: cara del juego en banda de cámara) | 110 |
| usuario_captura3_007.jpg | LIMITACIÓN DOCUMENTADA (SIFT 17 <20; el clip 214 se rechaza por sus fotogramas de render) | 17 |
| en183_t7.jpg | PASAR (correcto, sangrado leve borde derecho) | 7 |
| en184_t7.jpg | PASAR (correcto, sangrado leve) | 5 |
| en186_t7.jpg | PASAR (correcto, sangrado leve; decisión §5.2: mayoría cámara) | 3 |
| en189_t7.jpg | PASAR (correcto; cuarto oscuro + escena nocturna) | 3 |
| en190_t7.jpg | PASAR (correcto) | 4 |
| en191_t7.jpg | RECHAZAR (duplicado) | 53 |
| 197_t1.jpg | RECHAZAR (banda de cámara = juego nocturno, sin cara; rejilla real) | 21 |
| 197_t3.jpg | RECHAZAR (banda de cámara = juego nocturno, sin cara; rejilla real) | 29 |
| 197_t5.jpg | RECHAZAR (banda de cámara = juego nocturno, sin cara; rejilla real) | 26 |
| en197_t7.jpg | PASAR por fotograma (cara arriba, juego abajo; el CLIP 197 se rechaza por t=1,3,5) | 4 |
| en199_t7.jpg | RECHAZAR (duplicado) | 431 |
| en214_t7.jpg | RECHAZAR (duplicado: cara del juego) | 98 |
| 244_t1.jpg | RECHAZAR (avatar sobre mapa; rejilla real) | 54 |
| 244_t3.jpg | RECHAZAR (avatar sobre mapa; rejilla real) | 64 |
| en244_t7.jpg | RECHAZAR por §5.1 (avatar sobre mapa; el mapa está en ambas zonas) | 35 |
| es246_t7.jpg | PASAR (correcto) | 5 |
| es247_t7.jpg | RECHAZAR (duplicado; sin job vivo: el job 165 fue eliminado, verificado 2026-11-26 ~23:20) | 132 |
| es248_t7.jpg | PASAR (correcto) | 6 |
| clip12_t7.jpg | PASAR (avatar rubius; share 0,34 ≥ 0,20) | — (no es duplicado; su SIFT no se afirma) |

## Calibración (medida 2026-11-26, scripts en /tmp/en_diag/)

Corpus: **49 fotogramas** (13 clips × t=2,7,15,25; 183, 190 y 244 no tienen t=25)
**+ 39 de rejilla** (13 clips × t=1,3,5) + 3 capturas. La v2 decía 52: el número
correcto es 49 para el corpus inicial. **t=2 no se muestrea nunca** (la rejilla del
gate es t=1,3,5,7,…); por eso 197_t2 y 244_t2 se retiraron de los fixtures (quedan
en `/tmp/en_diag/` como referencia de medición).

- **SIFT (ct=0,02, nf=2000, zonas completas): CORRECTO máx 18 (es248_t15), cada clip
  DUPLICADO tiene ≥1 fotograma ≥21 (mín por clip 29 en 197). Umbral 20 separa a
  nivel clip sin falsos positivos (6/6 DUP, 7/7 CORRECTO).** Capturas: 465 / 110 / 17.
  A nivel fotograma hay solape (199_t3=15 por desenfoque de transición, es248_t15=18);
  el gate es a nivel clip (1 fotograma que falle → clip rechazado), y el test
  afirma a nivel clip + los valores SIFT de referencia por fotograma.
- **ORB (franja del banner excluida, umbral 7): DESCARTADO como señal única.**
  es248_t1 (correcto, verificado visualmente) da **9** — falso positivo a nivel clip.
  El OR (ORB≥7 O SIFT≥20) hereda ese falso positivo. ORB queda documentado como
  medido-pero-no-shippeado.
- **Método por celdas (NCC 32×32, 4×2, búsqueda ±4): 0 coincidencias en TODOS los
  fotogramas. NO separa; eliminado.**
- MAD, NCC global/grueso, template matching, Haar, histogramas (gris/tono) y NCC de
  bordes: ninguno separa (ver spec §5.1).
- **Limitación conocida — captura 3:** SIFT 17, por debajo del máximo CORRECTO (18).
  Ningún método probado la caza a nivel fotograma sin rechazar falsamente un render
  correcto (es248_t15 = 18). El CLIP del que viene (214) sí se rechaza por sus otros
  fotogramas (SIFT 96-246). El test fija su valor medido (17) y la afirmación a
  nivel clip de 214, no un RECHAZAR falso.
- **197:** su segmento duplicado dura t=1..5 (cámara ausente al inicio; SIFT 21/29/26
  en t=1/3/5); t=7/15/25 son correctos (SIFT 4/1/2). Por eso la puerta NO salta los
  fotogramas de la ventana del banner: se evalúan (el banner no rompe SIFT).
- **186:** PASAR (mayoría cámara; SIFT 3). El sangrado leve del borde derecho existe
  también en 183/184/189, correctos. Se corrige con snap de caja, no rechazando el
  clip. Esta es LA decisión (spec §5.2); el borrador adoptado dice FALLA — la spec
  combinada manda aquí.
- **244:** avatar píxeles/zona 0,08-0,10 → sin_webcam (§4.3). **Clip 12:** píxeles
  0,34 → rubius. Umbral `avatar_share_min = 0,20`.
