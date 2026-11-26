"""Tests de la recopilación larga de YouTube (clipagent/compilation.py).

Spec v3 2026-11-26: selección/corte/dedupe/fórmula de duración,
segmentos virales (teaser + countdown + revelación), capítulos,
contrato de publish_longform y guarda de re-ejecución. El render
real se verifica con vídeos sintéticos (ffmpeg testsrc) y la
publicación con un servicio de YouTube simulado: nunca se toca la
API real ni el publicador de Shorts.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import compilation as comp

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _clip(clip_id, score=50, influencer_id=1, start=0, end=40,
          title="GTA 6: Momento", created=1000, viral=None, **over):
    row = {
        "id": clip_id,
        "title": title,
        "score": score,
        "status": "en_carpeta",
        "language": "es",
        "video_r2_key": f"clips/x/{clip_id}.mp4",
        "publish_blocked": 0,
        "archived": 0,
        "start_sec": start,
        "end_sec": end,
        "influencer_id": influencer_id,
        "source_id": 0,
        "source_key": "",
        "created_at": created,
        "viral": viral if viral is not None else {
            "quality_gate": {"pasa": True}},
    }
    row.update(over)
    return row


def _pool_40s(n, start_id=1):
    """n clips de 40 s, influencers distintos (sin dedupe)."""
    return [_clip(start_id + i, score=60 - i, influencer_id=100 + i,
                  start=0, end=40, title=f"GTA 6: Momento {i}",
                  created=1000 + i)
            for i in range(n)]


def _make_video(path, duration=2.0, freq=440):
    cmd = [FFMPEG, "-y", "-v", "error",
           "-f", "lavfi", "-i",
           f"testsrc2=size=1080x1920:rate=30:duration={duration}",
           "-f", "lavfi", "-i",
           f"sine=frequency={freq}:duration={duration}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", path]
    subprocess.run(cmd, check=True, timeout=120)


class TestFilterPool(unittest.TestCase):
    def test_pasa_todo_ok(self):
        kept, rejected = comp.filter_pool([_clip(1)])
        self.assertEqual([c["id"] for c in kept], [1])
        self.assertEqual(rejected, [])

    def test_quality_gate_como_string_json(self):
        row = _clip(1, viral=json.dumps({"quality_gate": {"pasa": True}}))
        kept, _ = comp.filter_pool([row])
        self.assertEqual(len(kept), 1)

    def test_publicado_con_pasa_entra(self):
        kept, _ = comp.filter_pool([_clip(1, status="publicado")])
        self.assertEqual([c["id"] for c in kept], [1])

    def test_publicado_sin_pasa_entra_por_haber_publicado(self):
        # Enmienda 14:24: los publicados se aceptan aunque su `pasa`
        # no sea 1 en D1 (ya pasaron las puertas al publicarse).
        row = _clip(1, status="publicado",
                    viral={"quality_gate": {"pasa": False}})
        kept, rejected = comp.filter_pool([row])
        self.assertEqual([c["id"] for c in kept], [1])
        self.assertEqual(rejected, [])

    def test_publicado_sin_veredicto_entra(self):
        row = _clip(1, status="publicado", viral={})
        kept, _ = comp.filter_pool([row])
        self.assertEqual([c["id"] for c in kept], [1])

    def test_rechazos(self):
        cases = [
            (_clip(1, status="programado"), "status"),
            (_clip(2, viral={"quality_gate": {"pasa": False}}), "veredicto"),
            (_clip(3, viral={}), "sin veredicto"),
            (_clip(4, video_r2_key=""), "sin r2"),
            (_clip(5, publish_blocked=1), "bloqueado"),
            (_clip(6, archived=1), "archivado"),
            (_clip(7, language="en"), "idioma"),
            (_clip(8, start=40, end=40), "duración"),
        ]
        for row, _label in cases:
            kept, rejected = comp.filter_pool([row])
            self.assertEqual(kept, [], f"debió rechazar {row['id']}")
            self.assertEqual(len(rejected), 1)


class TestDedupe(unittest.TestCase):
    def test_misma_ventana_mismo_influencer_se_descarta(self):
        a = _clip(1, score=60, influencer_id=2, start=100, end=140)
        b = _clip(2, score=55, influencer_id=2, start=130, end=170)
        kept, dropped = comp.dedupe_pool([a, b])
        self.assertEqual([c["id"] for c in kept], [1])
        self.assertEqual([c["id"] for c in dropped], [2])

    def test_ventana_sin_solape_se_conserva(self):
        a = _clip(1, score=60, influencer_id=2, start=100, end=140)
        b = _clip(2, score=55, influencer_id=2, start=200, end=240)
        kept, dropped = comp.dedupe_pool([a, b])
        self.assertEqual([c["id"] for c in kept], [1, 2])
        self.assertEqual(dropped, [])

    def test_mismo_momento_otro_influencer_se_conserva(self):
        a = _clip(1, score=60, influencer_id=2, start=100, end=140)
        b = _clip(2, score=55, influencer_id=3, start=100, end=140)
        kept, _ = comp.dedupe_pool([a, b])
        self.assertEqual([c["id"] for c in kept], [1, 2])

    def test_sin_influencer_usa_titulo_mas_ventana(self):
        a = _clip(1, score=60, influencer_id=None, start=100, end=140,
                  title="GTA 6: Risa imparable")
        b = _clip(2, score=55, influencer_id=None, start=130, end=170,
                  title="GTA 6: Risa imparable!")
        kept, dropped = comp.dedupe_pool([a, b])
        self.assertEqual([c["id"] for c in kept], [1])
        self.assertEqual([c["id"] for c in dropped], [2])

    def test_sin_influencer_titulo_distinto_se_conserva(self):
        a = _clip(1, score=60, influencer_id=None, start=100, end=140,
                  title="GTA 6: Risa imparable")
        b = _clip(2, score=55, influencer_id=None, start=130, end=170,
                  title="GTA 6: El robo más absurdo")
        kept, _ = comp.dedupe_pool([a, b])
        self.assertEqual([c["id"] for c in kept], [1, 2])


class TestFormulaYCorte(unittest.TestCase):
    def test_formula_total(self):
        # 20 clips de 40 s: 800 + 12 - 0,3*20 = 806,0
        self.assertAlmostEqual(comp.estimate_total([40.0] * 20), 806.0)
        # 21 clips: 840 + 12 - 0,3*21 = 845,7
        self.assertAlmostEqual(comp.estimate_total([40.0] * 21), 845.7)

    def test_20_clips_de_40s_no_llegan_a_1330(self):
        # Filo de la spec: 20x40 s = 806 s < 810 s (13:30).
        selected, total = comp.select_clips(_pool_40s(20))
        self.assertEqual(len(selected), 20)
        self.assertLess(total, comp.MIN_TOTAL_S)

    def test_21_clips_de_40s_si_llegan(self):
        # 21x40 s = 845,7 s: por encima del objetivo 14:00 pero
        # dentro de la banda de verificación 13:30-14:30 (§7).
        selected, total = comp.select_clips(_pool_40s(21))
        self.assertEqual(len(selected), 21)
        self.assertGreaterEqual(total, comp.MIN_TOTAL_S)
        self.assertLessEqual(total, comp.HARD_TOTAL_S)

    def test_sustitucion_por_clip_mas_corto(self):
        pool = _pool_40s(20)
        # El siguiente (60 s) empujaría a 871,7 > 840; el de 15 s
        # que viene después sí cabe (820,7).
        pool.append(_clip(100, score=30, influencer_id=200,
                          start=0, end=60, title="GTA 6: Largo"))
        pool.append(_clip(101, score=29, influencer_id=201,
                          start=0, end=15, title="GTA 6: Corto"))
        selected, total = comp.select_clips(pool)
        ids = [c["id"] for c in selected]
        self.assertNotIn(100, ids)
        self.assertIn(101, ids)
        self.assertGreaterEqual(total, comp.MIN_TOTAL_S)
        self.assertLessEqual(total, comp.TARGET_TOTAL_S)

    def test_orden_score_desc(self):
        pool = [_clip(1, score=50, influencer_id=1),
                _clip(2, score=60, influencer_id=2),
                _clip(3, score=55, influencer_id=3)]
        self.assertEqual([c["id"] for c in comp.sort_pool(pool)],
                         [2, 3, 1])


class TestSegmentosYCapitulos(unittest.TestCase):
    def _selected_21(self):
        selected, _ = comp.select_clips(_pool_40s(21))
        return selected

    def test_estructura_teaser_countdown_reveal(self):
        segments = comp.build_segments(self._selected_21())
        # teaser + 20 countdown + reveal = 22 segmentos
        self.assertEqual(len(segments), 22)
        self.assertEqual(segments[0]["kind"], "teaser")
        self.assertEqual(segments[0]["duration"], 12.0)
        self.assertIsNone(segments[0]["label"])
        self.assertEqual(segments[-1]["kind"], "reveal")
        self.assertEqual(segments[-1]["duration"], 40.0)
        countdown = segments[1:-1]
        self.assertEqual(len(countdown), 20)
        # Countdown de peor a mejor: el primero mostrado es el #21
        # y el último antes de la revelación es el #2.
        self.assertTrue(countdown[0]["label"].startswith("#21 ·"))
        self.assertTrue(countdown[-1]["label"].startswith("#2 ·"))
        self.assertTrue(all(s["kind"] == "countdown" for s in countdown))

    def test_capitulos_primero_000_y_minimo_10s(self):
        segments = comp.build_segments(self._selected_21())
        chapters = comp.chapter_timestamps(segments)
        self.assertEqual(len(chapters), 22)
        self.assertEqual(comp.format_timestamp(chapters[0][0]), "0:00")
        starts = [s for s, _ in chapters]
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertTrue(all(g >= 10.0 for g in gaps),
                        f"capítulo <10 s: {gaps}")
        # El #1 se lista dos veces: teaser y revelación.
        titles = [t for _, t in chapters]
        self.assertEqual(titles[0], "El mejor momento")
        self.assertTrue(titles[-1].startswith("#1 ·"))
        self.assertEqual(len([t for t in titles
                              if t.startswith("#1 ·")]), 1)

    def test_descripcion_con_capitulos(self):
        segments = comp.build_segments(self._selected_21())
        desc = comp.build_description(segments)
        self.assertIn("0:00 El mejor momento", desc)
        self.assertIn("#21 ·", desc)
        self.assertLessEqual(len(desc), comp.MAX_DESC_CHARS)
        self.assertEqual(len(comp.TITLE), 45)
        self.assertLessEqual(len(comp.TITLE), comp.MAX_TITLE_CHARS)
        self.assertLessEqual(len(comp.TAGS), comp.MAX_TAGS)


class TestValidacion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.video = os.path.join(self.tmp, "clip.mp4")
        if FFMPEG:
            _make_video(self.video, duration=2.0)
        self.token = os.path.join(self.tmp, "token.json")
        with open(self.token, "w", encoding="utf-8") as f:
            json.dump({"refresh_token": "x"}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _checks(self, **over):
        kw = {"video_path": self.video, "title": comp.TITLE,
              "description": "d", "tags": comp.TAGS,
              "token_path": self.token}
        kw.update(over)
        return comp.validate_longform_inputs(**kw)

    @unittest.skipUnless(FFMPEG, "sin ffmpeg")
    def test_contrato_pasa(self):
        failed = [d for ok, d in self._checks() if not ok]
        self.assertEqual(failed, [])

    def test_titulo_mayor_60_falla(self):
        checks = self._checks(title="x" * 61)
        self.assertFalse(all(ok for ok, _ in checks))

    def test_mas_de_15_tags_falla(self):
        checks = self._checks(tags=[f"t{i}" for i in range(16)])
        self.assertFalse(all(ok for ok, _ in checks))

    def test_descripcion_mayor_5000_falla(self):
        checks = self._checks(description="x" * 5001)
        self.assertFalse(all(ok for ok, _ in checks))

    def test_archivo_inexistente_falla(self):
        checks = self._checks(video_path="/no/existe.mp4")
        self.assertFalse(all(ok for ok, _ in checks))

    def test_sin_token_falla(self):
        checks = self._checks(token_path="/no/existe.json")
        self.assertFalse(all(ok for ok, _ in checks))

    @unittest.skipUnless(FFPROBE, "sin ffprobe")
    def test_probe_duration_sintetico(self):
        self.assertAlmostEqual(comp.probe_duration(self.video), 2.0,
                               delta=0.2)


class _FakeInsert:
    def __init__(self, outer, body):
        self.outer = outer
        self.body = body

    def execute(self):
        return {"id": "abc123XYZ"}


class _FakeVideos:
    def __init__(self, outer):
        self.outer = outer

    def insert(self, part=None, body=None, media_body=None):
        self.outer.calls.append({"part": part, "body": body})
        return _FakeInsert(self.outer, body)


class _FakeYouTube:
    def __init__(self):
        self.calls = []

    def videos(self):
        return _FakeVideos(self)


class TestPublishLongform(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.video = os.path.join(self.tmp, "recop.mp4")
        if FFMPEG:
            _make_video(self.video, duration=2.0)
        self.token = os.path.join(self.tmp, "token.json")
        with open(self.token, "w", encoding="utf-8") as f:
            json.dump({"refresh_token": "x"}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipUnless(FFMPEG, "sin ffmpeg")
    def test_sube_con_watch_url_y_categoria_gaming(self):
        fake = _FakeYouTube()
        with mock.patch.object(comp, "probe_duration", return_value=845.0):
            result = comp.publish_longform(
                self.video, comp.TITLE, "desc", comp.TAGS,
                token_path=self.token, youtube_service=fake)
        self.assertEqual(
            result["url"], "https://www.youtube.com/watch?v=abc123XYZ")
        self.assertEqual(result["video_id"], "abc123XYZ")
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call["part"], "snippet,status")
        snippet = call["body"]["snippet"]
        self.assertEqual(snippet["categoryId"], "20")
        self.assertEqual(snippet["defaultLanguage"], "es")
        self.assertEqual(snippet["title"], comp.TITLE)
        self.assertLessEqual(len(snippet["tags"]), 15)
        self.assertEqual(call["body"]["status"]["privacyStatus"],
                         "public")

    @unittest.skipUnless(FFMPEG, "sin ffmpeg")
    def test_contrato_roto_no_llama_a_la_api(self):
        fake = _FakeYouTube()
        with self.assertRaises(comp.CompilationError):
            comp.publish_longform(
                self.video, "x" * 61, "desc", comp.TAGS,
                token_path=self.token, youtube_service=fake)
        self.assertEqual(fake.calls, [])


@unittest.skipUnless(FFMPEG and FFPROBE, "sin ffmpeg/ffprobe")
class TestRender(unittest.TestCase):
    def test_render_sintetico_1080x1920_con_audio(self):
        tmp = tempfile.mkdtemp()
        try:
            clips_dir = os.path.join(tmp, "clips")
            os.makedirs(clips_dir)
            for cid in (1, 2, 3):
                _make_video(os.path.join(clips_dir, f"{cid}.mp4"),
                            duration=2.0, freq=440 + cid * 40)
            selected = [
                _clip(1, score=60, influencer_id=1, start=0, end=2),
                _clip(2, score=55, influencer_id=2, start=0, end=2),
                _clip(3, score=50, influencer_id=3, start=0, end=2),
            ]
            segments = comp.build_segments(selected)
            out = os.path.join(tmp, "recop.mp4")
            comp.render_compilation(segments, clips_dir, out,
                                    os.path.join(tmp, "work"))
            self.assertTrue(os.path.isfile(out))
            self.assertGreater(os.path.getsize(out), 10000)
            # 4 segmentos de 2 s - 0,3*3 = 7,1 s
            self.assertAlmostEqual(comp.probe_duration(out), 7.1,
                                   delta=0.6)
            has_video, has_audio = comp.probe_streams(out)
            self.assertTrue(has_video)
            self.assertTrue(has_audio)
            probe = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of",
                 "csv=p=0", out], capture_output=True, text=True,
                timeout=60)
            self.assertEqual(probe.stdout.strip(), "1080,1920")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestRunPrecheck(unittest.TestCase):
    def test_pool_insuficiente_para_y_no_construye(self):
        # 11 clips de 40 s (440 s) + 2 duplicados por ventana solapada
        # que el dedupe descarta: tras dedupe quedan 11 / 440 s, total
        # estimado 448,7 s < 810 s (13:30), como el pool real de seba.
        pool = _pool_40s(11)
        pool.append(_clip(100, score=40, influencer_id=100,
                          start=20, end=60, title="GTA 6: Duplicado"))
        pool.append(_clip(101, score=39, influencer_id=101,
                          start=20, end=60, title="GTA 6: Duplicado 2"))
        tmp = tempfile.mkdtemp()
        try:
            with self.assertRaises(comp.PoolInsufficientError):
                comp.run_compilation("sebastian", dry_run=True,
                                     base_dir=tmp, pool=pool)
            precheck = json.load(open(os.path.join(
                tmp, comp.RUNS_DIRNAME, "sebastian", "precheck.json")))
            self.assertEqual(precheck["tras_dedupe"], 11)
            self.assertEqual(precheck["suma_metadata_s"], 440.0)
            self.assertAlmostEqual(precheck["total_estimado_s"], 448.7)
            self.assertFalse(precheck["pasa"])
            # No se escribió report.json ni se construyó vídeo.
            self.assertFalse(os.path.exists(os.path.join(
                tmp, comp.RUNS_DIRNAME, "sebastian", "report.json")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_pool_mixto_borradores_mas_publicados_pasa(self):
        # Enmienda 14:24: 11 borradores (440 s) + 10 publicados
        # (400 s, con `pasa` False: entran por haber publicado) =
        # 21 clips, total estimado 845,7 s >= 810 s. La corrida
        # llega hasta la descarga (sin bridge) pero el precheck
        # ya quedó escrito y en verde.
        pool = _pool_40s(11)
        pool += [_clip(101 + i, score=45 - i, influencer_id=200 + i,
                       start=0, end=40,
                       title=f"GTA 6: Publicado {i}",
                       created=2000 + i, status="publicado",
                       viral={"quality_gate": {"pasa": False}})
                 for i in range(10)]
        tmp = tempfile.mkdtemp()
        try:
            with mock.patch.object(
                    comp, "check_youtube_credentials",
                    return_value=(True, "ok")):
                with self.assertRaises(comp.CompilationError):
                    comp.run_compilation("sebastian", dry_run=True,
                                         base_dir=tmp, pool=pool)
            precheck = json.load(open(os.path.join(
                tmp, comp.RUNS_DIRNAME, "sebastian", "precheck.json")))
            self.assertTrue(precheck["pasa"])
            self.assertEqual(precheck["aptos"], 21)
            self.assertEqual(precheck["borradores_aptos"], 11)
            self.assertEqual(precheck["publicados_aptos"], 10)
            self.assertEqual(precheck["tras_dedupe"], 21)
            self.assertEqual(precheck["seleccionados"], 21)
            self.assertEqual(precheck["publicados_en_seleccion"], 10)
            self.assertAlmostEqual(precheck["total_estimado_s"], 845.7)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_guarda_re_ejecucion(self):
        tmp = tempfile.mkdtemp()
        try:
            report_path = os.path.join(
                tmp, comp.RUNS_DIRNAME, "kaez", "report.json")
            os.makedirs(os.path.dirname(report_path), exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump({"video_id": "abc", "url": "https://x"}, f)
            with self.assertRaises(comp.CompilationError):
                comp.run_compilation("kaez", dry_run=True,
                                     base_dir=tmp, pool=_pool_40s(21))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cli_pool_json_sale_2(self):
        pool = _pool_40s(11)
        tmp = tempfile.mkdtemp()
        pool_path = os.path.join(tmp, "pool.json")
        with open(pool_path, "w", encoding="utf-8") as f:
            json.dump(pool, f)
        try:
            code = comp.main(["--tenant", "sebastian",
                              "--dry-run", "--pool-json", pool_path])
            self.assertEqual(code, 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            # Limpia el precheck que el CLI escribió en el repo.
            pre = os.path.join(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))),
                comp.RUNS_DIRNAME, "sebastian", "precheck.json")
            if os.path.isfile(pre):
                os.remove(pre)


class TestRepartoCombinado(unittest.TestCase):
    """Modo combinado (spec §2b, enmienda 14:40)."""

    def test_reparto_alternado_por_rango(self):
        pool = _pool_40s(6)
        a, b = comp.split_alternating(pool)
        # Rangos 1, 3, 5 -> A (scores 60, 58, 56); 2, 4, 6 -> B.
        self.assertEqual([c["id"] for c in a], [1, 3, 5])
        self.assertEqual([c["id"] for c in b], [2, 4, 6])

    def test_reparto_disjunto_y_cada_video_llega_a_1330(self):
        # 42 clips de 40 s, mismo score (desempate por created_at):
        # A = 21 clips, B = 21 clips, cada uno 845,7 s >= 810 s.
        pools = _combined_pools(21, 21)
        combined = []
        for tenant, rows in pools.items():
            for clip in rows:
                row = dict(clip)
                row["tenant_origen"] = tenant
                combined.append(row)
        kept, dropped = comp.dedupe_pool(combined)
        self.assertEqual(len(kept), 42)
        self.assertEqual(dropped, [])
        a, b = comp.split_alternating(kept)
        self.assertEqual(len(a), 21)
        self.assertEqual(len(b), 21)
        ids_a = {(c["tenant_origen"], c["id"]) for c in a}
        ids_b = {(c["tenant_origen"], c["id"]) for c in b}
        self.assertEqual(ids_a & ids_b, set())
        self.assertEqual(len(ids_a | ids_b), 42)
        # Ambos mezclan ambas colas.
        for sub in (a, b):
            tenants = {c["tenant_origen"] for c in sub}
            self.assertEqual(tenants, {"sebastian", "kaez"})
        for sub in (a, b):
            selected, total = comp.select_clips(sub)
            self.assertEqual(len(selected), 21)
            self.assertGreaterEqual(total, comp.MIN_TOTAL_S)
            self.assertLessEqual(total, comp.HARD_TOTAL_S)

    def test_dedupe_combinado_mismo_momento_entre_tenants(self):
        # El mismo momento (mismo influencer + ventana solapada)
        # cortado en ambas colas va a UN solo vídeo.
        a = _clip(1, score=90, influencer_id=5, start=100, end=140,
                  title="GTA 6: Risa")
        b = _clip(2, score=85, influencer_id=5, start=120, end=160,
                  title="GTA 6: Risa")
        a = dict(a, tenant_origen="sebastian")
        b = dict(b, tenant_origen="kaez")
        kept, dropped = comp.dedupe_pool([a, b])
        self.assertEqual([c["id"] for c in kept], [1])
        self.assertEqual([c["id"] for c in dropped], [2])

    def test_clip_filename_lleva_tenant_de_origen(self):
        self.assertEqual(
            comp.clip_filename({"id": 8, "tenant_origen": "kaez"}),
            "kaez_8.mp4")
        self.assertEqual(comp.clip_filename({"id": 8}), "8.mp4")

    def test_token_propio_del_tenant(self):
        tmp = tempfile.mkdtemp()
        try:
            self.assertIsNone(
                comp.token_path_for_tenant("sebastian", tmp))
            tokens = os.path.join(tmp, "tokens")
            os.makedirs(tokens)
            path = os.path.join(tokens, "youtube_shorts_kaez.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"refresh_token": "x"}, f)
            self.assertEqual(
                comp.token_path_for_tenant("kaez", tmp), path)
            self.assertIsNone(
                comp.token_path_for_tenant("sebastian", tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_pool_insuficiente_informa_lo_que_falta_al_segundo(self):
        # 15 + 15 clips de 40 s = 30: el primero llega (845,7 s
        # con 21) pero no hay material para el segundo.
        pools = _combined_pools(15, 15)
        tmp = tempfile.mkdtemp()
        try:
            with self.assertRaises(comp.PoolInsufficientError) as ctx:
                comp.run_combined_compilation(
                    dry_run=True, base_dir=tmp, pools=pools, bridges={})
            self.assertIn("faltan", str(ctx.exception))
            for label in ("a", "b"):
                pre = json.load(open(os.path.join(
                    tmp, comp.RUNS_DIRNAME, f"combinado-{label}",
                    "precheck.json")))
                self.assertIn("video", pre)
            # No se escribió report.json en ningún vídeo.
            for label in ("a", "b"):
                self.assertFalse(os.path.exists(os.path.join(
                    tmp, comp.RUNS_DIRNAME, f"combinado-{label}",
                    "report.json")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _combined_pools(n_seba=21, n_kaez=21):
    """Pool combinado sintético: mismo score (desempate por
    created_at: kaez primero), influencers distintos."""
    seba = [_clip(i + 1, score=60, influencer_id=100 + i,
                  start=0, end=40, title=f"GTA 6: Seba {i}",
                  created=1000 + i) for i in range(n_seba)]
    kaez = [_clip(101 + i, score=60, influencer_id=200 + i,
                  start=0, end=40, title=f"GTA 6: Kaez {i}",
                  created=2000 + i) for i in range(n_kaez)]
    return {"sebastian": seba, "kaez": kaez}


class _FakeBridgeDescarga:
    """Bridge falso: la descarga copia un vídeo sintético."""

    def __init__(self, src_video):
        self.src = src_video
        self.downloaded = []

    def download_video(self, clip_id, out_path):
        self.downloaded.append(clip_id)
        shutil.copy(self.src, out_path)
        return out_path


def _fake_probe_combined(path):
    # El MP4 final mide 845 s; cada clip descargado, 40 s (metadata).
    if "recopilacion-combinado-" in path:
        return 845.0
    return 40.0


class TestRunCombinado(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "src.mp4")
        if FFMPEG:
            _make_video(self.src, duration=2.0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **over):
        kw = {"dry_run": False, "base_dir": self.tmp,
              "pools": _combined_pools(21, 21),
              "bridges": {
                  "sebastian": _FakeBridgeDescarga(self.src),
                  "kaez": _FakeBridgeDescarga(self.src)}}
        kw.update(over)
        return comp.run_combined_compilation(**kw)

    @unittest.skipUnless(FFMPEG, "sin ffmpeg")
    def test_sin_token_propio_construye_igual_y_marca_bloqueo(self):
        # Sin tokens propios: los MP4 se construyen igual y se
        # marcan bloqueados por credenciales; nada se sube.
        def _fake_render(segments, clips_dir, out_path, work_dir,
                         ffmpeg="ffmpeg"):
            shutil.copy(self.src, out_path)
            return out_path

        with mock.patch.object(
                comp, "probe_duration",
                side_effect=_fake_probe_combined), \
                mock.patch.object(comp, "render_compilation",
                                  side_effect=_fake_render), \
                mock.patch.object(comp, "publish_longform") as pub:
            result = self._run()
        self.assertEqual(pub.call_count, 0)
        for key, label in (("video_a", "a"), ("video_b", "b")):
            report = result[key]
            self.assertEqual(report["video"], label.upper())
            self.assertEqual(report["estado"], "construido_sin_subir")
            self.assertIn("credenciales", report["bloqueo"])
            self.assertIsNone(report["video_id"])
            self.assertIsNone(report["url"])
            self.assertEqual(len(report["clips"]), 21)
            self.assertEqual(report["duracion_final_s"], 845.0)
            out = os.path.join(
                self.tmp, comp.RUNS_DIRNAME, f"combinado-{label}",
                f"recopilacion-combinado-{label}.mp4")
            self.assertTrue(os.path.isfile(out))
            saved = json.load(open(os.path.join(
                self.tmp, comp.RUNS_DIRNAME, f"combinado-{label}",
                "report.json")))
            self.assertEqual(saved["estado"], "construido_sin_subir")
        # Disjuntos: ningún (tenant, clip) en los dos vídeos.
        ids_a = {(c["tenant_origen"], c["id"])
                 for c in result["video_a"]["clips"]}
        ids_b = {(c["tenant_origen"], c["id"])
                 for c in result["video_b"]["clips"]}
        self.assertEqual(ids_a & ids_b, set())
        self.assertEqual(len(ids_a | ids_b), 42)

    @unittest.skipUnless(FFMPEG, "sin ffmpeg")
    def test_con_token_propio_sube_cada_video_a_su_tenant(self):
        tokens = os.path.join(self.tmp, "tokens")
        os.makedirs(tokens)
        for tenant in ("sebastian", "kaez"):
            with open(os.path.join(
                    tokens, f"youtube_shorts_{tenant}.json"),
                    "w", encoding="utf-8") as f:
                json.dump({"refresh_token": "x"}, f)

        def _fake_render(segments, clips_dir, out_path, work_dir,
                         ffmpeg="ffmpeg"):
            shutil.copy(self.src, out_path)
            return out_path

        uploads = [{"video_id": "vidA",
                    "url": "https://www.youtube.com/watch?v=vidA"},
                   {"video_id": "vidB",
                    "url": "https://www.youtube.com/watch?v=vidB"}]
        with mock.patch.object(
                comp, "probe_duration",
                side_effect=_fake_probe_combined), \
                mock.patch.object(comp, "render_compilation",
                                  side_effect=_fake_render), \
                mock.patch.object(
                    comp, "check_youtube_credentials",
                    return_value=(True, "ok")), \
                mock.patch.object(comp, "publish_longform",
                                  side_effect=uploads) as pub:
            result = self._run()
        self.assertEqual(pub.call_count, 2)
        # Cada subida usa el token propio de su tenant.
        token_paths = [call.kwargs["token_path"]
                       for call in pub.call_args_list]
        self.assertIn(os.path.join(
            tokens, "youtube_shorts_sebastian.json"), token_paths)
        self.assertIn(os.path.join(
            tokens, "youtube_shorts_kaez.json"), token_paths)
        self.assertEqual(result["video_a"]["estado"], "subido")
        self.assertEqual(result["video_a"]["video_id"], "vidA")
        self.assertEqual(result["video_b"]["video_id"], "vidB")

    def test_cli_combinado_sin_pool_sale_2(self):
        tmp = tempfile.mkdtemp()
        pool_path = os.path.join(tmp, "pool.json")
        with open(pool_path, "w", encoding="utf-8") as f:
            json.dump(_combined_pools(11, 11), f)
        try:
            code = comp.main(["--combinado", "--dry-run",
                              "--pool-json", pool_path])
            self.assertEqual(code, 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            for label in ("a", "b"):
                pre = os.path.join(os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))),
                    comp.RUNS_DIRNAME, f"combinado-{label}",
                    "precheck.json")
                if os.path.isfile(pre):
                    os.remove(pre)


if __name__ == "__main__":
    unittest.main()
