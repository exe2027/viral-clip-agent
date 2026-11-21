#!/usr/bin/env python3
"""Tests A4 (2026-11-20, anti-recaída) del puente cerebro -> panel
(clipagent/panel_bridge.py) y de los umbrales compartidos
(clipagent/retry.py).

Nada de red real: el transporte se sustituye por un _request_once
guionizado (fallos 502/400/7500 y éxitos) y la espera por una
sleep falsa que registra los retrasos. Los tests del vigilante
viven en test_watchdog.py (freno A4 de re-disparo).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.panel_bridge import PanelBridge, PanelError  # noqa: E402
from clipagent.retry import (  # noqa: E402
    MAX_RETRIES_PER_DAY,
    RETRY_BACKOFF_MINUTES,
    RetryBudget,
    can_retry_today,
    is_d1_quota_error,
    is_transient_panel_error,
    next_d1_reset_ms,
    panel_quota_circuit_open,
    redispatch_allowed,
    reset_panel_quota_circuit,
    retry_delay_seconds,
    santiago_day_key,
    santiago_day_start_ms,
    trip_panel_quota_circuit,
)

NOW = int(time.time() * 1000)
MIN = 60000


class ScriptedBridge(PanelBridge):
    """Bridge con transporte guionizado: cada llamada a
    _request_once consume el siguiente resultado de la lista
    (PanelError o dict de respuesta). Sin red, sin login real:
    el token de sesión ya viene puesto."""

    def __init__(self, script):
        sleeps = []
        super().__init__(base_url="http://127.0.0.1:9",
                         tenant="sebastian", token="fake-session",
                         sleep_fn=sleeps.append)
        self.script = list(script)
        self.calls = 0
        self.sleeps = sleeps

    def _request_once(self, method, path, body=None, raw=None,
                      content_type="application/json"):
        self.calls += 1
        if not self.script:
            raise AssertionError(
                f"llamada {method} {path} sin guion (llamada "
                f"nº {self.calls})")
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _err502(msg="HTTP 502"):
    return PanelError(f"POST /api/clips/102/publish -> {msg}: "
                      "Bad Gateway")


def _err400():
    return PanelError("POST /api/clips/102/publish -> HTTP 400: "
                      "clip inválido")


def _err7500():
    return PanelError("POST /api/clips/102/publish -> HTTP 500: "
                      "D1_ERROR: Too many writes (7500)")


# --- Umbrales (única fuente, clipagent/retry.py) ------------------------

def test_backoff_1_5_15_60():
    assert RETRY_BACKOFF_MINUTES == (1, 5, 15, 60)
    assert retry_delay_seconds(1) == 60
    assert retry_delay_seconds(2) == 300
    assert retry_delay_seconds(3) == 900
    assert retry_delay_seconds(4) == 3600
    assert retry_delay_seconds(5) is None
    assert retry_delay_seconds(0) is None
    assert MAX_RETRIES_PER_DAY == 3


def test_tope_3_reintentos_por_dia():
    assert can_retry_today(0) is True
    assert can_retry_today(2) is True
    assert can_retry_today(3) is False
    assert can_retry_today(4) is False


def test_deteccion_7500():
    assert is_d1_quota_error("D1_ERROR: Too many writes (7500)")
    assert is_d1_quota_error("falla 7500 al escribir")
    assert is_d1_quota_error("D1_ERROR: quota exceeded")
    assert is_d1_quota_error("D1: límite de escrituras superado")
    assert not is_d1_quota_error("HTTP 500 Internal Server Error")
    assert not is_d1_quota_error("HTTP 429 Too Many Requests")
    assert not is_d1_quota_error("red: Network connection lost")
    assert not is_d1_quota_error(None)
    assert not is_d1_quota_error("")


def test_transitorio_solo_429_5xx_red():
    assert is_transient_panel_error("HTTP 429 Too Many Requests")
    assert is_transient_panel_error("HTTP 502 Bad Gateway")
    assert is_transient_panel_error("HTTP 503 Service Unavailable")
    assert is_transient_panel_error("urlopen error timed out")
    assert not is_transient_panel_error("HTTP 400: clip inválido")
    assert not is_transient_panel_error("HTTP 401: no autorizado")
    assert not is_transient_panel_error("HTTP 404: no encontrado")
    assert not is_transient_panel_error("D1_ERROR 7500")


def test_dia_santiago_y_reset_cuota():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    # 12:00 -03 del 20-11-2026: día 2026-11-20, inicio 03:00Z.
    noon = int(datetime(2026, 11, 20, 12, 0, 0,
                        tzinfo=ZoneInfo("America/Santiago")
                        ).timestamp() * 1000)
    assert santiago_day_key(noon) == "2026-11-20"
    expected_start = int(datetime(2026, 11, 20, 3, 0, 0,
                                  tzinfo=timezone.utc).timestamp() * 1000)
    assert santiago_day_start_ms(noon) == expected_start
    # Reset de cuota: próxima 00:00 UTC (21:00 -03).
    expected_reset = int(datetime(2026, 11, 21, 0, 0, 0,
                                  tzinfo=timezone.utc).timestamp() * 1000)
    assert next_d1_reset_ms(
        int(datetime(2026, 11, 20, 12, 0, 0,
                     tzinfo=timezone.utc).timestamp() * 1000)
    ) == expected_reset


def test_redispatch_allowed_puro():
    # Sin re-disparos hoy: se puede.
    assert redispatch_allowed([], NOW)[0] is True
    # 1 hoy hace 40 min: se puede (backoff 1 min cumplido).
    assert redispatch_allowed([NOW - 40 * MIN], NOW)[0] is True
    # 1 hoy hace 30 s: backoff 1 min, faltan ~30 s.
    ok, why = redispatch_allowed([NOW - 30 * 1000], NOW)
    assert ok is False and "backoff" in why
    # 2 hoy, último hace 3 min: backoff 5 min, falta.
    ok, why = redispatch_allowed([NOW - 40 * MIN, NOW - 3 * MIN], NOW)
    assert ok is False and "backoff" in why
    # 2 hoy, último hace 6 min: se puede.
    assert redispatch_allowed([NOW - 40 * MIN, NOW - 6 * MIN],
                              NOW)[0] is True
    # 3 hoy: tope del día, aunque pasaran horas.
    ok, why = redispatch_allowed(
        [NOW - 70 * MIN, NOW - 80 * MIN, NOW - 90 * MIN], NOW)
    assert ok is False and "tope de 3" in why


def test_retry_budget_por_operacion_y_dia():
    now = [NOW]

    def clock():
        return now[0]

    budget = RetryBudget(now_fn=clock)
    key = "POST /api/clips/102/publish"
    assert budget.can_retry(key) is True
    budget.record_retry(key)
    budget.record_retry(key)
    assert budget.retries_used(key) == 2
    assert budget.can_retry(key) is True
    budget.record_retry(key)
    assert budget.can_retry(key) is False
    # Otra operación (otro clip) tiene su propio presupuesto.
    assert budget.can_retry("POST /api/clips/103/publish") is True
    # Al día siguiente se resetea.
    now[0] = NOW + 26 * 3600 * 1000
    assert budget.can_retry(key) is True
    assert budget.retries_used(key) == 0


def test_circuito_7500_hasta_el_reset():
    from datetime import datetime, timezone
    reset_panel_quota_circuit()
    trip_at = int(datetime(2026, 11, 20, 12, 0, 0,
                           tzinfo=timezone.utc).timestamp() * 1000)
    assert panel_quota_circuit_open(trip_at) is False
    trip_panel_quota_circuit(trip_at)
    assert panel_quota_circuit_open(trip_at + 60 * MIN) is True
    reset_at = next_d1_reset_ms(trip_at)
    assert panel_quota_circuit_open(reset_at - 1000) is True
    assert panel_quota_circuit_open(reset_at) is False
    reset_panel_quota_circuit()


# --- Reintento del puente --------------------------------------------------

def test_publicar_reintenta_502_con_backoff_1_5_15():
    reset_panel_quota_circuit()
    bridge = ScriptedBridge([_err502(), _err502(), _err502(),
                             {"jobs": [{"id": 7}]}])
    res = bridge.publish_clip(102)
    assert res == {"jobs": [{"id": 7}]}
    assert bridge.calls == 4  # intento + 3 reintentos
    assert bridge.sleeps == [60, 300, 900]


def test_publicar_agota_tope_3_y_no_reintenta_mas_hoy():
    reset_panel_quota_circuit()
    bridge = ScriptedBridge([_err502(), _err502(), _err502(),
                             _err502()])
    try:
        bridge.publish_clip(102)
        raise AssertionError("debía levantar PanelError")
    except PanelError as e:
        assert "sin más reintentos hoy" in str(e)
    assert bridge.calls == 4
    assert bridge.sleeps == [60, 300, 900]
    # Segundo intento del MISMO clip hoy: 1 llamada, 0 reintentos.
    bridge.script = [_err502()]
    try:
        bridge.publish_clip(102)
        raise AssertionError("debía levantar PanelError")
    except PanelError:
        pass
    assert bridge.calls == 5
    assert bridge.sleeps == [60, 300, 900]
    # Otro clip tiene su propio presupuesto: reintenta.
    bridge.script = [_err502(), {"jobs": []}]
    assert bridge.publish_clip(103) == {"jobs": []}
    assert bridge.calls == 7
    assert bridge.sleeps == [60, 300, 900, 60]


def test_error_400_no_se_reintenta():
    reset_panel_quota_circuit()
    bridge = ScriptedBridge([_err400()])
    try:
        bridge.publish_clip(102)
        raise AssertionError("debía levantar PanelError")
    except PanelError as e:
        assert "HTTP 400" in str(e)
    assert bridge.calls == 1
    assert bridge.sleeps == []


def test_error_7500_sin_reintento_y_circuito_abierto():
    reset_panel_quota_circuit()
    bridge = ScriptedBridge([_err7500()])
    try:
        bridge.publish_clip(102)
        raise AssertionError("debía levantar PanelError")
    except PanelError as e:
        assert "7500" in str(e)
    assert bridge.calls == 1
    assert bridge.sleeps == []
    assert panel_quota_circuit_open() is True
    # Segunda llamada: no toca la red (el circuito la frena).
    bridge.script = [{"jobs": []}]
    try:
        bridge.publish_clip(102)
        raise AssertionError("debía levantar por el circuito")
    except PanelError as e:
        assert "circuito 7500" in str(e)
    assert bridge.calls == 1
    reset_panel_quota_circuit()


def test_get_reintentable_list_clips():
    reset_panel_quota_circuit()
    bridge = ScriptedBridge([_err502(), _err502(),
                             {"clips": [{"id": 102}]}])
    assert bridge.list_clips() == [{"id": 102}]
    assert bridge.calls == 3
    assert bridge.sleeps == [60, 300]
    reset_panel_quota_circuit()


def test_create_clip_no_se_reintenta():
    # POST /api/clips no es idempotente: un fallo ambiguo no se
    # reintenta (un segundo POST crearía un clip duplicado).
    reset_panel_quota_circuit()
    bridge = ScriptedBridge([_err502()])
    try:
        bridge.create_clip({"title": "t"})
        raise AssertionError("debía levantar PanelError")
    except PanelError:
        pass
    assert bridge.calls == 1
    assert bridge.sleeps == []
    reset_panel_quota_circuit()
