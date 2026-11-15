"""Security readouts must not confuse missing data with no vulnerabilities."""

import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "arcane_security",
    Path(__file__).resolve().parents[1] / "custom_components/arcane/security.py",
)
security = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(security)


class SecurityTests(unittest.TestCase):
    def test_missing_data_is_not_a_zero_score_or_clean_result(self):
        self.assertEqual(security.security_values({}, {}), {})

    def test_unscanned_images_are_not_reported_as_safe(self):
        values = security.security_values(
            {"scannedImages": 0, "totalImages": 23, "summary": {"critical": 0}},
            {"riskScore": 0, "scoreStatus": "complete"},
        )
        self.assertEqual(values["images_scanned"], 0)
        self.assertEqual(values["images_total"], 23)
        self.assertNotIn("risk_score", values)
        self.assertNotIn("vulnerabilities_critical", values)

    def test_overview_priority_severity_coverage_and_details(self):
        values = security.security_values(
            {"scannedImages": 11, "totalImages": 23, "actionableCount": 403},
            {
                "riskScore": 84, "riskBand": "high", "scoreStatus": "complete",
                "summary": {"critical": 66, "high": 858, "total": 2336},
                "scoreDriver": {"vulnerabilityId": "CVE-2026-11497", "fixedVersion": "1.1.0"},
            },
        )
        self.assertEqual(values["risk_score"], 84)
        self.assertEqual(values["risk_band"], "high")
        self.assertEqual(values["vulnerabilities_critical"], 66)
        self.assertEqual(values["images_scanned"], 11)
        self.assertEqual(values["actionable"], 403)
        self.assertEqual(values["details"]["highest_priority_cve"], "CVE-2026-11497")

    def test_unavailable_score_is_not_zero(self):
        values = security.security_values(
            {"scannedImages": 2}, {"scoreStatus": "unavailable", "riskScore": 0},
        )
        self.assertNotIn("risk_score", values)

    def test_invalid_scores_are_rejected(self):
        for score in (True, -1, 101, float("nan"), float("inf"), "84"):
            with self.subTest(score=score):
                values = security.security_values(
                    {"scannedImages": 1}, {"scoreStatus": "complete", "riskScore": score},
                )
                self.assertNotIn("risk_score", values)

    def test_unsynchronized_intelligence_does_not_mean_zero_kev(self):
        values = security.security_values(
            {"scannedImages": 11},
            {"threatIntel": {"enabled": True}, "drivers": {"knownExploited": 0, "highEpss": 0}},
        )
        self.assertEqual(values["threat_intel_status"], "not_synced")
        self.assertNotIn("known_exploited", values)
        self.assertNotIn("high_epss", values)

    def test_synced_but_stale_intelligence_is_marked(self):
        values = security.security_values(
            {"scannedImages": 11},
            {
                "threatIntel": {"enabled": True, "lastSyncedAt": "2026-11-10", "stale": True},
                "drivers": {"knownExploited": 2, "overdueKnownExploited": 1, "highEpss": 3},
            },
        )
        self.assertEqual(values["threat_intel_status"], "stale")
        self.assertEqual(values["known_exploited"], 2)
        self.assertEqual(values["overdue_kev"], 1)
        self.assertEqual(values["high_epss"], 3)

    def test_disabled_intelligence_is_not_ready(self):
        values = security.security_values({}, {"threatIntel": {"enabled": False}})
        self.assertEqual(values["threat_intel_status"], "disabled")

    def test_summary_fallback_when_overview_is_unavailable(self):
        values = security.security_values(
            {"scannedImages": 2, "summary": {"critical": 0, "high": 3, "total": 3}},
            {},
        )
        self.assertEqual(values["vulnerabilities_high"], 3)
        self.assertEqual(values["vulnerabilities_critical"], 0)
        self.assertNotIn("risk_score", values)

    def test_overview_coverage_fallback(self):
        values = security.security_values(
            {}, {"drivers": {"imagesScanned": 2, "imagesTotal": 3}, "summary": {"total": 5}},
        )
        self.assertEqual(values["images_scanned"], 2)
        self.assertEqual(values["vulnerabilities_total"], 5)
