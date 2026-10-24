"""Gemini Vision food passport extractor for TickTock Meals.

Converts 1 to 3 food surplus photos and a donor handling form into a
validated FoodPassport with dishes, visual flags, allergen hints,
and human verification flags.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from PIL import Image
from pydantic import BaseModel, Field

from app.config import get_config
from app.input_guards import build_hardened_vision_prompt, validate_photo_file
from app.llm.client import LLMClient
from app.models import (
    AllergenHint,
    ContainerType,
    Dish,
    DishCategory,
    DonorForm,
    FoodPassport,
    StorageCondition,
    VegStatus,
    VisibleFlag,
)


class RawVisionDish(BaseModel):
    name: str
    category: str
    veg_status: str
    est_portions: int
    visible_flags: List[str]
    confidence: float


class RawVisionPassport(BaseModel):
    dishes: List[RawVisionDish] = Field(default_factory=list)
    allergen_hints: List[str] = Field(default_factory=list)
    photo_quality_ok: bool = True
    photo_quality_note: str = "Acceptable clarity"


VISION_SYSTEM_PROMPT = """You are an objective food triage inspection assistant for surplus meal rescue.
Examine the provided image(s) and donor details.

CRITICAL RULES:
1. Describe ONLY what is directly visible in the photograph.
2. NEVER claim that the food is safe to eat or fit for human consumption.
3. Output JSON strictly matching the requested schema.
4. If an ingredient, dish name, or state is ambiguous or unidentifiable, use "unknown" instead of guessing.
5. Create exactly one Dish entry per distinct food item or container visible.
6. Inspect surfaces for visible flags: 'spillage', 'discoloration', 'mold', 'off_visual', 'contamination_risk', or 'none'.
7. Note allergen hints strictly as visual estimates.
"""


def preprocess_image(
    image_input: Union[bytes, str, Path],
    max_side: int = 1024,
) -> Tuple[bytes, bool]:
    """Resize image to max_side while stripping all EXIF and GPS metadata.

    Returns:
        Tuple of (clean_jpeg_bytes, exif_was_removed_or_absent)
    """
    if isinstance(image_input, bytes):
        pil_img = Image.open(io.BytesIO(image_input))
    else:
        pil_img = Image.open(str(image_input))

    had_exif = bool(pil_img.getexif())

    # Convert to RGB (handles RGBA, P, grayscale cleanly)
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")

    # Resize if any dimension exceeds max_side
    width, height = pil_img.size
    if max(width, height) > max_side:
        pil_img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    # Save to a new memory stream without any EXIF attachment
    out_io = io.BytesIO()
    pil_img.save(out_io, format="JPEG", quality=85, optimize=True)
    clean_bytes = out_io.getvalue()

    # Verify that the generated image has no EXIF data
    verified_img = Image.open(io.BytesIO(clean_bytes))
    verified_exif = verified_img.getexif()
    if verified_exif and len(verified_exif) > 0:
        verified_img.info.clear()

    return clean_bytes, had_exif


def compute_needs_human_check(
    dishes: List[Dish],
    photo_quality_ok: bool,
    confidence_threshold: float = 0.6,
) -> bool:
    """Compute whether the listing requires a mandatory human verification check."""
    if not photo_quality_ok:
        return True
    if not dishes:
        return True

    for dish in dishes:
        if dish.confidence < confidence_threshold:
            return True
        for flag in dish.visible_flags:
            if flag != VisibleFlag.NONE:
                return True

    return False


def _match_fixture_name(photo_path_or_name: str) -> Optional[str]:
    """Identify matching test fixture name from a file path or name."""
    stem = Path(photo_path_or_name).stem.lower()
    mapping = {
        "cooked_rice": "cooked_rice_and_sambar",
        "sambar": "cooked_rice_and_sambar",
        "biryani": "biryani",
        "bread": "packaged_bread",
        "packaged_bread": "packaged_bread",
        "curd_rice": "curd_rice_discoloration",
        "discoloration": "curd_rice_discoloration",
        "fruit": "fruit_salad",
        "fruit_salad": "fruit_salad",
        "blurry": "blurry_photo",
        "blurry_photo": "blurry_photo",
    }
    for key, fixture in mapping.items():
        if key in stem:
            return fixture
    return None


def extract_passport(
    photos: List[Union[bytes, str, Path]],
    form: DonorForm,
    client: Optional[LLMClient] = None,
    mode: Optional[str] = None,
) -> FoodPassport:
    """Extract a validated FoodPassport from 1 to 3 images and a donor handling form."""
    if not (1 <= len(photos) <= 3):
        raise ValueError("Must provide between 1 and 3 food photos.")

    app_cfg = get_config()
    conf_threshold = app_cfg.vision_confidence_threshold
    llm_client = client or LLMClient()
    active_mode = mode or llm_client.mode

    # Clean and resize all photos, validating against malicious inputs
    cleaned_bytes_list: List[bytes] = []
    for p in photos:
        is_ok, reason = validate_photo_file(p)
        if not is_ok:
            raise ValueError(f"Invalid photo upload: {reason}")
        b, _ = preprocess_image(p, max_side=1024)
        cleaned_bytes_list.append(b)

    # ---------------- MOCK & OFFLINE REPLAY / FALLBACK MODES ----------------
    if active_mode in ("mock", "replay", "fallback", "cache_only"):
        fixture_name = None
        for p in photos:
            if isinstance(p, (str, Path)):
                matched = _match_fixture_name(str(p))
                if matched:
                    fixture_name = matched
                    break

        fixture_dict = None
        if fixture_name:
            candidates = [
                Path("tests/fixtures") / f"{fixture_name}.json",
                Path("fixtures") / f"{fixture_name}.json",
                Path("tests/fixtures/recordings") / f"{fixture_name}.json",
                Path("fixtures/recordings") / f"{fixture_name}.json",
            ]
            for c in candidates:
                if c.is_file():
                    with open(c, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict) and "passport" in loaded:
                        fixture_dict = loaded["passport"]
                    else:
                        fixture_dict = loaded
                    break

        if fixture_dict is None:
            # Fallback deterministic mock
            fixture_dict = {
                "dishes": [
                    {
                        "name": "Standard Mixed Vegetable Meal",
                        "category": "dry_veg",
                        "veg_status": "veg",
                        "est_portions": form.est_portions or 25,
                        "visible_flags": ["none"],
                        "confidence": 0.85,
                    }
                ],
                "allergen_hints": [],
                "photo_quality_ok": True,
                "photo_quality_note": "Clear mock container",
                "needs_human_check": False,
            }

        # Build typed Dish list
        dishes: List[Dish] = []
        for d in fixture_dict.get("dishes", []):
            cat_val = d.get("category", "other")
            try:
                category = DishCategory(cat_val)
            except ValueError:
                category = DishCategory.OTHER

            veg_val = d.get("veg_status", "veg")
            try:
                veg_status = VegStatus(veg_val)
            except ValueError:
                veg_status = VegStatus.UNKNOWN

            visible_flags = []
            for f in d.get("visible_flags", ["none"]):
                try:
                    visible_flags.append(VisibleFlag(f))
                except ValueError:
                    visible_flags.append(VisibleFlag.NONE)

            dishes.append(
                Dish(
                    name=d.get("name", "Unknown Dish"),
                    category=category,
                    veg_status=veg_status,
                    est_portions=d.get("est_portions", form.est_portions or 10),
                    visible_flags=visible_flags,
                    confidence=float(d.get("confidence", 0.8)),
                )
            )

        allergens: List[AllergenHint] = []
        for a in fixture_dict.get("allergen_hints", []):
            try:
                allergens.append(AllergenHint(a))
            except ValueError:
                allergens.append(AllergenHint.UNKNOWN)

        photo_quality_ok = bool(fixture_dict.get("photo_quality_ok", True))
        photo_quality_note = str(fixture_dict.get("photo_quality_note", "Acceptable"))

        # Compute needs_human_check dynamically in code
        needs_human_check = compute_needs_human_check(
            dishes=dishes,
            photo_quality_ok=photo_quality_ok,
            confidence_threshold=conf_threshold,
        )

        return FoodPassport(
            dishes=dishes,
            allergen_hints=allergens,
            photo_quality_ok=photo_quality_ok,
            photo_quality_note=photo_quality_note,
            needs_human_check=needs_human_check,
            handling=form,
        )

    # ---------------- LIVE MODE ----------------
    base_prompt = (
        f"Analyze these surplus meal photos.\n"
        f"Donor reported cooked time: {form.cooked_at}, storage: {form.storage.value}, "
        f"container: {form.container.value}, estimated portions: {form.est_portions or 'unspecified'}."
    )
    prompt = build_hardened_vision_prompt(base_prompt, form.notes)
    prompt += "\nReturn JSON adhering to RawVisionPassport schema."

    try:
        raw_res = llm_client.generate_with_images(
            prompt=prompt,
            images=cleaned_bytes_list,
            schema=RawVisionPassport,
            system_instruction=VISION_SYSTEM_PROMPT,
        )
        if isinstance(raw_res, dict):
            raw_passport = RawVisionPassport.model_validate(raw_res)
        else:
            raw_passport = raw_res
    except Exception as first_err:
        # Retry once with feedback
        try:
            retry_prompt = f"{prompt}\nNOTE: Previous output had validation error: {first_err}. Please ensure valid schema."
            raw_res = llm_client.generate_with_images(
                prompt=retry_prompt,
                images=cleaned_bytes_list,
                schema=RawVisionPassport,
                system_instruction=VISION_SYSTEM_PROMPT,
            )
            raw_passport = (
                RawVisionPassport.model_validate(raw_res)
                if isinstance(raw_res, dict)
                else raw_res
            )
        except Exception:
            # Safe fallback: never crash!
            return FoodPassport(
                dishes=[],
                allergen_hints=[AllergenHint.UNKNOWN],
                photo_quality_ok=False,
                photo_quality_note="Model output validation failed; fallback triggered",
                needs_human_check=True,
                handling=form,
            )

    # Parse and validate raw passport into typed FoodPassport
    dishes = []
    for d in raw_passport.dishes:
        try:
            cat = DishCategory(d.category)
        except ValueError:
            cat = DishCategory.OTHER
        try:
            veg = VegStatus(d.veg_status)
        except ValueError:
            veg = VegStatus.UNKNOWN

        flags = []
        for f in d.visible_flags:
            try:
                flags.append(VisibleFlag(f))
            except ValueError:
                flags.append(VisibleFlag.NONE)

        dishes.append(
            Dish(
                name=d.name,
                category=cat,
                veg_status=veg,
                est_portions=max(1, d.est_portions),
                visible_flags=flags or [VisibleFlag.NONE],
                confidence=min(1.0, max(0.0, d.confidence)),
            )
        )

    allergens = []
    for a in raw_passport.allergen_hints:
        try:
            allergens.append(AllergenHint(a))
        except ValueError:
            allergens.append(AllergenHint.UNKNOWN)

    needs_check = compute_needs_human_check(
        dishes=dishes,
        photo_quality_ok=raw_passport.photo_quality_ok,
        confidence_threshold=conf_threshold,
    )

    return FoodPassport(
        dishes=dishes,
        allergen_hints=allergens,
        photo_quality_ok=raw_passport.photo_quality_ok,
        photo_quality_note=raw_passport.photo_quality_note,
        needs_human_check=needs_check,
        handling=form,
    )


def main() -> None:
    """CLI utility to test vision extraction on an image."""
    parser = argparse.ArgumentParser(description="Extract FoodPassport from food photograph.")
    parser.add_argument("image", type=str, help="Path to food image file")
    parser.add_argument(
        "--mode",
        choices=["mock", "live"],
        default="mock",
        help="LLM execution mode (default: mock)",
    )
    parser.add_argument(
        "--cooked-at",
        type=str,
        default="13:00",
        help="Cooked at time (HH:MM)",
    )
    parser.add_argument(
        "--storage",
        type=str,
        default="ambient",
        choices=["hot_held", "ambient", "refrigerated", "unknown"],
    )
    args = parser.parse_args()

    form = DonorForm(
        cooked_at=args.cooked_at,
        storage=StorageCondition(args.storage),
        est_portions=40,
        container=ContainerType.COVERED,
        notes="Automated CLI extraction run",
    )

    passport = extract_passport([args.image], form=form, mode=args.mode)
    print("\n--- Food Passport Extracted ---")
    print(passport.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
