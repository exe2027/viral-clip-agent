#!/usr/bin/env python3
"""Tests del workflow cerebro-watchdog.yml (Vigilante genérico).

Regresión 2026-11-27: el workflow NO fijaba PANEL_BASE_URL y
PanelBridge caía en el default (Worker de sebastian). Un dispatch
panel-watchdog con token de kaez se canjeaba en el Worker
equivocado -> 401 "Token del cerebro inválido" -> el paso
"Credenciales desde el panel" fallaba y la corrida no reportaba
nada (5 corridas fallidas el 27-nov: 15:40, 16:21, 17:00, 17:15
y 18:25 UTC). El arreglo es la misma expresión condicional por
tenant que ya usa cerebro-live.yml.
"""
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHDOG = os.path.join(HERE, ".github", "workflows",
                        "cerebro-watchdog.yml")
LIVE = os.path.join(HERE, ".github", "workflows", "cerebro-live.yml")

KAEZ_URL = "https://avc-kaez.clipsultravirales768.workers.dev"
SEBA_URL = "https://avc.vikingosfoodspa.workers.dev"


def _load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_watchdog_fija_panel_base_url_por_tenant():
    wf = _load(WATCHDOG)
    base = (wf.get("env") or {}).get("PANEL_BASE_URL")
    assert base, "cerebro-watchdog.yml debe fijar PANEL_BASE_URL a nivel de workflow"
    assert KAEZ_URL in base, "la base de kaez debe estar en PANEL_BASE_URL"
    assert SEBA_URL in base, "la base de sebastian debe estar en PANEL_BASE_URL"
    assert "client_payload.tenant" in base, "la base debe depender del tenant del dispatch"


def test_watchdog_usa_la_misma_expresion_que_live():
    watchdog = _load(WATCHDOG)
    live = _load(LIVE)
    assert (watchdog.get("env") or {}).get("PANEL_BASE_URL") == \
        (live.get("env") or {}).get("PANEL_BASE_URL")


def test_bootstrap_sigue_antes_de_vigilar():
    wf = _load(WATCHDOG)
    steps = wf["jobs"]["watchdog"]["steps"]
    names = [s.get("name") for s in steps]
    boot = names.index("Credenciales desde el panel")
    assert boot < names.index("Vigilar (con panel)")
    boot_step = steps[boot]
    assert boot_step.get("if") == \
        "${{ github.event.client_payload.brain_token != '' }}"
