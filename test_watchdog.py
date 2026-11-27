#!/usr/bin/env python3
"""Tests del vigilante con auto-reparación (clipagent/watchdog.py).

Los fixtures de mensajes son TEXTUALES REALES de worker_events
(D1 clips-avc, tenant sebastian, 2026-11-19, leídos de solo
lectura): cada regla log -> acción tiene su test con el mensaje
tal como lo escribió el Worker. Nada de red real: los clientes
son fakes inyectables.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.validate import ConfigError  # noqa: E402
from clipagent.watchdog import (  # noqa: E402
    LOG_ONLY, REDISPATCH, REFRESH_TWITCH_TOKEN, RESTART_WORKER,
    VOD_FALLBACK, build_telegram_message, classify_log_event,
    diagnose, load_watchdog_config, repair,
)

WCFG = {
    "stuck_after_min": 45, "cooldown_min": 30, "no_dispatch_min": 20,
    "no_cycle_min": 20, "max_vod_clips": 2,
    "max_redispatch_per_hour": 2, "telegram_enabled": True,
    "dispatch_workflow": "cerebro-live.yml",
    "watchdog_workflow": "cerebro-watchdog.yml",
}
NOW = int(time.time() * 1000)
MIN = 60000


@pytest.fixture(autouse=True)
def _fresh_now():
    # NOW se capturaba una sola vez al importar el módulo; en una suite
    # lenta (~20 min) quedaba obsoleto y los tests de cooldown/tope
    # fallaban (el dispatch "hace 10 min" en realidad tenía ~30 min y
    # cruzaba el cooldown). Se refresca antes de cada test; los helpers
    # (_logs/_state/_runs) y los tests leen el global al correr.
    globals()["NOW"] = int(time.time() * 1000)

# --- Fixtures reales de D1 (worker_events, 2026-11-19) -------------------
MSG_DISPATCH_OK = ("Dispatch panel-live enviado a GitHub "
                   "(exe2027/viral-clip-agent).")
MSG_DISPATCH_FAIL = ("Dispatch panel-live falló: GitHub devolvió "
                     "HTTP 502.")
MSG_AVIS0_ATASCADO = (
    "Validador del Worker: Atascado: sin clips nuevos en 1 h con "
    "20 streamer(s) activo(s) con permiso en el panel (ES 12 · EN 8) "
    "(hay 142 en Cola, pero son viejos: el último llegó hace 1 h). "
    "Con 18 streamers entre ES y EN en el cerebro, casi imposible "
    "que no haya un live o VOD: revisa el dispatch al cerebro y la "
    "credencial de Twitch.")
MSG_CICLO = ("Ciclo automático: 5 jobs procesados, "
             "0 atascados recuperados.")
MSG_CORRIDA_EN_CURSO = (
    "Hay una corrida de cerebro-live en curso para sebastian: no se "
    "dispara panel-live este ciclo (reintento en el siguiente).")
MSG_FRENADO_CALOR = (
    "Clip #125 frenado en tiktok: calentamiento: 1/1 hoy en tiktok "
    "(cuenta nueva: ni a mano se supera el tope de su etapa).")
MSG_SIN_SUBS = (
    "Clip #121 frenado en youtube_shorts: sin subtítulos completos: "
    "necesita transcripción humana.")
MSG_MEDICION = (
    "Medición automática tiktok falló: TikTok: la cuenta guardada "
    "es vía Zernio (su clave no es un access token de TikTok); la "
    "medición directa exige reautorizar TikTok con video.list.")
MSG_YT_403 = (
    "Medición automática youtube falló: YouTube: permiso denegado "
    "(HTTP 403): el token concedido no incluye youtube.readonly; "
    "reautorizar OAuth en Cuentas.")
MSG_CORTE_VIRALIDAD = (
    "rubius: «GTA 6: Derrota épica y risas» no se pudo encolar en "
    "el panel (POST /api/clips -> HTTP 400: {\"ok\":false,\"error\":"
    "\"Viralidad 44: bajo el mínimo de 45 en Ajustes. Busca un "
    "momento más fuerte.\"})")
MSG_CORTE_EXCEPCION = ("burn: corte fallido en pico 78s "
                       "(division by zero)")
MSG_TWITCH_401 = ("Twitch Helix devolvió HTTP 401: token inválido "
                  "o expirado.")
MSG_TOKEN_TWITCH = ("No se pudo emitir el token del cerebro para "
                    "panel-live (sebastian): token de Twitch "
                    "expirado.")


def _logs(*items):
    """[(minutos_atrás, kind, message)] -> logs con at en ms."""
    return [{"at": NOW - m * MIN, "kind": k, "message": msg}
            for m, k, msg in items]


def _state(age_min=90, running=True):
    return {"running": running,
            "lastClipAt": NOW - age_min * MIN, "now_ms": NOW}


def _runs(created_min_ago=100, conclusion="success"):
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp((NOW - created_min_ago * MIN) / 1000,
                                tz=timezone.utc)
    return [{"id": 44639214161, "status": "completed",
             "conclusion": conclusion,
             "created_at": dt.isoformat().replace("+00:00", "Z")}]


def _live():
    return [{"name": "rubius", "platform": "twitch", "language": "es"},
            {"name": "xqc", "platform": "kick", "language": "en"}]


class FakePanel:
    def __init__(self, fail_restart=False, fail_telegram=False):
        self.fail_restart = fail_restart
        self.fail_telegram = fail_telegram
        self.telegrams = []
        self.restarts = 0
        self.phases = []

    def restart_worker(self):
        if self.fail_restart:
            raise RuntimeError("POST /api/worker/start -> HTTP 502")
        self.restarts += 1
        return {"ok": True, "running": True}

    def notify_telegram(self, message):
        if self.fail_telegram:
            raise RuntimeError("POST /api/bridge/notify -> HTTP 404: "
                               "No encontrado.")
        self.telegrams.append(message)
        return {"ok": True}

    def report_phase(self, phase, detail="", run_id=""):
        self.phases.append((phase, detail))
        return {"ok": True}


class FakeGitHub:
    def __init__(self, in_flight=False, fail=False, dispatch_runs=None):
        self.in_flight = in_flight
        self.fail = fail
        self.dispatched = []
        # created_at (epoch ms) de re-disparos previos por
        # workflow_dispatch, para el cooldown durable y el tope
        # horario (2026-11-20, cuota D1).
        self.dispatch_runs = list(dispatch_runs or [])

    def list_runs(self, workflow, per_page=5):
        return _runs()

    def workflow_dispatch_runs_since(self, workflow, since_ms,
                                     per_page=20):
        return sorted((at for at in self.dispatch_runs if at >= since_ms),
                      reverse=True)

    def has_run_in_flight(self, workflow):
        return self.in_flight

    def dispatch_workflow(self, workflow, tenant, ref="main"):
        if self.fail:
            raise RuntimeError("POST /dispatches -> HTTP 502")
        self.dispatched.append((workflow, tenant))
        return True


class FakeTwitch:
    def __init__(self, fail=False):
        self.fail = fail
        self.refreshed = 0

    def refresh_token(self):
        if self.fail:
            raise RuntimeError(
                "refrescar token de Twitch -> HTTP 403: sospecha "
                "de secreto de Twitch revocado")
        self.refreshed += 1
        return "fake-token-ok"


class FakeVod:
    def __init__(self, n=1, fail=False):
        self.n = n
        self.fail = fail
        self.calls = 0

    def __call__(self, max_vods=2, max_clips=2, top_n=2, bridge=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("modo VOD falló: sin VODs recientes")
        return self.n, (f"modo VOD: 1 VOD procesado, {self.n} clip(s) "
                        "en la Cola del panel (sin publicar)")


def _clients(tmp_path, panel=None, github=None, twitch=None,
             vod=None, recheck=None):
    return {"panel": panel or FakePanel(),
            "github": github or FakeGitHub(),
            "twitch": twitch or FakeTwitch(),
            "vod": vod or FakeVod(),
            "recheck": recheck}


# --- 1. Una regla log -> acción por test (mensajes reales) ---------------
def test_twitch_401_refresca_token():
    assert classify_log_event(MSG_TWITCH_401,
                              "github_dispatch") == REFRESH_TWITCH_TOKEN


def test_token_twitch_expirado_refresca():
    assert classify_log_event(MSG_TOKEN_TWITCH,
                              "aviso") == REFRESH_TWITCH_TOKEN


def test_youtube_403_no_es_twitch():
    # El 403 es de YouTube (youtube.readonly), no de Twitch: NONE.
    assert classify_log_event(MSG_YT_403, "analytics") is None


def test_dispatch_fallido_redispatch():
    assert classify_log_event(MSG_DISPATCH_FAIL,
                              "github_dispatch") == REDISPATCH


def test_dispatch_enviado_solo_no_es_fallo():
    # "Enviado" sin corrida posterior lo decide el cruce con GitHub,
    # no la línea sola.
    assert classify_log_event(MSG_DISPATCH_OK,
                              "github_dispatch") is None


def test_corrida_en_curso_es_throttle_normal():
    assert classify_log_event(MSG_CORRIDA_EN_CURSO,
                              "live_dispatch") is None


def test_ciclo_automatico_informativo():
    assert classify_log_event(MSG_CICLO, "ciclo") is None


def test_frenado_calentamiento_informativo():
    assert classify_log_event(MSG_FRENADO_CALOR,
                              "auto_publish") is None


def test_sin_subtitulos_informativo():
    assert classify_log_event(MSG_SIN_SUBS, "auto_publish") is None


def test_medicion_analytics_informativa():
    assert classify_log_event(MSG_MEDICION, "analytics") is None


def test_aviso_atascado_es_evidencia_no_causa():
    # El aviso describe el atasco; la causa sale del cruce
    # logs+estado, no de esta línea.
    assert classify_log_event(MSG_AVIS0_ATASCADO, "aviso") is None


def test_corte_viralidad_bajo_minimo_no_es_atasco():
    assert classify_log_event(MSG_CORTE_VIRALIDAD,
                              "corte_fallido") is None


def test_corte_excepcion_redispatch():
    assert classify_log_event(MSG_CORTE_EXCEPCION,
                              "corte_fallido") == REDISPATCH


def test_job_publicacion_solo_se_registra():
    assert classify_log_event(
        "job de publicación fallido en tiktok: timeout subiendo el "
        "video", "aviso") == LOG_ONLY


def test_sin_directos_pide_vod():
    assert classify_log_event("Sin directos en GTA VI: modo VOD.",
                              "ciclo") == VOD_FALLBACK
    assert classify_log_event("nadie en GTA VI ahora mismo",
                              "aviso") == VOD_FALLBACK


def test_worker_parado_pide_reinicio():
    assert classify_log_event("Worker parado desde el panel.",
                              "parada") == RESTART_WORKER


def test_sin_token_github_redispatch():
    assert classify_log_event(
        "Sin GITHUB_TOKEN: <redacted>", "github_dispatch") == REDISPATCH


# --- 2. Detección de atasco ----------------------------------------------
def test_atasco_con_vivos_y_clip_viejo():
    diag = diagnose(
        _logs((5, "github_dispatch", MSG_DISPATCH_OK),
              (12, "aviso", MSG_TWITCH_401),
              (20, "github_dispatch", MSG_DISPATCH_FAIL)),
        _state(90), _runs(100), WCFG, live_now=_live(), token_ok=False)
    assert diag.stuck is True
    assert diag.cause == REDISPATCH  # dispatch-sin-corrida manda
    assert diag.dispatch_without_run is True
    assert REFRESH_TWITCH_TOKEN in diag.actions
    assert any(MSG_TWITCH_401 in e for e in diag.evidence)


def test_sin_vivos_no_es_atasco():
    diag = diagnose(
        _logs((5, "github_dispatch", MSG_DISPATCH_OK)),
        _state(90), _runs(100), WCFG, live_now=[], token_ok=False)
    assert diag.stuck is False
    assert any("sin vivos" in n for n in diag.notes)


def test_clip_reciente_no_es_atasco():
    diag = diagnose(
        _logs((5, "github_dispatch", MSG_DISPATCH_OK)),
        _state(10), _runs(100), WCFG, live_now=_live(), token_ok=False)
    assert diag.stuck is False
    assert any("aún no es atasco" in n for n in diag.notes)


def test_justo_en_el_umbral_es_atasco():
    diag = diagnose(
        _logs((5, "github_dispatch", MSG_DISPATCH_FAIL)),
        _state(45), _runs(100), WCFG, live_now=_live())
    assert diag.stuck is True


def test_un_minuto_antes_del_umbral_no_es_atasco():
    diag = diagnose(
        _logs((5, "github_dispatch", MSG_DISPATCH_FAIL)),
        _state(44), _runs(100), WCFG, live_now=_live())
    assert diag.stuck is False


def test_dispatch_sin_corrida_cruce_github():
    # Incidente 2026-11-19: dispatches 21:13/21:41/21:57 sin corrida
    # (última 20:37). Dispatch 5 min atrás, última corrida 100 min.
    diag = diagnose(
        _logs((5, "github_dispatch", MSG_DISPATCH_OK)),
        _state(90), _runs(100), WCFG, live_now=_live())
    assert diag.dispatch_without_run is True
    assert diag.cause == REDISPATCH
    assert any("Dispatch panel-live enviado" in e
               for e in diag.evidence)


def test_dispatch_con_corrida_posterior_no_es_causa():
    diag = diagnose(
        _logs((5, "github_dispatch", MSG_DISPATCH_OK)),
        _state(90), _runs(2), WCFG, live_now=_live())
    assert diag.dispatch_without_run is False


def test_silencio_del_ciclo_pide_reinicio():
    diag = diagnose(
        _logs((40, "ciclo", MSG_CICLO)),
        _state(90), _runs(100), WCFG, live_now=_live())
    assert RESTART_WORKER in diag.actions
    assert any("silencio del ciclo" in n for n in diag.notes)


def test_sin_atasco_sin_causa():
    diag = diagnose(
        _logs((2, "ciclo", MSG_CICLO),
              (5, "github_dispatch", MSG_DISPATCH_OK)),
        _state(90), _runs(2), WCFG, live_now=_live())
    assert diag.stuck is False
    assert diag.actions == []


# --- 3. Config (falla fuerte con el nombre del valor) --------------------
def test_config_sin_seccion_falla():
    try:
        load_watchdog_config({})
    except ConfigError as e:
        assert "watchdog" in str(e)
    else:
        raise AssertionError("debió fallar sin la sección watchdog")


def test_config_falta_clave_nombra_la_clave():
    bad = dict(WCFG)
    del bad["cooldown_min"]
    try:
        load_watchdog_config({"watchdog": bad})
    except ConfigError as e:
        assert "watchdog.cooldown_min" in str(e)
    else:
        raise AssertionError("debió nombrar watchdog.cooldown_min")


# --- 4. Reparación: orden, cooldown, Telegram ----------------------------
def _stuck_diag():
    return diagnose(
        _logs((5, "github_dispatch", MSG_DISPATCH_OK),
              (12, "aviso", MSG_TWITCH_401),
              (20, "github_dispatch", MSG_DISPATCH_FAIL)),
        _state(90), _runs(100), WCFG, live_now=_live(), token_ok=False)


def test_orden_escalera_completo(tmp_path):
    # El recheck dice que sigue atascado hasta el VOD: la escalera
    # corre entera en orden.
    calls = {"n": 0}

    def recheck():
        calls["n"] += 1
        return calls["n"] < 4

    panel, gh, tw, vod = FakePanel(), FakeGitHub(), FakeTwitch(), FakeVod()
    res = repair(_stuck_diag(), _clients(tmp_path, panel, gh, tw, vod,
                                         recheck),
                 WCFG, tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert [s["step"] for s in res.steps] == [
        REDISPATCH, REFRESH_TWITCH_TOKEN, RESTART_WORKER, VOD_FALLBACK]
    assert res.repaired is True
    assert gh.dispatched == [("cerebro-live.yml", "sebastian")]
    assert tw.refreshed == 1 and panel.restarts == 1
    assert vod.calls == 1
    assert panel.telegrams == []  # reparado: no avisa


def test_reparado_no_avisa_telegram(tmp_path):
    # Causa solo token: el refresco repara y la escalera se corta.
    diag = diagnose(_logs((12, "aviso", MSG_TWITCH_401)), _state(90),
                    _runs(2), WCFG, live_now=_live(), token_ok=False)
    assert diag.cause == REFRESH_TWITCH_TOKEN
    panel = FakePanel()
    res = repair(diag, _clients(tmp_path, panel=panel), WCFG,
                 tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert res.repaired is True
    assert panel.telegrams == []
    assert [s["step"] for s in res.steps] == [REFRESH_TWITCH_TOKEN]


def test_cooldown_no_redispara(tmp_path):
    spath = str(tmp_path / "state.json")
    diag = _stuck_diag()
    res1 = repair(diag, _clients(tmp_path), WCFG, tenant="sebastian",
                  state_path=spath)
    assert res1.skipped_cooldown is False
    res2 = repair(diag, _clients(tmp_path), WCFG, tenant="sebastian",
                  state_path=spath)
    assert res2.skipped_cooldown is True
    assert res2.steps == []


def test_cooldown_durable_no_redispara(tmp_path):
    # 2026-11-20, cuota D1: el estado local (state.json) NO sobrevive
    # entre corridas (runner efímero), así que el cooldown se mide
    # en el historial de GitHub: un re-disparo hace 10 min frena la
    # escalera aunque el state.json esté limpio.
    gh = FakeGitHub(dispatch_runs=[NOW - 10 * MIN])
    res = repair(_stuck_diag(), _clients(tmp_path, github=gh), WCFG,
                 tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert res.skipped_cooldown is True
    assert res.steps == []
    assert gh.dispatched == []


def test_cooldown_durable_expirado_sigue(tmp_path):
    # Re-disparo hace 40 min (> cooldown_min 30): la escalera corre.
    gh = FakeGitHub(dispatch_runs=[NOW - 40 * MIN])
    res = repair(_stuck_diag(), _clients(tmp_path, github=gh), WCFG,
                 tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert res.skipped_cooldown is False
    assert gh.dispatched != []


def test_tope_horario_no_redispara(tmp_path):
    # 2 re-disparos en la última hora (35 y 40 min atrás: fuera del
    # cooldown de 30 min, dentro de la hora) = tope alcanzado: el
    # peldaño REDISPATCH no dispara, pero la escalera sigue a los
    # demás peldaños (no se aborta entera).
    gh = FakeGitHub(dispatch_runs=[NOW - 35 * MIN, NOW - 40 * MIN])
    res = repair(_stuck_diag(), _clients(tmp_path, github=gh), WCFG,
                 tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert gh.dispatched == []
    step = next(s for s in res.steps if s["step"] == REDISPATCH)
    assert step["ok"] is False
    assert "tope horario" in step["detail"]


def test_tope_horario_no_bloquea_con_uno(tmp_path):
    # 1 re-disparo en la última hora (< tope 2): REDISPATCH dispara.
    gh = FakeGitHub(dispatch_runs=[NOW - 40 * MIN])
    res = repair(_stuck_diag(), _clients(tmp_path, github=gh), WCFG,
                 tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert gh.dispatched != []
    step = next(s for s in res.steps if s["step"] == REDISPATCH)
    assert step["ok"] is True


def test_config_sin_max_redispatch_falla():
    # Single-source: sin la clave en config.yaml, falla fuerte.
    import pytest

    section = {k: v for k, v in WCFG.items()
               if k != "max_redispatch_per_hour"}
    with pytest.raises(ConfigError):
        load_watchdog_config({"watchdog": section})


def test_no_redispara_si_hay_corrida_en_curso(tmp_path):
    gh = FakeGitHub(in_flight=True)
    res = repair(_stuck_diag(), _clients(tmp_path, github=gh), WCFG,
                 tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert gh.dispatched == []
    step = next(s for s in res.steps if s["step"] == REDISPATCH)
    assert step["ok"] is False
    assert "en curso" in step["detail"]


def test_todo_falla_avisa_con_lineas_citadas(tmp_path):
    panel = FakePanel(fail_restart=True, fail_telegram=True)
    gh = FakeGitHub(fail=True)
    tw = FakeTwitch(fail=True)
    vod = FakeVod(fail=True)
    res = repair(_stuck_diag(),
                 _clients(tmp_path, panel, gh, tw, vod), WCFG,
                 tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert res.repaired is False
    assert res.telegram_sent is False  # la ruta aún no existe (404)
    assert "No encontrado" in res.telegram_note
    msg = res.telegram_message
    assert MSG_TWITCH_401 in msg  # líneas citadas textualmente
    assert MSG_DISPATCH_FAIL in msg
    assert "REDISPATCH" in msg and "VOD_FALLBACK" in msg
    assert "necesita un humano" in msg


def test_todo_falla_con_telegram_ok_si_envia(tmp_path):
    panel = FakePanel()
    res = repair(_stuck_diag(),
                 _clients(tmp_path, panel, FakeGitHub(fail=True),
                          FakeTwitch(fail=True), FakeVod(fail=True)),
                 WCFG, tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert res.telegram_sent is True
    assert len(panel.telegrams) == 1
    assert "sigue atascado" in panel.telegrams[0]


def test_dry_run_no_actua(tmp_path):
    panel, gh, tw, vod = (FakePanel(), FakeGitHub(), FakeTwitch(),
                          FakeVod())
    res = repair(_stuck_diag(), _clients(tmp_path, panel, gh, tw, vod),
                 WCFG, dry_run=True, tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert gh.dispatched == [] and tw.refreshed == 0
    assert panel.restarts == 0 and vod.calls == 0
    assert panel.telegrams == []
    assert all(s["ok"] is None for s in res.steps)


def test_vod_ultimo_recurso_solo_si_sigue_atascado(tmp_path):
    # Solo REDISPATCH en las acciones y el recheck dice reparado
    # tras el dispatch: VOD no corre.
    diag = diagnose(_logs((5, "github_dispatch", MSG_DISPATCH_FAIL)),
                    _state(90), _runs(2), WCFG, live_now=_live())
    vod = FakeVod()
    res = repair(diag, _clients(tmp_path, vod=vod, recheck=lambda: False),
                 WCFG, tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert res.repaired is True
    assert vod.calls == 0


def test_sin_atasco_no_hace_nada(tmp_path):
    diag = diagnose(_logs((2, "ciclo", MSG_CICLO)), _state(10),
                    _runs(2), WCFG, live_now=_live())
    res = repair(diag, _clients(tmp_path), WCFG, tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert res.steps == [] and res.repaired is False


def test_mensaje_telegram_cita_lineas():
    diag = _stuck_diag()
    msg = build_telegram_message(
        diag, [{"step": REDISPATCH, "ok": False, "detail": "falló"}],
        tenant="sebastian")
    assert "sebastian" in msg
    assert MSG_TWITCH_401 in msg
    assert "REDISPATCH" in msg
    assert "necesita un humano" in msg


# --- A4 (2026-11-20, anti-recaída): tope 3 re-disparos/día + backoff
# 1/5/15/60 en el peldaño REDISPATCH ------------------------------------
# El tope horario (2/h) y cooldown_min (30) siguen vigentes: el
# freno A4 es ADICIONAL. Los runs de hace 35/40/50 min están fuera
# del cooldown (30) y dos de ellos dentro de la hora: el orden de
# los frenos importa y se verifica en cada test.
def _a4_base():
    """Base de reloj para los tests A4: NOW, salvo en la primera
    hora del día de Santiago, donde los runs 'hace 35/40/50 min'
    caen en el día anterior y el tope diario (3/día) no se alcanza
    — el horario frena antes y el test falla por la hora, no por el
    código (flake de medianoche, visto 2026-11-27 00:20 -03 en CI y
    en local). Se ancla a las 01:30 del mismo día: los offsets
    quedan dentro del día y el cooldown (30 min) sigue fuera."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = NOW
    dt = datetime.fromtimestamp(now / 1000,
                                tz=ZoneInfo("America/Santiago"))
    if dt.hour == 0:
        base = dt.replace(hour=1, minute=30, second=0, microsecond=0)
        return int(base.timestamp() * 1000)
    return now


def test_a4_tope_3_redispatches_dia(tmp_path):
    # 3 re-disparos hoy (35, 40 y 50 min atrás: fuera del cooldown
    # de 30 min): el tope del día frena el re-disparo aunque el
    # tope horario también lo frenaría; manda el mensaje A4.
    base = _a4_base()
    gh = FakeGitHub(dispatch_runs=[base - 35 * MIN, base - 40 * MIN,
                                   base - 50 * MIN])
    res = repair(_stuck_diag(), _clients(tmp_path, github=gh), WCFG,
                 tenant="sebastian",
                 state_path=str(tmp_path / "state.json"),
                 now_ms=base)
    assert gh.dispatched == []
    step = next(s for s in res.steps if s["step"] == REDISPATCH)
    assert step["ok"] is False
    assert "tope de 3 re-disparos por día" in step["detail"]


def test_a4_backoff_entre_redispatches(tmp_path):
    # 2 hoy, el último hace 3 min: el backoff (5 min tras el 2.º)
    # aún no se cumplió: no se re-dispara. Con el cooldown_min de
    # producción (30) el cooldown durable frenaría antes que este
    # backoff (la escalera se saltaría entera); por eso el test usa
    # cooldown_min=1: verifica la puerta A4 en sí, no el cooldown.
    wcfg = dict(WCFG, cooldown_min=1)
    base = _a4_base()
    gh = FakeGitHub(dispatch_runs=[base - 3 * MIN, base - 40 * MIN])
    res = repair(_stuck_diag(), _clients(tmp_path, github=gh), wcfg,
                 tenant="sebastian",
                 state_path=str(tmp_path / "state.json"),
                 now_ms=base)
    assert gh.dispatched == []
    step = next(s for s in res.steps if s["step"] == REDISPATCH)
    assert step["ok"] is False
    assert "backoff" in step["detail"]


def test_a4_backoff_cumplido_redispara(tmp_path):
    # 2 hoy, el último hace 70 min (fuera de la hora y del
    # backoff de 5 min): el freno A4 deja pasar y el tope horario
    # tampoco frena: se re-dispara.
    gh = FakeGitHub(dispatch_runs=[NOW - 70 * MIN, NOW - 80 * MIN])
    res = repair(_stuck_diag(), _clients(tmp_path, github=gh), WCFG,
                 tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert gh.dispatched != []
    step = next(s for s in res.steps if s["step"] == REDISPATCH)
    assert step["ok"] is True


def test_a4_no_rompe_tope_horario(tmp_path):
    # 2 hoy (35 y 40 min: backoff A4 cumplido, 5 min) pero dentro
    # de la hora: el tope horario (2/h) sigue frenando con SU
    # mensaje; el freno A4 no lo tapa ni lo relaja.
    gh = FakeGitHub(dispatch_runs=[NOW - 35 * MIN, NOW - 40 * MIN])
    res = repair(_stuck_diag(), _clients(tmp_path, github=gh), WCFG,
                 tenant="sebastian",
                 state_path=str(tmp_path / "state.json"))
    assert gh.dispatched == []
    step = next(s for s in res.steps if s["step"] == REDISPATCH)
    assert step["ok"] is False
    assert "tope horario" in step["detail"]


def test_a4_redispatch_allowed_puro():
    from clipagent.retry import redispatch_allowed
    assert redispatch_allowed([], NOW)[0] is True
    assert redispatch_allowed([NOW - 40 * MIN], NOW)[0] is True
    ok, why = redispatch_allowed([NOW - 30 * 1000], NOW)
    assert ok is False and "backoff" in why
    ok, why = redispatch_allowed(
        [NOW - 70 * MIN, NOW - 80 * MIN, NOW - 90 * MIN], NOW)
    assert ok is False and "tope de 3" in why
