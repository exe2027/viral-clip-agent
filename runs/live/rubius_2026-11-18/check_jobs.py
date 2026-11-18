#!/usr/bin/env python3
"""Solo LEE el estado de los jobs de un clip (sin publicar nada).

Uso: python3 check_jobs.py <clip_id>
Imprime por plataforma: state, evidence_url y last_error.
Nunca llama a POST /publish ni re-encola: es solo lectura, seguro
de usar mientras hay un enfriamiento de Facebook en curso.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
from clipagent.panel_bridge import PanelBridge

CLIP_ID = int(sys.argv[1])

bridge = PanelBridge()
jobs = bridge.list_jobs(limit=100)
mine = [j for j in jobs if j.get("clip_id") == CLIP_ID]
if not mine:
    print(f"SIN JOBS para el clip {CLIP_ID}")
    sys.exit(0)
print(f"=== JOBS clip {CLIP_ID} ===")
for j in sorted(mine, key=lambda x: x.get("platform", "")):
    print(f"  {j.get('platform')}: job {j.get('id')} | state={j.get('state')} | "
          f"url={j.get('evidence_url') or '-'} | error={j.get('last_error') or '-'}")
posted = [j for j in mine if j.get("state") == "posted"]
print(f"posted: {len(posted)}/{len(mine)}")
