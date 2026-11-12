"""Published subscription tiers and their current product limits."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    monthly_usd_cents: int
    daily_ai_analyses: int
    custom_setups_per_day: int | None
    monthly_ai_credits: int
    mt5_account_limit: int


PLANS = {
    "pro": Plan(
        id="pro",
        name="Pro",
        monthly_usd_cents=4_000,
        daily_ai_analyses=12,
        custom_setups_per_day=2,
        monthly_ai_credits=0,
        mt5_account_limit=1,
    ),
    "ultimate": Plan(
        id="ultimate",
        name="Ultimate",
        monthly_usd_cents=9_000,
        daily_ai_analyses=24,
        custom_setups_per_day=None,
        monthly_ai_credits=0,
        mt5_account_limit=1,
    ),
    "platinum": Plan(
        id="platinum",
        name="Platinum",
        monthly_usd_cents=29_099,
        daily_ai_analyses=24,
        custom_setups_per_day=None,
        monthly_ai_credits=5_000,
        mt5_account_limit=10,
    ),
}

TRIAL_PLAN = "ultimate"
TRIAL_DAYS = 7
AI_FEATURES_ENABLED = False
PAID_PLANS_ENABLED = os.getenv("PAID_PLANS_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
}


def plan_public_data(plan: Plan) -> dict:
    data = asdict(plan)
    data["ai_features_enabled"] = AI_FEATURES_ENABLED
    data["monthly_price_usd"] = f"{plan.monthly_usd_cents / 100:.2f}"
    return data
