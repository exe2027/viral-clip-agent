"""Tests del camino de CircleCI para la recopilación larga.

SPEC runs/compilacion-circleci-2026-11-26 §7.1: driver
clipagent/circle_compilation.py, guarda anti-duplicado y título
dinámico en clipagent/compilation.py, list_published en el
bridge, _mask solo en GitHub Actions y el job `compilation` de
.circleci/config.yml. Nada toca la API real de YouTube ni el
panel: bridges y servicios de YouTube son falsos.
"""
import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml

from clipagent import circle_compilation as driver
from clipagent import compilation as comp
from clipagent import panel_bridge
from clipagent.panel_credentials import _mask

SANTIAGO = ZoneInfo("America/Santiago")
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


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


def _combined_pools(n_seba=21, n_kaez=21):
    seba = [_clip(i + 1, score=60, influencer_id=100 + i,
                  start=0, end=40, title=f"GTA 6: Seba {i}",
                  created=1000 + i) for i in range(n_seba)]
    kaez = [_clip(101 + i, score=60, influencer_id=200 + i,
                  start=0, end=40, title=f"GTA 6: Kaez {i}",
                  created=2000 + i) for i in range(n_kaez)]
    return {"sebastian": seba, "kaez": kaez}


class _FakeBridge:
    """Bridge falso: pool y credenciales en memoria."""

    def __init__(self, tenant, clips=None, published=None,
                 youtube_token=None, src=None):
        self.tenant = tenant
        self.clips = clips or []
        self.published = published or []
        self.youtube_token = youtube_token
        self.src = src
        self.downloaded = []
        self.list_published_limits = []

    def list_clips(self):
        return [dict(c) for c in self.clips]

    def list_published(self, limit=500):
        self.list_published_limits.append(limit)
        return [dict(c) for c in self.published]

    def brain_credentials(self):
        creds = {"ok": True, "tenant": self.tenant}
        if self.youtube_token is not None:
            creds["youtube"] = {"es": {
                "client_id": f"cid-{self.tenant}",
                "client_secret": f"csec-{self.tenant}",
                "refresh_token": self.youtube_token}}
        return creds

    def download_video(self, clip_id, out_path):
        self.downloaded.append(clip_id)
        if self.src:
            shutil.copy(self.src, out_path)
        else:
            with open(out_path, "wb") as f:
                f.write(b"fake-mp4")
        return out_path


class _FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeChannels:
    def __init__(self, uploads):
        self.uploads = uploads

    def list(self, part=None, mine=None):
        if self.uploads is None:
            return _FakeRequest({"items": [{"contentDetails": {
                "relatedPlaylists": {}}}]})
        return _FakeRequest({"items": [{"contentDetails": {
            "relatedPlaylists": {"uploads": self.uploads}}}]})


class _FakePlaylistItems:
    def __init__(self, service):
        self.service = service

    def list(self, part=None, playlistId=None, maxResults=None,
             pageToken=None):
        self.service.playlist_calls.append(
            {"playlistId": playlistId, "maxResults": maxResults,
             "pageToken": pageToken})
        idx = 0 if pageToken is None else int(pageToken)
        return _FakeRequest(self.service.pages[idx])


class _FakeYouTubeService:
    """Servicio de YouTube falso: uploads playlist por páginas."""

    def __init__(self, pages, uploads="UUxxx"):
        self.pages = pages
        self.uploads = uploads
        self.playlist_calls = []

    def channels(self):
        return _FakeChannels(self.uploads)

    def playlistItems(self):
        return _FakePlaylistItems(self)


def _today():
    return datetime.datetime.now(SANTIAGO).date()


def _published_at(day, hour=12):
    return f"{day.isoformat()}T{hour:02d}:00:00Z"


def _video_item(video_id, title, day, hour=12):
    return {"snippet": {
        "title": title,
        "publishedAt": _published_at(day, hour),
        "resourceId": {"videoId": video_id}}}


def _page(items, next_token=None):
    doc = {"items": items}
    if next_token:
        doc["nextPageToken"] = next_token
    return doc


def _token_file(base, tenant, refresh="x"):
    tokens = os.path.join(base, "tokens")
    os.makedirs(tokens, exist_ok=True)
    path = os.path.join(tokens, f"youtube_shorts_{tenant}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"refresh_token": refresh}, f)
    return path


def _fake_probe_combined(path):
    if "recopilacion-combinado-" in path:
        return 845.0
    return 40.0


def _fake_render(src):
    def _render(segments, clips_dir, out_path, work_dir,
                ffmpeg="ffmpeg", on_progress=None):
        shutil.copy(src, out_path)
        return out_path
    return _render


class TestBootstrapTwoTenants(unittest.TestCase):
    def test_dos_tenants_tokens_en_archivos_correctos(self):
        tmp = tempfile.mkdtemp()
        try:
            seba = _FakeBridge("sebastian", youtube_token="tok-seba")
            kaez = _FakeBridge("kaez", youtube_token="tok-kaez")
            sum_seba = driver.bootstrap_tenant_credentials(
                seba, "sebastian", tmp)
            sum_kaez = driver.bootstrap_tenant_credentials(
                kaez, "kaez", tmp)
            self.assertTrue(sum_seba["youtube_es"])
            self.assertTrue(sum_kaez["youtube_es"])
            for tenant, token in (("sebastian", "tok-seba"),
                                  ("kaez", "tok-kaez")):
                path = os.path.join(
                    tmp, "tokens", f"youtube_shorts_{tenant}.json")
                with open(path, encoding="utf-8") as f:
                    doc = json.load(f)
                self.assertEqual(doc["refresh_token"], token)
                mode = oct(os.stat(path).st_mode & 0o777)
                self.assertEqual(mode, "0o600")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tenant_sin_youtube_despues_de_otro_con_youtube(self):
        # B2: el segundo tenant no tiene YouTube ES; su archivo
        # por tenant NO debe existir (y menos con el token del
        # primero), y el compartido no debe conservarlo.
        tmp = tempfile.mkdtemp()
        try:
            seba = _FakeBridge("sebastian", youtube_token="tok-seba")
            kaez = _FakeBridge("kaez", youtube_token=None)
            driver.bootstrap_tenant_credentials(seba, "sebastian",
                                                tmp)
            summary = driver.bootstrap_tenant_credentials(
                kaez, "kaez", tmp)
            self.assertFalse(summary["youtube_es"])
            kaez_path = os.path.join(
                tmp, "tokens", "youtube_shorts_kaez.json")
            self.assertFalse(os.path.exists(kaez_path))
            shared = os.path.join(tmp, "tokens",
                                  "youtube_shorts.json")
            self.assertFalse(os.path.exists(shared))
            with open(os.path.join(
                    tmp, "tokens",
                    "youtube_shorts_sebastian.json"),
                    encoding="utf-8") as f:
                self.assertEqual(json.load(f)["refresh_token"],
                                 "tok-seba")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestMask(unittest.TestCase):
    def _run_mask(self, env):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False):
            if "GITHUB_ACTIONS" not in env:
                os.environ.pop("GITHUB_ACTIONS", None)
            with contextlib.redirect_stdout(buf):
                _mask("valor-secreto")
        return buf.getvalue()

    def test_sin_github_actions_no_emite(self):
        self.assertEqual(self._run_mask({}), "")

    def test_con_github_actions_si_emite(self):
        out = self._run_mask({"GITHUB_ACTIONS": "true"})
        self.assertIn("::add-mask::valor-secreto", out)

    def test_con_github_actions_false_no_emite(self):
        out = self._run_mask({"GITHUB_ACTIONS": "false"})
        self.assertEqual(out, "")


class TestPreflight(unittest.TestCase):
    def test_falta_variable_de_contexto_nombra_la_variable(self):
        env = {"PANEL_BASE_URL_SEBASTIAN": "https://x",
               "PANEL_USERNAME_SEBASTIAN": "u",
               "PANEL_PASSWORD_SEBASTIAN": "p",
               "PANEL_BASE_URL_KAEZ": "https://y",
               "PANEL_USERNAME_KAEZ": "u"}
        with self.assertRaises(driver.DriverError) as ctx:
            driver.build_bridges(env)
        self.assertIn("PANEL_PASSWORD_KAEZ", str(ctx.exception))

    def test_build_bridges_explicitos_por_tenant(self):
        env = {}
        for tenant, base in (("sebastian", "https://avc.example"),
                             ("kaez", "https://kaez.example")):
            env[f"PANEL_BASE_URL_{tenant.upper()}"] = base
            env[f"PANEL_USERNAME_{tenant.upper()}"] = f"u-{tenant}"
            env[f"PANEL_PASSWORD_{tenant.upper()}"] = f"p-{tenant}"
        bridges = driver.build_bridges(env)
        self.assertEqual(bridges["sebastian"].base_url,
                         "https://avc.example")
        self.assertEqual(bridges["kaez"].base_url,
                         "https://kaez.example")
        self.assertEqual(bridges["sebastian"].tenant, "sebastian")
        self.assertEqual(bridges["kaez"].tenant, "kaez")

    def test_sin_ffmpeg_falla(self):
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(driver.DriverError) as ctx:
                driver.preflight()
        self.assertIn("ffmpeg", str(ctx.exception))

    def test_find_font_ausente_falla_antes_de_descargar(self):
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "src.mp4")
            with open(src, "wb") as f:
                f.write(b"fake")
            bridges = {
                "sebastian": _FakeBridge("sebastian", src=src),
                "kaez": _FakeBridge("kaez", src=src)}
            with mock.patch.object(comp, "find_font",
                                  return_value=None):
                code = driver.main(["--combinado"], base_dir=tmp,
                                   bridges=bridges)
            self.assertNotEqual(code, 0)
            self.assertEqual(bridges["sebastian"].downloaded, [])
            self.assertEqual(bridges["kaez"].downloaded, [])
            self.assertTrue(os.path.isdir(
                os.path.join(tmp, "compilation-out")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestTituloDinamico(unittest.TestCase):
    def test_fecha_fija_27_nov(self):
        title = comp.compilation_title(datetime.date(2026, 11, 27))
        self.assertEqual(
            title, "Los momentos más divertidos de GTA 6 | 27 nov")
        self.assertLessEqual(len(title), comp.MAX_TITLE_CHARS)

    def test_meses_abreviados_espanol_minuscula(self):
        self.assertTrue(comp.compilation_title(
            datetime.date(2026, 1, 5)).endswith("| 5 ene"))
        self.assertTrue(comp.compilation_title(
            datetime.date(2026, 12, 31)).endswith("| 31 dic"))
        self.assertTrue(comp.compilation_title(
            datetime.date(2026, 8, 9)).endswith("| 9 ago"))

    def test_hoy_en_america_santiago(self):
        today = _today()
        months = ("ene", "feb", "mar", "abr", "may", "jun", "jul",
                  "ago", "sep", "oct", "nov", "dic")
        expected = (f"Los momentos más divertidos de GTA 6 | "
                    f"{today.day} {months[today.month - 1]}")
        self.assertEqual(comp.compilation_title(), expected)

    def test_longitud_siempre_menor_60(self):
        for day in (1, 9, 10, 28, 31):
            for month in (1, 2, 9, 11, 12):
                try:
                    fecha = datetime.date(2026, month, day)
                except ValueError:
                    continue
                self.assertLessEqual(
                    len(comp.compilation_title(fecha)), 60)


class TestGuardYaSubidoHoy(unittest.TestCase):
    def test_titulo_hoy_devuelve_url(self):
        title = comp.compilation_title()
        service = _FakeYouTubeService([_page([
            _video_item("vid-hoy", title, _today())])])
        found = comp.ya_subido_hoy(service, title)
        self.assertEqual(
            found, {"video_id": "vid-hoy",
                    "url": "https://www.youtube.com/watch?v=vid-hoy"})
        self.assertEqual(service.playlist_calls[0]["playlistId"],
                         "UUxxx")
        self.assertEqual(service.playlist_calls[0]["maxResults"], 50)

    def test_titulo_distinto_devuelve_none(self):
        service = _FakeYouTubeService([_page([
            _video_item("vid-otro", "Otro título", _today())])])
        self.assertIsNone(
            comp.ya_subido_hoy(service, comp.compilation_title()))

    def test_video_anterior_a_hoy_corta_paginacion(self):
        # Aunque quede nextPageToken, un vídeo anterior a hoy
        # cierra la búsqueda: la playlist va de reciente a antiguo.
        yesterday = _today() - datetime.timedelta(days=1)
        service = _FakeYouTubeService([_page(
            [_video_item("vid-viejo", comp.compilation_title(),
                         yesterday)],
            next_token="1"),
            _page([_video_item("vid-hoy", comp.compilation_title(),
                               _today())])])
        self.assertIsNone(
            comp.ya_subido_hoy(service, comp.compilation_title()))
        self.assertEqual(len(service.playlist_calls), 1)

    def test_paginacion_hasta_segunda_pagina(self):
        title = comp.compilation_title()
        service = _FakeYouTubeService([
            _page([_video_item("vid-otro", "Otro título", _today())],
                  next_token="1"),
            _page([_video_item("vid-hoy", title, _today())])])
        found = comp.ya_subido_hoy(service, title)
        self.assertEqual(found["video_id"], "vid-hoy")
        self.assertEqual(len(service.playlist_calls), 2)
        self.assertEqual(service.playlist_calls[1]["pageToken"], "1")

    def test_sin_uploads_playlist_devuelve_none(self):
        service = _FakeYouTubeService([_page([])], uploads=None)
        self.assertIsNone(
            comp.ya_subido_hoy(service, comp.compilation_title()))


class TestGuardInBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "src.mp4")
        with open(self.src, "wb") as f:
            f.write(b"fake-mp4")
        self.bridges = {
            "sebastian": _FakeBridge("sebastian", src=self.src),
            "kaez": _FakeBridge("kaez", src=self.src)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _selected_21(self):
        pools = _combined_pools(21, 21)
        combined = []
        for tenant, rows in pools.items():
            for clip in rows:
                row = dict(clip)
                row["tenant_origen"] = tenant
                combined.append(row)
        aptos, _ = comp.filter_pool(combined)
        kept, _ = comp.dedupe_pool(aptos)
        sub_a, _ = comp.split_alternating(kept)
        selected, _ = comp.select_clips(sub_a)
        return selected

    def _run_build(self, service, **over):
        kw = {"label": "A", "dest_tenant": "sebastian",
              "selected": self._selected_21(), "reserve": [],
              "runs_video_dir": os.path.join(
                  self.tmp, comp.RUNS_DIRNAME, "combinado-a"),
              "bridges": self.bridges, "dry_run": False,
              "base_dir": self.tmp}
        kw.update(over)
        _token_file(self.tmp, "sebastian")
        with mock.patch.object(comp, "probe_duration",
                              side_effect=_fake_probe_combined), \
                mock.patch.object(comp, "render_compilation",
                                  side_effect=_fake_render(self.src)), \
                mock.patch.object(comp, "check_youtube_credentials",
                                  return_value=(True, "ok")), \
                mock.patch.object(comp, "_build_youtube_service",
                                  return_value=service) as build, \
                mock.patch.object(comp, "publish_longform") as pub:
            pub.return_value = {
                "video_id": "vid-nuevo",
                "url": "https://www.youtube.com/watch?v=vid-nuevo"}
            report = comp._build_combined_video(**kw)
        return report, build, pub

    def test_ya_subido_hoy_no_llama_publish(self):
        title = comp.compilation_title()
        service = _FakeYouTubeService([_page([
            _video_item("vid-hoy", title, _today())])])
        report, _, pub = self._run_build(service)
        self.assertEqual(pub.call_count, 0)
        self.assertEqual(report["estado"], "ya_subido")
        self.assertEqual(report["video_id"], "vid-hoy")
        self.assertEqual(
            report["url"], "https://www.youtube.com/watch?v=vid-hoy")

    def test_titulo_distinto_si_llama_publish(self):
        service = _FakeYouTubeService([_page([
            _video_item("vid-otro", "Otro título", _today())])])
        report, _, pub = self._run_build(service)
        self.assertEqual(pub.call_count, 1)
        self.assertEqual(report["estado"], "subido")
        self.assertEqual(report["video_id"], "vid-nuevo")

    def test_un_solo_servicio_para_guarda_y_subida(self):
        service = _FakeYouTubeService([_page([
            _video_item("vid-otro", "Otro título", _today())])])
        report, build, pub = self._run_build(service)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(report["estado"], "subido")
        self.assertIs(pub.call_args.kwargs["youtube_service"],
                      service)

    def test_tenant_sin_youtube_bloqueado_y_el_otro_sigue(self):
        # B2 de punta a punta: solo seba tiene token propio; el
        # vídeo B queda construido_sin_subir, el A se sube, y el
        # archivo por tenant de kaez no existe.
        _token_file(self.tmp, "sebastian")
        service = _FakeYouTubeService([_page([
            _video_item("vid-otro", "Otro título", _today())])])
        with mock.patch.object(comp, "probe_duration",
                              side_effect=_fake_probe_combined), \
                mock.patch.object(comp, "render_compilation",
                                  side_effect=_fake_render(self.src)), \
                mock.patch.object(comp, "check_youtube_credentials",
                                  return_value=(True, "ok")), \
                mock.patch.object(comp, "_build_youtube_service",
                                  return_value=service), \
                mock.patch.object(comp, "publish_longform",
                                  return_value={
                                      "video_id": "vidA",
                                      "url": "https://www.youtube.com/"
                                      "watch?v=vidA"}) as pub:
            result = comp.run_combined_compilation(
                dry_run=False, base_dir=self.tmp,
                pools=_combined_pools(21, 21), bridges=self.bridges)
        self.assertEqual(pub.call_count, 1)
        self.assertEqual(
            pub.call_args.kwargs["token_path"],
            os.path.join(self.tmp, "tokens",
                         "youtube_shorts_sebastian.json"))
        self.assertEqual(result["video_a"]["estado"], "subido")
        self.assertEqual(result["video_a"]["video_id"], "vidA")
        self.assertEqual(result["video_b"]["estado"],
                         "construido_sin_subir")
        self.assertIn("credenciales", result["video_b"]["bloqueo"])
        self.assertFalse(os.path.exists(os.path.join(
            self.tmp, "tokens", "youtube_shorts_kaez.json")))


class TestListPublished(unittest.TestCase):
    def test_pide_get_correcto(self):
        class _RecordingBridge(panel_bridge.PanelBridge):
            def __init__(self):
                super().__init__(
                    base_url="https://panel.example",
                    username="u", password="p", tenant="sebastian",
                    token="tok")
                self.calls = []

            def _request_retry(self, method, path, key=None,
                               body=None, raw=None,
                               content_type="application/json"):
                self.calls.append((method, path))
                return {"clips": [{"id": 7}]}

        bridge = _RecordingBridge()
        rows = bridge.list_published(limit=500)
        self.assertEqual(rows, [{"id": 7}])
        self.assertEqual(bridge.calls, [
            ("GET", "/api/clips?status=publicado&limit=500")])
        bridge.list_published()
        self.assertEqual(
            bridge.calls[-1],
            ("GET", "/api/clips?status=publicado&limit=500"))
        bridge.list_published(limit=50)
        self.assertEqual(
            bridge.calls[-1],
            ("GET", "/api/clips?status=publicado&limit=50"))


class TestPoolBridge(unittest.TestCase):
    def test_viral_dict_pasa_filter_y_dedupe(self):
        # Filas como las devuelve clipOut: viral como dict, y
        # ruido de status (aprobado/programado) que filter_pool
        # rechaza sin romper nada.
        rows = [_clip(1, score=90, influencer_id=1),
                _clip(2, score=80, influencer_id=2,
                      status="publicado",
                      viral={"quality_gate": {"pasa": False}}),
                _clip(3, score=70, influencer_id=3,
                      status="aprobado"),
                _clip(4, score=60, influencer_id=4,
                      status="programado")]
        kept, rejected = comp.filter_pool(rows)
        self.assertEqual([c["id"] for c in kept], [1, 2])
        self.assertEqual(len(rejected), 2)
        kept2, dropped = comp.dedupe_pool(kept)
        self.assertEqual([c["id"] for c in kept2], [1, 2])
        self.assertEqual(dropped, [])

    def test_fetch_pool_fusiona_sin_duplicados(self):
        clips = [_clip(1, score=90, influencer_id=1),
                 _clip(2, score=80, influencer_id=2)]
        published = [_clip(2, score=80, influencer_id=2,
                           status="publicado"),
                     _clip(3, score=70, influencer_id=3,
                           status="publicado")]
        bridge = _FakeBridge("sebastian", clips=clips,
                             published=published)
        pool = driver.fetch_pool(bridge, "sebastian")
        self.assertEqual([c["id"] for c in pool], [1, 2, 3])
        self.assertEqual(bridge.list_published_limits, [500])


class TestOnProgress(unittest.TestCase):
    def test_descarga_invoca_callback(self):
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "src.mp4")
            with open(src, "wb") as f:
                f.write(b"fake")
            bridge = _FakeBridge("sebastian", src=src)
            clips = [_clip(1, score=60, influencer_id=1),
                     _clip(2, score=55, influencer_id=2)]
            clips_dir = os.path.join(tmp, "clips")
            os.makedirs(clips_dir, exist_ok=True)
            seen = []
            final, downloaded, dropped = (
                comp._download_with_substitution(
                    list(clips), [], clips_dir,
                    {"sebastian": bridge},
                    on_progress=seen.append))
            self.assertEqual(len(seen), 2)
            self.assertIn("1", seen[0])
            self.assertIn("2", seen[1])
            self.assertEqual(len(final), 2)
            self.assertEqual(dropped, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_render_invoca_callback(self):
        tmp = tempfile.mkdtemp()
        try:
            clips_dir = os.path.join(tmp, "clips")
            os.makedirs(clips_dir)
            clips = []
            for cid in (1, 2, 3):
                path = os.path.join(clips_dir, f"{cid}.mp4")
                with open(path, "wb") as f:
                    f.write(b"fake")
                row = _clip(cid, score=60 - (cid - 1) * 5,
                            influencer_id=cid, start=0, end=2)
                row["_local_path"] = path
                clips.append(row)
            segments = comp.build_segments(clips)
            seen = []

            def _fake_run(cmd, what):
                with open(cmd[-1], "wb") as f:
                    f.write(b"fake")
                return mock.Mock(returncode=0, stderr="")

            with mock.patch.object(comp, "probe_streams",
                                  return_value=(True, True)), \
                    mock.patch.object(comp, "_run",
                                      side_effect=_fake_run):
                out = os.path.join(tmp, "recop.mp4")
                comp.render_compilation(
                    segments, clips_dir, out,
                    os.path.join(tmp, "work"), on_progress=seen.append)
            self.assertTrue(os.path.isfile(out))
            self.assertGreater(len(seen), 0)
            self.assertTrue(any("segmento" in s for s in seen))
            self.assertTrue(any("xfade" in s for s in seen))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_wiring_run_combined_invoca_on_progress(self):
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "src.mp4")
            with open(src, "wb") as f:
                f.write(b"fake")
            _token_file(tmp, "sebastian")
            _token_file(tmp, "kaez")
            bridges = {
                "sebastian": _FakeBridge("sebastian", src=src),
                "kaez": _FakeBridge("kaez", src=src)}
            seen = []
            orig_download = comp._download_with_substitution
            orig_render = comp.render_compilation

            def _spy_download(*args, **kwargs):
                self.assertIsNotNone(kwargs.get("on_progress"))
                return orig_download(*args, **kwargs)

            def _spy_render(*args, **kwargs):
                self.assertIsNotNone(kwargs.get("on_progress"))
                return orig_render(*args, **kwargs)

            def _fake_run(cmd, what):
                with open(cmd[-1], "wb") as f:
                    f.write(b"fake")
                return mock.Mock(returncode=0, stderr="")

            service = _FakeYouTubeService([_page([
                _video_item("vid-otro", "Otro título", _today())])])
            with mock.patch.object(comp, "_download_with_substitution",
                                  side_effect=_spy_download), \
                    mock.patch.object(comp, "render_compilation",
                                      side_effect=_spy_render), \
                    mock.patch.object(comp, "_run",
                                      side_effect=_fake_run), \
                    mock.patch.object(comp, "probe_streams",
                                      return_value=(True, True)), \
                    mock.patch.object(comp, "probe_duration",
                                      side_effect=_fake_probe_combined), \
                    mock.patch.object(comp, "check_youtube_credentials",
                                      return_value=(True, "ok")), \
                    mock.patch.object(comp, "_build_youtube_service",
                                      return_value=service), \
                    mock.patch.object(comp, "ya_subido_hoy",
                                      return_value=None), \
                    mock.patch.object(comp, "publish_longform",
                                      return_value={
                                          "video_id": "v1",
                                          "url": "https://www.youtube.com/"
                                          "watch?v=v1"}):
                result = comp.run_combined_compilation(
                    dry_run=False, base_dir=tmp,
                    pools=_combined_pools(21, 21), bridges=bridges,
                    on_progress=seen.append)
            self.assertEqual(result["video_a"]["estado"], "subido")
            self.assertEqual(result["video_b"]["estado"], "subido")
            self.assertGreater(len(seen), 0)
            self.assertTrue(any("descargando" in s for s in seen))
            self.assertTrue(any("render" in s for s in seen))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestExitCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "src.mp4")
        with open(self.src, "wb") as f:
            f.write(b"fake")
        self.bridges = {
            "sebastian": _FakeBridge("sebastian",
                                     youtube_token="tok-seba",
                                     src=self.src),
            "kaez": _FakeBridge("kaez", youtube_token="tok-kaez",
                                src=self.src)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _report(self, estado, video_id=None, url=None):
        return {"tenant": "sebastian", "estado": estado,
                "duracion_final_s": 845.0, "video_id": video_id,
                "url": url, "bloqueo": None}

    def _run_main(self, result=None, error=None, already=None):
        patches = [
            mock.patch.object(comp, "find_font",
                              return_value="/usr/share/fonts/"
                              "truetype/dejavu/DejaVuSans.ttf"),
            mock.patch.object(driver, "check_already_uploaded_today",
                              return_value=already or {}),
        ]
        if error is not None:
            patches.append(mock.patch.object(
                comp, "run_combined_compilation",
                side_effect=error))
        else:
            patches.append(mock.patch.object(
                comp, "run_combined_compilation",
                return_value=result))
        for patch in patches:
            patch.start()
        try:
            return driver.main(["--combinado"], base_dir=self.tmp,
                               bridges=self.bridges)
        finally:
            for patch in reversed(patches):
                patch.stop()

    def test_construido_sin_subir_sale_3(self):
        result = {
            "video_a": self._report("construido_sin_subir"),
            "video_b": self._report(
                "subido", "vidB",
                "https://www.youtube.com/watch?v=vidB")}
        code = self._run_main(result=result)
        self.assertEqual(code, 3)
        resumen = json.load(open(os.path.join(
            self.tmp, "compilation-out", "resumen.json")))
        self.assertEqual(resumen["video_a"]["estado"],
                         "construido_sin_subir")
        self.assertEqual(resumen["video_b"]["estado"], "subido")
        self.assertIsNone(resumen["creditos_estimados"])

    def test_ya_subido_sale_0(self):
        result = {
            "video_a": self._report(
                "ya_subido", "vidA",
                "https://www.youtube.com/watch?v=vidA"),
            "video_b": self._report(
                "subido", "vidB",
                "https://www.youtube.com/watch?v=vidB")}
        code = self._run_main(result=result)
        self.assertEqual(code, 0)

    def test_pool_insuficiente_sale_2(self):
        code = self._run_main(
            error=comp.PoolInsufficientError("pool corto"))
        self.assertEqual(code, 2)

    def test_ambos_ya_subidos_al_inicio_sale_0_sin_render(self):
        already = {
            "sebastian": {"video_id": "a1",
                          "url": "https://www.youtube.com/watch?v=a1"},
            "kaez": {"video_id": "b1",
                     "url": "https://www.youtube.com/watch?v=b1"}}
        with mock.patch.object(comp, "find_font",
                              return_value="/usr/share/fonts/"
                              "truetype/dejavu/DejaVuSans.ttf"), \
                mock.patch.object(driver,
                                  "check_already_uploaded_today",
                                  return_value=already), \
                mock.patch.object(comp, "run_combined_compilation",
                                  side_effect=AssertionError(
                                      "no debe renderizar")):
            code = driver.main(["--combinado"], base_dir=self.tmp,
                               bridges=self.bridges)
        self.assertEqual(code, 0)
        resumen = json.load(open(os.path.join(
            self.tmp, "compilation-out", "resumen.json")))
        self.assertEqual(resumen["video_a"]["estado"], "ya_subido")
        self.assertEqual(resumen["video_b"]["video_id"], "b1")


class TestCircleCIYaml(unittest.TestCase):
    def test_config_compilation(self):
        path = os.path.join(REPO_ROOT, ".circleci", "config.yml")
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        params = config["parameters"]
        self.assertEqual(params["compilation"]["type"], "boolean")
        self.assertIs(params["compilation"]["default"], False)
        job = config["jobs"]["compilation"]
        self.assertEqual(job["docker"][0]["image"],
                         "cimg/base:current-24.04")
        self.assertEqual(job["resource_class"], "small")
        driver_step = None
        artifacts_step = None
        for step in job["steps"]:
            if isinstance(step, dict) and "run" in step:
                run = step["run"]
                if "circle_compilation" in run.get("command", ""):
                    driver_step = run
            if isinstance(step, dict) and "store_artifacts" in step:
                artifacts_step = step["store_artifacts"]
        self.assertIsNotNone(driver_step)
        self.assertEqual(driver_step["no_output_timeout"], "120m")
        self.assertIn("PYTHONUNBUFFERED=1", driver_step["command"])
        self.assertIn("python -m clipagent.circle_compilation "
                      "--combinado", driver_step["command"])
        self.assertEqual(artifacts_step["path"], "compilation-out")
        self.assertEqual(artifacts_step["destination"],
                         "recopilacion-larga")
        self.assertEqual(artifacts_step["when"], "always")
        workflow = config["workflows"]["compilation"]
        self.assertEqual(workflow["when"],
                         "<< pipeline.parameters.compilation >>")
        self.assertEqual(workflow["jobs"],
                         [{"compilation":
                           {"context": "compilation-panel"}}])
        main_when = config["workflows"]["main"]["when"]
        self.assertEqual(main_when, {
            "not": {"equal": [True,
                              "<< pipeline.parameters.compilation >>"]}})
        # Los jobs existentes siguen ahí, sin tocar.
        self.assertIn("tests", config["jobs"])
        self.assertIn("render", config["jobs"])


if __name__ == "__main__":
    unittest.main()
