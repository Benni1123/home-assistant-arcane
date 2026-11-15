"""Normalize Arcane security summaries without interpreting missing data as safe."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def security_values(
    summary: Mapping[str, Any], overview: Mapping[str, Any]
) -> dict[str, Any]:
    """Expose existing results only; unavailable scores/intelligence stay unknown."""
    result: dict[str, Any] = {}
    drivers = overview.get("drivers") or {}
    for key, value in (
        ("images_scanned", summary.get("scannedImages", drivers.get("imagesScanned"))),
        ("images_total", summary.get("totalImages", drivers.get("imagesTotal"))),
    ):
        if _count(value):
            result[key] = value
    has_scans = result.get("images_scanned", 0) > 0
    if has_scans:
        severity = overview.get("summary") or summary.get("summary") or {}
        for key in ("critical", "high", "medium", "low", "unknown", "total"):
            if _count(value := severity.get(key)):
                result[f"vulnerabilities_{key}"] = value
        for key, source in (
            ("actionable", summary.get("actionableCount")),
            ("exposed_critical_high", drivers.get("exposedCriticalHigh")),
            ("fixable", drivers.get("fixable")),
        ):
            if _count(source):
                result[key] = source
    score = overview.get("riskScore")
    if (
        has_scans
        and overview.get("scoreStatus") == "complete"
        and isinstance(score, (int, float))
        and not isinstance(score, bool)
        and math.isfinite(score)
        and 0 <= score <= 100
    ):
        result["risk_score"] = score
        if overview.get("riskBand") in ("none", "low", "medium", "high", "critical"):
            result["risk_band"] = overview["riskBand"]

    intel = overview.get("threatIntel") or {}
    if intel.get("enabled") is False:
        result["threat_intel_status"] = "disabled"
    elif intel.get("enabled") is True:
        if not intel.get("lastSyncedAt"):
            result["threat_intel_status"] = "not_synced"
        else:
            result["threat_intel_status"] = "stale" if intel.get("stale") else "ready"
            if has_scans:
                for key, source in (
                    ("known_exploited", "knownExploited"),
                    ("overdue_kev", "overdueKnownExploited"),
                    ("high_epss", "highEpss"),
                ):
                    if _count(value := drivers.get(source)):
                        result[key] = value
    if overview:
        result["details"] = {
            "computed_at": overview.get("computedAt"),
            "threat_intel_last_synced": intel.get("lastSyncedAt"),
            "threat_intel_stale": intel.get("stale"),
            "highest_priority_cve": (overview.get("scoreDriver") or {}).get("vulnerabilityId"),
            "highest_priority_image": (overview.get("scoreDriver") or {}).get("imageName"),
            "highest_priority_package": (overview.get("scoreDriver") or {}).get("pkgName"),
            "fixed_version": (overview.get("scoreDriver") or {}).get("fixedVersion"),
            "images_scanned": result.get("images_scanned"),
            "images_total": result.get("images_total"),
        }
    return result
