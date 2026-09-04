# Currency Conversion Tool

A lightweight HTTP service designed as an AI agent tool for currency conversions based on European Central Bank (ECB) rates via Frankfurter.

---

## Quick Start (Under 1 Minute)

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the Service
Starts on port `8080` by default (or reads `$PORT` and `$FX_UPSTREAM_BASE`):
```bash
./run.sh
# or: uvicorn main:app --port 8080
```

### 3. Run the Tests (100% Offline)
```bash
./test.sh
# or: pytest test_main.py -v
```
*Note: Tests run entirely offline using mocked HTTP transports without touching the network.*

---

## API Specification

### Endpoint
`GET /tools/convert`

**Query Parameters:**
* `amount` *(required, decimal)*: Amount to convert. Must be > 0 and have at most 10 decimal places.
* `from` *(optional, string, default: "EUR")*: Source 3-letter currency code.
* `to` *(optional, string, default: "TRY")*: Target 3-letter currency code.
* `date` *(optional, string, default: "latest")*: ISO format date (`YYYY-MM-DD`) or `"latest"`. (`on` is also supported as an alias).

#### Example Request
```bash
curl "http://localhost:8080/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
```

#### Success Response (`200 OK`)
```json
{
  "amount": 250,
  "from": "EUR",
  "to": "TRY",
  "rate": 47.1234,
  "result": 11780.85,
  "rate_date": "2026-08-28",
  "asked_date": "2026-08-28",
  "source": "ECB via frankfurter.dev"
}
```

---

## How Edge Cases Are Handled

| Scenario | Behavior / Response | Rationale |
| :--- | :--- | :--- |
| **Weekends / Holidays** (No ECB rate published) | Returns `200 OK` with the rate from the latest available ECB business day. `rate_date` shows the actual publication day (e.g. Friday), while `asked_date` retains the user's requested date (e.g. Saturday). | Never invents a rate; makes the date difference transparent so the agent can inform the customer. |
| **Future Date** | Returns `400 Bad Request` (`error: future_date`). | Financial rates cannot be predicted. |
| **Date Before Series Starts** (< 1999-01-04) | Returns `400 Bad Request` (`error: date_too_early`). | ECB euro reference rates began on January 4, 1999. |
| **Same Currencies** (`from == to`) | Returns `400 Bad Request` (`error: same_currency`). | Rejecting prevents ambiguous zero-spread transactions and upstream misuse. |
| **Non-existent Currency** | Returns `404 Not Found` (`error: not_found`). | Transparent failure when ECB does not support the currency. |
| **Malformed Currency Code** | Returns `400 Bad Request` (`error: invalid_currency`). | Currency codes must be 3-letter alphabetic ISO codes. |
| **Amount Missing** | Returns `400 Bad Request` (`error: invalid_input`). | Required parameter missing. |
| **Amount Zero or Negative** | Returns `400 Bad Request` (`error: invalid_amount`). | Financial amounts must be strictly positive. |
| **Amount Decimal Places** | Up to 10 decimal places are accepted with exact `Decimal` precision. > 10 decimal places returns `400 Bad Request` (`error: too_many_decimals`). | Guards against precision attacks and float serialization inaccuracies. |
| **Slow Upstream / Timeout** | Returns `504 Gateway Timeout` (`error: upstream_timeout`). | 5-second timeout ensures caller is not hung indefinitely. |
| **Upstream 500 / Non-JSON** | Returns `502 Bad Gateway` (`error: upstream_error` / `invalid_upstream_response`). | Distinguishes upstream infrastructure failures from client input errors. |
| **Upstream Unreachable** | Returns `503 Service Unavailable` (`error: upstream_unreachable`). | Host connection failure. |

---

## Machine Error Codes

All errors return a non-2xx status with payload:
```json
{
  "error": "<short_machine_code>",
  "message": "<a sentence a person could read>"
}
```

| HTTP Status | Error Code | Description |
| :--- | :--- | :--- |
| `400` | `invalid_input` | Missing or invalid query parameter structure. |
| `400` | `invalid_amount` | Amount is zero or negative. |
| `400` | `too_many_decimals` | Amount has more than 10 decimal places. |
| `400` | `invalid_currency` | Currency code is empty or not a valid 3-letter code. |
| `400` | `same_currency` | Source and target currencies are identical. |
| `400` | `invalid_date_format` | Date is not formatted as `YYYY-MM-DD`. |
| `400` | `future_date` | Requested date is in the future. |
| `400` | `date_too_early` | Requested date is before ECB data began (1999-01-04). |
| `404` | `not_found` | Currency code not found, not supported by ECB, or no rate available for the requested date. |
| `502` | `upstream_error` | Upstream service returned a 4xx/5xx status. |
| `502` | `invalid_upstream_response`| Upstream returned malformed or non-JSON body. |
| `503` | `upstream_unreachable` | Upstream host is unreachable or connection refused. |
| `504` | `upstream_timeout` | Upstream request timed out (> 5 seconds). |

---

## Caching Strategy

* **TTL In-Memory Cache**: Powered by `cachetools.TTLCache(maxsize=1024, ttl=3600)` (with standard dictionary fallback). Requests are keyed by `(date, from_currency, to_currency)`.
* **Day-Boundary Invalidation**: Requests for `"latest"` embed the current date into their cache key (`latest-YYYY-MM-DD-FROM-TO`), ensuring cached rates never outlive midnight.
* **Network Reduction**: Repeated requests for the same date and currency pair are served immediately from memory without contacting the upstream API.
* **Response Header**: Cached responses include `X-Cache: HIT` (or `X-Cache: MISS`), while strictly keeping the body field `"source": "ECB via frankfurter.dev"`.
