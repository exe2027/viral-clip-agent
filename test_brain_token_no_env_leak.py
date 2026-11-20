#!/usr/bin/env python3
"""Regresión 2026-11-20: el token del cerebro no viaja en env:.

El runner imprime el bloque env: de cada paso en el registro; en
cerebro-live.yml y enqueue-clip.yml el token de un solo uso iba en
PANEL_BRAIN_TOKEN dentro de env: y quedaba en claro en los logs de
un repo público (verificado en la run 44688008771, paso
"Credenciales desde el panel"). Ahora se lee del archivo del evento
en tiempo de ejecución y se enmascara antes de usarlo.
"""
import json
import os
import subprocess
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOWS = [
    os.path.join(HERE, ".github", "workflows", "cerebro-live.yml"),
    os.path.join(HERE, ".github", "workflows", "enqueue-clip.yml"),
]
# Valor falso: nunca es un token real; solo prueba que el valor
# llega al módulo sin imprimirse en ningún lado.
FAKE_TOKEN = "TOKEN_DE_PRUEBA_SIN_VALOR_REAL"

FAKE_MODULE = '''\
import os
import sys


def main():
    token = os.environ.get("PANEL_BRAIN_TOKEN", "")
    assert token == "TOKEN_DE_PRUEBA_SIN_VALOR_REAL", (
        "el modulo no recibio el token")
    print("[panel-credentials] bootstrap OK (sin valores)",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

FAKE_EVENTS = {
    "cerebro-live.yml": {
        "client_payload": {"tenant": "sebastian",
                           "brain_token": FAKE_TOKEN},
    },
    "enqueue-clip.yml": {
        "inputs": {"tenant": "sebastian",
                   "brain_token": FAKE_TOKEN},
    },
}


def _all_steps(workflow):
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []):
            yield step


def _bootstrap_step(workflow):
    boots = [s for s in _all_steps(workflow)
             if s.get("name") == "Credenciales desde el panel"
             and "panel_credentials" in str(s.get("run", ""))]
    if not boots:
        # enqueue-clip.yml en HEAD aún no tiene bootstrap (llega
        # con el refactor genérico sin commitear): se salta.
        pytest.skip("sin paso bootstrap con panel_credentials")
    return boots[0]


def _load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.mark.parametrize("path", WORKFLOWS)
def test_token_no_en_env(path):
    """Ningún paso expone el token en su bloque env:."""
    workflow = _load(path)
    for step in _all_steps(workflow):
        for key, value in (step.get("env") or {}).items():
            assert "brain_token" not in str(value), (
                f"{path}: el paso {step.get('name')} expone el "
                "token en env:")
            assert not (key == "PANEL_BRAIN_TOKEN"
                        and "${{" in str(value)), (
                f"{path}: PANEL_BRAIN_TOKEN con expresión en env:")


@pytest.mark.parametrize("path", WORKFLOWS)
def test_bootstrap_lee_del_evento_y_enmascara(path):
    """El bootstrap lee el token del evento y lo enmascara."""
    run = _bootstrap_step(_load(path))["run"]
    assert "GITHUB_EVENT_PATH" in run
    assert "::add-mask::" in run
    assert "brain_token" in run
    assert "PANEL_BRAIN_TOKEN" in run


def _run_bootstrap(script, tmp_path, event):
    fake = tmp_path / "fake"
    (fake / "clipagent").mkdir(parents=True, exist_ok=True)
    (fake / "clipagent" / "__init__.py").write_text("", encoding="utf-8")
    (fake / "clipagent" / "panel_credentials.py").write_text(
        FAKE_MODULE, encoding="utf-8")
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    gh_env = tmp_path / "gh_env"
    gh_env.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_ENV": str(gh_env),
        "PYTHONPATH": str(fake),
        # El workflow usa `python` (setup-python lo pone en PATH);
        # en el test se resuelve con el intérprete actual.
        "PATH": os.path.dirname(sys.executable) + os.pathsep
        + env.get("PATH", ""),
    })
    proc = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", script],
        text=True, cwd=tmp_path, env=env,
        capture_output=True, timeout=60)
    return proc


def _sin_mascaras(salida):
    """Quita las líneas ::add-mask:: (el runner las consume y no
    las muestra en el registro); el resto no debe tener el valor."""
    return "\n".join(
        line for line in salida.splitlines()
        if not line.startswith("::add-mask::"))


@pytest.mark.parametrize("path", WORKFLOWS)
def test_bootstrap_no_imprime_el_token(tmp_path, path):
    """El token llega al módulo sin imprimirse en ningún lado."""
    name = os.path.basename(path)
    run = _bootstrap_step(_load(path))["run"]
    proc = _run_bootstrap(run, tmp_path, FAKE_EVENTS[name])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    salida = _sin_mascaras(proc.stdout + proc.stderr)
    assert FAKE_TOKEN not in salida


@pytest.mark.parametrize("path", WORKFLOWS)
def test_bootstrap_sin_token_falla_en_alto(tmp_path, path):
    """Sin token en el evento, el bootstrap falla con mensaje claro
    (nunca sigue sin credenciales)."""
    name = os.path.basename(path)
    event = dict(FAKE_EVENTS[name])
    key = ("client_payload" if name == "cerebro-live.yml"
           else "inputs")
    event[key] = {"tenant": "sebastian"}
    run = _bootstrap_step(_load(path))["run"]
    proc = _run_bootstrap(run, tmp_path, event)
    assert proc.returncode != 0
    assert "sin token del cerebro" in proc.stdout + proc.stderr
