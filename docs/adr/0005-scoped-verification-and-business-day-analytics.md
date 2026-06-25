# ADR-0005: Scoped Verification and Business-Day Analytics

## Status

Accepted (2026-06-25)

## Context

Customer/order operations now rely on OTP verification tokens, and daily analytics reports are generated at 00:10 local time for the previous day. The first implementation allowed unscoped verification tokens to satisfy protected resource checks and interpreted analytics dates with naive local windows while stored timestamps are UTC naive.

## Decision

Use customer-scoped verification as the default identity model. A customer-scoped token may authorize protected resources owned by that customer. An order-scoped token may authorize only that order. A token with neither customer nor order scope cannot authorize protected customer/order resources.

Daily analytics dates are business days interpreted in `REPORT_TIMEZONE`, defaulting to `Asia/Shanghai`. The analytics module converts the business-day window to UTC naive datetimes before querying persisted timestamps.

Failure usage telemetry is part of the orchestrator run contract. If a run raises after entering orchestration, the runtime records a metadata-only failed usage event and then re-raises the original error.

## Consequences

- Callers do not need to bind every customer token to every order, but protected resource checks must pass the resource customer id into the verification module.
- Order-only tokens are intentionally narrower than customer tokens.
- Daily reports match the business schedule even though database timestamps remain UTC naive.
- Usage analytics may include failed runs, but never raw customer messages or full customer replies.