"""Tests del agente navegador TikTok (clipagent/tiktok_browser.py).

Navegador MOCKEADO: no se sube nada real, no hay credenciales, no se
necesita Playwright instalado. El fake implementa solo la superficie
que usa el agente: goto, locator, set_input_files, wait_for_timeout,
screenshot, content y url.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from clipagent.tiktok_browser import (  # noqa: E402
    DEFAULT_ACCOUNT,
    MAX_ATTEMPTS,
    TikTokBrowserAgent,
    TikTokClip,
    StateStore,
    idempotency_key,
)


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
        self.first = self

    def count(self):
        return self.page.locator_counts.get(self.selector, 0)

    def click(self):
        self.page.clicks.append(self.selector)
        # Clic en Publicar: TikTok redirige al vídeo y muestra el toast.
        if self.selector in ("text=Post", "text=Publicar"):
            self.page.url = ("https://www.tiktok.com/@elclipdegta6/"
                             "video/1234567890123456789")
            self.page.html = ("<div>Your video has been uploaded</div>"
                              "<a href=\"/video/1234567890123456789\">")
        return None

    def fill(self, text):
        self.page.filled[self.selector] = text
        return None

    def text_content(self):
        return self.page.filled.get(self.selector, "")

    def is_visible(self):
        return self.count() > 0


class FakePage:
    """Fake mínimo de una página Playwright."""

    def __init__(self, html="<html></html>", url="https://www.tiktok.com/"):
        self.html = html
        self.url = url
        self.locator_counts = {
            "div[contenteditable='true']": 1,
            "text=Post": 1,
            "video": 1,
        }
        self.clicks = []
        self.filled = {}
        self.uploaded = []
        self.screenshots = []
        self.visited = []

    def goto(self, url):
        self.visited.append(url)
        self.url = url
        # Al ir al perfil, el vídeo aparece si ya se "publicó".
        if url.startswith("https://www.tiktok.com/@"):
            self.html = ("<a href=\"/video/1234567890123456789\">"
                         "clip</a>")
        return None

    def locator(self, selector):
        return FakeLocator(self, selector)

    def set_input_files(self, selector, path):
        self.uploaded.append((selector, path))
        return None

    def wait_for_timeout(self, ms):
        return None

    def screenshot(self, path=None):
        self.screenshots.append(path)
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"fake-png")
        return None

    def content(self):
        return self.html


@pytest.fixture()
def store(tmp_path):
    return StateStore(str(tmp_path / "state"))


@pytest.fixture()
def agent(store):
    return TikTokBrowserAgent(store)


def _clip(tmp_path, name="clip_01.mp4"):
    mp4 = tmp_path / name
    mp4.write_bytes(b"fake-mp4")
    return TikTokClip(mp4_path=str(mp4), title="GTA 6 clipazo",
                      description="El mejor momento",
                      hashtags=["gta6", "gta6clips"],
                      account=DEFAULT_ACCOUNT, date="2026-11-17")


def test_idempotency_key_determinista_y_unica_por_dia_cuenta():
    k1 = idempotency_key("clip_01", "@elclipdegta6", "2026-11-17")
    k2 = idempotency_key("clip_01", "@elclipdegta6", "2026-11-17")
    assert k1 == k2 and len(k1) == 64
    assert k1 != idempotency_key("clip_01", "@elclipdegta6", "2026-11-18")
    assert k1 != idempotency_key("clip_01", "@otra", "2026-11-17")
    assert k1 != idempotency_key("clip_02", "@elclipdegta6", "2026-11-17")


def test_caption_lleva_titulo_descripcion_y_hashtags():
    clip = TikTokClip(mp4_path="/tmp/clip_01.mp4", title="GTA 6 clipazo",
                      description="El mejor momento",
                      hashtags=["gta6", "gta6clips"],
                      account=DEFAULT_ACCOUNT, date="2026-11-17")
    assert clip.caption.startswith("GTA 6 clipazo")
    assert "#gta6" in clip.caption and "#gta6clips" in clip.caption


def test_upload_ok_marca_posted_con_evidencia(tmp_path, agent, store):
    clip = _clip(tmp_path)
    page = FakePage()
    result = agent.run(clip, page)
    assert result["status"] == "posted"
    ev = result["evidence"]
    assert ev["toast"] is True
    assert "/video/" in ev["url"]
    assert ev["screenshot"] and os.path.isfile(ev["screenshot"])
    assert ev["profile_verified"] is True
    key = idempotency_key(clip.clip_id, clip.account, clip.date)
    assert store.get(key)["state"] == "posted"
    # El MP4 NO se borra jamás.
    assert os.path.isfile(clip.mp4_path)


def test_duplicado_posted_no_resube(tmp_path, agent):
    clip = _clip(tmp_path)
    page = FakePage()
    assert agent.run(clip, page)["status"] == "posted"
    page2 = FakePage()
    result = agent.run(clip, page2)
    assert result["status"] == "skipped_duplicate"
    assert page2.uploaded == []


def test_duplicado_processing_no_resube(tmp_path, agent, store):
    clip = _clip(tmp_path)
    key = idempotency_key(clip.clip_id, clip.account, clip.date)
    store.put(key, {"state": "processing", "account": clip.account,
                    "date": clip.date})
    page = FakePage()
    result = agent.run(clip, page)
    assert result["status"] == "skipped_duplicate"
    assert page.uploaded == []


def test_tope_un_clip_por_dia(tmp_path, agent):
    clip1 = _clip(tmp_path, "clip_01.mp4")
    assert agent.run(clip1, FakePage())["status"] == "posted"
    clip2 = _clip(tmp_path, "clip_02.mp4")
    result = agent.run(clip2, FakePage())
    assert result["status"] == "skipped_daily_limit"


def test_captcha_detiene_marca_waiting_y_avisa(tmp_path, agent, store):
    clip = _clip(tmp_path)
    page = FakePage(html="<div>Please complete the captcha to "
                    "continue</div>")
    result = agent.run(clip, page)
    assert result["status"] == "waiting_captcha"
    assert page.uploaded == []  # no se subió nada
    key = idempotency_key(clip.clip_id, clip.account, clip.date)
    rec = store.get(key)
    assert rec["state"] == "waiting_captcha"
    assert rec.get("captcha_notice") \
        and os.path.isfile(rec["captcha_notice"])
    assert rec.get("captcha_screenshot") \
        and os.path.isfile(rec["captcha_screenshot"])


def test_captcha_queda_visible_en_el_panel(tmp_path, agent, store):
    """Pausa avisada, no paro silencioso: panel_status.json dice
    waiting_captcha con captura, aviso y hora."""
    clip = _clip(tmp_path)
    page = FakePage(html="<div>Please complete the captcha to "
                    "continue</div>")
    result = agent.run(clip, page)
    assert result["status"] == "waiting_captcha"
    status_path = os.path.join(str(tmp_path / "state"),
                               "panel_status.json")
    assert os.path.isfile(status_path)
    with open(status_path, encoding="utf-8") as f:
        status = json.load(f)
    assert status["platform"] == "tiktok"
    assert status["status"] == "waiting_captcha"
    assert status["clip"] == clip.clip_id
    assert status["account"] == clip.account
    assert status["screenshot"] and os.path.isfile(status["screenshot"])
    assert status["notice"] and os.path.isfile(status["notice"])
    assert status["updated_at"]


def test_posted_tambien_queda_en_panel_status(tmp_path, agent):
    clip = _clip(tmp_path)
    result = agent.run(clip, FakePage())
    assert result["status"] == "posted"
    with open(os.path.join(str(tmp_path / "state"), "panel_status.json"),
              encoding="utf-8") as f:
        status = json.load(f)
    assert status["status"] == "posted"
    assert "/video/" in status["url"]


def test_resume_tras_captcha_exige_mismo_mp4_titulo(tmp_path, agent):
    clip = _clip(tmp_path)
    page = FakePage(html="<div>captcha</div>")
    assert agent.run(clip, page)["status"] == "waiting_captcha"
    # Humano resolvió el captcha; la página ya no lo muestra.
    page2 = FakePage()
    otro = TikTokClip(mp4_path=clip.mp4_path, title="OTRO TÍTULO",
                      description=clip.description,
                      hashtags=clip.hashtags, account=clip.account,
                      date=clip.date)
    result = agent.run(otro, page2, resume=True)
    assert result["status"] == "skipped_resume_mismatch"
    assert page2.uploaded == []
    # Con los mismos datos sí retoma y publica.
    result = agent.run(clip, FakePage(), resume=True)
    assert result["status"] == "posted"


def test_rechazo_pausa_2h_y_un_reintento(tmp_path, agent, store):
    clip = _clip(tmp_path)
    page = FakePage()
    page.locator_counts["text=Post"] = 0
    page.locator_counts["text=Publicar"] = 0  # sin botón: fallo seguro
    result = agent.run(clip, page)
    assert result["status"] == "failed_retryable"
    assert "retry_after" in result
    key = idempotency_key(clip.clip_id, clip.account, clip.date)
    rec = store.get(key)
    assert rec["state"] == "failed" and rec["attempts"] == 1
    # Dentro de la pausa de 2 h no reintenta.
    result = agent.run(clip, FakePage())
    assert result["status"] == "failed_retryable"
    assert "2 h" in result["reason"]
    # Tras la pausa, un reintento con la misma sesión...
    rec["retry_after"] = "2020-01-01T00:00:00-03:00"
    store.put(key, rec)
    page2 = FakePage()
    page2.locator_counts["text=Post"] = 0
    page2.locator_counts["text=Publicar"] = 0
    result = agent.run(clip, page2)
    # ...y si vuelve a fallar: Plan B, sin más reintentos hoy.
    assert result["status"] == "plan_b"
    assert result["manual_package"] \
        and os.path.isfile(result["manual_package"])
    assert store.get(key)["state"] == "plan_b"
    assert store.get(key)["attempts"] == MAX_ATTEMPTS


def test_sin_evidencia_no_se_marca_posted(tmp_path, agent, store):
    clip = _clip(tmp_path)
    page = FakePage()
    # Publicar sin toast ni URL: el fake no cambia url/html al clicar.
    orig_click = FakeLocator.click

    def click_sin_evidencia(self):
        self.page.clicks.append(self.selector)
        return None
    FakeLocator.click = click_sin_evidencia
    try:
        result = agent.run(clip, page)
    finally:
        FakeLocator.click = orig_click
    assert result["status"] == "failed_retryable"
    key = idempotency_key(clip.clip_id, clip.account, clip.date)
    assert store.get(key)["state"] != "posted"


def test_needs_verification_se_confirma_con_verify(tmp_path, agent):
    clip = _clip(tmp_path)
    page = FakePage()
    # Toast + URL sí, pero el perfil no muestra el vídeo todavía.
    page.html = "<div>Your video has been uploaded</div>"
    orig_goto = FakePage.goto

    def goto_sin_video(self, url):
        self.visited.append(url)
        self.url = url
        self.html = "<div>perfil vacío</div>"
    FakePage.goto = goto_sin_video
    try:
        result = agent.run(clip, page)
    finally:
        FakePage.goto = orig_goto
    assert result["status"] == "needs_verification"
    # El humano revisa el perfil; ahora sí aparece: verify lo marca.
    result = agent.verify_posted(clip, FakePage())
    assert result["status"] == "posted"


def test_estado_persiste_en_disco(tmp_path, store):
    clip = _clip(tmp_path)
    key = idempotency_key(clip.clip_id, clip.account, clip.date)
    store.put(key, {"state": "scheduled", "account": clip.account,
                    "date": clip.date})
    again = StateStore(str(tmp_path / "state"))
    assert again.get(key)["state"] == "scheduled"
    assert os.path.isfile(os.path.join(str(tmp_path / "state"),
                                       "tiktok_browser.json"))


def test_mp4_inexistente_falla_sin_tocar_estado(tmp_path, agent, store):
    clip = TikTokClip(mp4_path=str(tmp_path / "noexiste.mp4"),
                      title="x", account=DEFAULT_ACCOUNT,
                      date="2026-11-17")
    with pytest.raises(Exception):
        agent.run(clip, FakePage())
    assert store._data["records"] == {}


def test_cli_dry_run_no_necesita_navegador(tmp_path):
    from clipagent.tiktok_browser import main
    mp4 = tmp_path / "clip_01.mp4"
    mp4.write_bytes(b"fake-mp4")
    rc = main(["upload", "--clip", str(mp4), "--title", "t",
               "--state-dir", str(tmp_path / "state"), "--dry-run"])
    assert rc == 0
