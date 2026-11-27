# Fixtures de regresión — duplicación de layout (SPEC-ingles-correcto-2026-11-26 v4, §5.3)

> **REGLA DEL REPO (2026-11-27): el repo es PÚBLICO — las imágenes NO van a
> git.** Las 22 imágenes vivieron en este directorio en el commit 30ba071 y se
> sacaron el 2026-11-27 (quedan en el historial de ese commit; la limpieza del
> historial está pendiente de decisión, spec §11.5). Ubicación actual:
> `~/workspace/gta6-clips/runs/layout-gate-calib/fixtures-duplicacion/`.
> Este README queda en el repo porque solo contiene mediciones y etiquetas
> (texto), no imágenes. Los tests que usan las imágenes
> (`test_layout_gate.py`) las leen del workspace y se saltan si no están.

Fotogramas extraídos de los renders finales (MP4 de R2). **Todos en la rejilla real
del gate** (`extract_gate_frames`, `final_review.py:780`: t=1,3,5,7,… con paso 2 s).
Etiquetas según §5.3 de la spec v4 (verificadas visualmente 2026-11-26/27).
**La estadística de la puerta es SIFT en celdas distintas** (`layout_gate`,
`final_review.py:1914`; umbral 8 celdas). Los valores SIFTct02 (buenos matches,
umbral 20) se conservan como historial de medición (v3), no como método.

## Las 3 capturas del usuario (recortadas al área de vídeo)

Capturas de teléfono 1290×2796. Recorte canónico (v4): `(107,431)-(1182,2363)` →
**1075×1932** (aspecto 0,556 ≈ 1080/1920), detectado por filas/columnas oscuras.
El recorte fijo de `test_layout_gate.py` (`CAPTURA_VIDEO`, filas 66-2376, cols
95-1195, aspecto 0,476, incluye UI) da los mismos veredictos (celdas 257/43/22);
la v4 ordena actualizarlo al recorte canónico. La captura 3 lleva chrome del
reproductor encima del vídeo (icono pausa, barra 0:07, silencio): es parte del
fixture, no del render.

## Tabla (22 imágenes + README)

Columna «Celdas»: celdas distintas SIFT (RANSAC, rejilla 20 px) entre zona de
cámara (y 0-840) y zona de juego (y 840-1920), a fotograma único t7 salvo que se
indique otra cosa. Umbral de la puerta: **8 celdas**. Columna «SIFTct02»:
buenos matches (historial v3, umbral 20).

| Archivo | Etiqueta (v4) | Celdas | SIFTct02 |
|---|---|---|---|
| usuario_captura1_calle.jpg | RECHAZAR (duplicado: caja = fotograma completo) | 307 | 465 |
| usuario_captura2_banner_chat_said_it.jpg | RECHAZAR (duplicado: cara del juego en banda de cámara) | 57 | 110 |
| usuario_captura3_007.jpg | **RECHAZAR** (duplicado; la limitación de la v3 desaparece: 21 ≥ 8) | 21 | 17 |
| en183_t7.jpg | RECHAZAR (sangrado: caja demasiado ancha; clip 183: 35 celdas) | 19 | 7 |
| en184_t7.jpg | RECHAZAR (sangrado; clip 184: 51 celdas) | 27 | 5 |
| en186_t7.jpg | **FALLA a t7** (sin cara en ese fotograma; el CLIP 186 PASA: 6 celdas, share 0,85) | 3 | 3 |
| en189_t7.jpg | RECHAZAR (sangrado; clip 189: 20 celdas) | 4 | 3 |
| en190_t7.jpg | PASAR (correcto) | 0 | 4 |
| en191_t7.jpg | RECHAZAR (duplicado) | 23 | 53 |
| 197_t1.jpg | RECHAZAR a nivel clip (segmento duplicado t=1..5; a fotograma único pasa (i)/(iv): 3 celdas) | 3 | 21 |
| 197_t3.jpg | RECHAZAR a nivel clip (segmento duplicado; a fotograma único: 4 celdas) | 4 | 29 |
| 197_t5.jpg | RECHAZAR a nivel clip (segmento duplicado; a fotograma único: 4 celdas) | 4 | 26 |
| en197_t7.jpg | RECHAZAR a t7 por (iii) (cara de primer plano desplazada, cx 0,422, área 0,176) | 5 | 4 |
| en199_t7.jpg | RECHAZAR (duplicado) | 281 | 431 |
| en214_t7.jpg | RECHAZAR (duplicado: cara del juego; solo lo caza (i)) | 52 | 98 |
| 244_t1.jpg | RECHAZAR (avatar sobre mapa; 9 celdas) | 9 | 54 |
| 244_t3.jpg | RECHAZAR (avatar sobre mapa; 10 celdas) | 10 | 64 |
| en244_t7.jpg | RECHAZAR por (i) (avatar sobre mapa; clip 244: 10 celdas) | 9 | 35 |
| es246_t7.jpg | PASAR (correcto) | 3 | 5 |
| es247_t7.jpg | RECHAZAR (duplicado; sin job vivo: el job 165 fue eliminado, verificado 2026-11-26 ~23:20) | 101 | 132 |
| es248_t7.jpg | PASAR (correcto; 9 inliers en 5 celdas — las celdas arreglan el falso positivo de inliers) | 2 | 6 |
| clip12_t7.jpg | **RECHAZAR** (avatar, caja demasiado ancha; clip 12: 42 celdas — re-render con caja ajustada, no exención) | 3 | — |

## Calibración (medida 2026-11-26/27, gate actual; scripts y fotogramas en `runs/layout-gate-calib/`)

Conjunto completo del gate (t=1,3,5,…, 20 fotogramas por clip): **186/190/246/248
PASAN** (celdas 6/2/5/5); **183/184/189/191/197/199/214/244/12/247 FALLAN**
(celdas 35/51/20/231/6/318/96/10/42/151). 170 pasa a t7 (4 celdas). Las 3
capturas fallan (307/57/21). Limpios máx 6 celdas; DUP mín 10 celdas (244);
umbral 8 → margen 2 a cada lado. **t7 a fotograma único: 15/15 coinciden con
`ESPERADO_T7` de `test_layout_gate.py`.**

- **SIFTct02 (v3, retirado):** CORRECTO máx 18 (es248_t15), cada clip DUPLICADO
  tiene ≥1 fotograma ≥21 (mín por clip 29 en 197). Umbral 20 separaba a nivel
  clip, pero el conteo colapsa (170: 15 inliers en 4 celdas). Lo sustituye el
  conteo en celdas.
- **ORB (umbral 7): DESCARTADO como señal única.** es248_t1 (correcto) da **9**
  — falso positivo. El OR (ORB≥7 O SIFT≥20) lo hereda.
- **Método por celdas NCC (32×32, 4×2, ±4): 0 coincidencias en TODOS los
  fotogramas. NO separa; eliminado.** MAD, NCC global, template matching, Haar,
  histogramas y NCC de bordes: ninguno separa (ver spec §5.4).
- **Captura 3 (limitación v3, resuelta):** SIFTct02 17 < 20, pero celdas 21 ≥ 8.
  Se caza con el gate actual.
- **197:** su segmento duplicado dura t=1..5 (cámara ausente al inicio); t=7/15/25
  son correctos. A fotograma único, t1/t3/t5 pasan (i)/(iv) (3/4/4 celdas; Haar
  ve un artefacto de juego a cx 0,116, área 0,012 — no una cara real). **El clip
  se caza a nivel clip por (iv):** cara mediana cx 0,45, área 0,106 + máx 6
  celdas ≥ 5. Por eso la puerta NO salta los fotogramas de la ventana del banner:
  se evalúan (el banner no rompe SIFT). La etiqueta de 197_t1/t3/t5 es a nivel
  clip, no a fotograma único.
- **186: PASA en conjunto completo (6 celdas, share 0,85, cx 0,475, área 0,167),
  FALLA a t7 (sin cara en ese fotograma).** Son conjuntos distintos; la decisión
  está en un solo sitio (spec §5.3). El docstring de `layout_gate` («186 sin
  cara», cx 0,41-0,44) es un artefacto de su rejilla t2/t7/t20; se re-ancla en
  Fase 1. **183/184/189: la v3 los pasaba; la v4 los rechaza** (35/51/20
  celdas): el sangrado es caja demasiado ancha y se corrige con snap, no se
  tolera. **Muestreo SIFT:** el gate evalúa SIFT sobre ≤8 fotogramas repartidos
  (t ∈ {1,7,11,17,23,29,33,39} para n=20); 186 tiene un fotograma no muestreado
  con 14 celdas (t37) — limitación documentada (spec §5.3).
- **Avatar (v4, sin primitiva de píxeles):** varianza temporal refutada (clip 12
  estático 15-21%, 244 estático 81% — el mapa es más estático que el avatar).
  `avatar_share_min = 0,20` eliminado. Clip 12 y 244 se re-renderizan con la
  caja ajustada del candidato vtuber; el gate los valida con (i) SIFT y cara
  exenta (`avatar=True`, spec §5.2). **Clip 12: el render actual FALLA (i) con
  42 celdas** (t1 arena de juego junto al avatar; t37 póster de esqueleto junto
  al avatar). A t7 solo da 3 celdas (avatar centrado sobre fondo oscuro) — por
  eso la etiqueta es a nivel clip.
- **244:** 10 celdas, sistemático 8-12 en los 20 fotogramas. Mismo arreglo que
  clip 12 (caja ajustada del candidato).
- Corpus: 49 fotogramas iniciales (13 clips × t=2,7,15,25) + 39 de rejilla
  (13 × t=1,3,5) + 3 capturas + conjuntos completos n=20 por clip
  (14 clips, `runs/layout-gate-calib/`). **t=2 no se muestrea nunca** (la
  rejilla del gate es t=1,3,5,7,…); 197_t2 y 244_t2 se retiraron de los fixtures.
