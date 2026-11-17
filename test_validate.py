#!/usr/bin/env python3
"""Pruebas de 5.4: validación de config con la clave exacta en el error.
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import validate
from clipagent.config import DEFAULT_CONFIG, load_config


def base_cfg():
    return copy.deepcopy(DEFAULT_CONFIG)


class TestValidate(unittest.TestCase):
    def test_defaults_validos(self):
        self.assertEqual(validate.validate_config(base_cfg()), [])
        # La config real del repo también pasa.
        self.assertEqual(validate.validate_config(load_config()), [])

    def test_clave_exacta_en_cada_error(self):
        cases = [
            ({"watcher": {"poll_minutes": "quince"}},
             "watcher.poll_minutes"),
            ({"watcher": {"poll_minutes": 0}}, "watcher.poll_minutes"),
            ({"clips": {"crf": 99}}, "clips.crf"),
            ({"clips": {"preset": "turbo"}}, "clips.preset"),
            ({"clips": {"min_duration_s": 50, "target_duration_s": 30,
                        "max_duration_s": 40}},
             "clips.min_duration_s"),
            ({"game": {"release_date": "19-11-2026"}},
             "game.release_date"),
            ({"game": {"timezone": "Chile/Santiago"}}, "game.timezone"),
            ({"games": [{"name": "GTA 6", "id": "gta6",
                         "patterns": "gta 6"}]}, "games[0].patterns"),
            ({"subtitles": {"subtitle_lang": "fr"}},
             "subtitles.subtitle_lang"),
            ({"subtitles": {"default_style": "inexistente"}},
             "subtitles.default_style"),
            ({"subtitle_policy": {"es": "traducir"}},
             "subtitle_policy.es"),
            ({"facecam_box": [1, 2, 3]}, "facecam_box"),
            ({"layout": {"mode": "loco"}}, "layout.mode"),
            ({"layout": {"pip_corner": "centro"}}, "layout.pip_corner"),
            ({"autopublish": {"threshold": -1}},
             "autopublish.threshold"),
            ({"autopublish": {"mode": "turbo"}}, "autopublish.mode"),
            ({"channel": {"language": "pt"}}, "channel.language"),
            ({"autopublish": {"veto_window_minutes": {"youtube": -5}}},
             "autopublish.veto_window_minutes.youtube"),
        ]
        for patch, key in cases:
            cfg = base_cfg()
            for section, values in patch.items():
                if isinstance(values, dict):
                    cfg.setdefault(section, {}).update(values)
                else:
                    cfg[section] = values
            with self.assertRaises(validate.ConfigError, msg=key) as ctx:
                validate.validate_config(cfg)
            self.assertIn(key, str(ctx.exception), key)

    def test_varios_errores_a_la_vez(self):
        cfg = base_cfg()
        cfg["clips"]["crf"] = 99
        cfg["watcher"] = {"poll_minutes": 0,
                          "max_new_vods_per_streamer_per_day": 5,
                          "state_file": "seen_vods.json"}
        with self.assertRaises(validate.ConfigError) as ctx:
            validate.validate_config(cfg)
        msg = str(ctx.exception)
        self.assertIn("clips.crf", msg)
        self.assertIn("watcher.poll_minutes", msg)

    def test_clave_desconocida_solo_avisa(self):
        cfg = base_cfg()
        cfg["clipz"] = {"crf": 23}
        warnings = validate.validate_config(cfg)
        self.assertEqual(len(warnings), 1)
        self.assertIn("clipz", warnings[0])

    def test_load_config_rechaza_config_mala(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("clips:\n  crf: 99\n")
            with self.assertRaises(validate.ConfigError) as ctx:
                load_config(path)
            self.assertIn("clips.crf", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
