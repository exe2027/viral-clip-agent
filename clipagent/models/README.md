# Modelos vendoreados

## `face_detection_yunet_2023mar.onnx` (228 KB)

Detector de caras YuNet (OpenCV Zoo, licencia Apache-2.0), usado por
`clipagent/yunet_facecam.py` como fallback de detección de webcam.

- Fuente: https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
- SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
- Vendoreado a propósito (2026-11-19): el código NUNCA lo descarga en
  runtime. Sin este archivo, el fallback YuNet devuelve `None` con un
  aviso y el pipeline sigue con el comportamiento anterior.
- El port del detector (`find_cam`) es local; ver el docstring de
  `clipagent/yunet_facecam.py`.
