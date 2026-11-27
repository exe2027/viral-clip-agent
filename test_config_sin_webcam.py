"""Alta de `rubius_sin_webcam` en la config (spec §6.2).

- La receta exige todas las claves de rubius MENOS `cam`.
- resolve_template acepta el 4º nombre y rechaza un 5º
  desconocido en voz alta.
- load_validator_config exige la receta en cuanto el nombre
  entra en TEMPLATE_NAMES (§6.1: el alta va en el mismo cambio
  que la receta en config.yaml; la receta ya está en disco, así
  que el caso "falta la receta" se simula quitándola del dict).
"""
import unittest

from clipagent.config import (
    TEMPLATE_REQUIRED_KEYS,
    load_config,
    load_validator_config,
    resolve_template,
)
from clipagent.validate import ConfigError


def _vcfg_with_recipe(recipe=True):
    """Cfg real de disco, con la receta sin_webcam inyectada o
    quitada (la receta YA está en config.yaml, §6.1: quitarla del
    dict simula el estado previo al alta)."""
    cfg = load_config()
    cfg = {k: (dict(v) if isinstance(v, dict) else v)
           for k, v in cfg.items()}
    templates = dict(cfg.get("templates") or {})
    if recipe:
        templates["rubius_sin_webcam"] = {
            "game": {"y0": 0, "y1": 1920, "w": 1080, "h": 1920},
            "title_banner": {"y0": 811, "y1": 1099},
            "subs_zone": {"y0": 1100, "y1": 1920},
            "subs_style": "word_white",
            "subs_over_game": True,
            "cam_full_width": False,
            "blurred_bg": False,
        }
    cfg["templates"] = templates
    return cfg


class TestSinWebcamConfig(unittest.TestCase):
    def test_receta_sin_cam(self):
        keys = TEMPLATE_REQUIRED_KEYS["rubius_sin_webcam"]
        self.assertNotIn("cam", keys)
        for k in ("game", "title_banner", "subs_zone", "subs_style",
                  "subs_over_game", "cam_full_width", "blurred_bg"):
            self.assertIn(k, keys)

    def test_resolve_acepta_cuarto_nombre(self):
        vcfg = {"default_template": "rubius",
                "templates": {"rubius": {"game": {}},
                              "rubius_sin_webcam": {"game": {}}}}
        name, _ = resolve_template(vcfg, "rubius_sin_webcam")
        self.assertEqual(name, "rubius_sin_webcam")
        # Sin declarar plantilla, el default sigue siendo rubius.
        self.assertEqual(resolve_template(vcfg)[0], "rubius")

    def test_resolve_rechaza_quinto_nombre(self):
        vcfg = {"default_template": "rubius", "templates": {}}
        with self.assertRaises(ConfigError) as cm:
            resolve_template(vcfg, "otro")
        self.assertIn("rubius_sin_webcam", str(cm.exception))

    def test_loader_exige_receta_cuando_el_nombre_entra(self):
        # TEMPLATE_NAMES ya trae rubius_sin_webcam (§6.1): el
        # loader exige su receta en config.yaml.
        vcfg = load_validator_config(_vcfg_with_recipe(recipe=True))
        self.assertIn("rubius_sin_webcam", vcfg["templates"])
        sin_receta = _vcfg_with_recipe(recipe=False)
        sin_receta["templates"].pop("rubius_sin_webcam", None)
        with self.assertRaises(ConfigError) as cm:
            load_validator_config(sin_receta)
        self.assertIn("rubius_sin_webcam", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
