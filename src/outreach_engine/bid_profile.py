from __future__ import annotations

from typing import Any


DEFAULT_BID_PROFILE: dict[str, Any] = {
    "company_name": "",
    "tone": "British professional, warm, understated and commercially confident",
    "target_discount_percent": 10,
    "company_overview": "",
    "experience": "",
    "strengths": "",
    "standard_assumptions": "",
}


def normalise_bid_profile(payload: object) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    profile = dict(DEFAULT_BID_PROFILE)
    for key in (
        "company_name",
        "tone",
        "company_overview",
        "experience",
        "strengths",
        "standard_assumptions",
    ):
        value = source.get(key, profile[key])
        profile[key] = str(value).strip()[:12_000]
    try:
        discount = int(source.get("target_discount_percent", 10))
    except (TypeError, ValueError):
        discount = 10
    profile["target_discount_percent"] = max(0, min(discount, 50))
    return profile
