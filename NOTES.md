

## Design Decisions & Trade-Offs

### 1. Financial Precision: `Decimal` over `float`
* **Decision:** `amount` and calculations use Python's `Decimal` module. The result is quantized with `ROUND_HALF_UP` to 2 decimal places.
* **Rationale:** IEEE 754 floating-point arithmetic introduces silent inaccuracies (e.g. `0.1 + 0.2 != 0.3`). In currency conversion, rounding errors accumulate into real monetary discrepancies.
* **Trade-off:** Minimal CPU overhead compared to native floats, but essential for customer trust.

### 2. Transparent Rate Dating (The Core Requirement)
* **Decision:** When the ECB has no rate for the requested date (weekends, bank holidays), the endpoint serves the most recent published rate from the upstream and exposes both `asked_date` and `rate_date` in the payload.
* **Rationale:** A paying customer making a transaction must know the date their rate was established. Fabricating a weekend rate or reporting Sunday when the rate is from Friday is misleading. Returning an honest, dated rate allows the LLM caller to inform the user explicitly: *"Using Friday's closing rate of 47.12..."*.

### 3. Rejecting Same Currency Conversions (`from == to`)
* **Decision:** Returns `400 same_currency` rather than returning rate `1.0`.
* **Rationale:** The tool is designed to query ECB exchange rates. Converting EUR to EUR or USD to USD indicates an upstream prompt misunderstanding or an agent logic error. Rejecting it provides immediate feedback to the agent workflow instead of quietly executing a redundant conversion.

### 4. In-Memory Caching Strategy
* **Decision:** In-memory caching using `cachetools.TTLCache(maxsize=1024, ttl=3600)` (with standard dictionary fallback). Keys are composed of `(date, from_currency, to_currency)`. For `"latest"` requests, the current date is injected into the cache key (`latest-YYYY-MM-DD-FROM-TO`).
* **Rationale:** Prevents hammering the upstream Frankfurter API for identical queries while ensuring rates are invalidated when passing midnight, and prevents unbounded memory growth via maxsize and 1-hour TTL.
* **Trade-off:** In-process cache does not survive process restarts and is not shared across multi-worker instances.

### 5. Error Contract & Upstream Fault Isolation
* **Decision:** Distinguish client errors (`400`, `404`) from upstream failures (`502`, `503`, `504`), always returning `{"error": "<code_snake_case>", "message": "<human text>"}`.
* **Rationale:** An AI agent needs predictable machine error codes to decide recovery strategies (e.g. prompt the user vs retry vs alert an engineer).

---

## What I Would Do Next (With More Time)

If preparing this service for high-volume enterprise production:

1. **Distributed Caching (Redis)**: Replace the in-process cache with Redis, adding explicit TTLs (e.g. 1 hour for historical business days, 15 minutes for intraday/latest), LRU eviction, and multi-node coherence.
2. **Circuit Breaker**: Implement `resilience4j` or `pybreaker` pattern around upstream calls. If Frankfurter fails 5 consecutive times, trip the circuit to fail fast without queuing requests during upstream outages.
3. **Observability & Metrics**:
   * Prometheus metrics: request duration histogram, cache hit/miss counter, upstream status code counts.
   * Structured JSON logging with correlation IDs (`trace_id`) propagated across agent calls.
4. **Health & Readiness Endpoints**: Add `/healthz` and `/readyz` probes for Kubernetes / container orchestration.
5. **Rate Limiting**: Add IP and API-token based rate limiting using a token bucket algorithm to prevent DoS attacks.

---

## Research & AI-Assisted Engineering Workflow

Before writing code or prompting an AI assistant, I researched production failure modes in financial/FX systems and idiomatic Python/FastAPI architecture:

1. **Domain Research First:** I analyzed where currency tools typically fail in agent runtimes: IEEE 754 floating-point drift, date misattribution on weekends/holidays, and vague error codes that cause LLMs to hallucinate or misinform customers.
2. **Ecosystem & Best Practices:** Coming from TypeScript, I researched modern Python/FastAPI standards for clean, maintainable services: application lifespan management (`asynccontextmanager`), strict quantization via `Decimal`, and offline mock testing (`respx`).
3. **Establishing Rules Before Generation:** Armed with this research, I defined non-negotiable architectural constraints first. Instead of accepting unconstrained AI code, I steered the assistant strictly within these financial rules.
4. **Critical Review & Verification:** Every generated component was audited against edge cases (such as day-boundary cache pollution and precision limits) and validated with a 100% offline test suite.

---

## Personal Learnings & Challenges Faced (TS/Node.js to Python)

As requested in the prompt, I wanted to highlight a few areas where I had to pause, research, and adapt my TypeScript/Node.js mindset to Python and FastAPI.

**Challenges in Part A (Development):**
1. **Mocking & Testing Paradigms:** In the Jest/Node environment, I frequently use `nock` or `jest.spyOn()` for intercepting HTTP calls. Finding the right Python equivalent (`respx` coupled with `httpx`) and understanding how `pytest` fixtures inject dependencies (like my `clear_cache` fixture) required a paradigm shift. Once understood, I found `pytest` to be incredibly elegant.
2. **Type Validation (Pydantic vs. Zod/Class-Validator):** In NestJS, I rely heavily on DTOs and decorators. Learning how FastAPI seamlessly integrates Pydantic via `Query(...)` and aliases (`alias="from"`) was a learning curve, but it ultimately felt cleaner and required less boilerplate than TypeScript alternatives.
3. **Floating Point Safety:** JavaScript relies on a single `Number` type (Float64). Realizing I had to explicitly import Python's `Decimal` module and carefully handle scientific notation (e.g., `1e-15` via `.as_tuple().exponent`) to strictly enforce the 10-decimal limit was a critical learning moment.

**Challenges in Part B (Code Review):**
1. **Looking Past the Syntax:** Coming from strict TypeScript, my initial instinct when looking at `tool.py` was to critique the lack of type hints or formatting. The challenge was shifting my mindset from "linter" to "business logic reviewer" to spot the real dangers (like the unbounded dictionary and fabricated dates).
2. **The "Single Fix" Dilemma:** Deciding on the *one* thing to fix tonight was an architectural dilemma. The unbounded memory cache (`_cache = {}`) is a ticking time bomb for an OOM crash. However, I ultimately chose the silent `0.0` failure. Realizing that a crashing service (500 error) is safer than a service that lies to customers was my biggest takeaway from this exercise.