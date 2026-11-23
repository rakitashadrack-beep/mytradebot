"""Trading Economics calendar integration for post-release NFP/CPI context."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class NewsDataError(RuntimeError):
    """The configured calendar provider did not return usable data."""


def _number(value: Any) -> float | None:
    if value is None:
        return None
    match = re.search(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


def _event_type(event: dict[str, Any]) -> str | None:
    title = f"{event.get('Category', '')} {event.get('Event', '')}".lower()
    if re.search(r"\b(?:cpi|consumer price index)\b", title):
        return "CPI"
    if re.search(r"\bnon[\s-]?farm\b.*\bpayroll|\bpayroll\b.*\bnon[\s-]?farm\b", title):
        return "NFP"
    return None


def _reaction(event_type: str, actual: Any, forecast: Any) -> str:
    actual_value = _number(actual)
    forecast_value = _number(forecast)
    if actual_value is None or forecast_value is None or actual_value == forecast_value:
        return "Neutral / unavailable"
    if actual_value > forecast_value:
        return "USD bullish bias"
    return "USD bearish bias"


def get_nfp_cpi_events(api_key: str, now: datetime | None = None) -> dict[str, Any]:
    if not api_key.strip():
        return {
            "configured": False,
            "provider": "Trading Economics",
            "events": [],
            "message": "Economic news is unavailable. Configure TRADING_ECONOMICS_API_KEY.",
        }

    current_time = now or datetime.now(timezone.utc)
    start_date = (current_time.date() - timedelta(days=14)).isoformat()
    end_date = (current_time.date() + timedelta(days=30)).isoformat()
    url = (
        "https://api.tradingeconomics.com/calendar/country/United%20States/"
        f"{start_date}/{end_date}?f=json"
    )
    request = Request(url, headers={"Authorization": api_key, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read())
    except HTTPError as exc:
        raise NewsDataError(
            f"Trading Economics returned HTTP {exc.code}; check the API key and plan"
        ) from exc
    except (OSError, URLError, TimeoutError) as exc:
        raise NewsDataError("Could not reach Trading Economics") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise NewsDataError("Trading Economics returned invalid calendar data") from exc

    if not isinstance(data, list):
        raise NewsDataError("Trading Economics returned an unexpected calendar response")

    events = []
    for raw_event in data:
        if not isinstance(raw_event, dict):
            continue
        country = str(raw_event.get("Country", "")).casefold()
        if country and country not in {"united states", "united states of america"}:
            continue
        event_type = _event_type(raw_event)
        if event_type is None:
            continue
        actual = raw_event.get("Actual")
        forecast = raw_event.get("Forecast")
        events.append({
            "type": event_type,
            "date": raw_event.get("Date"),
            "event": raw_event.get("Event") or raw_event.get("Category"),
            "actual": actual,
            "forecast": forecast,
            "previous": raw_event.get("Previous"),
            "importance": raw_event.get("Importance"),
            "usd_bias": _reaction(event_type, actual, forecast),
            "released": _number(actual) is not None,
        })

    return {
        "configured": True,
        "provider": "Trading Economics",
        "retrieved_at": current_time.isoformat(),
        "events": events,
        "message": (
            "Bias compares reported actual with consensus forecast. It is not a guaranteed "
            "market direction or an automatic trade instruction."
        ),
    }
