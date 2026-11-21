#!/usr/bin/env python3
"""Tests del vigilante de tenant (clipagent/tenant_watchdog.py).

Nada de red real ni escrituras D1: el puente y la API de GitHub
son fakes inyectables. El caso de referencia es kaez 2026-11-21:
Worker en marcha, last_live_dispatch_at NULL, 0 clips y 0 jobs
(ATASCADO "Sin señal del cerebro"), y las 3 corridas de
cerebro.yml que fallaron con 401 "Token del cerebro inválido"
porque el cerebro genérico resolvía la URL del Worker de
sebastian. El vigilante de kaez es INDEPENDIENTE: dispara solo
`panel-start-kaez` a su workflow propio y nunca toca sebastian
(ni su D1, bloqueada hasta las 21:00).
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

import clipagent.retry as _retry  # noqa: E402
from clipagent.panel_bridge import PanelError  # noqa: E402
from clipagent.tenant_watchdog import (  # noqa: E402
    dispatch_brain,
    dispatch_event_type,
    is_tenant_stuck,
    load_tenant_watchdog_config,
    recent_brain_run,
    run,
)
from clipagent.tenant_watchdog import GithubError  # noqa: E402
from clipagent.validate import ConfigError  # noqa: E402

WCFG = {"cooldown_min": 60, "stale_live_dispatch_min": 60}
HOUR = 3600000
MIN = 60000
NOW = int(datetime(2026, 11, 21, 13, 30, tzinfo=timezone.utc)
          .timestamp() * 1000)


@pytest.fixture(autouse=True)
def _reset_quota_circuit():
    """El circuito 7500 es global del proceso: cada test arranca
    con el estado que dejó el anterior y lo restaura."""
    prev = _retry._panel_quota_tripped_at_ms
    yield
    _retry._panel_quota_tripped_at_ms = prev


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Sin credenciales del panel ni token de GitHub por defecto."""
    for var in ("PANEL_USERNAME", "PANEL_PASSWORD",
                "PANEL_BRAIN_TOKEN", "PANEL_SESSION_TOKEN",
                "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)


class FakeBridge:
    """Puente falso: estado + Cola + contador de reinicios."""

    def __init__(self, state=None, clips=None, jobs=None,
                 quota_on=None):
        self.state = state if state is not None else {
            "running": True, "lastSweepAt": None,
            "activity": [], "lastNote": ""}
        self.clips = clips if clips is not None else []
        self.jobs = jobs if jobs is not None else []
        self.quota_on = quota_on  # "read" | "restart" | None
        self.restarts = 0

    def worker_state(self):
        if self.quota_on == "read":
            raise PanelError("D1 7500 cuota agotada")
        return dict(self.state)

    def list_clips(self):
        if self.quota_on == "read":
            raise PanelError("D1 7500 cuota agotada")
        return list(self.clips)

    def list_jobs(self):
        if self.quota_on == "read":
            raise PanelError("D1 7500 cuota agotada")
        return list(self.jobs)

    def restart_worker(self):
        if self.quota_on == "restart":
            raise PanelError("D1 7500 cuota agotada")
        self.restarts += 1
        return {"ok": True, "running": True}


class FakeGithub:
    """API de GitHub falsa: registra llamadas y simula runs."""

    def __init__(self, runs=None, dispatch_status=204):
        self.runs = runs if runs is not None else []
        self.dispatch_status = dispatch_status
        self.calls = []

    def __call__(self, method, path, token, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and "/actions/workflows/" in path:
            return 200, {"workflow_runs": list(self.runs)}
        if method == "POST" and path.endswith("/dispatches"):
            return self.dispatch_status, {}
        raise AssertionError(f"llamada no esperada: {method} {path}")

    def dispatch_calls(self):
        return [c for c in self.calls
                if c[0] == "POST" and c[1].endswith("/dispatches")]


def _run_created(minutes_ago):
    ts = datetime.fromtimestamp((NOW - minutes_ago * MIN) / 1000,
                                timezone.utc)
    return {"created_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "in_progress"}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_load_tenant_watchdog_config_real():
    cfg = load_tenant_watchdog_config()
    assert cfg["cooldown_min"] == 60
    assert cfg["stale_live_dispatch_min"] == 60


def test_load_tenant_watchdog_config_missing():
    with pytest.raises(ConfigError):
        load_tenant_watchdog_config(cfg={})
    with pytest.raises(ConfigError):
        load_tenant_watchdog_config(cfg={"tenant_watchdog": {}})
    with pytest.raises(ConfigError):
        load_tenant_watchdog_config(
            cfg={"tenant_watchdog": {"cooldown_min": 60}})


# ---------------------------------------------------------------------------
# Detección de atascado
# ---------------------------------------------------------------------------

def test_is_tenant_stuck_kaez_case():
    """El caso real de kaez 2026-11-21: en marcha, sin dispatch
    nunca, Cola vacía -> atascado."""
    stuck, reason = is_tenant_stuck(
        {"running": True, "lastSweepAt": None}, 0, 0, NOW, 60)
    assert stuck is True
    assert reason == "sin-dispatch-nunca"


def test_is_tenant_stuck_stale_dispatch():
    old = NOW - 61 * MIN
    stuck, reason = is_tenant_stuck(
        {"running": True, "lastSweepAt": old}, 0, 0, NOW, 60)
    assert stuck is True
    assert reason == "dispatch-obsoleto"


def test_is_tenant_stuck_not_stuck():
    # Worker parado: no es de este módulo.
    stuck, _ = is_tenant_stuck(
        {"running": False, "lastSweepAt": None}, 0, 0, NOW, 60)
    assert stuck is False
    # Dispatch reciente: el cerebro está en curso.
    stuck, _ = is_tenant_stuck(
        {"running": True, "lastSweepAt": NOW - 10 * MIN},
        0, 0, NOW, 60)
    assert stuck is False
    # Ya hay clips: no es el caso "nunca arrancó".
    stuck, _ = is_tenant_stuck(
        {"running": True, "lastSweepAt": None}, 1, 0, NOW, 60)
    assert stuck is False
    stuck, _ = is_tenant_stuck(
        {"running": True, "lastSweepAt": None}, 0, 2, NOW, 60)
    assert stuck is False
    # lastSweepAt ilegible: no se decide a ciegas.
    stuck, _ = is_tenant_stuck(
        {"running": True, "lastSweepAt": "no-fecha"}, 0, 0, NOW, 60)
    assert stuck is False


# ---------------------------------------------------------------------------
# Dispatch a GitHub
# ---------------------------------------------------------------------------

def test_dispatch_event_type():
    assert dispatch_event_type("kaez") == "panel-start-kaez"
    assert dispatch_event_type("sebastian") == "panel-start-sebastian"


def test_dispatch_brain_posts_event():
    gh = FakeGithub()
    assert dispatch_brain("kaez", "tok", repo="o/r", http=gh) is True
    calls = gh.dispatch_calls()
    assert len(calls) == 1
    method, path, body = calls[0]
    assert method == "POST"
    assert path == "/repos/o/r/dispatches"
    assert body["event_type"] == "panel-start-kaez"
    assert body["client_payload"] == {"tenant": "kaez"}


def test_dispatch_brain_rejected():
    gh = FakeGithub(dispatch_status=422)
    with pytest.raises(GithubError):
        dispatch_brain("kaez", "tok", repo="o/r", http=gh)


def test_recent_brain_run():
    gh = FakeGithub(runs=[_run_created(10)])
    assert recent_brain_run("kaez", "tok", repo="o/r", now_ms=NOW,
                            cooldown_min=60, http=gh) is True
    assert "cerebro-kaez.yml" in gh.calls[0][1]
    gh = FakeGithub(runs=[_run_created(61)])
    assert recent_brain_run("kaez", "tok", repo="o/r", now_ms=NOW,
                            cooldown_min=60, http=gh) is False
    gh = FakeGithub(runs=[])
    assert recent_brain_run("kaez", "tok", repo="o/r", now_ms=NOW,
                            cooldown_min=60, http=gh) is False
    gh = FakeGithub(runs=[{"created_at": "no-fecha"}])
    assert recent_brain_run("kaez", "tok", repo="o/r", now_ms=NOW,
                            cooldown_min=60, http=gh) is False


# ---------------------------------------------------------------------------
# run(): el ciclo completo
# ---------------------------------------------------------------------------

def test_run_dry_run_stuck_would_dispatch():
    gh = FakeGithub()
    report = run(bridge=FakeBridge(), tenant="kaez", now_ms=NOW,
                 live=False, wcfg=dict(WCFG), http=gh,
                 github_token="tok")
    assert report["stuck"] is True
    assert report["action"] == "would-dispatch"
    assert report["dispatched"] is False
    assert gh.calls == []  # dry-run: ni consulta ni dispatch


def test_run_live_stuck_dispatches():
    gh = FakeGithub()
    report = run(bridge=FakeBridge(), tenant="kaez", now_ms=NOW,
                 live=True, wcfg=dict(WCFG), http=gh,
                 github_token="tok")
    assert report["stuck"] is True
    assert report["action"] == "dispatched"
    assert report["dispatched"] is True
    assert len(gh.dispatch_calls()) == 1
    assert gh.dispatch_calls()[0][2]["event_type"] == "panel-start-kaez"


def test_run_live_stuck_cooldown_no_dispatch():
    gh = FakeGithub(runs=[_run_created(10)])
    report = run(bridge=FakeBridge(), tenant="kaez", now_ms=NOW,
                 live=True, wcfg=dict(WCFG), http=gh,
                 github_token="tok")
    assert report["stuck"] is True
    assert report["action"] == "cooldown"
    assert report["dispatched"] is False
    assert gh.dispatch_calls() == []


def test_run_not_stuck_no_dispatch():
    gh = FakeGithub()
    bridge = FakeBridge(state={"running": True,
                               "lastSweepAt": NOW - 5 * MIN,
                               "activity": []})
    report = run(bridge=bridge, tenant="kaez", now_ms=NOW,
                 live=True, wcfg=dict(WCFG), http=gh,
                 github_token="tok")
    assert report["stuck"] is False
    assert report["action"] == "none"
    assert gh.calls == []


def test_run_quota_error_stops():
    gh = FakeGithub()
    bridge = FakeBridge(quota_on="read")
    report = run(bridge=bridge, tenant="kaez", now_ms=NOW,
                 live=True, wcfg=dict(WCFG), http=gh,
                 github_token="tok")
    assert report["action"] == "quota"
    assert report["dispatched"] is False
    assert gh.calls == []
    assert _retry.panel_quota_circuit_open() is True


def test_run_live_no_github_token():
    gh = FakeGithub()
    report = run(bridge=FakeBridge(), tenant="kaez", now_ms=NOW,
                 live=True, wcfg=dict(WCFG), http=gh,
                 github_token="")
    assert report["action"] == "no-github-token"
    assert report["dispatched"] is False
    assert gh.calls == []


def test_run_no_bridge():
    report = run(bridge=None, tenant="kaez", now_ms=NOW,
                 live=False, wcfg=dict(WCFG))
    assert report["action"] == "no-bridge"


def test_run_rejects_unknown_tenant():
    from clipagent.tenant import TenantError
    with pytest.raises(TenantError):
        run(bridge=FakeBridge(), tenant="intruso", now_ms=NOW,
            live=False, wcfg=dict(WCFG))


# ---------------------------------------------------------------------------
# Workflows: kaez independiente y ENCENDIDO ahora (no a las 21:00)
# ---------------------------------------------------------------------------

def _read_workflow(name):
    root = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(root, ".github", "workflows", name),
              encoding="utf-8") as f:
        return f.read()


def test_kaez_workflow_independent():
    """cerebro-kaez.yml: solo kaez, su Worker y su entorno. Nada
    de sebastian como destino (ni su URL ni su entorno ni su
    tenant). Los comentarios pueden nombrarlo como contexto."""
    text = _read_workflow("cerebro-kaez.yml")
    assert "environment: kaez" in text
    assert "PANEL_TENANT: kaez" in text
    assert "https://avc-kaez.clipsultravirales768.workers.dev" in text
    assert "panel-start-kaez" in text
    assert "avc.vikingosfoodspa.workers.dev" not in text
    assert "environment: sebastian" not in text
    assert "PANEL_TENANT: sebastian" not in text


def test_tenant_watchdog_workflow_enabled_now():
    """El vigilante de kaez está ENCENDIDO ahora (no a las 21:00):
    la restricción de las 21:00 es solo por la cuota de sebastian
    y la D1 de kaez está fresca. Solo vigila kaez."""
    text = _read_workflow("cerebro-tenant-watchdog.yml")
    assert 'TENANT_WATCHDOG_ENABLED: "true"' in text
    assert "environment: kaez" in text
    assert '--tenant "$TENANT"' in text
    assert "inputs.tenant || 'kaez'" in text
    # Nada de sebastian como destino (los comentarios pueden
    # nombrarlo como contexto: "nunca toca sebastian").
    assert "environment: sebastian" not in text
    assert "PANEL_TENANT: sebastian" not in text
    assert "avc.vikingosfoodspa.workers.dev" not in text


def test_shared_brain_workflows_untouched_by_kaez():
    """Los workflows compartidos de sebastian no se tocan para
    encender kaez: cerebro.yml y cerebro-live.yml siguen con la
    URL de sebastian (el cerebro de kaez va por su workflow
    propio)."""
    for name in ("cerebro.yml", "cerebro-live.yml"):
        text = _read_workflow(name)
        assert "avc.vikingosfoodspa.workers.dev" in text
