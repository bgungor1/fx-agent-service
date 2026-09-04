# Code Review — `tool.py`

**Date:** 2026-09-02  
**File:** `tool.py` — 86-line FastAPI currency-conversion service

---

## Summary

The service runs and its core logic is sound. Four issues will cause real harm to a paying customer; one of them must be fixed before this ships.

---

## Findings (most dangerous first)

### 1 · Silent `0.0` on any failure — the service lies to its caller 🔴 FIX TONIGHT

**What is wrong:** The `except Exception` block (line 71) catches every possible failure — upstream timeout, bad JSON, network drop — and returns HTTP 200 with `rate: 0.0` and `result: 0.0`.

**What it does to a customer:** The agent runtime receives a success response and tells the customer "250 EUR is 0 TRY." In a financial service, a confident wrong answer is worse than an honest failure. The customer may execute a transaction at a fictitious rate; no alarm fires because the HTTP status is 200 and the payload is structurally valid.

**How to verify:**
```bash
# Edit UPSTREAM to a dead host, start the service, then:
curl "http://localhost:8000/tools/convert?amount=250&from_=EUR&to=TRY"
# Returns HTTP 200  {"rate": 0.0, "result": 0.0}
```

**Fix (one line):** Delete the `except` block entirely and let FastAPI return 500, or replace it with `raise HTTPException(status_code=502, detail=str(exc))`.

---

### 2 · Cache key ignores the date — one query poisons every future query 🔴

**What is wrong:** Line 28 builds the key as `f"{base}-{target}"`. The requested date is not part of the key.

**What it does to a customer:** A request for EUR→TRY on `2010-01-15` (rate ≈ 2.1) writes `2.1` to the cache. The very next request for today's EUR→TRY (rate ≈ 35) hits that cache entry and returns `2.1`. The customer converts at a rate that is 15× wrong with no error signal.

**How to verify:**
```bash
curl "http://localhost:8000/tools/convert?amount=1&from_=EUR&to=TRY&on=2010-01-15"
curl "http://localhost:8000/tools/convert?amount=1&from_=EUR&to=TRY"
# Second call returns the 2010 rate, not today's
```

**Fix:** `key = f"{base}-{target}-{on or 'latest'}"`. Also add a TTL (see finding 3).

---

### 3 · "Latest" cache never expires — rates become arbitrarily stale 🟠

**What is wrong:** `_cache` is a plain `dict` with no eviction and no TTL. Once a `"EUR-TRY"` entry is written it is never refreshed.

**What it does to a customer:** If the service runs for three days, every "latest" request on day three returns Monday's closing rate. The customer is executing live conversions on stale data with no indication anything is wrong. Because `rate_date` is also wrong (finding 4), the caller cannot detect the staleness either.

**How to verify:** Start the service, note the returned rate, stop the upstream (or mock time forward 24 hours), query again — the same number comes back and the upstream is never called.

**Fix:** `from cachetools import TTLCache; _cache = TTLCache(maxsize=512, ttl=3600)`.

---

### 4 · `rate_date` is fabricated — the service reports a date it never read 🟠

**What is wrong:** Line 44 returns `str(on or date.today())` — the date the *caller requested*, not the date the upstream actually published. The upstream JSON body's `"date"` field is never read.

**What it does to a customer:** The customer requests Saturday's rate. ECB publishes no data on weekends, so the fallback returns Friday's rate. The service returns Friday's correct number but labels it Saturday. The customer — and every downstream audit trail — believes a Saturday-specific rate exists. Any reconciliation against the true ECB data will fail.

**How to verify:**
```bash
# Request any recent Sunday
curl "http://localhost:8000/tools/convert?amount=1&from_=EUR&to=TRY&on=2026-08-30"
# rate_date in response: "2026-08-30" (Sunday — ECB publishes nothing)
# Actual published rate date in Frankfurter response body: 2026-08-29 (Friday)
```

**Fix:** Read `payload["date"]` from the upstream response and return that instead.

---

## One thing that looks suspicious but is fine

**The weekend/holiday fallback (lines 36–40):** When the upstream returns no rates for the requested date, the code silently retries with `"latest"`. This looks like it could swallow errors, but it is the correct behaviour: ECB publishes nothing on non-business days, and returning the most recent available rate is the right semantic for a conversion tool. The only real problem here is finding 4 — the returned date is mislabelled. The fallback itself is not a defect.

---

## Tonight's single fix

**Finding 1 — remove the silent `0.0` return.**

Every other finding produces a wrong number. This one produces a wrong number *that looks correct*. No monitoring system, no alerting rule, and no caller can distinguish `rate: 0.0` from a legitimate zero result. The fix takes thirty seconds, carries zero regression risk, and eliminates the entire class of "service failed invisibly while the customer kept transacting."
