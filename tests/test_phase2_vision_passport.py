"""Unit tests for Phase 2: Gemini Vision to Food Passport extraction."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock
from PIL import Image
import pytest

from app.llm.vision import (
    compute_needs_human_check,
    extract_passport,
    preprocess_image,
)
from app.models import (
    AllergenHint,
    ContainerType,
    Dish,
    DishCategory,
    DonorForm,
    StorageCondition,
    VegStatus,
    VisibleFlag,
)


@pytest.fixture
def sample_donor_form() -> DonorForm:
    return DonorForm(
        cooked_at="12:30",
        storage=StorageCondition.AMBIENT,
        est_portions=35,
        container=ContainerType.COVERED,
        notes="Freshly packed surplus trays",
    )


# 1. Table-driven test for the 6 fixtures
FIXTURE_EXPECTATIONS = [
    (
        "tests/fixtures/cooked_rice_and_sambar.jpg",
        {DishCategory.COOKED_RICE, DishCategory.CURRY_GRAVY},
        False,  # needs_human_check
        True,   # photo_quality_ok
    ),
    (
        "tests/fixtures/biryani.jpg",
        {DishCategory.COOKED_RICE},
        False,
        True,
    ),
    (
        "tests/fixtures/packaged_bread.jpg",
        {DishCategory.BAKERY},
        False,
        True,
    ),
    (
        "tests/fixtures/curd_rice_discoloration.jpg",
        {DishCategory.DAIRY},
        True,  # discoloration forces human check
        True,
    ),
    (
        "tests/fixtures/fruit_salad.jpg",
        {DishCategory.FRUIT_VEG_RAW},
        False,
        True,
    ),
    (
        "tests/fixtures/blurry_photo.jpg",
        {DishCategory.OTHER},
        True,  # blurry + off_visual forces human check
        False,
    ),
]


@pytest.mark.parametrize(
    "img_path,expected_categories,expected_human_check,expected_quality_ok",
    FIXTURE_EXPECTATIONS,
)
def test_six_fixtures_extract_expected_categories_and_flags(
    img_path: str,
    expected_categories: set,
    expected_human_check: bool,
    expected_quality_ok: bool,
    sample_donor_form: DonorForm,
):
    """Each fixture yields its expected dish categories, quality status, and human check flags."""
    passport = extract_passport(
        photos=[img_path],
        form=sample_donor_form,
        mode="mock",
    )

    actual_categories = {d.category for d in passport.dishes}
    assert expected_categories.issubset(actual_categories)
    assert passport.photo_quality_ok == expected_quality_ok
    assert passport.needs_human_check == expected_human_check
    assert passport.handling.cooked_at == sample_donor_form.cooked_at


def test_needs_human_check_logic():
    """Verify that mold, discoloration, or low confidence always forces human verification."""
    # Clean dish
    dish_clean = Dish(
        name="Rice",
        category=DishCategory.COOKED_RICE,
        veg_status=VegStatus.VEG,
        est_portions=20,
        visible_flags=[VisibleFlag.NONE],
        confidence=0.90,
    )
    assert compute_needs_human_check([dish_clean], photo_quality_ok=True) is False

    # Mold flag present
    dish_mold = Dish(
        name="Bread",
        category=DishCategory.BAKERY,
        veg_status=VegStatus.VEG,
        est_portions=10,
        visible_flags=[VisibleFlag.MOLD],
        confidence=0.92,
    )
    assert compute_needs_human_check([dish_mold], photo_quality_ok=True) is True

    # Discoloration flag present
    dish_disc = Dish(
        name="Curd",
        category=DishCategory.DAIRY,
        veg_status=VegStatus.VEG,
        est_portions=15,
        visible_flags=[VisibleFlag.DISCOLORATION],
        confidence=0.88,
    )
    assert compute_needs_human_check([dish_disc], photo_quality_ok=True) is True

    # Low confidence (< 0.6)
    dish_low_conf = Dish(
        name="Mystery Curry",
        category=DishCategory.CURRY_GRAVY,
        veg_status=VegStatus.UNKNOWN,
        est_portions=15,
        visible_flags=[VisibleFlag.NONE],
        confidence=0.45,
    )
    assert compute_needs_human_check([dish_low_conf], photo_quality_ok=True) is True

    # Poor photo quality forces check even if dishes look fine
    assert compute_needs_human_check([dish_clean], photo_quality_ok=False) is True


def test_exif_removal():
    """Verify that EXIF metadata is completely stripped during image preprocessing."""
    # Create an image and inject EXIF tags
    base_img = Image.new("RGB", (300, 300), color=(100, 150, 200))
    exif = base_img.getexif()
    exif[0x010E] = "Sensitive User Location / Donor Tag"
    exif[0x0110] = "Donor Mobile Phone Model"

    in_io = io.BytesIO()
    base_img.save(in_io, format="JPEG", exif=exif)
    raw_with_exif = in_io.getvalue()

    # Confirm raw image has EXIF tags
    loaded_raw = Image.open(io.BytesIO(raw_with_exif))
    assert bool(loaded_raw.getexif()) is True
    assert 0x010E in loaded_raw.getexif()

    # Preprocess image
    cleaned_bytes, had_exif = preprocess_image(raw_with_exif, max_side=1024)
    assert had_exif is True

    # Verify output bytes have NO EXIF tags
    cleaned_img = Image.open(io.BytesIO(cleaned_bytes))
    clean_exif = cleaned_img.getexif()
    assert len(clean_exif) == 0


def test_image_resizing_respects_max_side():
    """Verify that large images are downscaled to max 1024 px on longest side."""
    large_img = Image.new("RGB", (2000, 1500), color=(50, 120, 200))
    in_io = io.BytesIO()
    large_img.save(in_io, format="JPEG")

    cleaned_bytes, _ = preprocess_image(in_io.getvalue(), max_side=1024)
    result_img = Image.open(io.BytesIO(cleaned_bytes))

    width, height = result_img.size
    assert max(width, height) <= 1024
    # Check aspect ratio preservation (4:3)
    assert width == 1024
    assert height == 768


def test_photo_count_limits(sample_donor_form: DonorForm):
    """Enforces between 1 and 3 photos."""
    with pytest.raises(ValueError, match="Must provide between 1 and 3 food photos"):
        extract_passport(photos=[], form=sample_donor_form, mode="mock")

    four_photos = [
        "tests/fixtures/biryani.jpg",
        "tests/fixtures/biryani.jpg",
        "tests/fixtures/biryani.jpg",
        "tests/fixtures/biryani.jpg",
    ]
    with pytest.raises(ValueError, match="Must provide between 1 and 3 food photos"):
        extract_passport(photos=four_photos, form=sample_donor_form, mode="mock")


def test_malformed_model_output_triggers_retry_and_safe_fallback(
    sample_donor_form: DonorForm,
):
    """If model output is malformed or throws, retry once then return safe fallback with empty dishes."""
    mock_client = MagicMock()
    # Both initial call and retry raise an error
    mock_client.mode = "live"
    mock_client.generate_with_images.side_effect = RuntimeError("Malformed LLM response JSON")

    passport = extract_passport(
        photos=["tests/fixtures/biryani.jpg"],
        form=sample_donor_form,
        client=mock_client,
        mode="live",
    )

    # Must invoke generate_with_images twice (initial attempt + 1 retry)
    assert mock_client.generate_with_images.call_count == 2

    # Must return safe fallback without crashing
    assert passport.needs_human_check is True
    assert passport.photo_quality_ok is False
    assert len(passport.dishes) == 0
    assert AllergenHint.UNKNOWN in passport.allergen_hints
