import os
import json
from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime
from typing import Dict, Any, Optional, MutableMapping

import httpx
from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from cachetools import TTLCache

_cache: MutableMapping[str, Any] = TTLCache(maxsize=1024, ttl=3600)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def get_upstream_base() -> str:
    base = os.getenv("FX_UPSTREAM_BASE", "https://api.frankfurter.dev/v1").rstrip("/")
    if "api.frankfurter.dev" in base and not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=5.0, follow_redirects=True)
    return _client


# ---------------------------------------------------------------------------
# Application lifecycle — ensures the shared HTTP client is properly closed
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client
    _client = httpx.AsyncClient(timeout=5.0, follow_redirects=True)
    yield
    if _client and not _client.is_closed:
        await _client.aclose()


app = FastAPI(title="Currency Conversion Tool", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Custom exception & exception handlers
# ---------------------------------------------------------------------------

class FXException(Exception):
    def __init__(self, status_code: int, error_code: str, message: str):
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


@app.exception_handler(FXException)
async def fx_exception_handler(request: Request, exc: FXException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error_code, "message": exc.message},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_input", "message": "Invalid or missing query parameters."},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cache_key(date_key: str, from_cur: str, to_cur: str) -> str:
    """
    Embed today's date for 'latest' requests so cached data never outlives
    a day boundary.
    """
    if date_key == "latest":
        date_key = f"latest-{date.today().isoformat()}"
    return f"{date_key}-{from_cur}-{to_cur}"


def _format_amount(amount: Decimal):
    """Return integer representation if no fractional part, else float."""
    return int(amount) if amount == amount.to_integral() else float(amount)


def _build_response(
    amount: Decimal,
    from_cur: str,
    to_cur: str,
    rate: Decimal,
    rate_date: str,
    asked_date: str,
) -> dict:
    result = (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "amount": _format_amount(amount),
        "from": from_cur,
        "to": to_cur,
        "rate": float(rate),
        "result": float(result),
        "rate_date": rate_date,
        "asked_date": asked_date,
        "source": "ECB via frankfurter.dev",
    }


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@app.get("/tools/convert")
async def convert(
    response: Response,
    amount: Decimal = Query(...),
    from_: str = Query("EUR", alias="from"),
    to: str = Query("TRY"),
    date_str: Optional[str] = Query(None, alias="date"),
    on: Optional[str] = Query(None, alias="on"),
):
    # Support both ?date=... and ?on=... (from tool.py template)
    if not date_str and on:
        date_str = on

    # --- amount validation ------------------------------------------------
    if amount <= 0:
        raise FXException(
            400, "invalid_amount",
            "Amount must be a positive number greater than zero.",
        )

    # Reject more than 10 decimal places (per spec)
    if amount.as_tuple().exponent < -10:
        raise FXException(
            400, "too_many_decimals",
            "Amount cannot have more than 10 decimal places.",
        )

    # --- currency normalisation & validation ------------------------------
    from_cur = (from_ or "").strip().upper()
    to_cur = (to or "").strip().upper()

    if not from_cur or not to_cur:
        raise FXException(400, "invalid_currency", "Currency codes must not be empty.")

    if len(from_cur) != 3 or not from_cur.isalpha() or len(to_cur) != 3 or not to_cur.isalpha():
        raise FXException(
            400, "invalid_currency",
            "Currency codes must be 3-letter ISO codes (e.g., EUR, USD).",
        )

    if from_cur == to_cur:
        raise FXException(
            400, "same_currency",
            "Source and target currencies cannot be the same.",
        )

    # --- date validation --------------------------------------------------
    if not date_str or date_str.strip() == "":
        date_str = "latest"

    if date_str == "latest":
        asked_date = date.today().isoformat()
    else:
        try:
            req_date = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
        except ValueError:
            raise FXException(
                400, "invalid_date_format",
                "Date must be in YYYY-MM-DD format.",
            )

        if req_date > date.today():
            raise FXException(
                400, "future_date",
                "Cannot request a conversion rate for a future date.",
            )
        if req_date < date(1999, 1, 4):
            raise FXException(
                400, "date_too_early",
                "ECB rate data is not available before January 4, 1999.",
            )

        date_str = req_date.isoformat()
        asked_date = date_str

    # --- cache lookup -----------------------------------------------------
    key = _cache_key(date_str, from_cur, to_cur)
    if key in _cache:
        cached = _cache[key]
        response.headers["X-Cache"] = "HIT"
        return _build_response(
            amount, from_cur, to_cur,
            rate=cached["rate"],
            rate_date=cached["rate_date"],
            asked_date=asked_date,
        )

    response.headers["X-Cache"] = "MISS"

    # --- upstream request -------------------------------------------------
    upstream_base = get_upstream_base()
    url = f"{upstream_base}/{date_str}"
    http_client = get_client()

    try:
        upstream_resp = await http_client.get(
            url,
            params={"base": from_cur, "symbols": to_cur},
        )

        # 404 from Frankfurter: unknown currency or date out of range
        if upstream_resp.status_code == 404:
            raise FXException(
                404, "not_found",
                "Currency code not found or no rate available for this date.",
            )

        # Any other non-2xx (e.g. upstream 500)
        if upstream_resp.status_code >= 400:
            raise FXException(
                502, "upstream_error",
                f"Upstream returned an unexpected status: {upstream_resp.status_code}.",
            )

        # Guard against non-JSON bodies
        try:
            data = upstream_resp.json()
        except (json.JSONDecodeError, ValueError):
            raise FXException(
                502, "invalid_upstream_response",
                "Upstream returned a non-JSON response.",
            )

        # Guard against unexpected schema (missing keys)
        try:
            rate = Decimal(str(data["rates"][to_cur]))
            rate_date: str = data["date"]
        except KeyError:
            raise FXException(
                404, "not_found",
                f"Currency '{to_cur}' is not available or not supported by ECB.",
            )
        except Exception:
            raise FXException(
                502, "invalid_upstream_response",
                "Upstream response was missing expected fields.",
            )

    except FXException:
        raise
    except httpx.TimeoutException:
        raise FXException(504, "upstream_timeout", "Upstream API timed out.")
    except httpx.RequestError:
        raise FXException(503, "upstream_unreachable", "Upstream API is currently unreachable.")

    # --- cache and respond ------------------------------------------------
    _cache[key] = {"rate": rate, "rate_date": rate_date}

    return _build_response(
        amount, from_cur, to_cur,
        rate=rate,
        rate_date=rate_date,
        asked_date=asked_date,
    )


# ---------------------------------------------------------------------------
# Direct execution support
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)