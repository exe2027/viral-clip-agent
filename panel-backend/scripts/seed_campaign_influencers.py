#!/usr/bin/env python3
"""Alta en D1 (por tenant) de los streamers virales de campaña FAMOSOS.

Lee los 4 de streamers.yaml y los crea ACTIVOS vía POST /api/influencers
del Worker del tenant. NO toca D1 directo y NO guarda credenciales:
el token de sesión del panel lo pasa el operador por env
(PANEL_SESSION_TOKEN, sacado de su propio panel) y la base del Worker
por --base o PANEL_BASE_URL (o frontend/dist/workers.json); nunca se
hardcodea una URL de tenant.

Regla 2026-11-23 18:50 (solo FAMOSOS, >= 50.000 seguidores): Bobby Van
Riper NO se da de alta activo aunque su campaña pague $1,50 — tiene
734 seguidores en Twitch. El script lo SALTA e imprime el motivo; si
supera 50.000 seguidores, se reactiva quitándolo de SKIP.

BenDaDonnn SÍ se da de alta activo (es famoso, 645.690 seguidores):
su campaña no da prioridad (neta $0,90 < $1/1k), pero eso lo decide
la tarifa, no la puerta; la puerta solo pide fama.

Uso (sin escribir nada, solo muestra lo que haría):
    python3 seed_campaign_influencers.py --tenant seba
Para escribir de verdad (el coordinador, con su sesión del panel):
    PANEL_SESSION_TOKEN=<token> PANEL_BASE_URL=<base del Worker> \\
        python3 seed_campaign_influencers.py --tenant seba --apply
    PANEL_SESSION_TOKEN=<token de kaez> PANEL_BASE_URL=<base de kaez> \\
        python3 seed_campaign_influencers.py --tenant kaez --apply

NUNCA pasar el token de otro tenant: la sesión es tenant-bound
(el token de seba da 401 en kaez). No hay --dry-run invertido:
por defecto NO escribe; --apply es lo único que escribe.
"""
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

# Los 4 virales de campaña FAMOSOS (regla 18:50). Espejo de
# VIRAL_CAMPAIGN_HANDLES en panel-backend/worker/src/db.ts.
FAMOUS_CAMPAIGN_STREAMERS = ["dasgasd3m", "arthurtv", "dylanpage", "bendadonnn"]
# Excluidos con motivo (no se dan de alta activos).
SKIP = {
    "bobby_vanriper": "regla 2026-11-23 18:50 (solo famosos): 734 seguidores "
                      "en Twitch, bajo el listón de 50.000",
}


def worker_base(tenant, base_arg):
    if base_arg:
        return base_arg.rstrip("/")
    env = os.environ.get("PANEL_BASE_URL", "").strip()
    if env:
        return env.rstrip("/")
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "frontend", "dist", "workers.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        base = (data.get(tenant) or data.get("workers", {}).get(tenant) or "").strip()
        if base:
            return base.rstrip("/")
    except (OSError, ValueError):
        pass
    raise SystemExit(
        "Sin base del Worker: pasa --base o PANEL_BASE_URL "
        "(o genera frontend/dist/workers.json). Nunca se hardcodea.")


def post_influencer(base, token, payload):
    req = urllib.request.Request(
        base + "/api/influencers",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"error": f"HTTP {e.code}"}
        return e.code, body


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", default="seba", choices=["seba", "kaez"])
    ap.add_argument("--base", default=None,
                    help="Base del Worker del tenant (o PANEL_BASE_URL)")
    ap.add_argument("--apply", action="store_true",
                    help="Escribe de verdad; sin esto solo muestra")
    args = ap.parse_args()

    from clipagent import streamers as streamers_mod
    entries = {e["name"]: e for e in streamers_mod.load_streamers()}
    base = worker_base(args.tenant, args.base)
    print(f"Tenant: {args.tenant}\nWorker: {base}\nModo: "
          f"{'APPLY (escribe)' if args.apply else 'DRY-RUN (no escribe)'}")
    token = None
    if args.apply:
        token = os.environ.get("PANEL_SESSION_TOKEN", "").strip()
        if not token:
            raise SystemExit("Falta PANEL_SESSION_TOKEN (sesión del panel "
                             "de ESTE tenant; nunca la de otro tenant).")
    for name in FAMOUS_CAMPAIGN_STREAMERS:
        e = entries.get(name)
        if e is None:
            print(f"- {name}: FALTA en streamers.yaml, no se da de alta")
            continue
        payload = {
            "handle": e["name"],
            "platform": e["platform"],
            "url": e["channel_url"],
            "language": e["language"],
            "permission": True,
            "active": True,
            "game": e["game"],
            "permission_date": e["fecha_permiso"],
            "publish_platforms": list(e["publish_platforms"]),
        }
        if not args.apply:
            print(f"- {name}: DRY-RUN, se enviaría {json.dumps(payload, ensure_ascii=False)}")
            continue
        status, body = post_influencer(base, token, payload)
        print(f"- {name}: HTTP {status} {json.dumps(body, ensure_ascii=False)[:300]}")
    for name, reason in SKIP.items():
        print(f"- {name}: SALTADO — {reason}")


if __name__ == "__main__":
    main()
