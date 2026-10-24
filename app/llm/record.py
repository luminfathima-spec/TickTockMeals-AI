"""Demo recorder module for TickTock Meals.

Records end-to-end responses for the 6 canonical demo scenarios
into fixture JSON files so the entire expo demonstration can replay
identically offline with 100% reliability.

Usage:
    python -m app.llm.record --scenarios demo [--live]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure root on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.clock import now as get_clock_now, set_fixed as clock_set_fixed
from app.config import get_config
from app.llm.client import LLMClient
from app.llm.messages import compose_message
from app.llm.vision import extract_passport
from app.models import (
    AllergenHint,
    ContainerType,
    DishCategory,
    DonorForm,
    FoodPassport,
    Listing,
    ListingStatus,
    Match,
    StorageCondition,
    Tier,
)
from app.safety.assess import assess_safety

SCENARIO_FIXTURES: List[Dict[str, Any]] = [
    {
        "name": "cooked_rice_and_sambar",
        "photo": "tests/fixtures/cooked_rice_and_sambar.jpg",
        "form": DonorForm(
            cooked_at="11:30:00",
            storage=StorageCondition.AMBIENT,
            est_portions=60,
            container=ContainerType.COVERED,
            notes="Freshly prepared lunch surplus from college mess.",
        ),
    },
    {
        "name": "biryani",
        "photo": "tests/fixtures/biryani.jpg",
        "form": DonorForm(
            cooked_at="12:00:00",
            storage=StorageCondition.HOT_HELD,
            est_portions=45,
            container=ContainerType.COVERED,
            notes="Hot held catering surplus from corporate seminar.",
        ),
    },
    {
        "name": "packaged_bread",
        "photo": "tests/fixtures/packaged_bread.jpg",
        "form": DonorForm(
            cooked_at="08:00:00",
            storage=StorageCondition.AMBIENT,
            est_portions=30,
            container=ContainerType.SEALED,
            notes="Factory-sealed bakery loaves.",
        ),
    },
    {
        "name": "curd_rice_discoloration",
        "photo": "tests/fixtures/curd_rice_discoloration.jpg",
        "form": DonorForm(
            cooked_at="10:00:00",
            storage=StorageCondition.AMBIENT,
            est_portions=25,
            container=ContainerType.OPEN,
            notes="Left in warm dining area; slight yellowing visible on surface.",
        ),
    },
    {
        "name": "fruit_salad",
        "photo": "tests/fixtures/fruit_salad.jpg",
        "form": DonorForm(
            cooked_at="12:30:00",
            storage=StorageCondition.REFRIGERATED,
            est_portions=20,
            container=ContainerType.COVERED,
            notes="Chilled cut seasonal fruit bowls.",
        ),
    },
    {
        "name": "blurry_photo",
        "photo": "tests/fixtures/blurry_photo.jpg",
        "form": DonorForm(
            cooked_at="11:00:00",
            storage=StorageCondition.AMBIENT,
            est_portions=35,
            container=ContainerType.UNKNOWN,
            notes="Taken quickly in low lighting.",
        ),
    },
]


def record_demo_scenarios(
    scenarios: str = "demo",
    live: bool = False,
    output_dir: Union[str, Path] = "tests/fixtures/recordings",
) -> List[Path]:
    """Execute scenario pipeline and save reproducible responses to JSON fixtures."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    mode = "live" if live else "mock"
    client = LLMClient()
    saved_files: List[Path] = []

    # Standardize clock for reproducible evaluation
    sim_time = datetime(2026, 10, 24, 12, 30, 0)
    clock_set_fixed(sim_time)

    for item in SCENARIO_FIXTURES:
        name = item["name"]
        photo_path = Path(item["photo"])
        form = item["form"]

        if not photo_path.is_file():
            # In case test fixture was not found locally, create a tiny placeholder
            photo_path.parent.mkdir(parents=True, exist_ok=True)
            from PIL import Image
            img = Image.new("RGB", (100, 100), color=(73, 109, 137))
            img.save(photo_path)

        # 1. Vision extraction
        passport = extract_passport([photo_path], form=form, client=client, mode=mode)

        # 2. Safety assessment
        verdict = assess_safety(passport, reference_time=sim_time, client=client, mode=mode)

        # 3. Multilingual Message Synthesis
        sample_match = Match(
            id=f"m_{name}",
            listing_id=f"l_{name}",
            recipient_id="recip_01",
            volunteer_id="vol_01",
            score=0.92,
            score_breakdown={"urgency": 0.35, "proximity": 0.25, "capacity": 0.20, "diet": 0.10, "hours": 0.10},
            why_this_match="Optimal proximity (1.8 km) and fits remaining capacity.",
            status="PROPOSED",
            created_at=sim_time,
        )

        msg_en = compose_message(
            role="recipient",
            language="en",
            match=sample_match,
            passport=passport,
            verdict=verdict,
            donor_area="Yelahanka New Town",
            client=client,
        )

        msg_kn = compose_message(
            role="recipient",
            language="kn",
            match=sample_match,
            passport=passport,
            verdict=verdict,
            donor_area="ಯಲಹಂಕ",
            client=client,
        )

        # Save structured recording artifact
        recording_payload = {
            "scenario": name,
            "recorded_at": sim_time.isoformat(),
            "mode": mode,
            "passport": passport.model_dump(mode="json"),
            "safety_verdict": verdict.model_dump(mode="json"),
            "messages": {
                "en": msg_en,
                "kn": msg_kn,
            },
        }

        target_file = out_path / f"{name}.json"
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(recording_payload, f, indent=2, default=str)

        saved_files.append(target_file)

    return saved_files


def main() -> None:
    parser = argparse.ArgumentParser(description="Record TickTock Meals demo scenarios to JSON fixtures.")
    parser.add_argument("--scenarios", type=str, default="demo", help="Scenario suite to run (default: 'demo')")
    parser.add_argument("--live", action="store_true", help="Record using live Gemini API calls")
    parser.add_argument("--output-dir", type=str, default="tests/fixtures/recordings", help="Output directory")

    args = parser.parse_args()
    print(f"Recording scenarios '{args.scenarios}' (live={args.live}) to {args.output_dir}...")
    saved = record_demo_scenarios(scenarios=args.scenarios, live=args.live, output_dir=args.output_dir)
    print(f"Successfully recorded {len(saved)} demo scenario fixtures:")
    for s in saved:
        print(f" - {s}")


if __name__ == "__main__":
    main()
