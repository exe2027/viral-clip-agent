#!/usr/bin/env python3
"""Tests de los 4 falsos positivos del validador final (2026-11-23).

Calibración en clips reales (runs/calibrate-fp-2026-11-23):
1. Cara: Haar 5/20 en el 117 era "duda" y la cadena paraba ahí;
   YuNet lo ve 12/12. La duda ya no cierra la puerta: se sigue
   la cadena y YuNet rescata.
2. sin_bandas: el juego oscuro (91 v2b fondo luma 17, 23 fundido
   luma 1) daba banda. Una banda real es casi negra Y uniforme Y
   con salto al contenido adyacente; el juego oscuro no tiene
   salto y el fundido tampoco.
3. titulo_banner: el título amarillo sobre negro (170) no se
   distingue del juego oscuro por píxeles (5 métricas dan lo
   mismo con título que sin él). Cuando el juez LEE el título,
   ya no es fallo duro (rescate en validate_final_clip y en la
   puerta de calidad).
4. subtitulos_blancos: el primer cue del 174 cae entero en la
   ventana del banner (0.5-3.5 s) y make_word_ass suprime esas
   palabras: contar esos instantes como "activos" era el falso
   negativo. Además el umbral es el de UNA palabra corta.

Sin red, sin D1, sin Gemini real (el rescate usa un gemini_fn
inyectado). Los clips reales solo se usan en el test de rescate
del título (170).

2026-11-24 (run 45299157326, 4/5 cortes rechazados solo por la
plantilla tras el arreglo de visión): dos calibraciones más en
la puerta de plantilla, ambas con revisión pez dorado:
5. subtitulos_una_palabra: el juego claro (cielo, carretera)
cubre los bordes con blanco aunque el subtítulo sea una sola
palabra centrada (IlloJuan: 1/18 en t=39.0s, palabra "But").
Un solo fotograma ya no es línea completa: falla solo si hay
>=2 fotogramas Y >10% de los activos (la línea completa real
es sistemática: scooter 4/7, 57%).
6. sfx_presente: decide_sfx devuelve [] por diseño en clips
hablados sin dip de silencio ni grito ni fail. La puerta ahora
verifica lo que SE DECIDIÓ poner: si no se decidió nada, pasa;
si se decidió SFX y no aparece en el audio, sigue fallando
(el mezclador roto sigue detectándose).
"""

import json
import os
import sys
import unittest
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.final_review import (
    _template_word_white,
    face_gate,
    template_gate,
    validate_final_clip,
)
from clipagent.quality_gate import build_quality_gate

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)

RUBIUS_RECIPE = {
    "cam": {"x": 0, "y": 0, "w": 1080, "h": 840},
    "game": {"y0": 840, "y1": 1920},
    "title_banner": {"x0": 103, "x1": 967, "y0": 811, "y1": 1099},
    "subs_zone": {"y0": 900, "y1": 1020},
    "subs_style": "word_white",
}

VCFG_FACE = {
    "face_min_share": 0.5,
    "face_min_frames": 1,
    "face_min_width_frac": 0.08,
    "face_sample_every_s": 2.0,
}


def _jpg(path, arr):
    from PIL import Image

    Image.fromarray(arr.astype(np.uint8)).save(path)


def _frames(tmp_path, n=4, seed=7):
    """n fotogramas 1080x1920 legibles, con variación entre ellos
    (para que banda_viva no los vea congelados)."""
    from pathlib import Path

    tmp_path = Path(tmp_path)
    rng = np.random.default_rng(seed)
    paths = []
    for i in range(n):
        arr = rng.integers(0, 30, size=(1920, 1080, 3), dtype=np.uint8)
        # Cámara con textura que cambia (viva).
        arr[0:840, :, :] = rng.integers(
            40, 120, size=(840, 1080, 3), dtype=np.uint8
        )
        p = tmp_path / f"f{i}.jpg"
        _jpg(str(p), arr)
        paths.append({"path": str(p), "ts": float(i * 2)})
    return paths


def _word_white_arrays(n=4, seed=7, game_luma=20, game_std=15):
    """Arrays 1080x1920 para _template_word_white: cámara con
    textura y juego oscuro con textura (sin banda real)."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        a = np.zeros((1920, 1080, 3), dtype=np.uint8)
        a[0:840] = rng.integers(40, 120, size=(840, 1080, 3))
        game = rng.normal(game_luma, game_std, size=(1080, 1080, 3))
        a[840:1920] = np.clip(game, 0, 255).astype(np.uint8)
        out.append((float(i * 2), a))
    return out


def _run_word_white(arrays, spans, recipe=None):
    checks = {}

    def _check(name, ok, medicion, motivo=""):
        checks[name] = {
            "pasa": bool(ok),
            "medicion": medicion,
            "motivo": motivo if not ok else "",
        }
        return bool(ok)

    _template_word_white(
        _check, arrays, spans, recipe or RUBIUS_RECIPE, {}, vcfg=None
    )
    return checks


class TestFaceDudaSigueLaCadena(unittest.TestCase):
    def test_haar_duda_y_yunet_fuerte_pasa_por_yunet(self):
        """Clip 117: Haar 5/20 (duda) ya no para la cadena."""
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            frames = _frames(d, n=4)
            res = face_gate(
                os.path.join(d, "clip.mp4"),
                VCFG_FACE,
                RUBIUS_RECIPE,
                frames=frames,
                out_dir=d,
                facecrop_fn=lambda _b, _w: {
                    "available": True,
                    "frames_with_face": 5,
                    "frames_total": 20,
                    "face_widths": [200],
                    "detail": "Haar en la banda de webcam",
                },
                edge_fn=lambda _c, _b, _w: {
                    "available": True,
                    "fired": False,
                    "detail": "sin rectángulo fijo",
                },
                yunet_fn=lambda _c, _b: {
                    "available": True,
                    "fired": True,
                    "frames_with_face": 20,
                    "frames_total": 20,
                    "face_widths": [200],
                    "detail": "YuNet modo face, confianza 0.75",
                },
            )
            self.assertTrue(res["pasa"], res)
            self.assertEqual(res["method"], "yunet")
            self.assertFalse(res["duda"])

    def test_haar_duda_y_nadie_mas_devuelve_duda_no_fallo_limpio(self):
        """Si ningún eslabón pasa, la duda se devuelve como duda."""
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            frames = _frames(d, n=4)
            res = face_gate(
                os.path.join(d, "clip.mp4"),
                VCFG_FACE,
                RUBIUS_RECIPE,
                frames=frames,
                out_dir=d,
                facecrop_fn=lambda _b, _w: {
                    "available": True,
                    "frames_with_face": 5,
                    "frames_total": 20,
                    "face_widths": [200],
                    "detail": "Haar en la banda de webcam",
                },
                edge_fn=lambda _c, _b, _w: {
                    "available": True,
                    "fired": False,
                    "detail": "sin rectángulo fijo",
                },
                yunet_fn=lambda _c, _b: {
                    "available": True,
                    "fired": False,
                    "detail": "YuNet modo none",
                },
            )
            self.assertFalse(res["pasa"])
            self.assertTrue(res["duda"])
            self.assertEqual(res["method"], "facecrop")
            self.assertEqual(res["frames_with_face"], 5)


class TestSinBandas(unittest.TestCase):
    def test_juego_oscuro_sin_salto_no_es_banda(self):
        """91 v2b: fondo luma 17 sin salto -> no es banda."""
        arrays = _word_white_arrays(game_luma=20, game_std=15)
        checks = _run_word_white(arrays, spans=[])
        self.assertTrue(checks["sin_bandas"]["pasa"], checks["sin_bandas"])

    def test_fundido_a_negro_no_es_banda(self):
        """23: todo negro (sin salto) -> no es banda."""
        arrays = _word_white_arrays(game_luma=1, game_std=0)
        checks = _run_word_white(arrays, spans=[])
        self.assertTrue(checks["sin_bandas"]["pasa"], checks["sin_bandas"])

    def test_banda_real_abajo_si_se_detecta(self):
        """Franja inferior negra sólida + juego claro encima: SÍ
        es banda y debe fallar."""
        rng = np.random.default_rng(11)
        arrays = []
        for i in range(4):
            a = np.zeros((1920, 1080, 3), dtype=np.uint8)
            a[0:840] = rng.integers(40, 120, size=(840, 1080, 3))
            # Juego claro (luma ~110) salvo la franja inferior.
            a[840:1920] = rng.integers(90, 130, size=(1080, 1080, 3)).astype(
                np.uint8
            )
            a[1800:1920] = 2  # banda negra sólida abajo
            arrays.append((float(i * 2), a))
        checks = _run_word_white(arrays, spans=[])
        self.assertFalse(checks["sin_bandas"]["pasa"], checks["sin_bandas"])


class TestSubtitulosBlancos(unittest.TestCase):
    def test_cue_en_ventana_del_banner_no_cuenta_como_activo(self):
        """174: el cue 1.2-2.5 s cae en la ventana del banner y
        sus palabras se suprimen; no debe contar como activo."""
        arrays = _word_white_arrays(n=4)
        # Sin blanco en la zona de subtítulos en ningún frame.
        checks = _run_word_white(arrays, spans=[(1.229, 2.549)])
        # Sin instantes activos fuera del banner: débil, no fallo.
        self.assertTrue(
            checks["subtitulos_blancos"]["pasa"], checks["subtitulos_blancos"]
        )

    def test_palabra_corta_fuera_del_banner_si_cuenta(self):
        """Una palabra corta (~200 px blancos) fuera de la ventana
        del banner cuenta con el umbral de palabra corta."""
        arrays = _word_white_arrays(n=4)
        # Palabra corta en el frame t=6 (fuera del banner 0.5-3.5).
        ts, a = arrays[3]
        a[950:960, 400:420] = 255  # 200 px blancos: palabra corta
        arrays[3] = (ts, a)
        checks = _run_word_white(arrays, spans=[(5.0, 7.0)])
        self.assertTrue(
            checks["subtitulos_blancos"]["pasa"], checks["subtitulos_blancos"]
        )
        self.assertIn("100%", checks["subtitulos_blancos"]["medicion"])

    def test_sin_palabra_en_instante_activo_si_falla(self):
        """El arreglo no es laxo: sin blanco en el instante
        activo, sigue fallando."""
        arrays = _word_white_arrays(n=4)
        checks = _run_word_white(arrays, spans=[(5.0, 7.0)])
        self.assertFalse(
            checks["subtitulos_blancos"]["pasa"], checks["subtitulos_blancos"]
        )


class TestTituloRescate(unittest.TestCase):
    def test_titulo_legible_segun_juez_no_es_fallo_duro(self):
        """170: banda no medida pero el juez lee el título ->
        el criterio es suave (no bloquea en observación)."""
        review = {
            "decision": "aprobado",
            "pasa": True,
            "hard_pasa": True,
            "observe_only": True,
            "scores": {
                "webcam_visible": 8.0,
                "titulo_legible": 8.0,
                "subtitulos_ok": 8.0,
                "sin_barras_negras": 8.0,
                "momento_comprensible": 8.0,
                "subtitulos_coherentes": 8.0,
                "plantilla": 8.0,
            },
            "fallos": [],
            "motivo": "",
            "transcript_words": 12,
            "face": {
                "pasa": True,
                "method": "facecrop",
                "share": 1.0,
                "motivo": "",
            },
            "template": {
                "pasa": False,
                "checks": {
                    "dimensiones": {"pasa": True},
                    "titulo_banner": {"pasa": False},
                },
            },
        }
        qg = build_quality_gate(review, thumbnail_text="EL GOL")
        self.assertEqual(qg["decision"], "observado")
        self.assertTrue(qg["pasa"])
        self.assertIn("titulo", qg["fallos"])
        self.assertNotIn("titulo", qg["fallos_duros"])
        self.assertFalse(qg["criterios"]["titulo"]["duro"])

    def test_titulo_ilegible_tambien_para_el_juez_si_es_duro(self):
        """173: sin banda Y el juez tampoco lo lee -> duro."""
        review = {
            "decision": "aprobado",
            "pasa": True,
            "hard_pasa": True,
            "observe_only": True,
            "scores": {
                "webcam_visible": 8.0,
                "titulo_legible": 2.0,
                "subtitulos_ok": 8.0,
                "sin_barras_negras": 8.0,
                "momento_comprensible": 8.0,
                "subtitulos_coherentes": 8.0,
                "plantilla": 8.0,
            },
            "fallos": [],
            "motivo": "",
            "transcript_words": 12,
            "face": {
                "pasa": True,
                "method": "facecrop",
                "share": 1.0,
                "motivo": "",
            },
            "template": {
                "pasa": False,
                "checks": {
                    "dimensiones": {"pasa": True},
                    "titulo_banner": {"pasa": False},
                },
            },
        }
        qg = build_quality_gate(review, thumbnail_text="EL GOL")
        self.assertEqual(qg["decision"], "rechazado")
        self.assertIn("titulo", qg["fallos_duros"])
        self.assertTrue(qg["criterios"]["titulo"]["duro"])


class TestTituloRescateE2E(unittest.TestCase):
    def test_170_con_juez_leyendo_no_es_rechazado_duro(self):
        """170 real: solo titulo_banner falla en la plantilla
        (SFX simulado presente) y el juez lee el título -> el
        validador ya no lo rechaza por la puerta dura."""
        clip = os.path.join(
            BASE, "manual-ig-Df_ziDOI4q_", "clip_auronplay_lucia_25s.mp4"
        )
        if not os.path.isfile(clip):
            pytest.skip("sin el MP4 local del 170")
        criterios = {
            c: {"score": 8.0, "motivo": "ok"}
            for c in (
                "webcam_visible",
                "titulo_legible",
                "subtitulos_ok",
                "sin_barras_negras",
                "momento_comprensible",
                "subtitulos_coherentes",
                "plantilla",
            )
        }
        payload = json.dumps(
            {"criterios": criterios, "fallos": [], "resumen": "todo bien"}
        )
        # SFX simulado presente: el 170 es anterior al SFX y sin
        # él la plantilla fallaría también por sfx_presente; el
        # rescate del título se prueba aislado.
        with mock.patch(
            "clipagent.final_review.detect_sfx_in_clip",
            return_value={
                "found": True,
                "effect": "vine_boom",
                "corr": 0.99,
                "lag_s": 6.0,
            },
        ):
            res = validate_final_clip(
                clip,
                "GTA 6: AuronPlay se harta de Lucía",
                transcript_text="intensita hoy",
                plantilla="rubius",
                gemini_fn=lambda _parts: payload,
                facecrop_fn=lambda _b, _w: {
                    "available": True,
                    "frames_with_face": 20,
                    "frames_total": 20,
                    "face_widths": [200],
                    "detail": "test",
                },
                out_dir=os.path.join(
                    BASE, "runs/verify-fixes-2026-11-23", "t170"
                ),
            )
        self.assertTrue(res["hard_pasa"], res)
        self.assertNotEqual(res["decision"], "rechazado", res)
        self.assertTrue(
            any("titulo_banner" in o for o in res.get("overridden") or []),
            res.get("overridden"),
        )


def _paint_banner_zone(arrays, idx, paint):
    """Pinta en la zona del banner (x103-967, y811-1099) del
    fotograma idx. ``paint`` recibe el array y pinta en él."""
    ts, a = arrays[idx]
    paint(a)
    arrays[idx] = (ts, a)


def _paint_title_yellow_on_black(a):
    """Título amarillo sobre negro (caso 170): fondo negro en la
    zona del banner + dos líneas de trazos amarillos gruesos.
    Los trazos suman 12.000 px amarillos: dentro del rango
    medido en títulos reales (10.410-33.517 px a 90-110 px,
    calibración V7 2026-11-23), muy por encima del amarillo
    disperso de juego sin título (scooter: 890 px)."""
    a[811:1099, 103:967] = 0
    a[850:862, 200:800] = (255, 255, 0)   # línea 1: 600x12
    a[950:962, 300:700] = (255, 255, 0)   # línea 2: 400x12


def _paint_scattered_yellow_on_black(a, n=1000):
    """Amarillo disperso sobre negro, SIN título: 1.000 px
    amarillos sueltos (caso scooter: 890 px). El negro solo no
    basta: sin suficientes píxeles amarillos debe seguir
    fallando."""
    a[811:1099, 103:967] = 0
    rng = np.random.default_rng(23)
    ys = rng.integers(811, 1099, size=n)
    xs = rng.integers(103, 967, size=n)
    a[ys, xs] = (255, 255, 0)


def _paint_classic_banner(a):
    """Banner amarillo clásico sólido (caso 52h: 79% amarillo)."""
    a[811:1099, 103:967] = (255, 220, 0)
    a[850:862, 200:800] = (0, 0, 0)       # texto negro línea 1
    a[950:962, 300:700] = (255, 255, 255)  # borde blanco


def _paint_sub_word(arrays, idx, w, h):
    """Palabra blanca centrada en la zona de subtítulos
    (recorte x350-730, y900-1020): un rectángulo de w*h px
    blancos que simula los trazos de la palabra. El chequeo
    solo cuenta píxeles de color, no hace OCR."""
    ts, a = arrays[idx]
    x0 = 540 - w // 2
    a[940:940 + h, x0:x0 + w] = 255
    arrays[idx] = (ts, a)


class TestTituloAmarilloSobreNegro(unittest.TestCase):
    """V7 (caso 170): el título amarillo sobre negro debe PASAR
    el chequeo de píxeles, no solo rescatarse por el juez."""

    def test_titulo_amarillo_sobre_negro_pasa(self):
        arrays = _word_white_arrays(n=4)
        # t=2 s está en la ventana del banner (0.5-3.5 s).
        _paint_banner_zone(arrays, 1, _paint_title_yellow_on_black)
        checks = _run_word_white(arrays, spans=[])
        self.assertTrue(
            checks["titulo_banner"]["pasa"], checks["titulo_banner"])

    def test_banner_amarillo_clasico_sigue_pasando(self):
        arrays = _word_white_arrays(n=4)
        _paint_banner_zone(arrays, 1, _paint_classic_banner)
        checks = _run_word_white(arrays, spans=[])
        self.assertTrue(
            checks["titulo_banner"]["pasa"], checks["titulo_banner"])

    def test_sin_titulo_sigue_fallando(self):
        arrays = _word_white_arrays(n=4)
        checks = _run_word_white(arrays, spans=[])
        self.assertFalse(
            checks["titulo_banner"]["pasa"], checks["titulo_banner"])

    def test_amarillo_disperso_sobre_negro_sigue_fallando(self):
        """El negro de fondo no basta: 1.000 px amarillos
        dispersos (scooter: 890) no son un título."""
        arrays = _word_white_arrays(n=4)
        _paint_banner_zone(arrays, 1, _paint_scattered_yellow_on_black)
        checks = _run_word_white(arrays, spans=[])
        self.assertFalse(
            checks["titulo_banner"]["pasa"], checks["titulo_banner"])


class TestSubtitulosPalabrasCortas(unittest.TestCase):
    """V7 (caso 174): palabras cortas de 1-2 caracteres deben
    contar como subtítulo presente. Las palabras se pintan en
    el fotograma t=6 s, instante activo del cue 5-7 s (fuera de
    la ventana del banner)."""

    def test_palabra_de_una_letra_pasa(self):
        """'y'/'a' realista a ~110 px: ~1.200 px blancos."""
        arrays = _word_white_arrays(n=4)
        _paint_sub_word(arrays, 3, w=20, h=60)
        checks = _run_word_white(arrays, spans=[(5.0, 7.0)])
        self.assertTrue(
            checks["subtitulos_blancos"]["pasa"],
            checks["subtitulos_blancos"])

    def test_palabra_de_dos_letras_pasa(self):
        arrays = _word_white_arrays(n=4)
        _paint_sub_word(arrays, 3, w=40, h=35)
        checks = _run_word_white(arrays, spans=[(5.0, 7.0)])
        self.assertTrue(
            checks["subtitulos_blancos"]["pasa"],
            checks["subtitulos_blancos"])

    def test_palabra_fina_degradada_pasa(self):
        """Glifo fino de 1 letra degradado por contorno negro y
        JPEG: solo 120 px blancos. El umbral viejo (150 en
        final_review, 300 en render_gate) lo rechazaba."""
        arrays = _word_white_arrays(n=4)
        _paint_sub_word(arrays, 3, w=10, h=12)
        checks = _run_word_white(arrays, spans=[(5.0, 7.0)])
        self.assertTrue(
            checks["subtitulos_blancos"]["pasa"],
            checks["subtitulos_blancos"])

    def test_sin_subtitulo_sigue_fallando(self):
        arrays = _word_white_arrays(n=4)
        checks = _run_word_white(arrays, spans=[(5.0, 7.0)])
        self.assertFalse(
            checks["subtitulos_blancos"]["pasa"],
            checks["subtitulos_blancos"])


class TestBannerTitlePresentHelpers(unittest.TestCase):
    """Los dos validadores (final_review, el que decide, y
    render_gate, su espejo de CircleCI) comparten la misma
    medición: misma zona, mismo veredicto. Offline, sin Gemini."""

    def _zone(self, paint):
        a = np.zeros((288, 864, 3), dtype=np.uint8)
        paint(a)
        return a

    def test_helpers_coinciden_en_los_cuatro_casos(self):
        from clipagent.final_review import _banner_title_present as f1
        from clipagent.render_gate import _banner_title_present as f2

        def texto(a):
            a[:, :] = 0
            a[39:51, 97:697] = (255, 255, 0)
            a[139:151, 197:597] = (255, 255, 0)

        def clasico(a):
            a[:, :] = (255, 220, 0)
            a[39:51, 97:697] = (0, 0, 0)
            a[139:151, 197:597] = (255, 255, 255)

        def disperso(a):
            a[:, :] = 0
            rng = np.random.default_rng(23)
            a[rng.integers(0, 288, size=1000),
              rng.integers(0, 864, size=1000)] = (255, 255, 0)

        def vacio(a):
            a[:, :] = 0

        for name, paint, esperado in (
                ("texto amarillo/negro", texto, True),
                ("banner clasico", clasico, True),
                ("amarillo disperso", disperso, False),
                ("negro vacio", vacio, False)):
            for fn in (f1, f2):
                ok, detalle = fn(self._zone(paint), 0.30)
                self.assertEqual(ok, esperado, (name, fn.__module__,
                                                detalle))

    def test_subs_white_present_mirror(self):
        """render_gate ya no usa 300 px: una palabra fina de
        120 px blancos pasa y el recorte vacío sigue fallando."""
        from clipagent.render_gate import _subs_white_present

        crop = np.zeros((120, 380, 3), dtype=np.uint8)
        self.assertFalse(_subs_white_present(crop))
        crop[40:52, 185:195] = 255  # 120 px: glifo fino de 1 letra
        self.assertTrue(_subs_white_present(crop))


def _paint_both_edges(arrays, idxs):
    """Pinta blanco los dos bordes de la zona de subtítulos
    (filas 900-1020, cols 0-150 y 930-1080) en los fotogramas
    indicados: imita un fotograma con texto en ambos bordes."""
    out = []
    for i, (ts, a) in enumerate(arrays):
        a = a.copy()
        if i in idxs:
            a[900:1020, 0:150] = 255
            a[900:1020, 930:1080] = 255
        out.append((ts, a))
    return out


class TestUnaPalabraTolerancia(unittest.TestCase):
    """2026-11-24: un solo fotograma con ambos bordes no es
    línea completa (el juego claro cubre los bordes). Falla
    solo si hay >=2 fotogramas Y >10% de los activos."""

    def test_un_fotograma_de_18_pasa_caso_illojuan(self):
        """El caso real: 1/18 en t=39.0s, palabra "But" (el
        blanco de los bordes era el cielo del juego)."""
        arrays = _paint_both_edges(_word_white_arrays(n=19), {18})
        checks = _run_word_white(arrays, [(0.0, 100.0)])
        self.assertTrue(checks["subtitulos_una_palabra"]["pasa"],
                        checks["subtitulos_una_palabra"])

    def test_un_fotograma_de_4_pasa_denominador_pequeno(self):
        """El mismo defecto físico (1 fotograma) no puede pasar
        o fallar según cuánto habló el streamer."""
        arrays = _paint_both_edges(_word_white_arrays(n=5), {4})
        checks = _run_word_white(arrays, [(0.0, 100.0)])
        self.assertTrue(checks["subtitulos_una_palabra"]["pasa"],
                        checks["subtitulos_una_palabra"])

    def test_cuatro_de_6_sigue_fallando_caso_scooter(self):
        """La línea completa real es sistemática (scooter 4/7)."""
        arrays = _paint_both_edges(
            _word_white_arrays(n=7), {2, 3, 4, 5})
        checks = _run_word_white(arrays, [(0.0, 100.0)])
        self.assertFalse(checks["subtitulos_una_palabra"]["pasa"],
                         checks["subtitulos_una_palabra"])

    def test_dos_de_19_sigue_fallando(self):
        """2 fotogramas Y 10.5% > 10%: sigue siendo línea
        completa, no ruido de un fotograma."""
        arrays = _paint_both_edges(
            _word_white_arrays(n=20), {18, 19})
        checks = _run_word_white(arrays, [(0.0, 100.0)])
        self.assertFalse(checks["subtitulos_una_palabra"]["pasa"],
                         checks["subtitulos_una_palabra"])


VCFG_TEMPLATE = {
    "face_sample_every_s": 2.0,
    "rubius_banner_t0": 0.5,
    "rubius_banner_t1": 3.5,
    "rubius_banner_yellow_frac": 0.30,
    "rubius_subs_edge_w": 150,
    "rubius_subs_edge_cov": 0.10,
    "sfx_min_ncc": 0.5,
}


class TestSfxPresenteDecidido(unittest.TestCase):
    """2026-11-24: sfx_presente verifica lo que SE DECIDIÓ poner,
    no lo que nunca se decidió (decide_sfx [] en clips hablados)."""

    def _gate(self, tmp_path, sfx_events, detect):
        frames = _frames(tmp_path, n=4)
        with mock.patch(
                "clipagent.final_review.detect_sfx_in_clip",
                return_value=detect) as det:
            res = template_gate(
                "clip.mp4", VCFG_TEMPLATE, RUBIUS_RECIPE,
                frames=frames, face_result={}, sfx_events=sfx_events)
        return res, det

    def test_sin_sfx_decididos_pasa_sin_llamar_al_detector(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            res, det = self._gate(
                d, [], {"found": False, "corr": 0.19,
                        "motivo": "sin SFX del pack en el audio"})
            self.assertTrue(res["checks"]["sfx_presente"]["pasa"],
                            res["checks"]["sfx_presente"])
            det.assert_not_called()

    def test_sfx_decidido_y_ausente_sigue_fallando(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            res, _det = self._gate(
                d, [{"effect": "riser", "t": 18.0}],
                {"found": False, "corr": 0.19,
                 "motivo": "sin SFX del pack en el audio"})
            self.assertFalse(res["checks"]["sfx_presente"]["pasa"],
                             res["checks"]["sfx_presente"])

    def test_sfx_decidido_y_presente_pasa(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            res, _det = self._gate(
                d, [{"effect": "riser", "t": 18.0}],
                {"found": True, "effect": "riser", "corr": 0.98,
                 "lag_s": 18.0})
            self.assertTrue(res["checks"]["sfx_presente"]["pasa"],
                            res["checks"]["sfx_presente"])

    def test_sin_info_de_sfx_exige_como_antes(self):
        """sfx_events=None (llamador sin la info: VOD, workers):
        el comportamiento viejo, sin cambio."""
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            res, det = self._gate(
                d, None, {"found": False, "corr": 0.19,
                          "motivo": "sin SFX del pack en el audio"})
            self.assertFalse(res["checks"]["sfx_presente"]["pasa"],
                             res["checks"]["sfx_presente"])
            det.assert_called_once()


if __name__ == "__main__":
    unittest.main()
