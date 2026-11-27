"""Regresión de la puerta de layout (regla del usuario 2026-11-26 19:59).

La puerta caza en el render final: (a) la duplicación (la mitad
superior y la inferior son la misma imagen) y (b) que la zona de
cámara sea mayoritariamente cámara, no gameplay. El validador la
marcaba "aprobado" porque Haar detecta la cara del personaje del
juego en la zona de cámara; la puerta nueva combina SIFT
(duplicación, contada en celdas distintas de destino) + geometría
de la cara (posición/tamaño, con corroboración SIFT) para cazar
los dos fallos sin tumbar webcams anchas legítimas (170).

Los fotogramas reales y las capturas del usuario viven FUERA del
repo (el repo es público: nunca van clips ni capturas del usuario
a git); los tests que los usan se saltan si no están en el
workspace. El test sintético corre siempre (CI).
"""
import os
import unittest

import cv2
import numpy as np

from clipagent.config import load_config, load_validator_config
from clipagent.final_review import (
    _frame_array,
    _layout_check_arrays,
    _sift_dup,
    _sift_dup_inliers,
    face_gate,
)
from clipagent.render import _cam_box_valida, _split_params, decide_layout
from clipagent.validate import ConfigError

FRAMES_DIR = os.path.expanduser(
    "~/workspace/gta6-clips/runs/layout-fix-2026-11-26/frames")
# Rejilla del gate (conjunto completo, n=20 fotogramas por clip,
# §1.2/§5.3): extracciones en runs/layout-gate-calib/, 252 aparte
# en frames252/ (mismo reparto t1..t39 impares).
CALIB_DIR = os.path.expanduser(
    "~/workspace/gta6-clips/runs/layout-gate-calib")
FRAMES252_DIR = os.path.join(CALIB_DIR, "frames252")
RUNS_DIR = os.path.expanduser(
    "~/workspace/gta6-clips/runs/layout-fix-2026-11-26")
SEG186 = os.path.expanduser(
    "~/workspace/gta6-clips/runs/layout-fix-2026-11-26/videos/"
    "seg186_source.mp4")
CAPTURAS = {
    "779DA4C6": os.path.expanduser(
        "~/workspace/user/files/779DA4C6-681B-4361-BE15-762D3E18AB0A-"
        "photo-B10FF8C1-D2D6-4FFC-936E-A18664F055C9.jpg"),
    "90C42C8E": os.path.expanduser(
        "~/workspace/user/files/90C42C8E-256E-4A89-BD1A-CDC193C4F698-"
        "photo-D3D0DB27-52CC-4B84-A13D-D784484160BF.jpg"),
    "A9AD4C7F": os.path.expanduser(
        "~/workspace/user/files/A9AD4C7F-6577-4316-AD8E-AAFBFEAB07BE-"
        "photo-D9D1B64B-780C-4E06-9928-765D299061EA.jpg"),
}
# Rectángulo del vídeo dentro de las capturas de pantalla del
# teléfono del usuario (1290x2796). Recorte CANÓNICO (§5.3/§11.6):
# puntos (x1,y1)=(107,431) y (x2,y2)=(1182,2363) — en numpy,
# filas 431..2363 y cols 107..1182. Re-verificado 2026-11-27:
# 331/57/22 celdas (capturas 1/2/3); el recorte viejo del test
# (filas 66-2376, cols 95-1195) daba 274/36/22, también verificado.
CAPTURA_VIDEO = (slice(431, 2363), slice(107, 1182))
# Celdas SIFT esperadas por captura con el recorte canónico (§1.2).
ESPERADO_CAPTURAS = {
    "779DA4C6": 331,
    "90C42C8E": 57,
    "A9AD4C7F": 22,
}

# Fotogramas reales a t=7: clip -> debe pasar la puerta.
# CLEAN: cámara real centrada y juego distinto abajo.
# DUP: la cámara ES el fotograma completo (juego dos veces).
# CONT: caja de cámara demasiado ancha (cara + franja de juego).
# 170 (ANCHO): webcam ancha legítima de AuronPlay (cara pequeña
# cx 0.43): pasa porque el SIFT colapsa en 4 celdas y la cara no
# tiene corroboración de juego en la cámara.
# 253/255 (TEMPORAL): el bug solo está en los primeros ~5 s
# (duplicado/PiP) y luego la cámara se ve bien; la puerta los
# caza por el máximo de celdas entre fotogramas (253: 69,
# 255: 8, justo en el umbral).
ESPERADO_T7 = {
    "170": True,
    "183": False, "184": False, "186": False, "189": False,
    "190": True, "191": False, "197": False, "199": False,
    "214": False, "244": False, "246": True, "247": False,
    "248": True, "252": False, "253": False, "254": False,
    "255": False, "256": False,
}

# Medidas a t7 sobre los fotogramas que lee ESTE test (frames/,
# rejilla t2/t7/t20 del layout_gate). Re-medidas 2026-11-27:
# la tabla §5.3 mezcla dos extracciones de t7 distintas — 186,
# 191, 197, 199, 214, 246, 247 y 252 coinciden con frames/,
# mientras 183, 184, 189, 190, 244 y 248 coinciden con la
# extracción de la rejilla del gate (layout-gate-calib/, mismo
# t=7 nominal pero otro JPEG). Los VEREDICTOS coinciden en ambas
# extracciones; las celdas/inliers no: se fijan los valores de
# frames/ y se documenta la discrepancia, no se inventa un
# único valor para las dos. 186 es inl=3 (M-16) en frames/.
ESPERADO_T7_MEDIDAS = {
    "170": (14, 4), "183": (28, 16), "184": (43, 30),
    "186": (3, 3), "189": (7, 4), "190": (2, 2),
    "191": (46, 23), "197": (10, 5), "199": (598, 281),
    "214": (73, 52), "244": (19, 8), "246": (4, 3),
    "247": (201, 101), "248": (3, 3), "252": (61, 22),
    "253": (7, 7), "254": (77, 34), "255": (1, 1), "256": (5, 5),
}

# Conjunto completo (§1.2/§5.3, n=20 fotogramas por clip, gate
# actual): clip -> (pasa, inliers, celdas, share, cx, área).
# 186 PASA aquí (share 0,85, cx 0,475, área 0,167) y FALLA a t7
# (sin cara en ese único fotograma): conjuntos de fotogramas
# distintos, no una contradicción — las dos tablas coexisten.
# 252 vive en frames252/; su inlier no está en calib_final.txt
# (None = no se afirma, solo celdas/share/cara).
ESPERADO_COMPLETO = {
    "12": (False, 90, 42, 0.15, 0.537, 0.014),
    "183": (False, 63, 35, 1.00, 0.445, 0.153),
    "184": (False, 102, 51, 0.85, 0.435, 0.144),
    "186": (True, 6, 6, 0.85, 0.475, 0.167),
    "189": (False, 29, 20, 0.95, 0.448, 0.139),
    "190": (True, 2, 2, 0.55, 0.619, 0.234),
    "191": (False, 501, 231, 1.00, 0.118, 0.044),
    "197": (False, 14, 6, 0.55, 0.45, 0.106),
    "199": (False, 679, 318, 1.00, 0.21, 0.038),
    "214": (False, 170, 96, 1.00, 0.522, 0.116),
    "244": (False, 27, 10, 0.05, 0.412, 0.036),
    "246": (True, 6, 5, 1.00, 0.514, 0.184),
    "247": (False, 366, 151, 0.75, 0.184, 0.019),
    "248": (True, 9, 5, 1.00, 0.503, 0.167),
    "252": (False, None, 70, 0.85, 0.609, 0.202),
}


def _vcfg_y_receta():
    vcfg = load_validator_config()
    return vcfg, vcfg["templates"]["rubius"]


class TestSiftDisponible(unittest.TestCase):
    def test_sift_existe(self):
        # Sin SIFT la puerta falla en voz alta y retiene el clip:
        # si el entorno de CI no lo trae, el fallo debe ser
        # visible aquí, no en producción.
        self.assertIsNotNone(cv2.SIFT_create)


class TestSiftDuplicacionSintetica(unittest.TestCase):
    """SIFT separa duplicado de limpio sin fotogramas reales."""

    @staticmethod
    def _textura_juego(w, h, seed):
        r = np.random.default_rng(seed)
        img = np.zeros((h, w), dtype=np.uint8)
        for _ in range(60):
            x, y = int(r.integers(0, w)), int(r.integers(0, h))
            rad = int(r.integers(20, 120))
            cv2.circle(img, (x, y), rad, int(r.integers(40, 220)), -1)
        for _ in range(25):
            x, y = int(r.integers(0, w)), int(r.integers(0, h))
            ww = int(r.integers(30, 200))
            hh = int(r.integers(30, 200))
            cv2.rectangle(img, (x, y), (x + ww, y + hh),
                          int(r.integers(40, 220)), 3)
        return cv2.GaussianBlur(img, (3, 3), 0)

    @staticmethod
    def _cam_limpia(w, h):
        # Webcam sintética: gradiente suave + elipse (cara) sin
        # textura que case con el juego.
        img = np.tile(np.linspace(60, 180, h, dtype=np.uint8)[:, None],
                      (1, w))
        cv2.ellipse(img, (w // 2, h // 2), (w // 4, h // 3),
                    0, 0, 360, 200, -1)
        cv2.ellipse(img, (w // 2 - w // 10, h // 2 - h // 10),
                    (w // 20, h // 25), 0, 0, 360, 90, -1)
        cv2.ellipse(img, (w // 2 + w // 10, h // 2 - h // 10),
                    (w // 20, h // 25), 0, 0, 360, 90, -1)
        return cv2.GaussianBlur(img, (5, 5), 0)

    def test_duplicado_vs_limpio(self):
        base = self._textura_juego(1920, 1080, 7)
        juego = base[0:1080, 420:1500]
        dup = cv2.resize(base, (1080, 840))
        limpio = self._cam_limpia(1080, 840)
        inl_dup, celdas_dup = _sift_dup(dup, juego)
        inl_lim, celdas_lim = _sift_dup(limpio, juego)
        self.assertGreaterEqual(inl_dup, 8)
        self.assertGreaterEqual(celdas_dup, 8)
        self.assertLess(inl_lim, 8)
        self.assertLess(celdas_lim, 8)
        # Wrapper de compatibilidad (§5.1/§10.1): _sift_dup_inliers
        # devuelve solo los inliers y coincide con el primer
        # elemento de _sift_dup; los sintéticos lo usan directamente.
        self.assertEqual(_sift_dup_inliers(dup, juego), inl_dup)
        self.assertEqual(_sift_dup_inliers(limpio, juego), inl_lim)


@unittest.skipUnless(os.path.isdir(FRAMES_DIR),
                     "fotogramas reales fuera del repo (workspace)")
class TestLayoutFotogramasReales(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vcfg, cls.recipe = _vcfg_y_receta()

    def test_t7_cada_clip(self):
        # A un fotograma (t=7) y en multi-fotograma (TODOS los
        # disponibles: la puerta real toma el máximo entre
        # fotogramas, y un clip marginal (189: 4 celdas a t=7, 9
        # a t=20) o temporal (255: 8 celdas solo a t=4) cambia
        # entre fotogramas. Las dos vistas deben dar el veredicto.
        import glob as _glob
        for clip, debe_pasar in sorted(ESPERADO_T7.items()):
            paths = {float(os.path.basename(p)[len(clip) + 2:-4]): p
                     for p in _glob.glob(os.path.join(
                         FRAMES_DIR, f"{clip}_t*.jpg"))}
            with self.subTest(clip=clip):
                self.assertIn(7.0, paths, f"falta {clip}_t7.jpg")
                res1 = _layout_check_arrays(
                    [(7.0, _frame_array(paths[7.0]))],
                    self.vcfg, self.recipe)
                self.assertEqual(res1["pasa"], debe_pasar,
                                 f"clip {clip} t=7: {res1['motivo']}")
                # Medidas §5.3: inliers y celdas exactos a t7.
                inl, celdas = ESPERADO_T7_MEDIDAS[clip]
                self.assertEqual(res1["sift_max_inliers"], inl,
                                 f"clip {clip} t=7 inliers")
                self.assertEqual(res1["sift_max_cells"], celdas,
                                 f"clip {clip} t=7 celdas")
                arrs = [(t, _frame_array(p))
                        for t, p in sorted(paths.items())]
                res2 = _layout_check_arrays(
                    arrs, self.vcfg, self.recipe)
                self.assertEqual(res2["pasa"], debe_pasar,
                                 f"clip {clip} multi: {res2['motivo']}")

    def test_252_multifotograma(self):
        # El 252 es el caso duro: cinemática del juego a otra
        # escala arriba y abajo; con SIFT de 800 puntos daba 4-7
        # y se escapaba. Con 2000 puntos el máximo entre los 4
        # fotogramas es 68.
        arrs = [(float(t), _frame_array(
            os.path.join(FRAMES_DIR, f"252_t{t}.jpg")))
            for t in (2, 7, 20, 35)]
        res = _layout_check_arrays(arrs, self.vcfg, self.recipe)
        self.assertFalse(res["pasa"], res["motivo"])
        self.assertGreaterEqual(res["sift_max_inliers"], 8)
        self.assertGreaterEqual(res["sift_max_cells"], 8)

    @unittest.skipUnless(
        os.path.isfile(os.path.join(
            RUNS_DIR, "renders", "186_truebox.mp4")),
        "falta el render del 186 con la caja verdadera")
    def test_caja_correcta_pasa(self):
        # El render del segmento del 186 con la caja VERDADERA de
        # la cámara ([0,190,410,410], medida a mano en la fuente)
        # tiene que PASAR la puerta real (20 muestras): cara
        # centrada (cx 0.60), área 0.23, SIFT 3 celdas. Solo pasa
        # porque face_min_share es 0.25: Burn da share 0.45
        # (cuarto oscuro, cara no frontal en la mitad de las
        # muestras) y con 0.5 este render correcto se frenaba.
        # Es el test de regresión del piso de share.
        from clipagent.final_review import layout_gate
        res = layout_gate(
            os.path.join(RUNS_DIR, "renders", "186_truebox.mp4"),
            self.vcfg, self.recipe, template_name="rubius")
        self.assertTrue(res["pasa"], res["motivo"])


@unittest.skipUnless(
    all(os.path.isfile(p) for p in CAPTURAS.values()),
    "capturas del usuario fuera del repo (workspace)")
class TestCapturasUsuario(unittest.TestCase):
    """Las 3 capturas que mandó el usuario: layout duplicado."""

    @classmethod
    def setUpClass(cls):
        cls.vcfg, cls.recipe = _vcfg_y_receta()

    def test_capturas_duplicadas(self):
        for nombre, path in sorted(CAPTURAS.items()):
            with self.subTest(captura=nombre):
                im = cv2.imread(path)
                crop = im[CAPTURA_VIDEO]
                frame = cv2.resize(crop, (1080, 1920))
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                res = _layout_check_arrays(
                    [(7.0, rgb)], self.vcfg, self.recipe)
                self.assertFalse(res["pasa"],
                                 f"captura {nombre}: {res['motivo']}")
                # Valores re-verificados con el recorte canónico
                # (§5.3/§11.6): 331/57/22 celdas.
                self.assertEqual(res["sift_max_cells"],
                                 ESPERADO_CAPTURAS[nombre],
                                 f"captura {nombre} celdas")


@unittest.skipUnless(os.path.isdir(CALIB_DIR),
                     "fotogramas de la rejilla del gate fuera del repo "
                     "(workspace: runs/layout-gate-calib)")
class TestLayoutConjuntoCompleto(unittest.TestCase):
    """Tabla §1.2: los 15 clips con sus 20 fotogramas.

    Coexiste con ESPERADO_T7 porque usa otro conjunto de
    fotogramas: 186 PASA aquí (share 0,85) y FALLA a t7 (sin cara
    en ese único fotograma). El gate en producción usa este
    conjunto (submuestreo a 8 fotogramas repartidos, §5.1).
    """

    @classmethod
    def setUpClass(cls):
        cls.vcfg, cls.recipe = _vcfg_y_receta()

    @staticmethod
    def _frames(clip):
        import glob as _glob
        base = FRAMES252_DIR if clip == "252" else CALIB_DIR
        paths = {float(os.path.basename(p)[len(clip) + 2:-4]): p
                 for p in _glob.glob(os.path.join(
                     base, f"{clip}_t*.jpg"))}
        return [(t, _frame_array(p)) for t, p in sorted(paths.items())]

    def test_conjunto_completo_cada_clip(self):
        for clip, (debe_pasar, inl, celdas, share, cx,
                   area) in sorted(ESPERADO_COMPLETO.items()):
            with self.subTest(clip=clip):
                arrs = self._frames(clip)
                self.assertEqual(len(arrs), 20,
                                 f"clip {clip}: se esperaban 20 "
                                 f"fotogramas, hay {len(arrs)}")
                res = _layout_check_arrays(
                    arrs, self.vcfg, self.recipe)
                self.assertEqual(res["pasa"], debe_pasar,
                                 f"clip {clip}: {res['motivo']}")
                self.assertEqual(res["sift_max_cells"], celdas,
                                 f"clip {clip} celdas")
                if inl is not None:
                    self.assertEqual(res["sift_max_inliers"], inl,
                                     f"clip {clip} inliers")
                self.assertAlmostEqual(res["face_share"], share,
                                       places=2,
                                       msg=f"clip {clip} share")
                if res["face_cx_mediana"] is not None:
                    self.assertAlmostEqual(res["face_cx_mediana"], cx,
                                           delta=0.02,
                                           msg=f"clip {clip} cx")
                    self.assertAlmostEqual(res["face_area_mediana"],
                                           area, delta=0.02,
                                           msg=f"clip {clip} área")


class TestLayoutAvatar(unittest.TestCase):
    """§5.2/§10.1: avatar salta las reglas de cara, no la (i) SIFT.

    12 y 244 fallan (i) con avatar=False Y con avatar=True
    (42/10 celdas): el re-render con la caja ajustada del
    candidato es el que debe pasar, no la exención.
    """

    @classmethod
    def setUpClass(cls):
        cls.vcfg, cls.recipe = _vcfg_y_receta()

    @unittest.skipUnless(os.path.isdir(CALIB_DIR),
                         "fotogramas de la rejilla fuera del repo")
    def test_avatar_no_salva_la_duplicacion(self):
        for clip, celdas in (("12", 42), ("244", 10)):
            with self.subTest(clip=clip):
                arrs = TestLayoutConjuntoCompleto._frames(clip)
                for avatar in (False, True):
                    res = _layout_check_arrays(
                        arrs, self.vcfg, self.recipe, avatar=avatar)
                    self.assertFalse(res["pasa"],
                                     f"clip {clip} avatar={avatar}: "
                                     f"{res['motivo']}")
                    self.assertEqual(res["sift_max_cells"], celdas,
                                     f"clip {clip} avatar={avatar} "
                                     f"celdas")

    @unittest.skipUnless(
        os.path.isfile(os.path.expanduser(
            "~/workspace/gta6-clips/runs/review-en-2026-11-26/"
            "clip_244.mp4")),
        "render del 244 fuera del repo (workspace)")
    def test_face_gate_244_haar_no_ve_el_avatar(self):
        # Corrección a §5.2 (medido 2026-11-27): la cifra del
        # spec (share 0,05, 1/20) es el eslabón facecrop (Haar):
        # Haar NO ve el avatar. Pero el face_gate COMPLETO pasa
        # por YuNet (method=yunet, share 1,0, 20/20 — YuNet sí ve
        # cara en el avatar). La exención avatar=True (I-23) sigue
        # siendo necesaria para el 244 en la puerta de LAYOUT
        # (regla (ii): share Haar 0,05 < 0,25), no en face_gate.
        path = os.path.expanduser(
            "~/workspace/gta6-clips/runs/review-en-2026-11-26/"
            "clip_244.mp4")
        res = face_gate(path, self.vcfg, self.recipe)
        crop = (res.get("links") or {}).get("facecrop") or {}
        self.assertEqual(crop.get("frames_with_face"), 1)
        self.assertEqual(crop.get("frames_total"), 20)
        self.assertAlmostEqual(
            crop["frames_with_face"] / crop["frames_total"],
            0.05, places=2)
        self.assertTrue(res["pasa"], res.get("motivo"))
        self.assertEqual(res["method"], "yunet")

    @unittest.skipUnless(
        os.path.isfile(os.path.expanduser(
            "~/workspace/gta6-clips/runs/review-en-2026-11-26/"
            "clip_12.mp4")),
        "render del 12 fuera del repo (workspace)")
    def test_face_gate_12_pasa_sin_flag(self):
        # 12 lo pasa sin flag (method=yunet, share 1,0, 20/20).
        path = os.path.expanduser(
            "~/workspace/gta6-clips/runs/review-en-2026-11-26/"
            "clip_12.mp4")
        res = face_gate(path, self.vcfg, self.recipe)
        self.assertTrue(res["pasa"], res.get("motivo"))
        self.assertEqual(res["method"], "yunet")
        self.assertAlmostEqual(res["share"], 1.0, places=2)
        self.assertEqual(res["frames_with_face"], 20)

    def test_face_gate_avatar_es_centinela_sin_medir(self):
        # Con avatar=True face_gate se salta entero (I-23): pasa
        # sin leer el vídeo ni la receta (la ruta ni existe).
        res = face_gate("/no/existe.mp4", self.vcfg, self.recipe,
                        avatar=True)
        self.assertTrue(res["pasa"])
        self.assertEqual(res["method"], "avatar")
        self.assertEqual(res["motivo"], "")
        self.assertIsNone(res["share"])


class TestLayoutConfig(unittest.TestCase):
    def test_falta_clave_layout_falla_en_voz_alta(self):
        cfg = load_config()
        del cfg["validator"]["layout_dup_sift_cells"]
        with self.assertRaises(ConfigError) as ctx:
            load_validator_config(cfg)
        self.assertIn("layout_dup_sift_cells", str(ctx.exception))

    def test_claves_layout_resueltas(self):
        vcfg = load_validator_config()
        self.assertEqual(vcfg["layout_dup_sift_cells"], 8)
        self.assertEqual(vcfg["layout_face_sift_cells_min"], 5)
        self.assertEqual(vcfg["layout_face_cx_min"], 0.45)
        self.assertEqual(vcfg["layout_face_cx_max"], 0.65)
        self.assertEqual(vcfg["layout_face_area_min"], 0.15)
        self.assertEqual(vcfg["layout_face_area_closeup_min"], 0.12)
        self.assertEqual(vcfg["layout_sift_max_frames"], 8)

    def test_170_webcam_ancha_pasa(self):
        # El 170 (AuronPlay lejos de la cámara, cara pequeña
        # cx 0.43) es el falso positivo que obligó a contar
        # celdas y a exigir corroboración SIFT para la cara:
        # 15 inliers pero solo 4 celdas.
        path = os.path.join(FRAMES_DIR, "170_t7.jpg")
        vcfg, recipe = _vcfg_y_receta()
        res = _layout_check_arrays(
            [(7.0, _frame_array(path))], vcfg, recipe)
        self.assertTrue(res["pasa"], res["motivo"])
        self.assertLess(res["sift_max_cells"], 8)
        # Caso que protege la corroboración (§10.1): 4 celdas < 5,
        # la regla (iv) no dispara aunque haya 14-15 inliers.
        self.assertEqual(res["sift_max_cells"], 4)
        self.assertEqual(res["sift_max_inliers"], 14)


class TestGuardaCajaCamara(unittest.TestCase):
    def test_fotograma_completo_invalido(self):
        ok, motivo = _cam_box_valida([0, 0, 1920, 1080], 1920, 1080, {})
        self.assertFalse(ok)
        self.assertIn("ancha", motivo)

    def test_caja_demasiado_ancha_invalida(self):
        ok, _ = _cam_box_valida([0, 0, 1200, 600], 1920, 1080, {})
        self.assertFalse(ok)

    def test_caja_186_valida_pero_contaminada(self):
        # La caja del 186 pasa la guarda (tamaño plausible: 21,6%
        # del ancho, 12,2% del área) pero CONTAMINA: la
        # contaminación la caza el validador, no la guarda.
        ok, _ = _cam_box_valida([228, 189, 414, 612], 1920, 1080, {})
        self.assertTrue(ok)

    def test_default_rdjavi_valido(self):
        ok, _ = _cam_box_valida([0, 0, 561, 365], 1920, 1080, {})
        self.assertTrue(ok)

    def test_avatar_salta_solo_posicion(self):
        # §4.1: caja centrada (posición inválida) pero de tamaño
        # plausible: con avatar=False no pasa; con avatar=True sí
        # (la caja avatar la da el detector: esquina + área +
        # present_frac + racha + textura).
        ok, _ = _cam_box_valida([760, 300, 400, 300], 1920, 1080, {})
        self.assertFalse(ok)
        ok, _ = _cam_box_valida(
            [760, 300, 400, 300], 1920, 1080, {}, avatar=True)
        self.assertTrue(ok)

    def test_avatar_no_salta_ancho_ni_area(self):
        # Ancho y área siguen aplicando con avatar=True.
        ok, _ = _cam_box_valida(
            [0, 300, 1200, 300], 1920, 1080, {}, avatar=True)
        self.assertFalse(ok)
        ok, _ = _cam_box_valida(
            [0, 0, 800, 950], 1920, 1080, {}, avatar=True)
        self.assertFalse(ok)

    def test_split_params_fotograma_completo_levanta(self):
        with self.assertRaises(ValueError):
            _split_params([0, 0, 160, 90], {})

    def test_split_params_186_no_levanta(self):
        _split_params([19, 16, 35, 51], {})


@unittest.skipUnless(os.path.isfile(SEG186),
                     "segmento 186 fuera del repo (workspace)")
class TestDecideLayoutGuarda(unittest.TestCase):
    def test_facecam_box_fotograma_completo_no_se_usa(self):
        # La salida de emergencia (fotograma completo como
        # "cámara") NO existe: con una caja explícita inválida,
        # decide_layout cae al auto y ningún tramo usa esa caja.
        res = decide_layout(SEG186, 40.0,
                            cfg={"facecam_box": [0, 0, 1920, 1080]})
        self.assertTrue(res["segments"])
        for seg in res["segments"]:
            params = seg.get("params") or {}
            box = params.get("box")
            self.assertNotEqual(box, [0, 0, 1920, 1080])
            self.assertNotEqual(box, [0, 0, 160, 90])


@unittest.skipUnless(os.path.isfile(SEG186),
                     "segmento 186 fuera del repo (workspace)")
class TestBoxStrategiesLayout(unittest.TestCase):
    def test_ajustada_presente_y_detectada_ausente_en_186(self):
        # find_facecam_box_in_source no encuentra nada en el 186
        # (la cámara de Burn es oscura y toca el borde), así que
        # no hay estrategia "detectada"; la "ajustada" (cara
        # mediana) sí está y el default cierra la lista.
        from clipagent.remake_all import box_strategies
        cfg = load_config()
        estrategias = box_strategies(SEG186, 40.0, cfg)
        nombres = [n for n, _b, _s in estrategias]
        self.assertIn("ajustada", nombres)
        self.assertIn("default", nombres)
        self.assertNotIn("detectada", nombres)

    def test_caja_detectada_invalida_se_filtra(self):
        from clipagent.remake_all import box_strategies
        from clipagent.render import _cam_box_valida, _probe_size
        cfg = load_config()
        detect_fn = lambda _seg, _dur, _cfg: {  # noqa: E731
            "box": [0, 0, 1920, 1080]}
        estrategias = box_strategies(SEG186, 40.0, cfg,
                                     detect_fn=detect_fn)
        nombres = [n for n, _b, _s in estrategias]
        self.assertNotIn("detectada", nombres)
        # Ninguna estrategia usa una caja inválida. El formato
        # de la caja es "w:h:x:y" (ancho:alto:x:y).
        w, h = _probe_size(SEG186)
        for _n, box_str, _sq in estrategias:
            bw, bh, bx, by = (float(v) for v in box_str.split(":"))
            ok, _ = _cam_box_valida([bx, by, bw, bh], w, h,
                                    cfg.get("layout") or {})
            self.assertTrue(ok, f"caja inválida en estrategia: {box_str}")


if __name__ == "__main__":
    unittest.main()
