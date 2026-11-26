"""Tests de la auditoría v2 (adopción 4/5): métricas de ig-audit
con fuente real + formato swipe file.

Reglas que se prueban aquí:
- Una métrica ratio solo existe si existen sus dos lados; lo que
  falta es None («no disponible»), nunca un 0 como si fuera medido.
- Un 0 REAL (0 shares medidos) sí es 0 y se distingue del ausente.
- shares_per_view NO es sends/reach (claves y etiquetas distintas).
- retention_pct es retención MEDIA, no hold@3s.
- Formato swipe: tercio superior/inferior, medianas de hook score
  y de palabras del gancho, fórmulas del tercio superior,
  nº de unclassified.
- clip_stats se lee de D1 (última medición por clip); sin filas o
  sin tabla, no hay v2 y la auditoría sigue.

Todo con datos simulados: sin red, sin D1 real, sin yt-dlp real.
Los valores de fixture son datos de ejemplo explícitos del propio
test, no se presentan como medidos.
"""

import csv
import os
import statistics
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import audit_published as ap
from clipagent.d1_client import D1Client
from clipagent.hook_formulas import UNCLASSIFIED, formula_name
from clipagent.hookscore import score_hook


def _clip(title, views, url="https://www.youtube.com/shorts/x",
          follows=None, duration_s=30, **v2):
    clip = {"title": title, "url": url, "views": views,
            "duration_s": duration_s, "posted_day": "2026-11-21",
            "follows": follows, "platform": "youtube_shorts"}
    clip.update(v2)
    return clip


class TestComputeV2Metrics(unittest.TestCase):
    def test_con_shares_y_reach_hay_sends_per_reach(self):
        # 90 envíos sobre 4.000 de alcance: 0,0225.
        m = ap.compute_v2_metrics({"shares": 90, "reach": 4000})
        self.assertEqual(m["sends_per_reach"], 0.0225)

    def test_con_follows_y_reach_hay_follows_per_reach(self):
        m = ap.compute_v2_metrics({"follows": 40, "reach": 4000})
        self.assertEqual(m["follows_per_reach"], 0.01)

    def test_hold_3s_directo_y_en_porcentaje(self):
        m = ap.compute_v2_metrics({"hold_3s": 0.71})
        self.assertEqual(m["hold_3s"], 0.71)
        m = ap.compute_v2_metrics({"hold_3s": 71})
        self.assertEqual(m["hold_3s"], 0.71)

    def test_retention_pct_es_retencion_media_y_pasa_tal_cual(self):
        m = ap.compute_v2_metrics({"retention_pct": 42.5})
        self.assertEqual(m["retention_pct"], 42.5)
        # No es hold@3s: sin dato de hold, hold_3s sigue ausente.
        self.assertIsNone(m["hold_3s"])

    def test_sin_reach_no_hay_sends_ni_follows_per_reach(self):
        # Shares y follows reales pero sin alcance: los ratios
        # son None, NO cero medido.
        m = ap.compute_v2_metrics({"shares": 90, "follows": 40})
        self.assertIsNone(m["sends_per_reach"])
        self.assertIsNone(m["follows_per_reach"])
        self.assertIsNone(m["reach"])

    def test_sin_dato_no_hay_hold_ni_no_seguidores(self):
        m = ap.compute_v2_metrics({"views": 4000, "shares": 90})
        self.assertIsNone(m["hold_3s"])
        self.assertIsNone(m["non_follower_pct"])

    def test_shares_por_vista_es_otro_ratio_y_no_sustituye(self):
        # shares/views existe (ambos reales) pero es OTRA clave:
        # nunca se presenta como sends/reach.
        m = ap.compute_v2_metrics({"shares": 90, "views": 4000})
        self.assertEqual(m["shares_per_view"], 0.0225)
        self.assertIsNone(m["sends_per_reach"])
        self.assertNotEqual(m["shares_per_view"],
                            m["sends_per_reach"])

    def test_cero_real_se_distingue_del_ausente(self):
        # 0 shares MEDIDOS sobre 4.000 de alcance: el ratio es 0,0
        # de verdad, no un ausente disfrazado.
        m = ap.compute_v2_metrics({"shares": 0, "reach": 4000})
        self.assertEqual(m["sends_per_reach"], 0.0)
        # Y sin shares en absoluto, sigue siendo None.
        m = ap.compute_v2_metrics({"reach": 4000})
        self.assertIsNone(m["sends_per_reach"])
        self.assertIsNone(m["shares"])

    def test_follows_puede_venir_de_new_subs(self):
        m = ap.compute_v2_metrics({"new_subs": 12, "reach": 4000})
        self.assertEqual(m["follows_per_reach"], 0.003)


class TestLoadStatsFromD1(unittest.TestCase):
    def _client(self, rows):
        def http_post(url, body, headers):
            return 200, {"success": True,
                         "result": [{"results": rows}]}
        return D1Client(token="t", http_post=http_post)

    def test_ultima_medicion_por_clip_y_mapeo_de_plataforma(self):
        # Dos mediciones del clip 7 (IG): manda la más reciente
        # (primera en ORDER BY recorded_at DESC).
        rows = [
            {"clip_id": 7, "views": 5000, "retention_pct": 42.0,
             "shares": 90, "saves": 10, "comments": 5,
             "new_subs": None, "recorded_at": 2000},
            {"clip_id": 7, "views": 4000, "retention_pct": 30.0,
             "shares": 10, "saves": 1, "comments": 1,
             "new_subs": None, "recorded_at": 1000},
            {"clip_id": 9, "views": 100, "retention_pct": None,
             "shares": None, "saves": None, "comments": None,
             "new_subs": None, "recorded_at": 1500},
        ]
        seen = {}

        def http_post(url, body, headers):
            seen["params"] = body.get("params")
            return 200, {"success": True,
                         "result": [{"results": rows}]}
        client = D1Client(token="t", http_post=http_post)
        stats = ap.load_stats_from_d1(client,
                                      platform="instagram_reels")
        # La plataforma del panel se traduce a la clave API.
        self.assertEqual(seen["params"], ["instagram"])
        self.assertEqual(stats[7]["views"], 5000)
        self.assertEqual(stats[7]["retention_pct"], 42.0)
        self.assertEqual(stats[7]["shares"], 90)
        # Un NULL real en D1 sigue siendo None, no 0.
        self.assertIsNone(stats[9]["retention_pct"])
        self.assertIsNone(stats[9]["shares"])

    def test_sin_cliente_no_hay_stats(self):
        self.assertEqual(ap.load_stats_from_d1(None), {})

    def test_si_d1_falla_no_tumba_la_auditoria(self):
        def http_post(url, body, headers):
            return 200, {"success": False, "errors": [
                {"message": "no such table: clip_stats"}]}
        client = D1Client(token="t", http_post=http_post)
        self.assertEqual(
            ap.load_stats_from_d1(client), {})


class TestCSVv2Columns(unittest.TestCase):
    def _csv(self, rows, fieldnames):
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", encoding="utf-8",
                       newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return path

    def test_columnas_v2_se_parsean_y_faltantes_son_none(self):
        path = self._csv([
            {"url": "https://www.youtube.com/shorts/a",
             "title": "Clip A", "platform": "youtube_shorts",
             "posted_day": "2026-11-21", "follows": "12",
             "views": "4000", "retention_pct": "42.5",
             "shares": "90", "reach": "4000", "hold_3s": "0.71",
             "non_follower_pct": "62"},
            {"url": "https://www.youtube.com/shorts/b",
             "title": "Clip B", "platform": "youtube_shorts",
             "posted_day": "2026-11-21", "follows": "",
             "views": "abc", "retention_pct": "",
             "shares": "", "reach": "", "hold_3s": "",
             "non_follower_pct": ""},
        ], ["url", "title", "platform", "posted_day", "follows",
            "views", "retention_pct", "shares", "reach", "hold_3s",
            "non_follower_pct"])
        try:
            clips = ap.load_from_csv(path)
        finally:
            os.unlink(path)
        self.assertEqual(clips[0]["views"], 4000)
        self.assertEqual(clips[0]["retention_pct"], 42.5)
        self.assertEqual(clips[0]["reach"], 4000)
        self.assertEqual(clips[0]["hold_3s"], 0.71)
        self.assertEqual(clips[0]["non_follower_pct"], 62.0)
        # Inválido o vacío: None, nunca 0.
        self.assertIsNone(clips[1]["views"])
        self.assertIsNone(clips[1]["retention_pct"])
        self.assertIsNone(clips[1]["shares"])
        self.assertIsNone(clips[1]["reach"])
        self.assertIsNone(clips[1]["hold_3s"])
        self.assertIsNone(clips[1]["follows"])

    def test_run_audit_csv_con_v2_calcula_sends_per_reach(self):
        path = self._csv([
            {"url": "https://www.youtube.com/shorts/a",
             "title": "Clip A", "platform": "youtube_shorts",
             "posted_day": "2026-11-21", "follows": "12",
             "views": "4000", "retention_pct": "42.5",
             "shares": "90", "reach": "4000", "hold_3s": "0.71",
             "non_follower_pct": "62"},
            {"url": "https://www.youtube.com/shorts/b",
             "title": "Clip B", "platform": "youtube_shorts",
             "posted_day": "2026-11-21", "follows": "4",
             "views": "60000", "retention_pct": "55.0",
             "shares": "120", "reach": "50000", "hold_3s": "0.64",
             "non_follower_pct": "48"},
            {"url": "https://www.youtube.com/shorts/c",
             "title": "Clip C", "platform": "youtube_shorts",
             "posted_day": "2026-11-21", "follows": "0",
             "views": "1000", "retention_pct": "30.0",
             "shares": "2", "reach": "900", "hold_3s": "0.31",
             "non_follower_pct": "9"},
        ], ["url", "title", "platform", "posted_day", "follows",
            "views", "retention_pct", "shares", "reach", "hold_3s",
            "non_follower_pct"])

        def no_yt_stats(url, runner=None, sleep=None):
            return None

        try:
            with mock.patch.object(ap, "fetch_youtube_stats",
                                   no_yt_stats):
                report, motivo = ap.run_audit(
                    "sebastian", csv_path=path)
        finally:
            os.unlink(path)
        self.assertEqual(motivo, "")
        self.assertIsNotNone(report)
        # Sin yt-dlp, las vistas del CSV mandan (mediana 4000).
        self.assertEqual(report["median_views"], 4000)
        v2 = report["v2"]
        self.assertEqual(v2["sends_per_reach"]["n"], 3)
        self.assertEqual(v2["follows_per_reach"]["n"], 3)
        self.assertEqual(v2["hold_3s"]["n"], 3)
        self.assertEqual(v2["non_follower_pct"]["n"], 3)
        self.assertEqual(v2["retention_pct"]["n"], 3)
        by_title = {r["title"]: r for r in report["ranked"]}
        self.assertEqual(by_title["Clip A"]["sends_per_reach"],
                         0.0225)
        self.assertEqual(by_title["Clip A"]["hold_3s"], 0.71)
        self.assertEqual(by_title["Clip A"]["non_follower_pct"],
                         62.0)
        self.assertEqual(report["v2_median_retention_pct"], 42.5)


class TestSwipeFormat(unittest.TestCase):
    def _ranked(self):
        # 9 clips, outlier 1..9: tercio = 3 (arriba 9/8/7, abajo 1/2/3).
        titles_top = [
            "Nadie te dice cuánto cuesta un clip",   # 6 palabras
            "Nadie te dice por qué falla el gancho",  # 7 palabras
            "Nadie te dice el precio del setup",      # 6 palabras
        ]
        titles_mid = ["Clip medio uno dos tres",
                      "Clip medio cuatro cinco seis",
                      "Clip medio siete ocho nueve"]
        titles_bottom = [
            "Clip flojo de prueba sin gancho ni texto ni nada",
            "Clip flojo de prueba sin gancho ni texto ni nada dos",
            "Clip flojo de prueba sin gancho ni texto ni nada tres",
        ]
        clips = []
        for i, title in enumerate(titles_top + titles_mid
                                  + titles_bottom):
            clips.append(_clip(title, 1000 * (i + 1)))
        # Outlier por posición: top (primeros 3) 9/8/7, bottom 3/2/1.
        outliers = [9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0]
        for clip, out in zip(clips, outliers):
            clip["outlier"] = out
        return clips, titles_top, titles_bottom

    def test_tercio_superior_e_inferior(self):
        clips, _top, _bottom = self._ranked()
        report = ap.build_report(clips, 1000, "sebastian",
                                 "youtube_shorts")
        self.assertEqual(report["n_third"], 3)
        self.assertEqual(
            [r["outlier"] for r in report["top_third"]],
            [9.0, 8.0, 7.0])
        self.assertEqual(
            [r["outlier"] for r in report["bottom_third"]],
            [1.0, 2.0, 3.0])
        # El ranking completo sigue ordenado por outlier.
        self.assertEqual(
            [r["outlier"] for r in report["ranked"]],
            [9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0])

    def test_mediana_de_hook_score_arriba_vs_abajo(self):
        clips, titles_top, titles_bottom = self._ranked()
        report = ap.build_report(clips, 1000, "sebastian",
                                 "youtube_shorts")
        top_scores = [score_hook(t)[0] for t in titles_top]
        bottom_scores = [score_hook(t)[0] for t in titles_bottom]
        self.assertEqual(
            report["median_hook_score_top"],
            round(statistics.median(top_scores), 1))
        self.assertEqual(
            report["median_hook_score_bottom"],
            round(statistics.median(bottom_scores), 1))
        # Las claves v1 siguen existiendo y son las medianas.
        self.assertEqual(report["hook_score_top"],
                         report["median_hook_score_top"])

    def test_mediana_de_palabras_del_gancho(self):
        clips, _top, _bottom = self._ranked()
        report = ap.build_report(clips, 1000, "sebastian",
                                 "youtube_shorts")
        # Tercio de arriba: 7, 8, 7 palabras -> mediana 7.
        self.assertEqual(report["median_hook_words_top"], 7.0)
        # Tercio de abajo: 10, 11, 11 palabras -> mediana 11.
        self.assertEqual(report["median_hook_words_bottom"], 11.0)

    def test_formulas_del_tercio_superior_y_unclassified(self):
        clips, _top, _bottom = self._ranked()
        report = ap.build_report(clips, 1000, "sebastian",
                                 "youtube_shorts")
        # Los 3 de arriba son «Nadie te dice…» (Nobody Tells You).
        self.assertEqual(report["top_formulas"],
                         [["Nobody Tells You", 3]])
        expected_unclassified = sum(
            1 for c in clips
            if formula_name(c["title"]) == UNCLASSIFIED)
        self.assertEqual(report["n_unclassified"],
                         expected_unclassified)
        self.assertGreater(report["n_unclassified"], 0)

    def test_render_swipe_sin_datos_no_pone_ceros(self):
        clips, _top, _bottom = self._ranked()
        report = ap.build_report(clips, 1000, "sebastian",
                                 "youtube_shorts")
        text = ap.render_text(report)
        self.assertIn("WHAT IS WORKING IN THIS BATCH", text)
        self.assertIn("median hook score:", text)
        self.assertIn("median hook length:", text)
        self.assertIn("unclassified:", text)
        self.assertIn("RANKING POR OUTLIER", text)
        # Sin fuente, cada métrica v2 se marca no disponible…
        self.assertIn("hold_3s: no disponible", text)
        self.assertIn("sends_per_reach: no disponible", text)
        self.assertIn("follows_per_reach: no disponible", text)
        self.assertIn("non_follower_pct: no disponible", text)
        # …y el texto nombra la fuente que haría falta.
        self.assertIn("clip_stats no tiene columna reach", text)
        # Ninguna métrica ausente aparece como 0 medido.
        self.assertNotIn("hold_3s: 0", text)
        self.assertNotIn("sends_per_reach: 0", text)

    def test_render_swipe_con_datos_muestra_los_ratios(self):
        clips, _top, _bottom = self._ranked()
        for clip in clips:
            clip["shares"] = 90
            clip["reach"] = 4000
            clip["retention_pct"] = 42.0
        report = ap.build_report(clips, 1000, "sebastian",
                                 "youtube_shorts")
        text = ap.render_text(report)
        self.assertIn("sends_per_reach: 9 de 9 con dato", text)
        self.assertIn("retención media (mediana): 42.0%", text)
        # shares/vista por clip: 90/1000 … 90/9000; mediana 0,018.
        self.assertIn("shares por vista (mediana): 1.80%", text)


if __name__ == "__main__":
    unittest.main()
