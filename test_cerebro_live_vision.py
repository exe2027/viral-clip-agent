#!/usr/bin/env python3
"""El cortador live necesita OpenCV en el runner (2026-11-23).

El 20-nov el suministro live se secó: el último clip live en D1
es el 166 (2026-11-20 03:25 UTC) y desde entonces todos los cortes
se descartaron con "sin cara verificada (eslabones probados: edge,
yunet)". La causa: los jobs `cut` y `vod-fallback` de
cerebro-live.yml instalaban solo .[whisper]; sin el extra `vision`
el runner no tiene cv2, así que decide_layout no encuentra la
webcam por cara (el render sale a juego completo), la puerta de
cara no tiene ningún eslabón disponible (Haar y YuNet caen) y el
re-render tampoco encuentra caja en la fuente. Verificado con el
artefacto del run 45283585849 (clip IlloJuan "Racistada"): la fuente
sí tiene la cara en PiP, el render final no, y el gate local con
cv2 también lo rechaza (cara en 1/20 muestras).

Estos tests impiden que alguien vuelva a quitar `vision` del
install del cortador live sin que la suite lo grite.
"""
import os
import re

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(
    HERE, ".github", "workflows", "cerebro-live.yml")
PYPROJECT = os.path.join(HERE, "pyproject.toml")

# Jobs que cortan/renderizan vídeo: sin vision no hay cv2 y el
# suministro live se seca. El job `detect` solo escanea directos
# (Helix/Kick, sin vídeo) y no lo necesita.
CUT_JOBS = ("cut", "vod-fallback")

INSTALL_RE = re.compile(
    r"pip install .*?-e\s+\"?\.?\[(?P<extras>[^\]]+)\]")


def _install_extras(workflow, job_name):
    job = workflow["jobs"][job_name]
    extras = set()
    for step in job.get("steps", []):
        run = step.get("run") or ""
        for match in INSTALL_RE.finditer(run):
            extras.update(
                e.strip() for e in match.group("extras").split(","))
    return extras


@pytest.fixture(scope="module")
def workflow():
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.mark.parametrize("job_name", CUT_JOBS)
def test_cut_jobs_instalan_vision(workflow, job_name):
    extras = _install_extras(workflow, job_name)
    assert "vision" in extras, (
        f"job {job_name}: el install no incluye el extra 'vision' "
        f"(extras: {sorted(extras)}). Sin cv2 el cortador live "
        "pierde la webcam y la puerta de cara descarta todo.")


def test_extra_vision_trae_opencv_con_pin():
    with open(PYPROJECT, encoding="utf-8") as f:
        text = f.read()
    vision = re.search(
        r"vision = \[(?P<body>.*?)\]", text, re.DOTALL).group("body")
    assert "opencv-python-headless" in vision
    assert "<5" in vision, (
        "el pin <5 de opencv es lo que conserva CascadeClassifier "
        "(cv2 5.x lo eliminó) y con él la detección Haar de caras.")
