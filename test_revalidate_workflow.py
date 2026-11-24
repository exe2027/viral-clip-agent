#!/usr/bin/env python3
"""Tests del workflow cerebro-revalidate.yml (auto-seo, 2026-11-24).

El loop de revalidación corre en un job de GitHub Actions con dos
pasos: bootstrap (canjea el token de un solo uso por una sesión) y
revalidación (python -m clipagent.revalidate_pending). El segundo
paso exige PANEL_TENANT (require_tenant) pero el env del paso de
bootstrap es solo de ese paso: si el bootstrap no lo exporta a
$GITHUB_ENV, la revalidación muere antes de tocar un clip. Así
fallaron las dos primeras corridas (runs 45420621290 y 45421600281,
2026-11-24 12:01 y 12:06 Chile): TenantError "Falta PANEL_TENANT".

Estos tests leen el YAML y comprueban el cableado, no solo que el
texto exista.
"""
import os

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(
    HERE, ".github", "workflows", "cerebro-revalidate.yml")

BOOTSTRAP_STEP = "Credenciales desde el panel"
REVALIDATE_STEP = "Revalidar clips pendientes"


@pytest.fixture(scope="module")
def workflow():
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def steps(workflow):
    return workflow["jobs"]["revalidate"]["steps"]


def _step(steps, name):
    for s in steps:
        if s.get("name") == name:
            return s
    raise AssertionError(f"paso {name!r} no existe en el workflow")


def _step_names(steps):
    return [s.get("name") for s in steps]


def test_bootstrap_antes_de_revalidar(steps):
    names = _step_names(steps)
    assert BOOTSTRAP_STEP in names
    assert REVALIDATE_STEP in names
    assert names.index(BOOTSTRAP_STEP) < names.index(REVALIDATE_STEP)


def test_revalidar_tiene_panel_tenant_disponible(workflow, steps):
    """PANEL_TENANT debe llegar al paso de revalidación: o bien el
    workflow lo define a nivel global (env:), o bien el bootstrap
    lo exporta a $GITHUB_ENV (el env de un paso no pasa al
    siguiente). Sin eso require_tenant aborta el run."""
    global_env = workflow.get("env") or {}
    if "PANEL_TENANT" in global_env:
        return
    bootstrap = _step(steps, BOOTSTRAP_STEP)
    run = bootstrap.get("run", "")
    assert 'PANEL_TENANT=' in run and 'GITHUB_ENV' in run, (
        "el bootstrap debe exportar PANEL_TENANT a $GITHUB_ENV "
        "(o definirlo en env: del workflow); si no, el paso de "
        "revalidación falla con TenantError 'Falta PANEL_TENANT'")


def test_base_url_por_tenant(workflow):
    """La sesión canjeada solo vale en el Worker que emitió el
    token (fix f6f0a0f): la URL debe resolverse por tenant, no ser
    siempre avc."""
    global_env = workflow.get("env") or {}
    base_url = global_env.get("PANEL_BASE_URL", "")
    assert "avc-kaez" in base_url and "avc.vikingosfoodspa" in base_url
    assert "client_payload.tenant" in base_url or "inputs.tenant" in base_url


def test_revalidar_no_tiene_cron(workflow):
    """Un run por cron no tendría token del cerebro: el workflow
    solo se dispara por repository_dispatch o manualmente."""
    on = workflow.get("on") or workflow.get(True) or {}
    assert "schedule" not in on
    assert "repository_dispatch" in on
    assert "workflow_dispatch" in on
