#!/usr/bin/env python3
"""Driver CircleCI de la recopilación larga (SPEC §4).

Un solo disparo construye y sube LOS DOS vídeos de 14 min
(A -> canal ES de seba, B -> canal ES de kaez). Lo que el CLI
--combinado NO puede hacer solo en CircleCI:

  1. Dos bridges explícitos (uno por tenant) desde las variables
     del contexto `compilation-panel`, nunca from_env().
  2. Pool por bridge (list_clips + list_published), porque en
     CircleCI no existen los wrappers wr_seba.py / wr_kaez.py
     que usa load_pool_d1.
  3. Tokens YouTube por tenant: materializa el compartido y lo
     copia a tokens/youtube_shorts_<tenant>.json solo si el
     resumen de ESE tenant dice youtube_es (B2).

Uso (un solo paso `run` del job `compilation`):
    PYTHONUNBUFFERED=1 python -m clipagent.circle_compilation \\
        --combinado [--force]

Exit codes (§4.7): 0 todo subido o ya_subido; 2 pool
insuficiente; 3 algún vídeo en otro estado (incluido
construido_sin_subir); 1 fallo de pre-vuelo o de credenciales.
Nunca imprime valores de credenciales, tokens ni URLs firmadas.
"""
import argparse
import importlib.util
import json
import os
import shutil
import sys
import time

from . import compilation as comp
from . import credentials as _creds
from .panel_bridge import PanelBridge
from .panel_credentials import (
    fetch_brain_credentials,
    materialize_credentials,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TENANTS = ("sebastian", "kaez")
VIDEO_BY_TENANT = {"sebastian": "video_a", "kaez": "video_b"}

# Variables del contexto `compilation-panel` (§2.2), por tenant:
# (base_url, username, password). Las 6 son obligatorias.
REQUIRED_ENV = {
    "sebastian": ("PANEL_BASE_URL_SEBASTIAN",
                  "PANEL_USERNAME_SEBASTIAN",
                  "PANEL_PASSWORD_SEBASTIAN"),
    "kaez": ("PANEL_BASE_URL_KAEZ",
             "PANEL_USERNAME_KAEZ",
             "PANEL_PASSWORD_KAEZ"),
}


class DriverError(Exception):
    """Fallo de pre-vuelo o de credenciales: exit 1."""


def build_bridges(env=None):
    """Dos PanelBridge explícitos desde el contexto (§4.1.1).

    `env` inyecta variables (tests); si no, os.environ. Si
    falta una variable, error con su NOMBRE, nunca su valor.
    """
    env = os.environ if env is None else env
    bridges = {}
    for tenant in TENANTS:
        base_var, user_var, pass_var = REQUIRED_ENV[tenant]
        base_url = env.get(base_var)
        username = env.get(user_var)
        password = env.get(pass_var)
        for var, value in ((base_var, base_url),
                           (user_var, username),
                           (pass_var, password)):
            if not value:
                raise DriverError(
                    f"falta la variable {var} del contexto "
                    "compilation-panel")
        bridges[tenant] = PanelBridge(
            base_url=base_url, username=username, password=password,
            tenant=tenant)
    return bridges


def preflight():
    """Comprobaciones previas (§4.4): falla con DriverError si
    falta algo, nunca sigue a ciegas. La respuesta de los
    bridges se prueba después (bootstrap + pool)."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise DriverError(f"falta {tool} en el PATH")
    if comp.find_font() is None:
        raise DriverError(
            "sin fuente para las etiquetas #N: find_font() es "
            "None (falta DejaVu Sans: fonts-dejavu-core)")
    if importlib.util.find_spec("googleapiclient") is None:
        raise DriverError(
            "falta google-api-python-client (extra youtube)")


def bootstrap_tenant_credentials(bridge, tenant, base_dir=None):
    """Bootstrap de credenciales por tenant (§4.3, B2).

    Orden: pedir credenciales -> BORRAR el compartido
    tokens/youtube_shorts.json ANTES de materializar -> copiar
    el compartido a tokens/youtube_shorts_<tenant>.json (0600)
    SOLO si summary["youtube_es"] is True de ESE tenant.
    Nunca se copia por existencia del archivo: si el tenant no
    tiene YouTube ES, el compartido seguiría siendo el token
    del tenant anterior. Devuelve el resumen.
    """
    base = base_dir or _creds.BASE_DIR
    creds = fetch_brain_credentials(bridge)
    shared = os.path.join(_creds.tokens_dir(base),
                          "youtube_shorts.json")
    try:
        os.unlink(shared)
    except FileNotFoundError:
        pass
    summary = materialize_credentials(creds, base)
    if summary.get("youtube_es") is True:
        dest = os.path.join(
            _creds.tokens_dir(base),
            f"youtube_shorts_{tenant}.json")
        shutil.copyfile(shared, dest)
        os.chmod(dest, 0o600)
    return summary


def fetch_pool(bridge, tenant):
    """Pool por bridge (§4.2): Cola + publicados, fusionados
    por (tenant, id) sin duplicados. Sin re-filtrar: el pool
    pasa tal cual a run_combined_compilation."""
    rows = bridge.list_clips() + bridge.list_published(limit=500)
    seen = set()
    pool = []
    for row in rows:
        key = (tenant, row.get("id"))
        if key in seen:
            continue
        seen.add(key)
        pool.append(row)
    return pool


def check_already_uploaded_today(bridges, base_dir, title):
    """Guarda §4.6 al inicio (I1): por tenant con token propio,
    comprueba en YouTube si el vídeo con este título ya se
    subió hoy. Devuelve {tenant: {"video_id", "url"}}. Si no se
    puede comprobar (red, credenciales), se sigue: la guarda
    de _build_combined_video lo volverá a intentar antes de
    subir.
    """
    found = {}
    for tenant in TENANTS:
        token_path = comp.token_path_for_tenant(tenant, base_dir)
        if token_path is None:
            continue
        try:
            service = comp._build_youtube_service(token_path)
            existing = comp.ya_subido_hoy(service, title)
        except Exception as e:  # noqa: BLE001 - se sigue igual
            print(f"[circle-compilation] guarda inicial {tenant}: "
                  f"no se pudo comprobar YouTube ({e})", flush=True)
            continue
        if existing is not None:
            found[tenant] = existing
            print(f"[circle-compilation] vídeo {tenant} ya subido "
                  "hoy en YouTube", flush=True)
    return found


def _progress(message):
    """Callback on_progress (B1 + M1): línea con marca temporal,
    con flush para que CircleCI la vea en vivo."""
    stamp = time.strftime("%H:%M:%S")
    print(f"[circle-compilation {stamp}] {message}", flush=True)


def _resumen_entry(report):
    return {
        "url": report.get("url"),
        "video_id": report.get("video_id"),
        "duracion_final_s": report.get("duracion_final_s"),
        "estado": report.get("estado"),
    }


def _write_resumen(out_dir, video_a, video_b):
    resumen = {
        "video_a": _resumen_entry(video_a),
        "video_b": _resumen_entry(video_b),
        "creditos_estimados": None,
    }
    with open(os.path.join(out_dir, "resumen.json"), "w",
              encoding="utf-8") as f:
        json.dump(resumen, f, indent=2, ensure_ascii=False)
    return resumen


def _copy_artifacts(base, out_dir):
    """Copia los JSONs de cada vídeo a compilation-out/ (§3.6):
    solo JSONs, nunca MP4s. Tolera que falten (corrida que
    falló antes de escribirlos)."""
    for label in ("a", "b"):
        src_dir = os.path.join(base, comp.RUNS_DIRNAME,
                               f"combinado-{label}")
        dst_dir = os.path.join(out_dir, f"combinado-{label}")
        for name in ("report.json", "precheck.json"):
            src = os.path.join(src_dir, name)
            if not os.path.isfile(src):
                continue
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copyfile(src, os.path.join(dst_dir, name))


def main(argv=None, base_dir=None, bridges=None):
    """CLI del driver: --combinado [--force]. Devuelve el exit
    code (§4.7). `bridges` y `base_dir` se inyectan en tests."""
    parser = argparse.ArgumentParser(
        description="Recopilación larga en CircleCI: dos vídeos "
        "de 14 min (A seba, B kaez).")
    parser.add_argument("--combinado", action="store_true",
                        required=True,
                        help="único modo en v1: los dos vídeos")
    parser.add_argument("--force", action="store_true",
                        help="permite una segunda subida (la "
                        "guarda de YouTube sigue aplicando)")
    args = parser.parse_args(argv)
    base = base_dir or REPO_ROOT
    out_dir = os.path.join(base, "compilation-out")
    os.makedirs(out_dir, exist_ok=True)
    print("[circle-compilation] inicio", flush=True)

    try:
        bridges = bridges or build_bridges()
        preflight()
        for tenant in TENANTS:
            summary = bootstrap_tenant_credentials(
                bridges[tenant], tenant, base)
            print(f"[circle-compilation] credenciales {tenant}: "
                  "youtube_es="
                  f"{'sí' if summary.get('youtube_es') else 'no'}",
                  flush=True)
    except DriverError as e:
        print(f"[circle-compilation] ERROR pre-vuelo: {e}",
              file=sys.stderr, flush=True)
        return 1
    except Exception as e:  # noqa: BLE001 - motivo al log
        print(f"[circle-compilation] ERROR credenciales: {e}",
              file=sys.stderr, flush=True)
        return 1

    title = comp.compilation_title()
    already = check_already_uploaded_today(bridges, base, title)
    if len(already) == len(TENANTS):
        # Los dos ya están subidos hoy: salir rápido sin gastar
        # el render (I1).
        resumen = _write_resumen(
            out_dir,
            {"url": already["sebastian"]["url"],
             "video_id": already["sebastian"]["video_id"],
             "duracion_final_s": None, "estado": "ya_subido"},
            {"url": already["kaez"]["url"],
             "video_id": already["kaez"]["video_id"],
             "duracion_final_s": None, "estado": "ya_subido"})
        print(f"[circle-compilation] los dos vídeos ya estaban "
              f"subidos hoy: {resumen['video_a']['url']} / "
              f"{resumen['video_b']['url']}", flush=True)
        return 0

    try:
        pools = {}
        for tenant in TENANTS:
            pools[tenant] = fetch_pool(bridges[tenant], tenant)
            print(f"[circle-compilation] pool {tenant}: "
                  f"{len(pools[tenant])} filas", flush=True)
    except Exception as e:  # noqa: BLE001 - motivo al log
        print(f"[circle-compilation] ERROR pool: {e}",
              file=sys.stderr, flush=True)
        return 1

    try:
        result = comp.run_combined_compilation(
            dry_run=False, force=args.force, base_dir=base,
            pools=pools, bridges=bridges, on_progress=_progress)
    except comp.PoolInsufficientError as e:
        print(f"[circle-compilation] PARADO: {e}",
              file=sys.stderr, flush=True)
        _write_resumen(
            out_dir,
            {"url": None, "video_id": None,
             "duracion_final_s": None,
             "estado": "pool_insuficiente"},
            {"url": None, "video_id": None,
             "duracion_final_s": None,
             "estado": "pool_insuficiente"})
        return 2
    except comp.CompilationError as e:
        print(f"[circle-compilation] ERROR: {e}",
              file=sys.stderr, flush=True)
        return 3

    _copy_artifacts(base, out_dir)
    _write_resumen(out_dir, result["video_a"], result["video_b"])
    for key in ("video_a", "video_b"):
        report = result[key]
        print(f"[circle-compilation] {key} tenant="
              f"{report['tenant']} estado={report['estado']} "
              f"duración={report['duracion_final_s']} s "
              f"url={report['url'] or 'sin subir'} "
              f"bloqueo={report['bloqueo'] or '-'}", flush=True)
    states = (result["video_a"].get("estado"),
              result["video_b"].get("estado"))
    if all(s in ("subido", "ya_subido") for s in states):
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
