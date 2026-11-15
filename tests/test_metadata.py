"""Keep the visual action form, translations and implementation in sync."""

import json
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1] / "custom_components/arcane"


class MetadataTests(unittest.TestCase):
    def test_translation_keys_match(self):
        def keys(value, prefix=""):
            return {
                path
                for key, child in value.items()
                for path in (
                    keys(child, f"{prefix}{key}.")
                    if isinstance(child, dict) else {f"{prefix}{key}"}
                )
            }

        source = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))
        for language in ("en", "de"):
            translated = json.loads(
                (ROOT / f"translations/{language}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(keys(source), keys(translated), language)

    def test_form_is_safe_by_default_and_fully_translated(self):
        form = yaml.safe_load((ROOT / "services.yaml").read_text(encoding="utf-8"))["prune"]
        strings = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(form["fields"]), set(strings["services"]["prune"]["fields"])
        )
        for resource in ("containers", "images", "networks", "volumes", "build_cache"):
            field = form["fields"][resource]
            self.assertEqual(field["default"], "none")
            selector = field["selector"]["select"]
            options = strings["selector"][selector["translation_key"]]["options"]
            self.assertEqual(set(selector["options"]), set(options))
        self.assertIs(form["fields"]["confirm"]["default"], False)
        self.assertIs(form["fields"]["confirm_volumes"]["default"], False)

    def test_container_switch_and_select_are_translated_in_every_language(self):
        for path in (
            ROOT / "strings.json",
            ROOT / "translations/en.json",
            ROOT / "translations/de.json",
        ):
            entity = json.loads(path.read_text(encoding="utf-8"))["entity"]
            for key in ("container_power", "container_power_named"):
                self.assertTrue(entity["switch"][key]["name"], (path.name, key))
            for key in ("container_control", "container_control_named"):
                states = entity["select"][key]["state"]
                self.assertEqual(
                    set(states), {"start", "resume", "restart", "pause", "stop"},
                    (path.name, key),
                )
            self.assertIn("{container}", entity["switch"]["container_power_named"]["name"])
            self.assertIn("{container}", entity["select"]["container_control_named"]["name"])

    def test_stale_removal_option_is_translated(self):
        for path in (
            ROOT / "strings.json",
            ROOT / "translations/en.json",
            ROOT / "translations/de.json",
        ):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("stale_removal_hours", data["options"]["step"]["init"]["data"])
