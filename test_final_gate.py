"""Puerta final DURA (2026-11-19): cara + plantilla sobre el VÍDEO
FINAL, y re-render automático en vivo y VOD. CUATRO plantillas.

Un clip sin cara en el vídeo final NO se encola ni se publica, NI
SIQUIERA en modo observación, EN NINGUNA plantilla; un clip con
duda tampoco pasa; un clip con cara verificada sí pasa. Un clip
con el formato de una plantilla pasa SOLO contra esa plantilla; un
clip con subtítulos sobre el gameplay pasa SOLO si es rubius (en
rdjavi/willyrex siguen prohibidos). Sin plantilla declarada, el
default es rubius.

Los vídeos son sintéticos (ffmpeg lavfi, sin material real): la
receta se dibuja en píxeles y la cara se inyecta con stubs de la
cadena face-crop -> bordes -> YuNet (los detectores reales se
verifican en el re-render de muestra, fuera de la suite).
"""
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from clipagent.config import load_validator_config
from clipagent import final_review as fr


def _templates():
    return {
        "default": "rubius",
        "rdjavi": {
            "cam": {"x": 248, "y": 0, "w": 583, "h": 595},
            "karaoke_band": {"y0": 595, "y1": 749},
            "game": {"y0": 749, "y1": 1786},
            "subs_band": {"y0": 1786, "y1": 1920},
            "subs_style": "karaoke", "subs_over_game": False,
            "cam_full_width": False, "blurred_bg": True},
        "willyrex": {
            "cam": {"x": 0, "y": 0, "w": 1080, "h": 702},
            "title_band": {"y0": 702, "y1": 800},
            "game": {"y0": 800, "y1": 1740},
            "subs_band": {"y0": 1740, "y1": 1920},
            "subs_style": "fixed_yellow", "subs_over_game": False,
            "cam_full_width": True, "blurred_bg": False},
        "rubius": {
            "cam": {"x": 0, "y": 0, "w": 1080, "h": 840},
            "game": {"y0": 840, "y1": 1920},
            "title_banner": {"x0": 103, "x1": 967,
                             "y0": 811, "y1": 1099},
            "subs_zone": {"y0": 900, "y1": 1020},
            "subs_style": "word_white", "subs_over_game": True,
            "cam_full_width": True, "blurred_bg": False},
        # 4ª receta (§6.1): sin `cam`, gameplay a pantalla completa.
        # load_validator_config exige una receta por cada nombre de
        # TEMPLATE_NAMES; sin ella _vcfg() levanta ConfigError.
        "rubius_sin_webcam": {
            "game": {"y0": 0, "y1": 1920},
            "title_banner": {"x0": 103, "x1": 967,
                             "y0": 811, "y1": 1099},
            "subs_zone": {"y0": 900, "y1": 1020},
            "subs_style": "word_white", "subs_over_game": True,
            "cam_full_width": True, "blurred_bg": False},
    }


def _cfg_dict(**over):
    """El dict de config.yaml (sin aplanar) para los tests."""
    base = {
        "validator": {
            "face_sample_every_s": 1, "face_min_frames": 4,
            "face_min_share": 0.5, "face_min_width_frac": 0.08,
            "min_score": 5, "max_rerenders": 1,
            "gemini_hard": False,
            # Umbrales rubius (config.yaml validator.*): mismos
            # valores calibrados que la config de disco.
            "rubius_banner_t0": 0.5, "rubius_banner_t1": 3.5,
            "rubius_banner_yellow_frac": 0.30,
            "rubius_subs_edge_w": 150, "rubius_subs_edge_cov": 0.10,
            "rubius_subs_outline_frac": 0.03,
            "sfx_min_ncc": 0.5,
            # Puerta de layout (2026-11-26): mismos valores
            # calibrados que config.yaml (SIFT 2000 puntos,
            # limpios <= 4 celdas, duplicados >= 8).
            "layout_dup_sift_cells": 8,
            "layout_face_sift_cells_min": 5,
            "layout_face_cx_min": 0.45,
            "layout_face_cx_max": 0.65,
            "layout_face_area_min": 0.15,
            "layout_face_area_closeup_min": 0.12,
            "layout_sift_max_frames": 8,
            "chain": ["facecrop", "edge", "yunet"],
        },
        "final_review": {"enabled": True, "observe_only": True},
        "templates": _templates(),
    }
    base["validator"].update(over)
    return base


def _vcfg(**over):
    """Config del gate (la única fuente: load_validator_config)."""
    return load_validator_config(_cfg_dict(**over))


def _recipe(name):
    return _vcfg()["templates"][name]


# -- vídeos sintéticos baratos (cajas estáticas + parche móvil) -----

def _box(x, y, w, h, color, enable=None):
    s = (f"drawbox=x={x}:y={y}:w={w}:h={h}:color={color}:t=fill")
    if enable:
        s += f":enable='{enable}'"
    return s


def _texture_boxes(template):
    """Cajas grises de textura (gameplay con std>5 y energía de
    bordes en laterales y centro de la cámara)."""
    if template == "rdjavi":
        return [
            _box(300, 100, 200, 150, "0x606060"),
            _box(550, 250, 200, 150, "0x303030"),
            _box(300, 350, 250, 120, "0x505050"),
            _box(100, 900, 300, 200, "0x606060"),
            _box(600, 1200, 300, 200, "0x303030"),
            _box(200, 1500, 250, 150, "0x505050"),
        ]
    boxes = [
        _box(50, 100, 250, 150, "0x606060"),
        _box(400, 200, 300, 150, "0x303030"),
        _box(800, 100, 200, 150, "0x505050"),
        _box(100, 900, 300, 200, "0x606060"),
        _box(600, 1200, 300, 200, "0x303030"),
        _box(200, 1500, 250, 150, "0x505050"),
    ]
    # Rayas verticales en la banda de cámara (toda la anchura):
    # una webcam real tiene textura en todo el ancho; sin ellas
    # los laterales quedan lisos y el chequeo sin_fondo_borroso
    # (energía de bordes laterales vs centro) falla en el vídeo
    # sintético aunque la receta sea correcta.
    for x in range(0, 1080, 60):
        color = "0x303030" if (x // 60) % 2 == 0 else "0x606060"
        boxes.append(_box(x, 0, 30, 702, color))
    return boxes


def _drawboxes(template, variant):
    """Filtros drawbox de la receta (1080x1920)."""
    boxes = list(_texture_boxes(template))
    if template == "willyrex":
        if variant != "sin_banda_titulo":
            boxes.append(_box(0, 702, 1080, 98, "black"))
            boxes.append(_box(200, 730, 680, 20, "white"))
        boxes.append(_box(0, 1740, 1080, 180, "black"))
        if variant == "subs_sobre_gameplay":
            boxes.append(_box(200, 1650, 680, 20, "0xFFFF00"))
        elif variant == "karaoke":
            boxes.append(_box(200, 1800, 680, 20, "white"))
        elif variant == "sin_subs_abajo":
            pass
        else:
            boxes.append(_box(200, 1800, 680, 20, "0xFFFF00"))
    elif template == "rdjavi":
        # Laterales morados lisos (fondo desenfocado) + cámara.
        boxes.append(_box(0, 0, 248, 595, "0x804080"))
        boxes.append(_box(831, 0, 249, 595, "0x804080"))
        if variant == "subs_sobre_gameplay":
            boxes.append(_box(200, 800, 680, 20, "0xFFFF00"))
        else:
            boxes.append(_box(200, 640, 300, 30, "0xFFFF00"))
            boxes.append(_box(550, 640, 300, 30, "white"))
        boxes.append(_box(0, 1786, 1080, 134, "black"))
    elif template == "rubius":
        if variant == "sin_banner":
            en = None
        elif variant == "banner_tarde":
            # Banner fuera de su ventana (t=5-6 s, la receta lo
            # pide en t=0.5-3.5 s): el gate tiene que fallarlo.
            en = "between(t,5,6)"
        else:
            # Banner amarillo transitorio (2-4 s) con borde blanco
            # y dos líneas (negra + roja).
            en = "between(t,2,4)"
        if en:
            boxes.append(
                "drawbox=x=103:y=811:w=864:h=288:color=white:t=6:"
                f"enable='{en}'")
            boxes.append(_box(109, 817, 852, 276, "0xFFFF00", en))
            boxes.append(_box(300, 850, 400, 40, "black", en))
            boxes.append(_box(300, 950, 400, 40, "0xFF0000", en))
        # Palabra blanca en la zona de subtítulos (siempre).
        boxes.append(_box(450, 930, 200, 50, "white"))
        if variant == "con_bandas":
            boxes.append(_box(0, 1740, 1080, 180, "black"))
    return ",".join(boxes)


def _patch_xy(template):
    """Posición animada del parche móvil (dentro de la cámara)."""
    if template == "rdjavi":
        return "mod(280+t*150,550)", 200
    return "mod(100+t*400,700)", 300


_VIDEO_CACHE = {}


def _make_video(template, variant="buena"):
    """Vídeo sintético 1080x1920 (6 s, 12 fps) con la receta (o la
    variante mala). Cacheado por proceso: el mismo archivo sirve
    a varios tests (solo se lee)."""
    key = (template, variant)
    if key in _VIDEO_CACHE:
        return _VIDEO_CACHE[key]
    out = os.path.join(
        tempfile.gettempdir(),
        f"gate_v2_{template}_{variant}.mp4")
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        # Reutiliza solo si es un vídeo completo (una corrida
        # muerta a mitad de codificación deja un archivo corto).
        try:
            from clipagent.thumbnails import get_duration
            if (get_duration(out) or 0) >= 5.5:
                _VIDEO_CACHE[key] = out
                return out
        except Exception:  # noqa: BLE001 - se regenera
            pass
    x_expr, y = _patch_xy(template)
    vf = ("color=c=0x404040:s=1080x1920:d=6,"
          + _drawboxes(template, variant))
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
           "-i", vf, "-f", "lavfi",
           "-i", "color=c=white:s=200x120:d=6"]
    if template == "rubius":
        # Audio con SFX real (vine_boom a t=3 s sobre un tono):
        # el gate rubius verifica SFX, así que el vídeo bueno
        # tiene que llevarlo; sin audio, el chequeo falla.
        sfx = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "clipagent", "sfx", "vine_boom.wav")
        cmd += ["-f", "lavfi",
                "-i", "sine=frequency=440:duration=6",
                "-i", sfx,
                "-filter_complex",
                f"[0:v][1:v]overlay=x='{x_expr}':y={y}[v];"
                "[2:a]volume=0.1[base];"
                "[3:a]adelay=3000:all=1[boom];"
                "[base][boom]amix=inputs=2:normalize=0[aout]",
                "-map", "[v]", "-map", "[aout]",
                "-r", "12", "-c:v", "libx264",
                "-preset", "ultrafast", "-crf", "30",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                out]
    else:
        cmd += ["-filter_complex",
                f"[0:v][1:v]overlay=x='{x_expr}':y={y}[v]",
                "-map", "[v]", "-r", "12", "-c:v", "libx264",
                "-preset", "ultrafast", "-crf", "30",
                "-pix_fmt", "yuv420p", out]
    subprocess.run(cmd, check=True, timeout=120)
    _VIDEO_CACHE[key] = out
    return out


def _make_srt(path, dur=6):
    with open(path, "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,000 --> 00:00:06,000\n"
                "Hola mundo, esto es GTA 6\n")


def _gemini_ok(parts):
    """Juez Gemini que aprueba todo (mock)."""
    criterios = {c: {"score": 9.0, "motivo": "bien"} for c in fr.CRITERIA}
    return json.dumps({"criterios": criterios, "fallos": [],
                       "resumen": "todo bien"})


def _gemini_rechaza(criterio, score=2.0):
    def _fn(parts):
        criterios = {c: {"score": 9.0, "motivo": "bien"}
                     for c in fr.CRITERIA}
        criterios[criterio] = {"score": score, "motivo": "mal"}
        return json.dumps({"criterios": criterios,
                           "fallos": [criterio],
                           "resumen": f"malo: {criterio}"})
    return _fn


def _face_stub(frames_with_face, frames_total=5, available=True):
    def _fn(band_arrays, min_face_w):
        return {"available": available,
                "frames_with_face": frames_with_face,
                "frames_total": frames_total,
                "face_widths": [200] * frames_with_face,
                "detail": "stub face-crop"}
    return _fn


def _edge_stub(fired, frames_with_face=5, box=None):
    def _fn(clip_path, band_arrays, min_face_w):
        return {"available": True, "fired": fired,
                "box": box or [0, 0, 1080, 702],
                "frames_with_face": frames_with_face,
                "frames_total": 5,
                "detail": "stub bordes"}
    return _fn


def _yunet_stub(fired):
    def _fn(clip_path, band_arrays):
        return {"available": True, "fired": fired,
                "frames_with_face": 5 if fired else 0,
                "frames_total": 5,
                "face_widths": [300] if fired else [],
                "detail": "stub YuNet"}
    return _fn


def _layout_stub(pasa=True):
    """Puerta de layout que pasa (mock): los vídeos sintéticos
    de estos tests no tienen cámara ni juego reales; la puerta
    real se prueba en test_layout_gate.py con fotogramas reales."""
    def _fn(clip_path, vcfg, recipe, frames=None, out_dir=None,
            template_name="rubius", avatar=False):
        return {"pasa": pasa,
                "motivo": "" if pasa else "layout: stub",
                "duda": False, "sift_max_inliers": 0,
                "sift_max_cells": 0,
                "face_share": 1.0, "face_cx_mediana": 0.5,
                "face_area_mediana": 0.2, "frames_total": 1,
                "frames_con_cara": 1}
    return _fn


class _SrtCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.srt = os.path.join(self.tmp.name, "clip.srt")
        _make_srt(self.srt)

    def tearDown(self):
        self.tmp.cleanup()


class TestFaceGate(_SrtCase):
    def test_sin_cara_no_pasa_en_ninguna_plantilla(self):
        for name in ("rdjavi", "willyrex", "rubius"):
            res = fr.face_gate(
                _make_video(name), _vcfg(), _recipe(name),
                facecrop_fn=_face_stub(0),
                edge_fn=_edge_stub(False),
                yunet_fn=_yunet_stub(False))
            self.assertFalse(res["pasa"], name)
            self.assertIsNone(res["method"])
            self.assertFalse(res["duda"])

    def test_duda_no_pasa(self):
        res = fr.face_gate(
            _make_video("rubius"), _vcfg(), _recipe("rubius"),
            facecrop_fn=_face_stub(2),
            edge_fn=_edge_stub(False),
            yunet_fn=_yunet_stub(False))
        self.assertFalse(res["pasa"])
        self.assertTrue(res["duda"])
        self.assertEqual(res["method"], "facecrop")

    def test_cara_verificada_pasa_por_facecrop(self):
        res = fr.face_gate(
            _make_video("rubius"), _vcfg(), _recipe("rubius"),
            facecrop_fn=_face_stub(4),
            edge_fn=_edge_stub(False),
            yunet_fn=_yunet_stub(False))
        self.assertTrue(res["pasa"])
        self.assertEqual(res["method"], "facecrop")
        self.assertGreaterEqual(res["share"], 0.5)

    def test_fallback_bordes_cuando_facecrop_no_disponible(self):
        res = fr.face_gate(
            _make_video("willyrex"), _vcfg(), _recipe("willyrex"),
            facecrop_fn=_face_stub(0, available=False),
            edge_fn=_edge_stub(True, frames_with_face=5),
            yunet_fn=_yunet_stub(False))
        self.assertTrue(res["pasa"])
        self.assertEqual(res["method"], "edge")

    def test_fallback_yunet_cuando_bordes_no_disparan(self):
        res = fr.face_gate(
            _make_video("rdjavi"), _vcfg(), _recipe("rdjavi"),
            facecrop_fn=_face_stub(0, available=False),
            edge_fn=_edge_stub(False),
            yunet_fn=_yunet_stub(True))
        self.assertTrue(res["pasa"])
        self.assertEqual(res["method"], "yunet")


class TestTemplateGate(_SrtCase):
    def test_willyrex_buena_pasa_solo_contra_willyrex(self):
        clip = _make_video("willyrex")
        res = fr.template_gate(clip, _vcfg(), _recipe("willyrex"),
                               srt_path=self.srt)
        self.assertTrue(res["pasa"], res["motivo"])
        for other in ("rdjavi", "rubius"):
            res2 = fr.template_gate(clip, _vcfg(), _recipe(other),
                                    srt_path=self.srt)
            self.assertFalse(res2["pasa"], other)

    def test_rdjavi_buena_pasa_solo_contra_rdjavi(self):
        clip = _make_video("rdjavi")
        res = fr.template_gate(clip, _vcfg(), _recipe("rdjavi"),
                               srt_path=self.srt)
        self.assertTrue(res["pasa"], res["motivo"])
        for other in ("willyrex", "rubius"):
            res2 = fr.template_gate(clip, _vcfg(), _recipe(other),
                                    srt_path=self.srt)
            self.assertFalse(res2["pasa"], other)

    def test_rubius_buena_pasa_solo_contra_rubius(self):
        clip = _make_video("rubius")
        res = fr.template_gate(clip, _vcfg(), _recipe("rubius"),
                               srt_path=self.srt)
        self.assertTrue(res["pasa"], res["motivo"])
        for other in ("rdjavi", "willyrex"):
            res2 = fr.template_gate(clip, _vcfg(), _recipe(other),
                                    srt_path=self.srt)
            self.assertFalse(res2["pasa"], other)

    def test_subs_sobre_gameplay_solo_pasan_en_rubius(self):
        # Blanco palabra por palabra sobre el gameplay: correcto
        # en rubius; en willyrex el amarillo sobre el gameplay
        # (y la falta de bandas) no pasa.
        rubius = _make_video("rubius")
        res = fr.template_gate(rubius, _vcfg(), _recipe("rubius"),
                               srt_path=self.srt)
        self.assertTrue(res["pasa"], res["motivo"])
        malo = _make_video("willyrex", "subs_sobre_gameplay")
        res2 = fr.template_gate(malo, _vcfg(), _recipe("willyrex"),
                                srt_path=self.srt)
        self.assertFalse(res2["pasa"])
        self.assertIn("subs_sin_solapar", res2["fallos"])

    def test_willyrex_sin_banda_titulo_falla(self):
        res = fr.template_gate(
            _make_video("willyrex", "sin_banda_titulo"), _vcfg(),
            _recipe("willyrex"), srt_path=self.srt)
        self.assertFalse(res["pasa"])
        self.assertIn("banda_titulo", res["fallos"])

    def test_willyrex_karaoke_falla(self):
        res = fr.template_gate(
            _make_video("willyrex", "karaoke"), _vcfg(),
            _recipe("willyrex"), srt_path=self.srt)
        self.assertFalse(res["pasa"])
        self.assertIn("sin_karaoke", res["fallos"])

    def test_willyrex_sin_subs_abajo_falla(self):
        res = fr.template_gate(
            _make_video("willyrex", "sin_subs_abajo"), _vcfg(),
            _recipe("willyrex"), srt_path=self.srt)
        self.assertFalse(res["pasa"])
        self.assertIn("subtitulos_amarillos", res["fallos"])

    def test_rubius_sin_banner_falla(self):
        res = fr.template_gate(
            _make_video("rubius", "sin_banner"), _vcfg(),
            _recipe("rubius"), srt_path=self.srt)
        self.assertFalse(res["pasa"])
        self.assertIn("titulo_banner", res["fallos"])

    def test_rubius_banner_tarde_falla(self):
        # El banner existe pero fuera de su ventana (t=5-6 s, la
        # receta lo pide en t=0.5-3.5 s): no cuenta.
        res = fr.template_gate(
            _make_video("rubius", "banner_tarde"), _vcfg(),
            _recipe("rubius"), srt_path=self.srt)
        self.assertFalse(res["pasa"])
        self.assertIn("titulo_banner", res["fallos"])

    def test_rubius_con_bandas_falla(self):
        res = fr.template_gate(
            _make_video("rubius", "con_bandas"), _vcfg(),
            _recipe("rubius"), srt_path=self.srt)
        self.assertFalse(res["pasa"])
        self.assertIn("sin_bandas", res["fallos"])


class TestValidateFinalClip(_SrtCase):
    def _validate(self, template_video, plantilla=None, **kw):
        kwargs = {"facecrop_fn": _face_stub(4),
                  "edge_fn": _edge_stub(False),
                  "yunet_fn": _yunet_stub(False),
                  "layout_fn": _layout_stub(True),
                  "gemini_fn": _gemini_ok,
                  "srt_path": self.srt,
                  "plantilla": plantilla}
        kwargs.update(kw)
        return fr.validate_final_clip(
            template_video, "Título", cfg=_cfg_dict(), **kwargs)

    def test_default_es_rubius_sin_plantilla(self):
        # Sin plantilla declarada, el default es rubius: un clip
        # rubius pasa y uno willyrex no.
        res = self._validate(_make_video("rubius"))
        self.assertTrue(res["pasa"], res["motivo"])
        self.assertEqual(res["plantilla"], "rubius")
        res2 = self._validate(_make_video("willyrex"))
        self.assertFalse(res2["pasa"])
        self.assertEqual(res2["plantilla"], "rubius")

    def test_plantilla_declarada_manda(self):
        res = self._validate(_make_video("willyrex"),
                             plantilla="willyrex")
        self.assertTrue(res["pasa"], res["motivo"])
        self.assertEqual(res["plantilla"], "willyrex")

    def test_plantilla_desconocida_falla_en_voz_alta(self):
        from clipagent.validate import ConfigError
        with self.assertRaises(ConfigError):
            self._validate(_make_video("rubius"),
                           plantilla="tiktok")

    def test_sin_cara_no_pasa_ni_en_observacion(self):
        # Modo observación ON y Gemini aprobando: la puerta dura
        # de cara sigue frenando (el fallo que dejó pasar los
        # clips sin cara era justo este: "observado" = pasa).
        for name in ("rubius", "willyrex"):
            res = self._validate(
                _make_video(name), plantilla=name,
                facecrop_fn=_face_stub(0),
                edge_fn=_edge_stub(False),
                yunet_fn=_yunet_stub(False))
            self.assertFalse(res["pasa"], name)
            self.assertEqual(res["decision"], "rechazado")
            self.assertTrue(res["needs_rerender"])
            self.assertIn("cara", res["hard_failures"])
            self.assertTrue(res["observe_only"])

    def test_duda_no_pasa(self):
        res = self._validate(
            _make_video("rubius"),
            facecrop_fn=_face_stub(2),
            edge_fn=_edge_stub(False),
            yunet_fn=_yunet_stub(False))
        self.assertFalse(res["pasa"])
        self.assertTrue(res["needs_rerender"])
        self.assertTrue(res["face"]["duda"])

    def test_cara_y_plantilla_pasan(self):
        res = self._validate(_make_video("rubius"))
        self.assertTrue(res["pasa"], res["motivo"])
        self.assertEqual(res["decision"], "aprobado")
        self.assertTrue(res["hard_pasa"])
        self.assertFalse(res["needs_rerender"])

    def test_plantilla_mala_no_pasa(self):
        res = self._validate(
            _make_video("willyrex", "subs_sobre_gameplay"),
            plantilla="willyrex")
        self.assertFalse(res["pasa"])
        self.assertIn("plantilla", res["hard_failures"])

    def test_gemini_rechaza_cara_pero_la_medida_manda(self):
        # Gemini dice que no hay cara, la cadena la verificó: la
        # medida determinista manda, la discrepancia queda
        # registrada y el clip pasa (observado, no rechazado).
        res = self._validate(
            _make_video("rubius"),
            gemini_fn=_gemini_rechaza("webcam_visible"))
        self.assertTrue(res["pasa"])
        self.assertEqual(res["decision"], "observado")
        self.assertTrue(res["overridden"])

    def test_gemini_subjetivo_rechazado_queda_observado(self):
        res = self._validate(
            _make_video("rubius"),
            gemini_fn=_gemini_rechaza("momento_comprensible"))
        self.assertTrue(res["pasa"])
        self.assertEqual(res["decision"], "observado")

    def test_gemini_hard_rechaza_y_dispara_rerender(self):
        cfg = _cfg_dict(gemini_hard=True)
        res = fr.validate_final_clip(
            _make_video("rubius"), "Título", cfg=cfg,
            srt_path=self.srt,
            facecrop_fn=_face_stub(4), edge_fn=_edge_stub(False),
            yunet_fn=_yunet_stub(False),
            layout_fn=_layout_stub(True),
            gemini_fn=_gemini_rechaza("momento_comprensible"))
        self.assertFalse(res["pasa"])
        self.assertEqual(res["decision"], "rechazado")
        self.assertTrue(res["needs_rerender"])

    def test_sin_gemini_queda_pendiente_sin_rerender(self):
        # Sin GEMINI_API_KEY, el juez queda pendiente: se encola
        # sin marcar (freno para humano), no se re-hace.
        # (get_api_key vive en clipagent.vision; final_review lo
        # importa dentro de review_final_clip.)
        with mock.patch("clipagent.vision.get_api_key",
                        return_value=""):
            res = self._validate(_make_video("rubius"),
                                 gemini_fn=None)
        self.assertEqual(res["decision"], "pendiente_revision")
        self.assertFalse(res["needs_rerender"])
        self.assertTrue(res["hard_pasa"])


class TestConfigUnSoloSitio(unittest.TestCase):
    """Regla single_source_config: el gate lee sus valores de UN
    solo sitio (config.yaml, vía load_validator_config) y TODOS
    los puntos de entrada los ven; si falta un valor, el run
    falla en voz alta."""

    def test_cambiar_un_valor_lo_ven_todos_los_puntos(self):
        # face_min_share 0.3: una cara al 40% pasa; con 0.9 no.
        # El mismo stub, la misma config cambiada en un solo
        # sitio, y la puerta (la que usan vivo, VOD y la puerta
        # de publicación) cambia con él.
        clip = _make_video("rubius")
        for share, esperado in ((0.3, True), (0.9, False)):
            res = fr.face_gate(
                clip, _vcfg(face_min_share=share),
                _recipe("rubius"),
                facecrop_fn=_face_stub(2),
                edge_fn=_edge_stub(False),
                yunet_fn=_yunet_stub(False))
            self.assertEqual(res["pasa"], esperado,
                             f"face_min_share={share}")

    def test_quitar_un_valor_falla_en_voz_alta(self):
        from clipagent.validate import ConfigError
        cfg = _cfg_dict()
        del cfg["validator"]["face_min_share"]
        with self.assertRaises(ConfigError) as ctx:
            load_validator_config(cfg)
        self.assertIn("face_min_share", str(ctx.exception))
        # Y el validador completo también: ningún punto de entrada
        # sigue con un valor inventado.
        with self.assertRaises(ConfigError):
            fr.validate_final_clip(_make_video("rubius"), "Título",
                                   cfg=cfg)

    def test_quitar_outline_frac_falla_en_voz_alta(self):
        """Fix 3: sin validator.rubius_subs_outline_frac el run
        para en voz alta (sin defaults silenciosos): la puerta no
        puede medir texto sin su umbral."""
        from clipagent.validate import ConfigError
        cfg = _cfg_dict()
        del cfg["validator"]["rubius_subs_outline_frac"]
        with self.assertRaises(ConfigError) as ctx:
            load_validator_config(cfg)
        self.assertIn("rubius_subs_outline_frac",
                      str(ctx.exception))

    def test_outline_frac_resuelto_vale_003(self):
        """La clave requerida se resuelve y llega al gate con el
        valor calibrado (R9: scooter 0.034-0.092, FP <= 0.015)."""
        vcfg = load_validator_config(_cfg_dict())
        self.assertEqual(vcfg["rubius_subs_outline_frac"], 0.03)

    def test_quitar_clave_layout_falla_en_voz_alta(self):
        """Sin validator.layout_dup_sift_cells el run para en
        voz alta: la puerta no puede medir duplicación sin su
        umbral (calibrado 2026-11-26: limpios <= 4 celdas,
        duplicados >= 8 con SIFT de 2000 puntos)."""
        from clipagent.validate import ConfigError
        cfg = _cfg_dict()
        del cfg["validator"]["layout_dup_sift_cells"]
        with self.assertRaises(ConfigError) as ctx:
            load_validator_config(cfg)
        self.assertIn("layout_dup_sift_cells", str(ctx.exception))

    def test_claves_layout_resueltas(self):
        vcfg = load_validator_config(_cfg_dict())
        self.assertEqual(vcfg["layout_dup_sift_cells"], 8)
        self.assertEqual(vcfg["layout_face_sift_cells_min"], 5)
        self.assertEqual(vcfg["layout_face_cx_min"], 0.45)
        self.assertEqual(vcfg["layout_face_cx_max"], 0.65)
        self.assertEqual(vcfg["layout_face_area_min"], 0.15)
        self.assertEqual(vcfg["layout_face_area_closeup_min"], 0.12)
        self.assertEqual(vcfg["layout_sift_max_frames"], 8)

    def test_sin_seccion_templates_falla_en_voz_alta(self):
        from clipagent.validate import ConfigError
        cfg = _cfg_dict()
        del cfg["templates"]
        with self.assertRaises(ConfigError):
            load_validator_config(cfg)

    def test_sin_seccion_validator_falla_en_voz_alta(self):
        from clipagent.validate import ConfigError
        # Sección validator vacía: faltan todas sus claves.
        with self.assertRaises(ConfigError):
            load_validator_config({"validator": {}})
        # Sin la sección (pero con final_review encendido): igual.
        with self.assertRaises(ConfigError):
            load_validator_config({"final_review": {"enabled": True}})

    def test_cfg_vacia_carga_config_yaml_desde_disco(self):
        # None o {} NO es "sin config": es "carga config.yaml"
        # (la única fuente). El archivo del repo trae las tres
        # secciones completas.
        for cfg in (None, {}):
            vcfg = load_validator_config(cfg)
            self.assertTrue(vcfg["enabled"])
            self.assertEqual(vcfg["default_template"], "rubius")
            self.assertEqual(vcfg["chain"],
                             ["facecrop", "edge", "yunet"])

    def test_validacion_apagada_no_exige_umbrales(self):
        # final_review.enabled=False: la puerta está apagada y no
        # hacen falta ni umbrales ni recetas (antes rompía con
        # ConfigError por la sección validator).
        vcfg = load_validator_config(
            {"final_review": {"enabled": False}})
        self.assertFalse(vcfg["enabled"])
        res = fr.validate_final_clip(
            "/tmp/noexiste.mp4", "Título",
            cfg={"final_review": {"enabled": False}})
        self.assertEqual(res["decision"], "desactivada")
        self.assertFalse(res["needs_rerender"])

    def test_cadena_fuera_de_orden_falla_en_voz_alta(self):
        from clipagent.validate import ConfigError
        cfg = _cfg_dict(chain=["yunet", "edge", "facecrop"])
        with self.assertRaises(ConfigError) as ctx:
            load_validator_config(cfg)
        self.assertIn("chain", str(ctx.exception))

    def test_default_fuera_de_las_tres_falla_en_voz_alta(self):
        from clipagent.validate import ConfigError
        cfg = _cfg_dict()
        cfg["templates"]["default"] = "tiktok"
        with self.assertRaises(ConfigError) as ctx:
            load_validator_config(cfg)
        self.assertIn("templates.default", str(ctx.exception))


class TestVodNoEncolaSinCara(unittest.TestCase):
    """VOD: un clip sin cara no se encola; si el re-render no es
    posible (sin segmento fuente), queda para revisión humana con
    motivo visible, nunca bloqueado en silencio."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "clips"),
                    exist_ok=True)
        self.video = os.path.join(self.tmp.name, "clips",
                                  "clip_01.mp4")
        with open(self.video, "wb") as f:
            f.write(b"fake-mp4")

    def _clip(self):
        return {"n": 1, "file": "clips/clip_01.mp4", "peak": 3220.0,
                "start": 3200, "duration": 48,
                "viral": {"best_title": "GTA 6: Rubius",
                          "game": "GTA VI", "description": "d",
                          "tags": ["#GTA6"]},
                "viral_score": {"total": 53.8, "band": "alto",
                                "chat_multiple": 1.9},
                "seo_scores": {p: {"score": 94}
                               for p in ("youtube", "tiktok",
                                         "instagram", "facebook")},
                "moment_card": {"game": "GTA VI"}}

    def test_hard_fail_no_encola_y_motivo_visible(self):
        from clipagent.vod_fallback import burn_and_enqueue

        class FakeBridge:
            def __init__(self):
                self.created = []
                self.uploaded = []
                self.published = []

            def create_clip(self, payload):
                self.created.append(payload)
                return 101

            def upload_video(self, clip_id, path):
                self.uploaded.append((clip_id, path))
                return True

        verdict = {"decision": "rechazado", "pasa": False,
                   "hard_pasa": False, "needs_rerender": True,
                   "rerender_reason": "sin cara verificada",
                   "hard_failures": ["cara"],
                   "needs_human_review": True,
                   "motivo": "sin cara verificada",
                   "resumen": "sin cara", "fallos": ["cara"],
                   "scores": {}, "frames_n": 0, "model": "",
                   "transcript_words": 0, "observe_only": True,
                   "face": {"pasa": False}, "template": {"pasa": True},
                   "gemini": {"decision": "rechazado"}}
        bridge = FakeBridge()
        with mock.patch("clipagent.workers.subtitle_clip",
                        return_value={"publish_blocked": False,
                                      "no_speech": False,
                                      "srt": self.video + ".srt",
                                      "burn": self.video,
                                      "transcript": None}), \
                mock.patch(
                    "clipagent.final_review.validate_final_clip",
                    return_value=verdict):
            panel_id, why = burn_and_enqueue(
                bridge, "rubius", "2932415021", self.tmp.name,
                self._clip())
        self.assertIsNone(panel_id)
        self.assertEqual(bridge.created, [])
        self.assertIn("revisión humana", why)


if __name__ == "__main__":
    unittest.main()
