import os
import pytest
from datetime import date, timedelta
from decimal import Decimal
import respx
import httpx
from fastapi.testclient import TestClient

import main
from main import app, _cache, get_upstream_base


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear memory cache before every test to ensure isolation."""
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Happy path & Business Logic Tests
# ---------------------------------------------------------------------------

@respx.mock
def test_convert_success_standard(client):
    """Test standard conversion with exact expected response schema."""
    base_url = get_upstream_base()
    respx.get(f"{base_url}/2026-08-28").mock(
        return_value=httpx.Response(
            200,
            json={
                "amount": 1.0,
                "base": "EUR",
                "date": "2026-08-28",
                "rates": {"TRY": 47.1234},
            },
        )
    )

    resp = client.get("/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28")
    assert resp.status_code == 200
    data = resp.json()

    assert data["amount"] == 250
    assert data["from"] == "EUR"
    assert data["to"] == "TRY"
    assert data["rate"] == 47.1234
    assert data["result"] == 11780.85
    assert data["rate_date"] == "2026-08-28"
    assert data["asked_date"] == "2026-08-28"
    assert data["source"] == "ECB via frankfurter.dev"
    assert resp.headers.get("X-Cache") == "MISS"


@respx.mock
def test_weekend_holiday_differs_rate_date_and_asked_date(client):
    """
    On weekends/holidays, the upstream returns the previous business day's date.
    The response must make this difference visible.
    """
    base_url = get_upstream_base()
    # User asks for Saturday 2024-03-02, upstream returns Friday 2024-03-01
    respx.get(f"{base_url}/2024-03-02").mock(
        return_value=httpx.Response(
            200,
            json={
                "amount": 1.0,
                "base": "EUR",
                "date": "2024-03-01",
                "rates": {"USD": 1.0825},
            },
        )
    )

    resp = client.get("/tools/convert?amount=100&from=EUR&to=USD&date=2024-03-02")
    assert resp.status_code == 200
    data = resp.json()

    assert data["asked_date"] == "2024-03-02"
    assert data["rate_date"] == "2024-03-01"
    assert data["rate"] == 1.0825
    assert data["result"] == 108.25
    assert data["source"] == "ECB via frankfurter.dev"


@respx.mock
def test_convert_latest_default(client):
    """When date is omitted, it defaults to latest published rates."""
    base_url = get_upstream_base()
    respx.get(f"{base_url}/latest").mock(
        return_value=httpx.Response(
            200,
            json={
                "amount": 1.0,
                "base": "EUR",
                "date": "2026-09-01",
                "rates": {"TRY": 47.5},
            },
        )
    )

    resp = client.get("/tools/convert?amount=100&from=EUR&to=TRY")
    assert resp.status_code == 200
    data = resp.json()

    assert data["asked_date"] == date.today().isoformat()
    assert data["rate_date"] == "2026-09-01"
    assert data["result"] == 4750.0


@respx.mock
def test_convert_with_on_parameter_alias(client):
    """Support 'on' parameter as an alias for 'date' (matching tool.py convention)."""
    base_url = get_upstream_base()
    respx.get(f"{base_url}/2024-03-01").mock(
        return_value=httpx.Response(
            200,
            json={
                "amount": 1.0,
                "base": "EUR",
                "date": "2024-03-01",
                "rates": {"USD": 1.0825},
            },
        )
    )

    resp = client.get("/tools/convert?amount=100&from=EUR&to=USD&on=2024-03-01")
    assert resp.status_code == 200
    assert resp.json()["asked_date"] == "2024-03-01"
    assert resp.json()["rate_date"] == "2024-03-01"


# ---------------------------------------------------------------------------
# Caching Tests
# ---------------------------------------------------------------------------

@respx.mock
def test_repeat_request_does_not_reask_upstream(client):
    """A repeat of the same question should serve from cache without calling upstream."""
    base_url = get_upstream_base()
    route = respx.get(f"{base_url}/2024-01-15").mock(
        return_value=httpx.Response(
            200,
            json={
                "amount": 1.0,
                "base": "EUR",
                "date": "2024-01-15",
                "rates": {"USD": 1.095},
            },
        )
    )

    # First call - MISS
    resp1 = client.get("/tools/convert?amount=100&from=EUR&to=USD&date=2024-01-15")
    assert resp1.status_code == 200
    assert resp1.headers.get("X-Cache") == "MISS"
    assert route.call_count == 1

    # Second call (same currencies and date, different amount) - HIT
    resp2 = client.get("/tools/convert?amount=200&from=EUR&to=USD&date=2024-01-15")
    assert resp2.status_code == 200
    assert resp2.headers.get("X-Cache") == "HIT"
    assert resp2.json()["amount"] == 200
    assert resp2.json()["result"] == 219.0
    assert resp2.json()["source"] == "ECB via frankfurter.dev"
    assert route.call_count == 1  # Upstream was NOT called again


# ---------------------------------------------------------------------------
# Currency Validation Tests
# ---------------------------------------------------------------------------

def test_same_currency_rejected(client):
    """Converting from and to the same currency is rejected with 400 same_currency."""
    resp = client.get("/tools/convert?amount=100&from=EUR&to=EUR")
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "same_currency"
    assert "cannot be the same" in data["message"].lower()


def test_invalid_currency_code_rejected(client):
    """Non-3-letter currency codes are rejected before calling upstream."""
    resp = client.get("/tools/convert?amount=100&from=EUROPE&to=TRY")
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "invalid_currency"


@respx.mock
def test_unknown_currency_upstream_404(client):
    """When upstream returns 404 for an unknown currency code, return 404."""
    base_url = get_upstream_base()
    respx.get(f"{base_url}/latest").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )

    resp = client.get("/tools/convert?amount=100&from=EUR&to=XYZ")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "not_found"


# ---------------------------------------------------------------------------
# Amount Validation Tests
# ---------------------------------------------------------------------------

def test_amount_missing(client):
    """Missing amount triggers validation error."""
    resp = client.get("/tools/convert?from=EUR&to=TRY")
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_input"


def test_amount_zero_or_negative(client):
    """Amount <= 0 is rejected."""
    resp_zero = client.get("/tools/convert?amount=0&from=EUR&to=TRY")
    assert resp_zero.status_code == 400
    assert resp_zero.json()["error"] == "invalid_amount"

    resp_neg = client.get("/tools/convert?amount=-50&from=EUR&to=TRY")
    assert resp_neg.status_code == 400
    assert resp_neg.json()["error"] == "invalid_amount"


def test_amount_more_than_ten_decimal_places(client):
    """Amount with more than 10 decimal places is rejected."""
    resp = client.get("/tools/convert?amount=1.12345678901&from=EUR&to=TRY")
    assert resp.status_code == 400
    assert resp.json()["error"] == "too_many_decimals"


@respx.mock
def test_amount_ten_decimal_places_allowed(client):
    """Amount with up to 10 decimal places is handled with full Decimal precision."""
    base_url = get_upstream_base()
    respx.get(f"{base_url}/2024-01-15").mock(
        return_value=httpx.Response(
            200,
            json={"amount": 1.0, "base": "EUR", "date": "2024-01-15", "rates": {"TRY": 30.0}},
        )
    )

    resp = client.get("/tools/convert?amount=1.1234567890&from=EUR&to=TRY&date=2024-01-15")
    assert resp.status_code == 200
    assert resp.json()["result"] == 33.70


# ---------------------------------------------------------------------------
# Date Validation Tests
# ---------------------------------------------------------------------------

def test_future_date_rejected(client):
    """Future dates are rejected with 400 future_date."""
    future = (date.today() + timedelta(days=5)).isoformat()
    resp = client.get(f"/tools/convert?amount=100&from=EUR&to=TRY&date={future}")
    assert resp.status_code == 400
    assert resp.json()["error"] == "future_date"


def test_date_before_series_starts_rejected(client):
    """Dates prior to ECB series inception (1999-01-04) are rejected."""
    resp = client.get("/tools/convert?amount=100&from=EUR&to=TRY&date=1998-12-31")
    assert resp.status_code == 400
    assert resp.json()["error"] == "date_too_early"


def test_invalid_date_format_rejected(client):
    """Non-ISO date format is rejected."""
    resp = client.get("/tools/convert?amount=100&from=EUR&to=TRY&date=28-08-2026")
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_date_format"


# ---------------------------------------------------------------------------
# Upstream Failure & Resilience Tests
# ---------------------------------------------------------------------------

@respx.mock
def test_upstream_timeout(client):
    """Upstream timeout returns 504 upstream_timeout."""
    base_url = get_upstream_base()
    respx.get(f"{base_url}/latest").mock(side_effect=httpx.TimeoutException("Connection timed out"))

    resp = client.get("/tools/convert?amount=100&from=EUR&to=TRY")
    assert resp.status_code == 504
    assert resp.json()["error"] == "upstream_timeout"


@respx.mock
def test_upstream_500_error(client):
    """Upstream 500 error returns 502 upstream_error."""
    base_url = get_upstream_base()
    respx.get(f"{base_url}/latest").mock(return_value=httpx.Response(500, text="Internal Server Error"))

    resp = client.get("/tools/convert?amount=100&from=EUR&to=TRY")
    assert resp.status_code == 502
    assert resp.json()["error"] == "upstream_error"


@respx.mock
def test_upstream_non_json_response(client):
    """Upstream returning non-JSON HTML returns 502 invalid_upstream_response."""
    base_url = get_upstream_base()
    respx.get(f"{base_url}/latest").mock(
        return_value=httpx.Response(200, text="<html><body>Bad Gateway</body></html>", headers={"content-type": "text/html"})
    )

    resp = client.get("/tools/convert?amount=100&from=EUR&to=TRY")
    assert resp.status_code == 502
    assert resp.json()["error"] == "invalid_upstream_response"


@respx.mock
def test_upstream_unreachable(client):
    """Upstream network connect failure returns 503 upstream_unreachable."""
    base_url = get_upstream_base()
    respx.get(f"{base_url}/latest").mock(side_effect=httpx.ConnectError("Failed to connect"))

    resp = client.get("/tools/convert?amount=100&from=EUR&to=TRY")
    assert resp.status_code == 503
    assert resp.json()["error"] == "upstream_unreachable"
