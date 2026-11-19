"""Barrido en vivo simultáneo (2026-11-18).

FALLO REAL 2026-11-18: el monitoreo en vivo simultáneo NO se estaba
haciendo. cerebro-sebastian corre 1 vez al día (09:00) + un dispatch
al pulsar Iniciar (una corrida de ~13 min y termina); cerebro-live
corría cada 30 min pero solo para rubius por defecto, 25 min por
corrida. Nadie miraba a todos los influencers a la vez.

Este módulo es el barrido que usa cerebro-live en cada ciclo (cron
*/15 + dispatch panel-live del Worker mientras running=1): lee TODOS
los enabled de streamers.yaml (ES + EN, Twitch y Kick), mira si
están en vivo y en GTA VI (Helix / Kick API v2), y devuelve la
lista de los que SÍ están en vivo en GTA VI para cortarlos EN
PARALELO (matrix de GitHub Actions, un job por streamer).

- Twitch: Helix si hay credenciales, si no GQL público (watcher).
- Kick: API pública v2 (sin key).
- YouTube: sin watcher en vivo todavía (solo twitch/kick); se salta
  con nota visible, no se inventa un directo.
- Un error en un streamer no tumba el barrido: queda en `skipped`
  con el motivo. El barrido nunca falla (exit 0): sin directos en
  GTA VI, la lista sale vacía y el workflow no corta nada.
"""
import json
import os
import sys


def scan_live(only=None, twitch_cfg=None):
    """Barrido: (live, skipped, checked, twitch_mode).

    live: [{"name", "platform", "language"}] en vivo y en GTA VI.
    El idioma viaja en la matriz (2026-11-19, calibración): sin
    él el workflow no podía pasar --lang al corte y todo el
    camino en vivo era español a la fuerza.
    skipped: ["nombre: motivo"] (youtube, sin lectura, error).
    only: lista de nombres (minúsculas) o None (= todos).
    twitch_cfg: {"twitch_client_id", "twitch_client_secret"} o None
    (usa env; sin credenciales, GQL público).
    """
    from .category import is_gta6
    from .streamers import load_streamers
    import watcher as _watcher

    only_set = {s.strip().lower() for s in (only or []) if s.strip()}
    entries = [e for e in load_streamers()
               if e.get("enabled", True)
               and (e.get("estado") or "") == "concedido"]
    if only_set:
        entries = [e for e in entries if e["name"] in only_set]
    cfg = twitch_cfg or {
        "twitch_client_id": os.environ.get("TWITCH_CLIENT_ID", ""),
        "twitch_client_secret": os.environ.get("TWITCH_CLIENT_SECRET", ""),
    }
    twitch, mode = _watcher.twitch_client(cfg)
    live, skipped = [], []
    for e in entries:
        name, platform = e["name"], e["platform"]
        if platform == "youtube":
            skipped.append(f"{name}: youtube sin watcher en vivo "
                           "todavía (solo twitch/kick)")
            continue
        if platform not in ("twitch", "kick"):
            skipped.append(f"{name}: plataforma {platform!r} sin "
                           "barrido en vivo")
            continue
        try:
            if platform == "kick":
                lv, _vods = _watcher.kick_check(name)
                game = (lv or {}).get("game", "") if lv else ""
                is_live = bool(lv)
            else:
                lv, _vods = twitch.check(name)
                game = (lv or {}).get("game", "") if lv else ""
                is_live = bool(lv)
        except Exception as exc:  # noqa: BLE001 - un streamer no
            skipped.append(f"{name}: sin lectura ({exc})")
            continue
        print(f"{name} ({platform}): live={is_live} game={game!r} "
              f"gta6={is_gta6(game)}", flush=True)
        if is_live and is_gta6(game):
            live.append({"name": name, "platform": platform,
                         "language": (e.get("language") or "es")})
    return {"live": live, "skipped": skipped,
            "checked": len(entries), "twitch_mode": mode}


def main(argv=None) -> int:
    """CLI del barrido (lo llama cerebro-live): imprime el resumen y
    escribe live=/count= en GITHUB_OUTPUT si existe. Exit 0 siempre.
    """
    only = [s for s in os.environ.get("STREAMERS", "").split(",")
            if s.strip()]
    result = scan_live(only=only or None)
    # Conteo ES (fallback VOD, 2026-11-19): el workflow dispara el
    # modo VOD solo si es_count == 0. Se calcula aquí, con el mismo
    # barrido, para no hacer una segunda lectura a Twitch/Kick.
    from .streamers import load_streamers
    _lang = {e["name"]: (e.get("language") or "")
             for e in load_streamers()}
    es_live = [x for x in result["live"]
               if _lang.get(x["name"], "") == "es"]
    print(f"EN VIVO en GTA VI: {len(result['live'])}: "
          f"{[x['name'] for x in result['live']]} "
          f"(ES: {len(es_live)}, revisados: {result['checked']}, "
          f"twitch: {result['twitch_mode']})", flush=True)
    # Fase en vivo (2026-11-19): el panel muestra qué hace el
    # cerebro AHORA. Sin credenciales del panel no se reporta (el
    # barrido sigue igual); nunca tumba el barrido.
    try:
        from .panel_bridge import report_brain_phase
        if es_live:
            names = ", ".join(x["name"] for x in es_live)
            report_brain_phase("buscando_picos", f"en vivo: {names}")
        else:
            report_brain_phase("sin_directos", "ningún ES en GTA VI")
    except Exception:  # noqa: BLE001 - la fase es informativa
        pass
    for s in result["skipped"]:
        print(f"salto: {s}", flush=True)
    out = os.environ.get("GITHUB_OUTPUT", "")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("live=" + json.dumps(result["live"]) + "\n")
            f.write(f"count={len(result['live'])}\n")
            f.write("es_live=" + json.dumps(es_live) + "\n")
            f.write(f"es_count={len(es_live)}\n")
    else:
        print(json.dumps(result["live"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
