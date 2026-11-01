"""Vibration conflict analysis between machine footprints.

A conflict is raised whenever two machines are close enough to interact
mechanically *and* either one emits above the noise floor (70 dB). Severity
is graded by how deeply they intrude into each other's sphere of influence.
"""

from __future__ import annotations

import math

from agents.schemas import EquipmentItem, Hazard

__all__ = ["DB_FLOOR", "DIST_BUFFER", "detect_vibration"]

DIST_BUFFER = 4.0
DB_FLOOR = 70.0


def _center(item: EquipmentItem) -> tuple[float, float]:
    return (item.x + item.w / 2.0, item.y + item.h / 2.0)


def detect_vibration(
    equipment: list[EquipmentItem],
    *,
    dist_buffer: float = DIST_BUFFER,
    db_floor: float = DB_FLOOR,
) -> list[Hazard]:
    """Return every vibration hazard found across pairwise machine proximity."""
    hazards: list[Hazard] = []
    n = len(equipment)
    for i in range(n):
        a = equipment[i]
        ax, ay = _center(a)
        for j in range(i + 1, n):
            b = equipment[j]
            bx, by = _center(b)
            dx = ax - bx
            dy = ay - by
            dist = math.hypot(dx, dy)

            footprint_radius = (max(a.w, a.h) + max(b.w, b.h)) / 2.0
            threshold = footprint_radius + dist_buffer
            if dist > threshold:
                continue
            if min(a.vibration_db, b.vibration_db) < db_floor:
                continue

            ratio = dist / threshold if threshold else 1.0
            if ratio <= 0.5:
                severity = "high"
            elif ratio <= 0.8:
                severity = "medium"
            else:
                severity = "low"

            hazards.append(
                Hazard(
                    type="vibration",
                    a_id=a.id,
                    b_id=b.id,
                    a_center=(int(ax), int(ay)),
                    b_center=(int(bx), int(by)),
                    threshold=threshold,
                    measured=dist,
                    severity=severity,
                )
            )
    return hazards
