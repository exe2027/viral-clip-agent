#!/usr/bin/env python3
"""Tests del vigilante de publicación (clipagent/publish_watchdog.py).

Nada de red real ni escrituras D1: los clientes son fakes
inyectables (D1, puente, verificación de URLs). Los valores de
reglas son los VIVOS de D1 (settings 2026-11-21: YT 6/6, resto
2/3, espaciamiento 2 h / 4 h YT, calentamiento 1/día) y el
fallback de limits.yaml se prueba aparte.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

import clipagent.retry as _retry  # noqa: E402
from clipagent.d1_client import D1QuotaError  # noqa: E402
from clipagent.publish_watchdog import (  # noqa: E402
    _build_simulate_clients,
    build_context,
    classify_job,
    gate_hold,
    load_publish_watchdog_config,
    load_slots,
    next_day_key,
    next_repub_n,
    plan_republish,
    repub_key,
    resolve_rules,
    run,
    santiago_day_key,
    santiago_ms_for,
    slot_just_ended,
    in_slot,
    verify_url,
    warmup_daily_cap,
    warmup_stage,
)
from clipagent.validate import ConfigError  # noqa: E402

WCFG = {
    "repair_window_min": 60, "max_cycles_per_run": 1,
    "stuck_processing_min": 30, "verify_timeout_s": 20,
    "telegram_enabled": True,
}
DAY = "2026-11-21"
HOUR = 3600000
MIN = 60000

LIVE_SETTINGS = [
    {"tenant_id": "sebastian", "data": {
        "daily_limit_youtube_shorts": 6, "daily_limit_tiktok": 3,
        "daily_limit_instagram": 3, "daily_limit_facebook": 3,
        "youtube_plan": 6, "youtube_max": 6,
        "resto_plan": 2, "resto_max": 3, "spacing_hours": 2,
        "new_account_daily": 1, "new_account_daily_youtube": 6,
        "min_virality": 45, "min_seo": 80}},
    {"tenant_id": "kaez", "data": {
        "daily_limit_youtube_shorts": 6, "daily_limit_tiktok": 3,
        "daily_limit_instagram": 3, "daily_limit_facebook": 3,
        "youtube_plan": 6, "youtube_max": 6,
        "resto_plan": 2, "resto_max": 3, "spacing_hours": 2,
        "new_account_daily": 1, "new_account_daily_youtube": 6,
        "min_virality": 45, "min_seo": 80}},
]


@pytest.fixture(autouse=True)
def _reset_quota_circuit():
    """El circuito 7500 es global del proceso: cada test arranca
    con el estado que dejó el anterior y lo restaura."""
    prev = _retry._panel_quota_tripped_at_ms
    yield
    _retry._panel_quota_tripped_at_ms = prev


def _sim_ctx(now_ms):
    """Contexto del escenario forzado (jobs 899-904)."""
    fakes = _build_simulate_clients(now_ms, DAY, down=True)
    rules = resolve_rules(LIVE_SETTINGS)
    jobs = fakes["d1"].query("SELECT * FROM jobs")
    accounts = fakes["d1"].query("SELECT * FROM api_accounts")
    ctx = build_context(jobs, accounts, rules, now_ms, DAY)
    return fakes, jobs, ctx


# --- Tiempo y franjas ------------------------------------------------------

def test_santiago_day_key_roundtrip():
    ms = santiago_ms_for(DAY, "14:45")
    assert santiago_day_key(ms) == DAY
    assert santiago_day_key(ms + 10 * HOUR) == "2026-11-22"
    assert next_day_key(DAY) == "2026-11-22"


def test_slot_just_ended():
    slots = load_slots()
    assert [s[0] for s in slots] == ["morning", "afternoon", "night"]
    assert slot_just_ended(santiago_ms_for(DAY, "14:45"), slots, 60) \
        == "morning"
    assert slot_just_ended(santiago_ms_for(DAY, "14:00"), slots, 60) \
        == "morning"
    assert slot_just_ended(santiago_ms_for(DAY, "15:00"), slots, 60) \
        is None
    assert slot_just_ended(santiago_ms_for(DAY, "13:00"), slots, 60) \
        is None
    assert slot_just_ended(santiago_ms_for(DAY, "20:30"), slots, 60) \
        == "afternoon"
    assert slot_just_ended(santiago_ms_for(DAY, "23:30"), slots, 60) \
        == "night"
    assert slot_just_ended(santiago_ms_for("2026-11-22", "02:30"),
                            slots, 60) is None


def test_in_slot():
    assert in_slot(santiago_ms_for(DAY, "13:00"))
    assert in_slot(santiago_ms_for(DAY, "22:00"))
    assert not in_slot(santiago_ms_for(DAY, "15:00"))
    assert not in_slot(santiago_ms_for(DAY, "14:45"))


# --- Calentamiento ---------------------------------------------------------

def test_warmup_stage():
    assert warmup_stage(None, 0, 0) == "semana1"
    assert warmup_stage(2, 5, 0) == "semana1"
    assert warmup_stage(6, 5, 0) == "semana1"
    assert warmup_stage(7, 5, 0) == "semana2"
    assert warmup_stage(13, 5, 0) == "semana2"
    assert warmup_stage(14, 19, 0) == "semana3"
    assert warmup_stage(14, 20, 0) == "establecida"
    assert warmup_stage(30, 25, 0) == "establecida"
    assert warmup_stage(2, 5, 1) == "semana1"
    assert warmup_stage(10, 5, 1) == "semana2"


def test_warmup_daily_cap():
    tt = {"plan": 2, "max": 3, "new_daily": 1}
    yt = {"plan": 6, "max": 6, "new_daily": 6}
    assert warmup_daily_cap("semana1", "tiktok", tt) == 1
    assert warmup_daily_cap("semana2", "tiktok", tt) == 1
    assert warmup_daily_cap("semana3", "tiktok", tt) == 2
    assert warmup_daily_cap("establecida", "tiktok", tt) == 3
    assert warmup_daily_cap("semana1", "youtube", yt) == 6
    assert warmup_daily_cap("establecida", "youtube", yt) == 6


# --- Reglas vivas ----------------------------------------------------------

def test_resolve_rules_from_d1():
    rules = resolve_rules(LIVE_SETTINGS)
    assert rules["sebastian"]["youtube"] == {
        "plan": 6, "max": 6, "spacing_h": 4, "new_daily": 6}
    assert rules["sebastian"]["tiktok"]["plan"] == 2
    assert rules["sebastian"]["tiktok"]["max"] == 3
    assert rules["sebastian"]["tiktok"]["spacing_h"] == 2
    assert rules["sebastian"]["tiktok"]["new_daily"] == 1
    assert rules["sebastian"]["instagram"]["max"] == 3
    assert rules["sebastian"]["facebook"]["max"] == 3
    assert rules["sebastian"]["rolling_max"] == 3
    assert rules["sebastian"]["from_d1"] is True
    assert rules["kaez"]["youtube"]["max"] == 6


def test_resolve_rules_fallback_limits():
    rules = resolve_rules([])
    assert rules["sebastian"]["from_d1"] is False
    # limits.yaml (reglas 2026-11-19): YT plan 1/máx 2.
    assert rules["sebastian"]["youtube"]["plan"] == 1
    assert rules["sebastian"]["youtube"]["max"] == 2
    assert rules["sebastian"]["tiktok"]["plan"] == 2
    assert rules["sebastian"]["tiktok"]["max"] == 3


# --- Republicaciones -------------------------------------------------------

def test_repub_key_and_next_n():
    key1 = repub_key(168, "facebook_reels", 1, "2026-11-22", 1)
    key2 = repub_key(168, "facebook_reels", 1, "2026-11-22", 2)
    assert len(key1) == 64 and key1 != key2
    assert repub_key(168, "facebook_reels", 1, "2026-11-22", 1) == key1
    assert next_repub_n([]) == 1
    assert next_repub_n(["abc"]) == 1
    assert next_repub_n(["x|repub1", "y|repub2"]) == 3
    assert next_repub_n(["x|repub3"]) == 4


def test_plan_republish():
    now_ms = santiago_ms_for(DAY, "14:45")
    job = {"clip_id": 51, "platform": "youtube_shorts",
           "api_account_id": 2, "title": "t", "caption": "c",
           "r2_key": "clips/x/51.mp4", "language": "es",
           "tenant_id": "sebastian"}
    clip = {"publish_blocked": 0, "video_r2_key": "clips/x/51.mp4"}
    plan = plan_republish(job, clip, [], "2026-11-22", now_ms)
    assert plan is not None
    assert plan["repub_n"] == 1
    assert plan["target_day"] == "2026-11-22"
    assert plan["next_run_at"] == santiago_ms_for("2026-11-22",
                                                  "12:00")
    assert plan["idempotency_key"] == repub_key(
        51, "youtube_shorts", 2, "2026-11-22", 1)
    plan2 = plan_republish(job, clip, ["k|repub1", "k|repub2"],
                           "2026-11-22", now_ms)
    assert plan2["repub_n"] == 3
    assert plan_republish(job, {"publish_blocked": 1,
                                "video_r2_key": "x"}, [],
                          "2026-11-22", now_ms) is None
    # Sin vídeo ni en el clip ni en el job: no hay republicación.
    assert plan_republish(dict(job, r2_key=""),
                          {"publish_blocked": 0, "video_r2_key": ""},
                          [], "2026-11-22", now_ms) is None


# --- Clasificación ---------------------------------------------------------

def test_classify_simulate_jobs():
    now_ms = santiago_ms_for(DAY, "14:45")
    _fakes, jobs, ctx = _sim_ctx(now_ms)
    by_id = {j["id"]: j for j in jobs}
    assert classify_job(by_id[899], ctx, "morning")[0] == "verify"
    assert classify_job(by_id[900], ctx, "morning")[0] == "verify"
    assert classify_job(by_id[901], ctx, "morning")[0] == "verify"
    assert classify_job(by_id[902], ctx, "morning")[0] == "ready"
    # Fuera de ventana de reparación: listo pero esperando franja.
    assert classify_job(by_id[902], ctx, None)[0] == "waiting_slot"
    verdict, _reason, when = classify_job(by_id[903], ctx, "morning")
    assert verdict == "held_calentamiento"
    assert "mañana" in when
    assert classify_job(by_id[904], ctx, "morning")[0] \
        == "failed_terminal"


def test_classify_processing_and_waiting():
    now_ms = santiago_ms_for(DAY, "14:45")
    _fakes, jobs, ctx = _sim_ctx(now_ms)
    base = dict(jobs[0])
    stale = dict(base, id=910, state="processing",
                 platform="youtube_shorts", api_account_id=2,
                 clip_id=60, updated_at=now_ms - 60 * MIN)
    assert classify_job(stale, ctx, "morning")[0] == "ready"
    fresh = dict(stale, id=911, updated_at=now_ms - 5 * MIN)
    assert classify_job(fresh, ctx, "morning")[0] == "processing"
    future = dict(base, id=912, state="scheduled",
                  platform="youtube_shorts", api_account_id=2,
                  clip_id=61, next_run_at=now_ms + 60 * MIN)
    assert classify_job(future, ctx, "morning")[0] \
        == "waiting_next_run"
    no_url = dict(base, id=913, state="posted", evidence_url="")
    assert classify_job(no_url, ctx, "morning")[0] == "posted_no_url"
    retryable = dict(base, id=914, state="failed", retryable=1,
                     last_error="timeout")
    assert classify_job(retryable, ctx, "morning")[0] \
        == "failed_retryable"
    captcha = dict(base, id=915, state="awaiting_captcha")
    assert classify_job(captcha, ctx, "morning")[0] == "captcha"


def test_gate_hold_spacing():
    now_ms = santiago_ms_for(DAY, "14:45")
    _fakes, jobs, ctx = _sim_ctx(now_ms)
    # YouTube ES publicó hace 5 h: espaciamiento 4 h cumplido.
    job = {"tenant_id": "sebastian", "api_account_id": 2,
           "platform": "youtube_shorts", "clip_id": 99}
    assert gate_hold(job, ctx) is None
    # Si hubiera publicado hace 1 h, el espaciamiento frena.
    ctx.accounts[("sebastian", 2)].last_published_at_ms = \
        now_ms - 60 * MIN
    reason, when = gate_hold(job, ctx)
    assert reason == "espaciamiento"
    assert "a las" in when


# --- Verificación de URLs --------------------------------------------------

def _fake_http(mapping):
    def get(url, headers, timeout):
        for needle, (status, body) in mapping.items():
            if needle in url:
                return status, body
        raise RuntimeError("sin red en el test")
    return get


def test_verify_url_youtube_tiktok():
    get = _fake_http({
        "GOOD": (200, '{"title": "ok"}'),
        "DEAD": (404, ""),
        "ERR": (500, ""),
    })
    assert verify_url("https://www.youtube.com/shorts/GOOD",
                      "youtube_shorts", http_get=get)[0] == "live"
    assert verify_url("https://www.youtube.com/shorts/DEAD",
                      "youtube_shorts", http_get=get)[0] == "down"
    assert verify_url("https://www.youtube.com/shorts/ERR",
                      "youtube_shorts", http_get=get)[0] == "unknown"
    assert verify_url("https://www.tiktok.com/@x/video/GOOD",
                      "tiktok", http_get=get)[0] == "live"
    assert verify_url("https://www.tiktok.com/@x/video/DEAD",
                      "tiktok", http_get=get)[0] == "down"
    assert verify_url("", "youtube_shorts", http_get=get)[0] \
        == "unknown"


def test_verify_url_instagram_facebook_is_weak():
    get = _fake_http({
        "LIVE123": (200, "<html>.../reel/LIVE123/... video</html>"),
        "DOWN": (200, "<html>Este reel no está disponible</html>"),
        "WALL": (404, ""),
    })
    # Un 200 con el id parece vivo, pero es evidencia DÉBIL.
    assert verify_url("https://www.instagram.com/reel/LIVE123/",
                      "instagram_reels", http_get=get)[0] == "weak"
    # Las marcas de caída solo generan sospecha (weak), nunca down.
    assert verify_url("https://www.facebook.com/reel/DOWN/",
                      "facebook_reels", http_get=get)[0] == "weak"
    assert verify_url("https://www.instagram.com/reel/WALL/",
                      "instagram_reels", http_get=get)[0] == "unknown"
    # Sin red: unknown, nunca down.
    def boom(url, headers, timeout):
        raise RuntimeError("sin red")
    assert verify_url("https://www.youtube.com/shorts/X",
                      "youtube_shorts", http_get=boom)[0] == "unknown"


# --- Orquestación (simulación) ----------------------------------------------

def test_run_dry_run_simulate(tmp_path):
    now_ms = santiago_ms_for(DAY, "14:45")
    fakes = _build_simulate_clients(now_ms, DAY, down=True)
    report = run(d1=fakes["d1"], bridge=fakes["bridge"],
                 http_get=fakes["http_get"], now_ms=now_ms,
                 live=False, out_dir=str(tmp_path), cfg=WCFG)
    # Dry-run: NUNCA escribe ni dispara ciclos ni avisa.
    assert fakes["d1"].executed == []
    assert fakes["bridge"].cycles == 0
    assert fakes["bridge"].telegrams == []
    assert report["live"] is False
    assert any("CAÍDA" in v for v in report["verified"])
    assert any("VIVA" in v for v in report["verified"])
    assert any("|repub1" in r and "[dry-run]" in r
               for r in report["republished"])
    assert any("902" in r and "[dry-run]" in r
               for r in report["repaired"])
    assert any("903" in h and "calentamiento" in h
               for h in report["held"])
    assert any("904" in u for u in report["unfixable"])
    assert (tmp_path / f"report-{DAY}-morning.md").exists()


def test_run_live_simulate(tmp_path):
    now_ms = santiago_ms_for(DAY, "14:45")
    fakes = _build_simulate_clients(now_ms, DAY, down=True)
    report = run(d1=fakes["d1"], bridge=fakes["bridge"],
                 http_get=fakes["http_get"], now_ms=now_ms,
                 live=True, out_dir=str(tmp_path), cfg=WCFG)
    # Live: 1 ciclo, 1 INSERT de republicación, 1 Telegram
    # (hay algo sin arreglar: el failed terminal 904).
    assert fakes["bridge"].cycles == 1
    assert len(fakes["d1"].executed) == 1
    sql, params = fakes["d1"].executed[0]
    assert "INSERT INTO jobs" in sql
    assert params[3] == repub_key(51, "youtube_shorts", 2,
                                  "2026-11-22", 1)
    assert params[-1] == "2026-11-22"  # slot_day = mañana
    assert len(fakes["bridge"].telegrams) == 1
    assert any("republicación |repub1 creada" in r
               for r in report["republished"])
    assert any("ciclo run-once" in r for r in report["repaired"])


def test_stale_unfixable_no_telegram(tmp_path):
    """Lo viejo queda en el informe pero NO dispara Telegram:
    si no, los 19 borradores TikTok cancelados anoche avisarían
    3 veces al día, para siempre."""
    now_ms = santiago_ms_for(DAY, "14:45")
    fakes = _build_simulate_clients(now_ms, DAY, down=False)
    # El 904 (failed terminal) pasó a ser de AYER.
    fakes["jobs"][5]["updated_at"] = now_ms - 25 * HOUR
    report = run(d1=fakes["d1"], bridge=fakes["bridge"],
                 http_get=fakes["http_get"], now_ms=now_ms,
                 live=True, out_dir=str(tmp_path), cfg=WCFG)
    assert any("904" in u for u in report["unfixable"])
    assert report["unfixable_fresh"] == []
    assert fakes["bridge"].telegrams == []


def test_run_no_down_no_republish(tmp_path):
    now_ms = santiago_ms_for(DAY, "14:45")
    fakes = _build_simulate_clients(now_ms, DAY, down=False)
    report = run(d1=fakes["d1"], bridge=fakes["bridge"],
                 http_get=fakes["http_get"], now_ms=now_ms,
                 live=True, out_dir=str(tmp_path), cfg=WCFG)
    assert fakes["d1"].executed == []
    assert report["republished"] == []
    assert fakes["bridge"].cycles == 1


def test_run_quota_error_stops(tmp_path):
    now_ms = santiago_ms_for(DAY, "14:45")
    fakes = _build_simulate_clients(now_ms, DAY, down=True)

    class QuotaD1:
        def query(self, sql, params=None):
            raise D1QuotaError("D1 -> cuota agotada (7500)")

        def execute(self, sql, params=None):
            raise D1QuotaError("D1 -> cuota agotada (7500)")

    report = run(d1=QuotaD1(), bridge=fakes["bridge"],
                 http_get=fakes["http_get"], now_ms=now_ms,
                 live=True, out_dir=str(tmp_path), cfg=WCFG)
    assert fakes["bridge"].cycles == 0
    assert fakes["d1"].executed == []
    assert any("cuota" in n for n in report["notes"])
    assert report["republished"] == []


def test_run_no_clients(tmp_path):
    report = run(d1=None, bridge=None, live=False,
                 out_dir=str(tmp_path), cfg=WCFG)
    assert any("sin D1 ni panel" in n for n in report["notes"])
    assert report["republished"] == [] and report["repaired"] == []


# --- Config y workflow ------------------------------------------------------

def test_load_publish_watchdog_config_real():
    cfg = load_publish_watchdog_config()
    assert cfg["repair_window_min"] == 60
    assert cfg["max_cycles_per_run"] == 1
    assert cfg["telegram_enabled"] is True
    with pytest.raises(ConfigError):
        load_publish_watchdog_config({})
    with pytest.raises(ConfigError):
        load_publish_watchdog_config({"publish_watchdog": {}})


def test_workflow_is_off_until_2100():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".github", "workflows",
                        "cerebro-publish-watchdog.yml")
    text = open(path, encoding="utf-8").read()
    assert 'WATCHDOG_ENABLED: "false"' in text
    assert '"30 17 * * *"' in text  # 14:30 Chile
    assert '"30 23 * * *"' in text  # 20:30 Chile
    assert '"30 2 * * *"' in text  # 23:30 Chile
    assert "clipagent.publish_watchdog" in text


def test_panel_bridge_run_once(monkeypatch):
    from clipagent.panel_bridge import PanelBridge
    monkeypatch.setenv("PANEL_TENANT", "sebastian")
    bridge = PanelBridge(base_url="https://example.invalid",
                         token="tok")
    seen = {}

    def fake_request(method, path, **kw):
        seen["method"] = method
        seen["path"] = path
        return {"ok": True, "ran": True, "processed": 1}

    bridge._request = fake_request
    res = bridge.run_once()
    assert seen == {"method": "POST", "path": "/api/worker/run-once"}
    assert res["ran"] is True
