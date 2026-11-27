"""Detector de Burn (spec inglés correcto v7.1, §10.3).

Burn NO tiene coordenadas documentadas en ningún sitio (§4.1):
el test no fija cajas, corre el detector sobre material real de
Burn y afirma que las cajas devueltas pasan la regla §4.1
(posición en esquina + y0 < 0,50 + ancho < 0,45 + área < 0,35).

Material disponible en el workspace (medido 2026-11-27):
- `videos/183.mp4` y `videos/191.mp4` (runs/layout-fix-2026-
  11-26): renders de 40 s de Burn; `detect_vod_facecam`
  devuelve caja en ambos (183 por voto, hits 2; 191 por rescate
  de bordes, hits 1). Son renders, no segmentos fuente: en el
  workspace no hay más segmentos fuente de Burn que el 186 (ver
  abajo), y el detector solo devuelve caja sobre estos renders.
- `videos/seg186_source.mp4`: el ÚNICO segmento fuente real de
  Burn en el workspace; el detector NO devuelve nada (cámara
  oscura que toca el borde). Es un hueco documentado, no un
  pase: `test_seg186_source_sin_deteccion_documentado` lo fija
  para que una mejora del detector lo haga visible.

Si los fixtures no están en el workspace, cada test se SALTA
(skipUnless) con mensaje explícito: en CI nunca están (viven
en runs/, fuera del repo público). Con fixtures presentes, una
detección vacía FALLA explícitamente (I-11): nunca un pase por
defecto.
"""
import os
import unittest

VIDEOS_DIR = os.path.expanduser(
    "~/workspace/gta6-clips/runs/layout-fix-2026-11-26/videos")

# clip -> ruta del fixture. 183: voto (hits 2); 191: rescate de
# bordes (hits 1). Medido 2026-11-27 con detect_vod_facecam.
FIXTURES = {
    "183": os.path.join(VIDEOS_DIR, "183.mp4"),
    "191": os.path.join(VIDEOS_DIR, "191.mp4"),
}
SEG186_SOURCE = os.path.join(VIDEOS_DIR, "seg186_source.mp4")

SAMPLE_W, SAMPLE_H = 160, 90


class TestDetectorBurn(unittest.TestCase):
    def test_cajas_devueltas_pasan_la_regla_41(self):
        from clipagent.render import _cam_box_valida
        from clipagent.render import detect_vod_facecam
        from clipagent.yunet_facecam import _caja_en_esquina
        for clip, path in sorted(FIXTURES.items()):
            with self.subTest(clip=clip):
                if not os.path.isfile(path):
                    self.skipTest(
                        f"fixture de Burn fuera del workspace: "
                        f"{path} (en CI nunca está: vive en runs/, "
                        f"fuera del repo público)")
                res = detect_vod_facecam(path, 40.0, cfg={})
                # Detección vacía con fixtures presentes: FALLO
                # explícito, nunca pase por defecto (I-11).
                box = (res or {}).get("facecam_box")
                self.assertIsNotNone(
                    box,
                    f"Burn {clip}: el detector no devolvió caja "
                    f"(method={(res or {}).get('method')}, "
                    f"hits={(res or {}).get('hits')})")
                self.assertGreaterEqual((res or {}).get("hits") or 0,
                                        1,
                                        f"Burn {clip}: sin votos")
                # La caja viene en coords de muestra 160x90: la
                # regla §4.1 se aplica ahí (posición + tamaño).
                ok, motivo = _cam_box_valida(
                    [float(v) for v in box], SAMPLE_W, SAMPLE_H, {})
                self.assertTrue(ok, f"Burn {clip}: caja {box} "
                                    f"no pasa §4.1 ({motivo})")
                frac = [box[0] / SAMPLE_W, box[1] / SAMPLE_H,
                        box[2] / SAMPLE_W, box[3] / SAMPLE_H]
                self.assertTrue(_caja_en_esquina(frac),
                                f"Burn {clip}: caja {box} fuera de "
                                f"esquina")

    def test_cadena_de_rescate_devuelve_caja_en_px_para_183(self):
        # find_facecam_box_in_source (voto -> YuNet -> bordes)
        # devuelve la caja en px de fuente para el 183; en
        # fracciones pasa validate_cam_box sin contención (la
        # caja explícita humana no exige cara detectada, §4.1).
        from clipagent.final_review import find_facecam_box_in_source
        from clipagent.render import _probe_size
        from clipagent.yunet_facecam import validate_cam_box
        path = FIXTURES["183"]
        if not os.path.isfile(path):
            self.skipTest(
                f"fixture de Burn fuera del workspace: {path}")
        res = find_facecam_box_in_source(path, 40.0, cfg={})
        self.assertIsNotNone(
            res, "Burn 183: la cadena de rescate no devolvió caja")
        box = res["box"]
        sw, sh = _probe_size(path)
        frac = [box[0] / sw, box[1] / sh, box[2] / sw, box[3] / sh]
        self.assertTrue(
            validate_cam_box(frac, [], require_containment=False),
            f"Burn 183: caja en px {box} no pasa §4.1 en fracciones")

    def test_seg186_source_sin_deteccion_documentado(self):
        # Hueco conocido (§4.1.5: "Burn: la cámara real es ~1,8x
        # la cara"; test_layout_gate: "la cámara de Burn es oscura
        # y toca el borde"): sobre el único segmento fuente real
        # de Burn, el detector no devuelve nada. Este test fija el
        # comportamiento actual para que una mejora del detector
        # (o un segmento con más luz) lo haga visible al fallar;
        # NO es un pase de la regla §4.1, es su límite medido.
        from clipagent.render import detect_vod_facecam
        if not os.path.isfile(SEG186_SOURCE):
            self.skipTest(
                f"segmento fuente de Burn fuera del workspace: "
                f"{SEG186_SOURCE}")
        res = detect_vod_facecam(SEG186_SOURCE, 40.0, cfg={})
        self.assertIsNone((res or {}).get("facecam_box"),
                          f"seg186_source ahora detecta "
                          f"{(res or {}).get('facecam_box')}: "
                          f"actualizar este test y §10.3")
        self.assertEqual((res or {}).get("method"), "none")


if __name__ == "__main__":
    unittest.main()
