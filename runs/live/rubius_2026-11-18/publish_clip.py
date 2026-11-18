#!/usr/bin/env python3
"""Publica la v3 karaoke 3220 en las 4 cuentas ES (REAL TEST).

Flujo del panel (verificado en worker/src/index.ts + gate.ts):
  1. POST /api/clips/:id/preview-seen (sin esto publishGate frena:
     "falta ver el preview en el Editor").
  2. POST /api/clips/:id/publish: corre publishGate por plataforma
     (SEO>=90, 40-60 s, GTA VI, subtitulos completos, topes diarios
     por plataforma+idioma, espaciamiento 2 h, cuenta verificada).
     Las que pasan se encolan como jobs; las que frenan quedan en
     `blocked` con el motivo. NO publica en el acto: el Worker
     (cron */5) ejecuta los jobs y cada 'posted' lleva evidence_url.
  3. Poll de GET /api/jobs hasta que los jobs del clip esten en
     estado terminal (posted/failed/draft) o se acabe el tiempo.

NO publica la version simple: el clip se identifica por id (del
paso de enqueue) o por 'v3_karaoke' en su reason (el titulo es el
mismo que el del clip simple 7).

Auth: PANEL_SESSION_TOKEN o PANEL_USERNAME+PANEL_PASSWORD en el
entorno (secretos de GitHub Actions, nunca en el repo).

Uso:
  python3 publish_clip.py <clip_id> [--wait-min 14]
  python3 publish_clip.py --find-v3 [--wait-min 14]
"""
import json
import os
import sys
import time

# Raiz del repo = 4 niveles sobre este script (runs/live/<sesion>/).
# Antes era una ruta fija de la VM local (/home/hatch/...), que no
# existe en el runner de GitHub Actions (REAL TEST, intento 1).
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _REPO)
from clipagent.panel_bridge import PanelBridge, PanelError

PLATFORMS = ["youtube_shorts", "tiktok", "instagram_reels",
             "facebook_reels"]


def find_v3(bridge):
    """La v3 es el clip mas reciente con 'v3_karaoke' en el reason."""
    clips = bridge.list_clips()
    cands = [c for c in clips
             if "v3_karaoke" in (c.get("reason") or "")]
    if not cands:
        raise PanelError("no hay ningun clip v3 en la Cola "
                         "(reason sin 'v3_karaoke').")
    cands.sort(key=lambda c: c.get("created_at") or 0)
    return cands[-1]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    wait_min = 14
    for a in sys.argv[1:]:
        if a.startswith("--wait-min="):
            wait_min = float(a.split("=", 1)[1])
    bridge = PanelBridge.from_env()
    if bridge is None:
        sys.exit("ABORTO: sin credenciales del panel.")
    print("health:", bridge.health())
    if args and args[0] != "--find-v3":
        clip_id = int(args[0])
        clip = bridge.get_clip(clip_id)
    else:
        clip = find_v3(bridge)
        clip_id = clip["id"]
    print(f"CLIP A PUBLICAR: id={clip_id} | titulo={clip.get('title')!r} "
          f"| estado={clip.get('status')} | preview_seen={clip.get('preview_seen')} "
          f"| video_r2_key={'SI' if clip.get('video_r2_key') else 'NO'} "
          f"| estilo={clip.get('subtitle_style')}")
    if "v3_karaoke" not in (clip.get("reason") or ""):
        sys.exit(f"ABORTO DE SEGURIDAD: el clip {clip_id} no es la v3 "
                 f"(reason sin 'v3_karaoke'). No se publica la simple.")
    if not clip.get("video_r2_key"):
        sys.exit(f"ABORTO: el clip {clip_id} no tiene MP4 en R2.")
    print("preview-seen:", bridge.preview_seen(clip_id))
    try:
        pub = bridge.publish_clip(clip_id)
    except PanelError as e:
        sys.exit(f"PUBLICACION RECHAZADA: {e}")
    print("PUBLISH:", json.dumps(pub, ensure_ascii=False))
    jobs = {j["platform"]: j for j in pub.get("jobs", [])}
    blocked = pub.get("blocked") or []
    if blocked:
        print("BLOQUEADAS POR EL GATE:")
        for b in blocked:
            print("  -", b)
    if not jobs:
        print("RESULTADO: ninguna plataforma encolada. No se publico nada.")
        return 1
    # Poll hasta estado terminal (posted/failed/draft).
    deadline = time.time() + wait_min * 60
    final = {}
    while time.time() < deadline:
        all_jobs = bridge.list_jobs(limit=100)
        mine = [j for j in all_jobs if j.get("clip_id") == clip_id]
        states = {}
        for j in mine:
            states[j["platform"]] = (j["state"], j.get("evidence_url"),
                                     j.get("last_error"))
        done = all(p in states and states[p][0] in
                   ("posted", "failed", "draft")
                   for p in jobs)
        print(f"[{time.strftime('%H:%M:%S')}] jobs:", json.dumps(
            {p: {"state": s[0], "url": s[1], "error": s[2]}
             for p, s in sorted(states.items())}, ensure_ascii=False))
        if done:
            final = states
            break
        time.sleep(60)
    else:
        all_jobs = bridge.list_jobs(limit=100)
        for j in all_jobs:
            if j.get("clip_id") == clip_id:
                final[j["platform"]] = (j["state"], j.get("evidence_url"),
                                        j.get("last_error"))
    print("=== RESULTADO FINAL v3 (clip %d) ===" % clip_id)
    ok = True
    for p in PLATFORMS:
        st = final.get(p)
        if not st:
            print(f"  {p}: SIN JOB (no se encolo)")
            ok = False
        elif st[0] == "posted":
            print(f"  {p}: PUBLICADO -> {st[1]}")
        else:
            print(f"  {p}: {st[0]} | error: {st[2]}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PanelError as e:
        sys.exit(f"ERROR PANEL: {e}")
