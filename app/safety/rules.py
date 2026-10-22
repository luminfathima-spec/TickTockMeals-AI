"""Deterministic food safety rules engine for TickTock Meals.

Implements official FSSAI / WHO guardrails with zero LLM dependence.
All time calculations use app.clock to power the demo time-machine.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml

from app.clock import now as get_clock_now
from app.models import (
    ContainerType,
    DishCategory,
    FoodPassport,
    StorageCondition,
    VisibleFlag,
)


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @property
    def severity(self) -> int:
        levels = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
        return levels[self.value]

    def __ge__(self, other: RiskLevel) -> bool:
        return self.severity >= other.severity

    def __gt__(self, other: RiskLevel) -> bool:
        return self.severity > other.severity

    def __le__(self, other: RiskLevel) -> bool:
        return self.severity <= other.severity

    def __lt__(self, other: RiskLevel) -> bool:
        return self.severity < other.severity


_SHELF_LIFE_CACHE: Optional[Dict[str, Dict[str, float]]] = None


def load_shelf_life_table(yaml_path: str = "app/safety/shelf_life.yaml") -> Dict[str, Dict[str, float]]:
    """Load and cache the shelf-life YAML matrix."""
    global _SHELF_LIFE_CACHE
    if _SHELF_LIFE_CACHE is None:
        p = Path(yaml_path)
        if not p.is_file():
            # Fallback default safe hours if file missing
            return {
                "default": {"hot_held": 3.5, "ambient": 2.0, "refrigerated": 12.0, "unknown": 2.0}
            }
        with open(p, "r", encoding="utf-8") as f:
            _SHELF_LIFE_CACHE = yaml.safe_load(f) or {}
    return _SHELF_LIFE_CACHE


def parse_cooked_at(cooked_at_str: str, reference_dt: Optional[datetime] = None) -> datetime:
    """Parse a cooked_at string (either full ISO datetime or HH:MM) into a datetime."""
    ref = reference_dt or get_clock_now()
    cooked_at_clean = cooked_at_str.strip()

    # Try ISO formats first
    try:
        return datetime.fromisoformat(cooked_at_clean)
    except ValueError:
        pass

    # Try HH:MM or HH:MM:SS format
    try:
        parts = cooked_at_clean.split(":")
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) > 2 else 0
        cooked_time = time(hour, minute, second)

        candidate_dt = datetime.combine(ref.date(), cooked_time)
        # If candidate is significantly in the future, assume it was yesterday
        if candidate_dt > ref + timedelta(minutes=15):
            candidate_dt = candidate_dt - timedelta(days=1)
        return candidate_dt
    except Exception:
        # Fallback: assume cooked 2 hours ago
        return ref - timedelta(hours=2)


def evaluate_rules(
    passport: FoodPassport,
    reference_time: Optional[datetime] = None,
    shelf_life_path: str = "app/safety/shelf_life.yaml",
) -> Tuple[RiskLevel, List[str], datetime, bool, bool, float]:
    """Execute deterministic guardrail rules on a FoodPassport.

    Returns:
        (max_risk, reasons, safe_until, human_ok, requires_human_check, base_safe_hours)
    """
    now = reference_time or get_clock_now()
    cooked_dt = parse_cooked_at(passport.handling.cooked_at, reference_dt=now)
    shelf_life = load_shelf_life_table(shelf_life_path)

    reasons: List[str] = []
    max_risk = RiskLevel.LOW
    human_ok = True
    requires_human_check = passport.needs_human_check

    storage_val = passport.handling.storage.value

    # If storage is unknown, apply most conservative rules
    if passport.handling.storage == StorageCondition.UNKNOWN:
        requires_human_check = True
        reasons.append("Storage unknown: conservative ambient danger-zone guidelines enforced.")

    # 1. Determine effective base safe hours across all dishes
    effective_safe_hours = 2.0  # conservative default
    candidate_hours: List[float] = []

    if not passport.dishes:
        default_row = shelf_life.get("default", {})
        effective_safe_hours = default_row.get(storage_val, default_row.get("unknown", 2.0))
    else:
        for dish in passport.dishes:
            cat_name = dish.category.value if isinstance(dish.category, DishCategory) else str(dish.category)
            row = shelf_life.get(cat_name, shelf_life.get("default", {}))
            hours = row.get(storage_val, row.get("unknown", 2.0))

            # Stricter ambient windows for cooked rice and dairy
            if storage_val == "ambient":
                if cat_name == "cooked_rice":
                    hours = min(hours, 3.5)
                elif cat_name == "dairy":
                    hours = min(hours, 2.5)

            candidate_hours.append(hours)

        effective_safe_hours = min(candidate_hours) if candidate_hours else 2.0

    safe_until = cooked_dt + timedelta(hours=effective_safe_hours)
    minutes_left = (safe_until - now).total_seconds() / 60.0

    # 2. Check Visual Hazard Flags (Strict Downward Guardrails)
    has_mold_or_contamination = False
    has_discoloration_or_off = False

    for dish in passport.dishes:
        for flag in dish.visible_flags:
            if flag in (VisibleFlag.MOLD, VisibleFlag.CONTAMINATION_RISK):
                has_mold_or_contamination = True
            elif flag in (VisibleFlag.DISCOLORATION, VisibleFlag.OFF_VISUAL):
                has_discoloration_or_off = True

    if has_mold_or_contamination:
        max_risk = RiskLevel.HIGH
        human_ok = False
        requires_human_check = True
        reasons.append("Hazard flag: Visible mold or contamination detected; strictly excluded from human consumption.")
    elif has_discoloration_or_off:
        if max_risk < RiskLevel.MEDIUM:
            max_risk = RiskLevel.MEDIUM
        requires_human_check = True
        reasons.append("Quality flag: Discoloration or off-visual appearance detected; requires human volunteer inspection.")

    # 3. Container Status Checks
    if passport.handling.container == ContainerType.OPEN:
        if max_risk < RiskLevel.MEDIUM:
            max_risk = RiskLevel.MEDIUM
        requires_human_check = True
        reasons.append("Hygiene flag: Open, uncovered container creates environmental contamination exposure.")

    # 4. Packaged food check
    for dish in passport.dishes:
        if dish.category == DishCategory.PACKAGED:
            # Requires volunteer physical verification of printed date
            requires_human_check = True
            reasons.append("Packaged food requires verification of printed manufacturer expiry date.")

    # 5. Time Elapsed & Expiration Arithmetic
    if now >= safe_until or minutes_left <= 0:
        max_risk = RiskLevel.HIGH
        human_ok = False
        reasons.append(
            f"Elapsed holding time exceeded safe threshold ({effective_safe_hours:.1f}h); danger zone proliferation risk."
        )
    elif minutes_left < 45:
        if max_risk < RiskLevel.MEDIUM:
            max_risk = RiskLevel.MEDIUM
        reasons.append(
            f"Urgent window: Only {int(minutes_left)} minutes remaining before safe threshold expires."
        )

    if not reasons:
        reasons.append(
            f"Within safe holding window: {int(minutes_left)} minutes remaining (base {effective_safe_hours:.1f}h)."
        )

    return max_risk, reasons, safe_until, human_ok, requires_human_check, effective_safe_hours
