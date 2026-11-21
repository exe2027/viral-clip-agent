#!/usr/bin/env python3
"""Tests del workflow cerebro.yml (diario genérico, 19 nov 2026).

Puerta Iniciar (flag en D1 vía PanelBridge, no panel-state.json),
persistencia con run-id, --live y observabilidad del watch. Los
scripts Python embebidos en el YAML se extraen y se ejecutan de
verdad en un directorio temporal, no solo se comprueba que el texto
exista. El gate importa clipagent.panel_bridge: se simula con un
paquete falso en PYTHONPATH (sin red).
"""
import os
import subprocess
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(
    HERE, ".github", "workflows", "cerebro.yml")

GATE_STEP = "Puerta Iniciar (botón del panel)"
WATCH_STEP = "Revisión de VODs"

# Formato real de watcher.py:912-914: "encolaría (dry-run)" (singular,
# sin 'n'). El run 44474668621 demostró que el parser con "encolarían"
# no matcheaba nunca: la línea real es la de abajo.
WATCH_LOG_OK = (
    "Watcher — 2026-11-19 09:00:00 (LIVE (encola lo que pase los frenos))\n"
    "[rubius] (twitch, permiso: concedido)\n"
    "\nResumen: en vivo=1 | VODs revisados=3 | encolados=2 | "
    "encolaría (dry-run)=0 | descartados=1 | ya vistos=0 | errores=0\n"
)
WATCH_LOG_ERROR_SIN_ENCOLADOS = (
    "\nResumen: en vivo=0 | VODs revisados=2 | encolados=0 | "
    "encolaría (dry-run)=0 | descartados=1 | ya vistos=0 | errores=1\n"
)
WATCH_LOG_ERROR_CON_ENCOLADOS = (
    "\nResumen: en vivo=0 | VODs revisados=2 | encolados=1 | "
    "encolaría (dry-run)=0 | descartados=0 | ya vistos=0 | errores=1\n"
)
# Variante con 'n' (por si watcher.py vuelve a "encolarían"): el
# regex la tolera; el parser debe matchearla igual.
WATCH_LOG_OK_PLURAL = (
    "\nResumen: en vivo=1 | VODs revisados=3 | encolados=2 | "
    "encolarían (dry-run)=0 | descartados=1 | ya vistos=0 | errores=0\n"
)

FAKE_BRIDGE = '''\
import os


class PanelBridge:
    """Simula el bridge: running sale de PANEL_RUNNING; sin
    sesión (PANEL_NO_SESSION=1), from_env devuelve None."""

    @classmethod
    def from_env(cls):
        if os.environ.get("PANEL_NO_SESSION") == "1":
            return None
        return cls()

    def worker_state(self):
        return {"running":
                os.environ.get("PANEL_RUNNING", "false") == "true"}
'''


@pytest.fixture(scope="module")
def workflow():
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def steps(workflow):
    return workflow["jobs"]["vod-check-and-publish"]["steps"]


def _step(steps, name):
    for step in steps:
        if step.get("name") == name:
            return step
    raise AssertionError(f"paso no encontrado: {name}")


def _heredoc(run_text, marker):
    """Extrae el script entre `python - <<'MARKER'` y `MARKER`."""
    lines = run_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().endswith(f"python - <<'{marker}'"):
            start = i + 1
            break
    assert start is not None, f"heredoc {marker} no encontrado"
    end = lines.index(marker, start)
    return "\n".join(lines[start:end]) + "\n"


def _read_outputs(path):
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line)


def _run_gate(script, tmp_path, event, action="", force="",
              panel_running=None):
    fake = tmp_path / "fake"
    (fake / "clipagent").mkdir(parents=True, exist_ok=True)
    (fake / "clipagent" / "__init__.py").write_text("", encoding="utf-8")
    (fake / "clipagent" / "panel_bridge.py").write_text(
        FAKE_BRIDGE, encoding="utf-8")
    gh_out = tmp_path / "gh_out"
    gh_out.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "EVENT": event,
        "DISPATCH_TYPE": action,
        "FORCE": force,
        "GITHUB_OUTPUT": str(gh_out),
        "PYTHONPATH": str(fake),
    })
    if panel_running is None:
        env["PANEL_NO_SESSION"] = "1"
    else:
        env["PANEL_RUNNING"] = "true" if panel_running else "false"
    proc = subprocess.run(
        [sys.executable, "-"], input=script, text=True, cwd=tmp_path,
        env=env, capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return _read_outputs(gh_out)


# (evento, acción, force, running del panel, started)
# running None = sin sesión del panel (from_env devuelve None).
GATE_CASES = [
    ("repository_dispatch", "panel-start", "", None, True),
    ("repository_dispatch", "panel-daily", "", None, True),
    ("repository_dispatch", "panel-daily", "", False, True),
    ("workflow_dispatch", "", "true", None, True),
    ("workflow_dispatch", "", "", None, True),
    ("workflow_dispatch", "", "false", True, True),
    ("workflow_dispatch", "", "false", False, False),
    ("workflow_dispatch", "", "false", None, False),
]


@pytest.mark.parametrize(
    "event,action,force,panel_running,started", GATE_CASES)
def test_gate(tmp_path, steps, event, action, force, panel_running,
             started):
    script = _heredoc(_step(steps, GATE_STEP)["run"], "EOF")
    outputs = _run_gate(
        script, tmp_path, event, action, force, panel_running)
    assert outputs["started"] == ("true" if started else "false")
    assert "persist_panel_state" not in outputs


def test_gate_no_usa_panel_state_json(steps):
    run = _step(steps, GATE_STEP)["run"]
    assert "panel-state.json" not in run
    assert "worker_state" in run


def test_watch_usa_live(steps):
    run = _step(steps, WATCH_STEP)["run"]
    assert 'WATCH_ARGS="--once --live"' in run
    assert "python -m clipagent watch $WATCH_ARGS" in run
    assert "--dry-run" not in run


def test_watch_acepta_streamer_dirigido(steps):
    run = _step(steps, WATCH_STEP)["run"]
    assert "--streamer ${{ inputs.streamer }}" in run


def test_restore_usa_run_id_del_ultimo_exitoso(steps):
    prev = _step(steps, "Resolver run previo exitoso")
    assert prev["id"] == "prev-run"
    run = prev["run"]
    assert "status=success" in run
    assert "per_page=1" in run
    assert "cerebro.yml/runs" in run
    assert "cerebro-sebastian.yml" not in run
    assert "GH_TOKEN" in prev.get("env", {})
    restore = _step(steps, "Restaurar estado del día anterior")
    # download-artifact@v4 con run-id falló siempre en este repo
    # ("Artifact not found") aunque el artefacto existe; se restaura
    # con `gh run download` (runs 44474668621 y 44476907092).
    assert "actions/download-artifact" not in str(restore)
    assert "gh run download" in restore["run"]
    # Artefacto por tenant, nunca el nombre fijo de un tenant.
    assert "cerebro-state-" in restore["run"]
    assert "cerebro-state-sebastian" not in restore["run"]
    assert "steps.prev-run.outputs.run_id" in restore["run"]
    assert "GH_TOKEN" in restore.get("env", {})
    assert restore.get("continue-on-error") is True
    assert restore.get("if") == "steps.prev-run.outputs.run_id != ''"


def test_permisos_incluyen_actions_read(workflow):
    assert workflow["permissions"]["actions"] == "read"


def test_dispatch_manual_tiene_input_tenant(workflow):
    # PyYAML lee la clave `on` como True (YAML 1.1).
    tenant = workflow[True]["workflow_dispatch"]["inputs"]["tenant"]
    assert tenant["required"] is True
    assert "default" not in tenant


def test_dispatch_manual_tiene_input_force(workflow):
    force = workflow[True]["workflow_dispatch"]["inputs"]["force"]
    assert force["default"] == "true"
    assert force["type"] == "boolean"


def test_dispatch_manual_tiene_input_streamer(workflow):
    streamer = workflow[True]["workflow_dispatch"]["inputs"]["streamer"]
    assert streamer["default"] == ""
    assert streamer["type"] == "string"


def test_dispatch_types_fijos_sin_tenant(workflow):
    types = workflow[True]["repository_dispatch"]["types"]
    assert types == ["panel-daily", "panel-start"]


def test_sin_environment_ni_secretos_por_tenant(workflow, steps):
    text = open(WORKFLOW, encoding="utf-8").read()
    # Ningún job declara `environment:` (solo se nombra en
    # comentarios, diciendo que no se usa).
    for job in workflow["jobs"].values():
        assert "environment" not in job
    assert "secrets.PANEL_USERNAME" not in text
    assert "secrets.PANEL_PASSWORD" not in text
    assert "secrets.TWITCH_CLIENT_ID" not in text
    assert "secrets.GEMINI_API_KEY" not in text
    # "cerebro-sebastian" solo se nombra en el comentario de
    # cabecera (qué reemplaza este workflow): en el YAML parseado
    # (sin comentarios) no aparece.
    assert "cerebro-sebastian" not in yaml.dump(workflow)
    # panel-state.json solo se nombra en comentarios (qué NO se
    # hace): ningún paso lo lee ni lo sube como artefacto.
    save = _step(steps, "Guardar estado para mañana")
    assert "panel-state.json" not in str(save.get("with", {}))
    gate = _step(steps, GATE_STEP)
    assert "panel-state.json" not in gate["run"]


def test_bootstrap_antes_de_trabajar(steps):
    names = [s.get("name") for s in steps]
    boot = names.index("Credenciales desde el panel")
    assert boot < names.index(WATCH_STEP)
    assert boot < names.index(
        "Cadena completa (detectar, cortar, subtitular, publicar)")
    run = _step(steps, "Credenciales desde el panel")["run"]
    assert "python -m clipagent.panel_credentials" in run


def test_pasos_de_trabajo_siguen_bajo_la_puerta(steps):
    for name in (WATCH_STEP,
                 "Cadena completa (detectar, cortar, subtitular, publicar)"):
        assert _step(steps, name)["if"] == (
            "steps.gate.outputs.started == 'true'")


def _run_parser(script, tmp_path, log, watch_exit):
    (tmp_path / "watch.log").write_text(log, encoding="utf-8")
    gh_out = tmp_path / "gh_out"
    gh_summary = tmp_path / "gh_summary"
    gh_out.write_text("", encoding="utf-8")
    gh_summary.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "WATCH_EXIT": str(watch_exit),
        "GITHUB_OUTPUT": str(gh_out),
        "GITHUB_STEP_SUMMARY": str(gh_summary),
    })
    proc = subprocess.run(
        [sys.executable, "-"], input=script, text=True, cwd=tmp_path,
        env=env, capture_output=True, timeout=60)
    return proc, _read_outputs(gh_out), \
        gh_summary.read_text(encoding="utf-8")


@pytest.mark.parametrize("log,watch_exit,code,enqueued,errors", [
    (WATCH_LOG_OK, 0, 0, "2", "0"),
    (WATCH_LOG_OK_PLURAL, 0, 0, "2", "0"),
    (WATCH_LOG_ERROR_CON_ENCOLADOS, 2, 0, "1", "1"),
    (WATCH_LOG_ERROR_SIN_ENCOLADOS, 2, 1, "0", "1"),
    ("sin línea de resumen\n", 2, 1, "0", "1"),
    ("sin línea de resumen\n", 0, 0, "0", "0"),
])
def test_watch_parser(tmp_path, steps, log, watch_exit, code,
                     enqueued, errors):
    script = _heredoc(_step(steps, WATCH_STEP)["run"], "PYEOF")
    proc, outputs, summary = _run_parser(
        script, tmp_path, log, watch_exit)
    assert proc.returncode == code, proc.stdout + proc.stderr
    assert outputs["enqueued"] == enqueued
    assert outputs["errors"] == errors
    assert outputs["watch_exit"] == str(watch_exit)
    if "Resumen:" in log:
        assert "Revisión de VODs" in summary
        assert f"| Encolados | {enqueued} |" in summary
