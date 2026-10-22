"""Database seed script for TickTock Meals.

Populates SQLite with a realistic synthetic network of donors, recipients,
and volunteers across North Bengaluru (Yelahanka, Hebbal, Jakkur, Rajanukunte),
plus 30 days of historical listings with planted patterns for analytics.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union

from app.config import get_config
from app.db import (
    create_donor,
    create_historical_listing,
    create_recipient,
    create_volunteer,
    get_connection,
    init_db,
)
from app.models import (
    Donor,
    DonorKind,
    HistoricalListing,
    Recipient,
    Tier,
    VehicleType,
    Volunteer,
)


def get_seed_donors() -> List[Donor]:
    """Return 8 synthetic food donors across North Bengaluru."""
    return [
        Donor(
            id="donor_01",
            name="Yelahanka Campus Central Mess",
            kind=DonorKind.HOSTEL_MESS,
            lat=13.1008,
            lon=77.5963,
            area="Yelahanka",
            typical_surplus_portions=60,
            active_hours="07:30-22:00",
        ),
        Donor(
            id="donor_02",
            name="Hebbal Palace Convention Hall",
            kind=DonorKind.WEDDING_HALL,
            lat=13.0358,
            lon=77.5880,
            area="Hebbal",
            typical_surplus_portions=150,
            active_hours="10:00-23:30",
        ),
        Donor(
            id="donor_03",
            name="Jakkur Grand Pavilion",
            kind=DonorKind.WEDDING_HALL,
            lat=13.0782,
            lon=77.6085,
            area="Jakkur",
            typical_surplus_portions=120,
            active_hours="11:00-23:00",
        ),
        Donor(
            id="donor_04",
            name="Reva Tech Park Canteen",
            kind=DonorKind.CANTEEN,
            lat=13.1250,
            lon=77.6250,
            area="Rajanukunte",
            typical_surplus_portions=45,
            active_hours="08:00-20:00",
        ),
        Donor(
            id="donor_05",
            name="Nandi Veg Dining",
            kind=DonorKind.RESTAURANT,
            lat=13.0950,
            lon=77.5850,
            area="Yelahanka",
            typical_surplus_portions=30,
            active_hours="11:00-22:30",
        ),
        Donor(
            id="donor_06",
            name="North Star Biryani & Curry",
            kind=DonorKind.RESTAURANT,
            lat=13.0420,
            lon=77.5920,
            area="Hebbal",
            typical_surplus_portions=35,
            active_hours="12:00-23:00",
        ),
        Donor(
            id="donor_07",
            name="Sahakar Community Hall",
            kind=DonorKind.EVENT,
            lat=13.0600,
            lon=77.5800,
            area="Sahakarnagar",
            typical_surplus_portions=80,
            active_hours="09:00-22:00",
        ),
        Donor(
            id="donor_08",
            name="Aero IT Corporate Cafeteria",
            kind=DonorKind.CANTEEN,
            lat=13.1150,
            lon=77.6100,
            area="Yelahanka",
            typical_surplus_portions=50,
            active_hours="08:00-21:00",
        ),
    ]


def get_seed_recipients() -> List[Recipient]:
    """Return 12 synthetic recipients (6 Human NGOs, 3 Animal Shelters, 3 Compost)."""
    return [
        # 6 Human NGOs (including 1 veg_only)
        Recipient(
            id="recip_01",
            name="Ashraya Children Home",
            tier=Tier.HUMAN,
            lat=13.1040,
            lon=77.5920,
            area="Yelahanka",
            capacity_portions_per_day=80,
            veg_only=False,
            open_from="08:00",
            open_to="21:00",
            languages=["en", "kn"],
            accepts_cooked=True,
        ),
        Recipient(
            id="recip_02",
            name="Karuna Sneha Care Home",
            tier=Tier.HUMAN,
            lat=13.0390,
            lon=77.5850,
            area="Hebbal",
            capacity_portions_per_day=60,
            veg_only=True,  # STRICTLY VEG ONLY
            open_from="08:30",
            open_to="20:30",
            languages=["kn", "hi"],
            accepts_cooked=True,
        ),
        Recipient(
            id="recip_03",
            name="Sneha Bhavan Youth Shelter",
            tier=Tier.HUMAN,
            lat=13.0750,
            lon=77.6050,
            area="Jakkur",
            capacity_portions_per_day=70,
            veg_only=False,
            open_from="09:00",
            open_to="21:30",
            languages=["en", "kn"],
            accepts_cooked=True,
        ),
        Recipient(
            id="recip_04",
            name="Sparsha Rural Care Center",
            tier=Tier.HUMAN,
            lat=13.1650,
            lon=77.5750,
            area="Rajanukunte",
            capacity_portions_per_day=50,
            veg_only=False,
            open_from="08:00",
            open_to="20:00",
            languages=["kn"],
            accepts_cooked=True,
        ),
        Recipient(
            id="recip_05",
            name="Sanjeevani Seva Trust",
            tier=Tier.HUMAN,
            lat=13.0980,
            lon=77.6010,
            area="Yelahanka",
            capacity_portions_per_day=90,
            veg_only=False,
            open_from="07:30",
            open_to="22:00",
            languages=["en", "kn", "hi"],
            accepts_cooked=True,
        ),
        Recipient(
            id="recip_06",
            name="Mitra Community Shelter",
            tier=Tier.HUMAN,
            lat=13.0480,
            lon=77.5910,
            area="Hebbal",
            capacity_portions_per_day=100,
            veg_only=False,
            open_from="08:00",
            open_to="21:00",
            languages=["en", "hi"],
            accepts_cooked=True,
        ),
        # 3 Animal Shelters
        Recipient(
            id="recip_07",
            name="CUPA North Canine Shelter",
            tier=Tier.ANIMAL,
            lat=13.0810,
            lon=77.6120,
            area="Jakkur",
            capacity_portions_per_day=120,
            veg_only=False,
            open_from="07:00",
            open_to="19:00",
            languages=["en", "kn"],
            accepts_cooked=True,
        ),
        Recipient(
            id="recip_08",
            name="Paws & Whiskers Refuge",
            tier=Tier.ANIMAL,
            lat=13.1110,
            lon=77.5880,
            area="Yelahanka",
            capacity_portions_per_day=80,
            veg_only=False,
            open_from="08:00",
            open_to="19:30",
            languages=["en"],
            accepts_cooked=True,
        ),
        Recipient(
            id="recip_09",
            name="Vana Animal Haven",
            tier=Tier.ANIMAL,
            lat=13.1700,
            lon=77.5680,
            area="Rajanukunte",
            capacity_portions_per_day=100,
            veg_only=False,
            open_from="07:30",
            open_to="18:30",
            languages=["kn"],
            accepts_cooked=True,
        ),
        # 3 Compost / Biogas Facilities
        Recipient(
            id="recip_10",
            name="Hasiru Bio-Methanation Facility",
            tier=Tier.COMPOST,
            lat=13.0320,
            lon=77.5810,
            area="Hebbal",
            capacity_portions_per_day=300,
            veg_only=False,
            open_from="06:00",
            open_to="22:00",
            languages=["en", "kn"],
            accepts_cooked=True,
        ),
        Recipient(
            id="recip_11",
            name="Yelahanka Green Compost Yard",
            tier=Tier.COMPOST,
            lat=13.1200,
            lon=77.6020,
            area="Yelahanka",
            capacity_portions_per_day=250,
            veg_only=False,
            open_from="06:00",
            open_to="21:00",
            languages=["kn"],
            accepts_cooked=True,
        ),
        Recipient(
            id="recip_12",
            name="Rajanukunte Rural Biogas Digester",
            tier=Tier.COMPOST,
            lat=13.1800,
            lon=77.5600,
            area="Rajanukunte",
            capacity_portions_per_day=400,
            veg_only=False,
            open_from="06:00",
            open_to="20:00",
            languages=["kn", "hi"],
            accepts_cooked=True,
        ),
    ]


def get_seed_volunteers() -> List[Volunteer]:
    """Return 10 synthetic couriers (mix of bikes and cars)."""
    return [
        Volunteer(
            id="vol_01",
            name="Aravind Kumar",
            lat=13.0980,
            lon=77.5950,
            vehicle=VehicleType.BIKE,
            max_portions_carry=35,
            languages=["en", "kn"],
            available=True,
            reliability=0.96,
        ),
        Volunteer(
            id="vol_02",
            name="Bhavana Rao",
            lat=13.0410,
            lon=77.5890,
            vehicle=VehicleType.CAR,
            max_portions_carry=120,
            languages=["en", "kn", "hi"],
            available=True,
            reliability=0.94,
        ),
        Volunteer(
            id="vol_03",
            name="Chetan Gowda",
            lat=13.0760,
            lon=77.6040,
            vehicle=VehicleType.BIKE,
            max_portions_carry=40,
            languages=["kn"],
            available=True,
            reliability=0.91,
        ),
        Volunteer(
            id="vol_04",
            name="Deepa Nayak",
            lat=13.1220,
            lon=77.6180,
            vehicle=VehicleType.BIKE,
            max_portions_carry=30,
            languages=["en", "hi"],
            available=True,
            reliability=0.88,
        ),
        Volunteer(
            id="vol_05",
            name="Eshwar Prasad",
            lat=13.0550,
            lon=77.5840,
            vehicle=VehicleType.CAR,
            max_portions_carry=140,
            languages=["en", "kn"],
            available=False,
            reliability=0.92,
        ),
        Volunteer(
            id="vol_06",
            name="Farhan Ahmed",
            lat=13.1020,
            lon=77.5910,
            vehicle=VehicleType.BIKE,
            max_portions_carry=45,
            languages=["hi", "en"],
            available=True,
            reliability=0.95,
        ),
        Volunteer(
            id="vol_07",
            name="Gowri Shankar",
            lat=13.1600,
            lon=77.5700,
            vehicle=VehicleType.BIKE,
            max_portions_carry=35,
            languages=["kn"],
            available=True,
            reliability=0.89,
        ),
        Volunteer(
            id="vol_08",
            name="Harish Bhat",
            lat=13.0850,
            lon=77.6110,
            vehicle=VehicleType.BIKE,
            max_portions_carry=40,
            languages=["en", "kn", "hi"],
            available=True,
            reliability=0.97,
        ),
        Volunteer(
            id="vol_09",
            name="Indira Nair",
            lat=13.0370,
            lon=77.5870,
            vehicle=VehicleType.CAR,
            max_portions_carry=110,
            languages=["en", "hi"],
            available=True,
            reliability=0.93,
        ),
        Volunteer(
            id="vol_10",
            name="Jayanth Murthy",
            lat=13.1180,
            lon=77.6000,
            vehicle=VehicleType.BIKE,
            max_portions_carry=35,
            languages=["kn", "en"],
            available=False,
            reliability=0.87,
        ),
    ]


def generate_history(
    start_date: Optional[datetime] = None,
    days: int = 30,
    seed: int = 42,
) -> List[HistoricalListing]:
    """Generate deterministic historical surplus records with planted patterns.

    Planted Patterns:
    1. Yelahanka Campus Mess (donor_01): Produces ~95-125 portions of cooked_rice on Sundays,
       whereas weekday mean is ~25-45 portions.
    2. Wedding Halls (donor_02, donor_03): Volume and frequency spike on Fridays and Saturdays.
    """
    rng = random.Random(seed)
    base_date = start_date or datetime(2026, 9, 21)  # 30 days starting on a Monday

    history: List[HistoricalListing] = []
    item_counter = 1

    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    for d in range(days):
        current_dt = base_date + timedelta(days=d)
        date_str = current_dt.strftime("%Y-%m-%d")
        weekday = weekday_names[current_dt.weekday()]
        is_sunday = (weekday == "Sunday")
        is_weekend_spike = (weekday in ("Friday", "Saturday"))

        # 1. Yelahanka Campus Central Mess (donor_01)
        if is_sunday:
            # Planted: heavy rice overproduction on Sundays
            portions = rng.randint(95, 125)
            dish = rng.choice(["Steamed Sona Masoori Rice", "Sunday Special Pulao", "Jeera Rice"])
        else:
            # Moderate weekday rice or curry
            portions = rng.randint(25, 45)
            dish = rng.choice(["Steamed White Rice", "Veg Biryani", "Sambar & Rice"])

        history.append(
            HistoricalListing(
                id=f"hist_{item_counter:04d}",
                donor_id="donor_01",
                date=date_str,
                weekday=weekday,
                category="cooked_rice",
                dish_name=dish,
                portions=portions,
                tier_rescued="HUMAN",
                outcome="DELIVERED",
            )
        )
        item_counter += 1

        # Additional donor_01 side dish on some days
        if d % 2 == 0:
            history.append(
                HistoricalListing(
                    id=f"hist_{item_counter:04d}",
                    donor_id="donor_01",
                    date=date_str,
                    weekday=weekday,
                    category="curry_gravy",
                    dish_name="Mixed Vegetable Dal Tadka",
                    portions=rng.randint(20, 35),
                    tier_rescued="HUMAN",
                    outcome="DELIVERED",
                )
            )
            item_counter += 1

        # 2. Wedding Halls: Hebbal Palace (donor_02) & Jakkur Pavilion (donor_03)
        if is_weekend_spike:
            # Friday & Saturday spike
            history.append(
                HistoricalListing(
                    id=f"hist_{item_counter:04d}",
                    donor_id="donor_02",
                    date=date_str,
                    weekday=weekday,
                    category="curry_gravy",
                    dish_name="Royal Paneer Butter Masala & Pulao",
                    portions=rng.randint(90, 160),
                    tier_rescued="HUMAN",
                    outcome="DELIVERED",
                )
            )
            item_counter += 1
            history.append(
                HistoricalListing(
                    id=f"hist_{item_counter:04d}",
                    donor_id="donor_03",
                    date=date_str,
                    weekday=weekday,
                    category="sweet",
                    dish_name="Mysore Pak & Gulab Jamun",
                    portions=rng.randint(60, 110),
                    tier_rescued="HUMAN",
                    outcome="DELIVERED",
                )
            )
            item_counter += 1
        else:
            # Weekday moderate event or reception
            history.append(
                HistoricalListing(
                    id=f"hist_{item_counter:04d}",
                    donor_id="donor_02",
                    date=date_str,
                    weekday=weekday,
                    category="dry_veg",
                    dish_name="Aloo Gobi & Tawa Roti",
                    portions=rng.randint(35, 65),
                    tier_rescued="HUMAN",
                    outcome="DELIVERED",
                )
            )
            item_counter += 1

        # 3. Canteens and Restaurants: Daily consistent surplus
        canteen_donor = "donor_04" if d % 2 == 0 else "donor_08"
        history.append(
            HistoricalListing(
                id=f"hist_{item_counter:04d}",
                donor_id=canteen_donor,
                date=date_str,
                weekday=weekday,
                category="cooked_rice",
                dish_name="South Indian Thali Meal Trays",
                portions=rng.randint(30, 50),
                tier_rescued="HUMAN",
                outcome="DELIVERED",
            )
        )
        item_counter += 1

        # Occasional animal feed or compost cascade in history
        if d % 5 == 0:
            history.append(
                HistoricalListing(
                    id=f"hist_{item_counter:04d}",
                    donor_id="donor_05",
                    date=date_str,
                    weekday=weekday,
                    category="bakery",
                    dish_name="Day-old Whole Wheat Buns & Pav",
                    portions=rng.randint(25, 40),
                    tier_rescued="ANIMAL",
                    outcome="DELIVERED",
                )
            )
            item_counter += 1
        elif d % 9 == 0:
            history.append(
                HistoricalListing(
                    id=f"hist_{item_counter:04d}",
                    donor_id="donor_06",
                    date=date_str,
                    weekday=weekday,
                    category="other",
                    dish_name="Kitchen Vegetable Trimmings",
                    portions=rng.randint(35, 60),
                    tier_rescued="COMPOST",
                    outcome="DELIVERED",
                )
            )
            item_counter += 1

    return history


def seed_database(
    conn_or_path: Optional[Union[sqlite3.Connection, str]] = None,
    reset: bool = True,
    seed: int = 42,
) -> Dict[str, int]:
    """Execute full database seeding, returning count summary."""
    close_after = False
    if isinstance(conn_or_path, sqlite3.Connection):
        conn = conn_or_path
    else:
        conn = get_connection(conn_or_path)
        close_after = True

    try:
        if reset:
            with conn:
                conn.executescript(
                    """
                    DROP TABLE IF EXISTS historical_listings;
                    DROP TABLE IF EXISTS audit_events;
                    DROP TABLE IF EXISTS outbox_messages;
                    DROP TABLE IF EXISTS matches;
                    DROP TABLE IF EXISTS listings;
                    DROP TABLE IF EXISTS volunteers;
                    DROP TABLE IF EXISTS recipients;
                    DROP TABLE IF EXISTS donors;
                    """
                )
        init_db(conn)

        donors = get_seed_donors()
        recipients = get_seed_recipients()
        volunteers = get_seed_volunteers()
        history = generate_history(seed=seed)

        for d in donors:
            create_donor(d, conn=conn)

        for r in recipients:
            create_recipient(r, conn=conn)

        for v in volunteers:
            create_volunteer(v, conn=conn)

        for h in history:
            create_historical_listing(h, conn=conn)

        summary = {
            "donors": len(donors),
            "recipients": len(recipients),
            "volunteers": len(volunteers),
            "historical_listings": len(history),
        }
        return summary
    finally:
        if close_after:
            conn.close()


def main() -> None:
    """CLI entrypoint for database seeding."""
    parser = argparse.ArgumentParser(description="Seed the TickTock Meals database.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop existing tables and re-initialize before seeding.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic generation.",
    )
    args = parser.parse_args()

    counts = seed_database(reset=args.reset, seed=args.seed)
    print("Database seeding completed successfully:")
    for k, v in counts.items():
        print(f"  - {k.replace('_', ' ').capitalize()}: {v}")


if __name__ == "__main__":
    main()
