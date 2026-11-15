"""Constants and shared container-key rules."""

import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "arcane_const",
    Path(__file__).resolve().parents[1] / "custom_components/arcane/const.py",
)
const = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(const)

LIFECYCLE_SPEC = importlib.util.spec_from_file_location(
    "arcane_lifecycle",
    Path(__file__).resolve().parents[1] / "custom_components/arcane/lifecycle.py",
)
lifecycle = importlib.util.module_from_spec(LIFECYCLE_SPEC)
LIFECYCLE_SPEC.loader.exec_module(lifecycle)


class ConstantsTests(unittest.TestCase):
    def test_platforms_include_switch_and_select(self):
        self.assertTrue({"sensor", "button", "update", "switch", "select"} <= set(const.PLATFORMS))

    def test_stale_removal_defaults_to_one_day(self):
        self.assertEqual(const.DEFAULT_STALE_REMOVAL_HOURS, 24)
        self.assertEqual(const.MAX_STALE_REMOVAL_HOURS, 8760)

    def test_every_container_entity_suffix_maps_back_to_its_container(self):
        for suffix in const.CONTAINER_ENTITY_SUFFIXES:
            with self.subTest(suffix=suffix):
                self.assertEqual(const.container_key(f"my-app_{suffix}"), "my-app")
        for suffix in ("power", "control"):
            self.assertIn(suffix, const.CONTAINER_ENTITY_SUFFIXES)

    def test_unrelated_unique_ids_are_not_container_keys(self):
        for rest in ("containers_total", "disk_used", "scan_security", "check_updates"):
            with self.subTest(rest=rest):
                self.assertIsNone(const.container_key(rest))


class LifecycleTests(unittest.TestCase):
    def test_action_rules_match_docker_states(self):
        expected = {
            "running": ["restart", "pause", "stop"],
            "paused": ["resume", "stop"],
            "exited": ["start", "restart"],
            "created": ["start", "restart"],
            "restarting": ["stop"],
        }
        for state, actions in expected.items():
            with self.subTest(state=state):
                self.assertEqual(lifecycle.available_actions(state), actions)

    def test_unknown_or_missing_states_offer_no_actions(self):
        for state in (None, "", "dead", "removing", "unknown"):
            with self.subTest(state=state):
                self.assertEqual(lifecycle.available_actions(state), [])

    def test_switch_presents_running_restarting_and_paused_as_on(self):
        self.assertEqual(
            lifecycle.ON_STATES, frozenset({"running", "restarting", "paused"})
        )
