# TickTock Meals

> **A Generative-AI Food Rescue Triage and Routing Platform**  
> Built for MERNOVA X 2026 | Final-year MCA Expo | Presidency University, Bangalore

---

## Overview

Surplus cooked food from hostel messes, canteens, weddings, and events is thrown away daily while nearby shelters and NGOs face shortages. Traditional listing boards suffer from lack of trust, uncertain food safety, and expiration during transit.

**TickTock Meals** treats food rescue as a **time-critical triage problem**:
1. **Food Passport**: Multimodal analysis converts surplus photos and handling logs into structured records with allergens, veg status, portions, and safety countdowns.
2. **Grounded Safety Engine**: Retrieval over FSSAI/WHO food safety guidelines paired with deterministic guardrails.
3. **Rescue Cascade**: When human consumption windows close, items automatically downgrade to animal feed, then compost/biogas—ensuring zero waste.
4. **Time-Feasible Matching & Routing**: Considers travel ETA, volunteer vehicle capacity, recipient operating hours, and remaining shelf life.
5. **Tamper-Evident Audit Chain**: SHA-256 hash-chained event logs for complete verification and transparency.

---

## Quickstart

```powershell
# Clone and enter directory
cd ticktockmeals

# Set up virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Run test suite
pytest -q
```
