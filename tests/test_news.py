import json
from datetime import datetime, timezone

import pytest

import web.news as news
import web.app as web_app


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.content


def test_missing_trading_economics_key_reports_unavailable():
    result = news.get_nfp_cpi_events("")

    assert result["configured"] is False
    assert result["provider"] == "Trading Economics"
    assert result["events"] == []


def test_news_route_reports_calendar_unconfigured_without_calling_provider(monkeypatch):
    monkeypatch.delenv("TRADING_ECONOMICS_API_KEY", raising=False)

    assert web_app.nfp_cpi_news()["configured"] is False


def test_calendar_filters_nfp_cpi_and_reports_post_release_usd_bias(monkeypatch):
    payload = [
        {
            "Country": "United States",
            "Category": "Non Farm Payrolls",
            "Event": "Non Farm Payrolls",
            "Date": "2026-11-06T13:30:00",
            "Actual": "300K",
            "Forecast": "200K",
            "Previous": "150K",
            "Importance": 3,
        },
        {
            "Country": "United States",
            "Category": "Core CPI",
            "Event": "Core CPI MoM",
            "Date": "2026-11-12T13:30:00",
            "Actual": "0.2%",
            "Forecast": "0.3%",
            "Previous": "0.2%",
            "Importance": 3,
        },
        {
            "Country": "United States",
            "Category": "CPI",
            "Event": "CPI YoY",
            "Date": "2026-11-20T13:30:00",
            "Actual": "",
            "Forecast": "3.1%",
            "Previous": "3.0%",
            "Importance": 3,
        },
        {
            "Country": "Canada",
            "Category": "CPI",
            "Event": "CPI YoY",
            "Actual": "2.0%",
            "Forecast": "2.0%",
        },
    ]
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(news, "urlopen", fake_urlopen)
    result = news.get_nfp_cpi_events(
        "private-test-api-key",
        now=datetime(2026, 11, 12, tzinfo=timezone.utc),
    )

    assert result["configured"] is True
    assert [event["type"] for event in result["events"]] == ["NFP", "CPI", "CPI"]
    assert result["events"][0]["usd_bias"] == "USD bullish bias"
    assert result["events"][1]["usd_bias"] == "USD bearish bias"
    assert result["events"][2]["released"] is False
    assert result["events"][2]["usd_bias"] == "Neutral / unavailable"
    request, timeout = calls[0]
    assert request.full_url.startswith(
        "https://api.tradingeconomics.com/calendar/country/United%20States/"
    )
    assert request.get_header("Authorization") == "private-test-api-key"
    assert "private-test-api-key" not in request.full_url
    assert timeout == 15


def test_provider_failure_is_reported_without_hiding_error(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise news.URLError("offline")

    monkeypatch.setattr(news, "urlopen", unavailable)

    with pytest.raises(news.NewsDataError, match="Could not reach Trading Economics"):
        news.get_nfp_cpi_events("test-api-key")
