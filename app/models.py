"""Domain models for TickTock Meals.

Defines Pydantic v2 models for donors, recipients, volunteers,
listings, matches, outbox messages, audit events, and historical analytics.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class DonorKind(str, Enum):
    HOSTEL_MESS = "hostel_mess"
    CANTEEN = "canteen"
    WEDDING_HALL = "wedding_hall"
    RESTAURANT = "restaurant"
    EVENT = "event"


class Tier(str, Enum):
    HUMAN = "HUMAN"
    ANIMAL = "ANIMAL"
    COMPOST = "COMPOST"
    DISCARD = "DISCARD"
    HUMAN_RESCUE = "HUMAN_RESCUE"
    ANIMAL_FEED = "ANIMAL_FEED"
    COMPOST_BIOGAS = "COMPOST_BIOGAS"
    DISCARD_UNSAFE = "DISCARD_UNSAFE"


class VehicleType(str, Enum):
    NONE = "none"
    BIKE = "bike"
    CAR = "car"


class ListingStatus(str, Enum):
    LISTED = "LISTED"
    VERIFIED = "VERIFIED"
    MATCHED = "MATCHED"
    ACCEPTED = "ACCEPTED"
    PICKED_UP = "PICKED_UP"
    DELIVERED = "DELIVERED"
    EXPIRED = "EXPIRED"
    RECLASSIFIED = "RECLASSIFIED"


class DishCategory(str, Enum):
    COOKED_RICE = "cooked_rice"
    CURRY_GRAVY = "curry_gravy"
    DRY_VEG = "dry_veg"
    DAIRY = "dairy"
    BAKERY = "bakery"
    FRUIT_VEG_RAW = "fruit_veg_raw"
    PACKAGED = "packaged"
    SWEET = "sweet"
    BEVERAGE = "beverage"
    OTHER = "other"


class VegStatus(str, Enum):
    VEG = "veg"
    NON_VEG = "non_veg"
    EGG = "egg"
    UNKNOWN = "unknown"


class VisibleFlag(str, Enum):
    SPILLAGE = "spillage"
    DISCOLORATION = "discoloration"
    MOLD = "mold"
    OFF_VISUAL = "off_visual"
    CONTAMINATION_RISK = "contamination_risk"
    NONE = "none"


class AllergenHint(str, Enum):
    NUTS = "nuts"
    DAIRY = "dairy"
    GLUTEN = "gluten"
    EGG = "egg"
    SOY = "soy"
    SESAME = "sesame"
    UNKNOWN = "unknown"


class StorageCondition(str, Enum):
    HOT_HELD = "hot_held"
    AMBIENT = "ambient"
    REFRIGERATED = "refrigerated"
    UNKNOWN = "unknown"


class ContainerType(str, Enum):
    OPEN = "open"
    COVERED = "covered"
    SEALED = "sealed"
    UNKNOWN = "unknown"


class DonorForm(BaseModel):
    """Donor self-reported handling form submitted alongside surplus food photos."""
    cooked_at: str = Field(..., description="Estimated time cooked (e.g. '12:30' or '2026-10-22T12:30:00')")
    storage: StorageCondition = Field(default=StorageCondition.UNKNOWN, description="Storage condition after prep")
    est_portions: Optional[int] = Field(default=None, description="Donor self-reported portions estimate")
    container: ContainerType = Field(default=ContainerType.COVERED, description="Packaging/container state")
    notes: Optional[str] = Field(default=None, description="Free text notes from donor")


class Dish(BaseModel):
    """Distinct food item identified from donor images."""
    name: str = Field(..., description="Recognized dish name")
    category: DishCategory = Field(..., description="Dish category classification")
    veg_status: VegStatus = Field(default=VegStatus.VEG, description="Vegetarian/egg/non-veg status")
    est_portions: int = Field(..., ge=1, description="Estimated number of meal portions")
    visible_flags: List[VisibleFlag] = Field(
        default_factory=lambda: [VisibleFlag.NONE],
        description="Visual quality indicators observed in image",
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence score (0.0 to 1.0)")


class FoodPassport(BaseModel):
    """Structured verifiable Food Passport created from photos and handling details."""
    dishes: List[Dish] = Field(default_factory=list, description="List of recognized dishes")
    allergen_hints: List[AllergenHint] = Field(
        default_factory=list,
        description="Visual allergen guesses (nuts, dairy, gluten, egg, soy, sesame, unknown)",
    )
    allergen_note: str = Field(
        default="Allergen hints are visual estimates only and must be verified by volunteers.",
        description="Mandatory disclaimer for allergen indicators",
    )
    photo_quality_ok: bool = Field(default=True, description="Whether image resolution and lighting are adequate")
    photo_quality_note: str = Field(default="Clear image", description="Photo clarity details")
    needs_human_check: bool = Field(
        default=False,
        description="True if confidence is low, quality is poor, or visible risk flags are detected",
    )
    handling: DonorForm = Field(..., description="Donor self-reported handling metadata")


class Donor(BaseModel):
    """Food donor profile (e.g., college mess, marriage hall, canteen)."""
    id: str = Field(..., description="Unique donor identifier (e.g. 'donor_01')")
    name: str = Field(..., description="Fictional organization name")
    kind: DonorKind = Field(..., description="Category of donor")
    lat: float = Field(..., description="Latitude coordinate in North Bengaluru")
    lon: float = Field(..., description="Longitude coordinate in North Bengaluru")
    area: str = Field(..., description="Locality (e.g. 'Yelahanka', 'Hebbal')")
    typical_surplus_portions: int = Field(..., ge=1, description="Average surplus portions per event")
    active_hours: str = Field(..., description="Operating hours window (e.g. '07:00-22:00')")


class Recipient(BaseModel):
    """Surplus food recipient (shelter, animal shelter, compost/biogas unit)."""
    id: str = Field(..., description="Unique recipient identifier (e.g. 'recip_01')")
    name: str = Field(..., description="Fictional organization name")
    tier: Tier = Field(..., description="Target tier: HUMAN, ANIMAL, or COMPOST")
    lat: float = Field(..., description="Latitude coordinate")
    lon: float = Field(..., description="Longitude coordinate")
    area: str = Field(..., description="Locality (e.g. 'Jakkur', 'Rajanukunte')")
    capacity_portions_per_day: int = Field(..., ge=1, description="Maximum portions recipient can absorb daily")
    veg_only: bool = Field(default=False, description="True if recipient strictly accepts vegetarian food only")
    open_from: str = Field(..., description="Opening time HH:MM (e.g. '09:00')")
    open_to: str = Field(..., description="Closing time HH:MM (e.g. '21:00')")
    languages: List[str] = Field(default_factory=lambda: ["en"], description="Languages understood by coordinator")
    accepts_cooked: bool = Field(default=True, description="Whether ready-to-eat cooked meals are accepted")


class Volunteer(BaseModel):
    """Food rescue courier volunteer."""
    id: str = Field(..., description="Unique volunteer identifier (e.g. 'vol_01')")
    name: str = Field(..., description="Fictional volunteer name")
    lat: float = Field(..., description="Current latitude")
    lon: float = Field(..., description="Current longitude")
    vehicle: VehicleType = Field(default=VehicleType.BIKE, description="Mode of transport")
    max_portions_carry: int = Field(..., ge=1, description="Maximum portions carry capacity")
    languages: List[str] = Field(default_factory=lambda: ["en"], description="Communication languages")
    available: bool = Field(default=True, description="Current availability status")
    reliability: float = Field(default=0.90, ge=0.0, le=1.0, description="Reliability score between 0 and 1")


class Listing(BaseModel):
    """Surplus food donation post."""
    id: str = Field(..., description="Unique listing identifier")
    donor_id: str = Field(..., description="Donor identifier reference")
    created_at: datetime = Field(..., description="Listing creation timestamp")
    photo_path: Optional[str] = Field(default=None, description="Path to donor food photograph")
    passport: Optional[Dict[str, Any]] = Field(default=None, description="Structured FoodPassport JSON")
    safety: Optional[Dict[str, Any]] = Field(default=None, description="Safety assessment verdict JSON")
    tier: Tier = Field(default=Tier.HUMAN, description="Current triage tier")
    status: ListingStatus = Field(default=ListingStatus.LISTED, description="State machine status")
    expires_at: Optional[datetime] = Field(default=None, description="Safe-until expiration timestamp")


class Match(BaseModel):
    """Assignment linking a listing to a recipient and courier volunteer."""
    id: str = Field(..., description="Unique match identifier")
    listing_id: str = Field(..., description="Listing identifier reference")
    recipient_id: str = Field(..., description="Recipient identifier reference")
    volunteer_id: Optional[str] = Field(default=None, description="Volunteer identifier reference")
    score: float = Field(default=0.0, description="Match algorithm aggregate score")
    score_breakdown: Optional[Dict[str, float]] = Field(default=None, description="Score component weights")
    why_this_match: Optional[str] = Field(default=None, description="Human-readable explanation of matching rationale")
    status: str = Field(default="PROPOSED", description="PROPOSED, ACCEPTED, DECLINED, TIMED_OUT, CANCELLED")
    created_at: datetime = Field(..., description="Timestamp match was generated")
    responded_at: Optional[datetime] = Field(default=None, description="Timestamp recipient/volunteer replied")


class OutboxMessage(BaseModel):
    """Simulated notification dispatch for coordinators and volunteers."""
    id: str = Field(..., description="Unique message identifier")
    recipient_or_volunteer_id: str = Field(..., description="Target entity ID")
    role: Literal["recipient", "volunteer"] = Field(..., description="Role of the recipient")
    language: str = Field(default="en", description="Message language (en, kn, hi)")
    message_text: str = Field(..., description="Formatted message body")
    status: Literal["queued", "delivered_sim", "accepted", "declined", "timed_out"] = Field(
        default="queued", description="Outbox delivery and response state"
    )
    created_at: datetime = Field(..., description="Message queued timestamp")
    match_id: Optional[str] = Field(default=None, description="Associated match reference")


class AuditEvent(BaseModel):
    """Tamper-evident audit trail event."""
    id: str = Field(..., description="Unique event identifier")
    timestamp: datetime = Field(..., description="Event timestamp")
    event_type: str = Field(..., description="Event category (e.g. 'LISTING_CREATED', 'RECLASSIFIED')")
    entity_type: str = Field(..., description="Associated entity type (e.g. 'listing', 'match')")
    entity_id: str = Field(..., description="Identifier of associated entity")
    payload: Optional[Dict[str, Any]] = Field(default=None, description="Structured event metadata")
    prev_hash: Optional[str] = Field(default=None, description="SHA-256 hash of previous audit event")
    hash: Optional[str] = Field(default=None, description="SHA-256 hash of this event entry")


class HistoricalListing(BaseModel):
    """Historical surplus log used for source-level prevention insights."""
    id: str = Field(..., description="Unique history record identifier")
    donor_id: str = Field(..., description="Reference donor identifier")
    date: str = Field(..., description="Event date YYYY-MM-DD")
    weekday: str = Field(..., description="Day of the week (e.g. 'Sunday')")
    category: str = Field(..., description="Food category (e.g. 'cooked_rice', 'curry_gravy')")
    dish_name: str = Field(..., description="Name of dish")
    portions: int = Field(..., ge=1, description="Quantity in portions")
    tier_rescued: str = Field(..., description="Tier that ultimately accepted the food")
    outcome: str = Field(default="DELIVERED", description="Terminal state (e.g. 'DELIVERED', 'EXPIRED')")
