"""Storage calculations and destructive-request safety, without Docker."""

import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "arcane_storage",
    Path(__file__).resolve().parents[1] / "custom_components/arcane/storage.py",
)
storage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(storage)


class StorageTests(unittest.TestCase):
    def test_capacity_and_percent(self):
        result = storage.storage_values({"diskTotal": 1000, "diskUsage": 556}, {})
        self.assertEqual(result, {
            "disk_total": 1000, "disk_used": 556,
            "disk_free": 444, "disk_percent": 55.6,
        })

    def test_missing_stats_are_not_zero(self):
        self.assertEqual(storage.storage_values({}, {}), {})

    def test_invalid_and_impossible_capacity(self):
        for total in (0, -1, True, "100", float("nan"), float("inf")):
            with self.subTest(total=total):
                self.assertEqual(storage.storage_values({"diskTotal": total}, {}), {})
        for used in (-1, 101, False, None, float("nan")):
            with self.subTest(used=used):
                self.assertEqual(
                    storage.storage_values({"diskTotal": 100, "diskUsage": used}, {}),
                    {"disk_total": 100},
                )

    def test_reclaimable_sum_requires_all_categories(self):
        values = {
            "unusedImagesBytes": 10, "buildCacheBytes": 20,
            "unusedVolumesBytes": 30, "stoppedContainersBytes": 0,
        }
        result = storage.storage_values({}, values)
        self.assertEqual(result["reclaimable_total"], 60)
        values.pop("unusedVolumesBytes")
        self.assertNotIn("reclaimable_total", storage.storage_values({}, values))

    def test_reclaimable_unknown_docker_sizes_remain_unknown(self):
        self.assertEqual(storage.storage_values({}, {"unusedImagesBytes": -1}), {})


class CleanupSelectionTests(unittest.TestCase):
    def test_confirmation_is_mandatory(self):
        for confirmation in (None, False, "true", 1):
            with self.subTest(confirmation=confirmation), self.assertRaises(ValueError):
                storage.build_prune_request({"images": "dangling", "confirm": confirmation})

    def test_nothing_is_selected_by_default(self):
        with self.assertRaisesRegex(ValueError, "Select at least one"):
            storage.build_prune_request({"confirm": True})

    def test_none_categories_are_omitted_not_sent_as_defaults(self):
        self.assertEqual(
            storage.build_prune_request({"confirm": True, "images": "dangling"}),
            {"images": {"mode": "dangling"}},
        )

    def test_all_modes_match_arcane_schema(self):
        for resource, modes in storage.PRUNE_MODES.items():
            for mode in modes:
                if mode in ("none", "older_than"):
                    continue
                with self.subTest(resource=resource, mode=mode):
                    result = storage.build_prune_request({
                        "confirm": True, "confirm_volumes": True, resource: mode,
                    })
                    key = "buildCache" if resource == "build_cache" else resource
                    self.assertEqual(result, {key: {"mode": mode}})

    def test_all_age_filters_have_independent_thresholds(self):
        options = {"confirm": True}
        for hours, (resource, field) in enumerate(storage.AGE_FIELDS.items(), 1):
            options.update({resource: "older_than", field: hours})
        result = storage.build_prune_request(options)
        for hours, resource in enumerate(storage.AGE_FIELDS, 1):
            key = "buildCache" if resource == "build_cache" else resource
            self.assertEqual(result[key], {"mode": "olderThan", "until": f"{hours}h"})

    def test_age_is_not_inferred_or_accepted_as_zero(self):
        for hours in (None, 0, -1, True, 1.5, "24", 87601):
            with self.subTest(hours=hours), self.assertRaises(ValueError):
                storage.build_prune_request({
                    "confirm": True, "containers": "older_than",
                    "container_age_hours": hours,
                })

    def test_all_volume_modes_require_separate_confirmation(self):
        for mode in ("anonymous", "all"):
            for confirmation in (None, False, "true", 1):
                with self.subTest(mode=mode, confirmation=confirmation):
                    with self.assertRaisesRegex(ValueError, "Volume cleanup"):
                        storage.build_prune_request({
                            "confirm": True, "volumes": mode,
                            "confirm_volumes": confirmation,
                        })

    def test_unknown_modes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid cleanup mode"):
            storage.build_prune_request({"confirm": True, "images": "everything"})


if __name__ == "__main__":
    unittest.main()
