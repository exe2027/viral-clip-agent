#!/usr/bin/env python3
"""Publica el clip IlloJuan borra GTA 6 solo en Instagram.

Variante de publish_clip.py (v3 Rubius) para este clip: el marcador
de seguridad es 'illojuan-borra-gta6' en el reason, no 'v3_karaoke'.

Flujo del panel:
  1. POST /api/clips/:id/preview-seen.
  2. POST /api/clips/:id/publish: publishGate por plataforma
     (SEO>=90, duracion segun Ajustes, GTA VI, subtitulos completos,
     topes diarios, espaciamiento, cuenta verificada).
     Las que pasan se encolan como jobs; el Worker (cron */5) las
     ejecuta y cada 'posted' lleva su evidence_url (link publico).
  3. Poll de GET /api/jobs hasta posted/failed/draft (max 14 min).

Auth: PANEL_USERNAME + PANEL_PASSWORD en el entorno (secretos de
GitHub Actions, nunca en el repo).

Uso:
  python3 publish_illojuan.py <clip_id> [--wait-min 14]
"""
import json
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _REPO)
from clipagent.panel_bridge import PanelBridge, PanelError

PLATFORMS = ["instagram_reels"]
MARKER = "illojuan-borra-gta6"


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
    if not args:
        sys.exit("ABORTO: falta <clip_id>.")
    clip_id = int(args[0])
    clip = bridge.get_clip(clip_id)
    print(f"CLIP A PUBLICAR: id={clip_id} | titulo={clip.get('title')!r} "
          f"| estado={clip.get('status')} | preview_seen={clip.get('preview_seen')} "
          f"| video_r2_key={'SI' if clip.get('video_r2_key') else 'NO'} "
          f"| estilo={clip.get('subtitle_style')}")
    if MARKER not in (clip.get("reason") or ""):
        sys.exit(f"ABORTO DE SEGURIDAD: el clip {clip_id} no es el de "
                 f"IlloJuan (reason sin '{MARKER}'). No se publica.")
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
    print(f"=== RESULTADO FINAL illojuan (clip {clip_id}) ===")
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
