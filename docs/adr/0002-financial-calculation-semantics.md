# ADR 0002: Financial calculation semantics

Status: accepted for application-layer development. Existing workflows retain
their public contracts; implementation gaps are identified below.

## Context

Application workflows combine optional broker fields into financial results.
Different modules must not silently use different rounding rules, mix currencies,
or substitute plausible values for missing inputs. The service layer remains the
only source of broker data; these rules do not change raw API contracts.

## Decision

### Decimal and rounding

Use `Decimal` for monetary values, weights, rates and financial calculations;
never pass financial values through binary floating point. Preserve broker
values in the underlying typed models. Additions use `exact_sum`: exact within
its documented 10,000-digit/exponent safety bounds, otherwise raise
`DataUnavailableError`. Ratios and other inherently rounded derived values use
an explicit local context of 34 significant digits and `ROUND_HALF_EVEN`,
independent of the caller's Decimal context. Document the operation order for
multi-step calculations. Do not quantize intermediate or final values to a
currency's minor unit unless a workflow explicitly specifies that operation.
Reject non-finite values and unsupported magnitudes rather than silently
producing a result. Non-financial counts and indices may use integers.

### Currency and valuation

Every monetary result identifies its currency. Sum or compare monetary amounts
only in the same currency. A mismatch or missing currency required for an
aggregate makes that calculation unavailable. Currency conversion requires an
explicit source and target currency, rate, valuation time and rate provenance;
no exchange rate is inferred. A rate is not an executable quote. Group history
by its reported currency rather than adding different currencies. Instrument
trading currency is a proxy for trading-currency exposure, not the issuer's
economic FX exposure. Do not infer sector, geography or industry.

Position wallet values and account totals are broker-reported in account
currency when the response establishes that fact. Account and position reads
are independent and may describe different moments. Preserve their values and
sources; do not force them to reconcile by inventing an adjustment.

### P&L and returns

Prefer reported account investment P&L and position wallet P&L to reconstructed
figures, and label the level and source. Account realised P&L is not the sum of
currently open positions. Display reported FX impact separately; do not add it
to a P&L figure that may already include it. A position's current unrealised
return fraction is its reported unrealised P&L divided by its reported positive
cost in the same valuation currency. A missing, zero or negative cost gives an
unavailable return. A contribution is position unrealised P&L divided by the
reported aggregate unrealised P&L only when the latter is nonzero; contributions
need not sum to one. Current holdings are insufficient for historical growth,
annualised returns, TWR or XIRR.

### Allocation and cash

Portfolio allocation and concentration use the sum of position values as their
denominator and exclude cash. Repeated ticker positions remain distinct unless
aggregation is explicitly requested. For concentration, missing, negative or
currency-inconsistent values are rejected; zero values have zero weight when
the total is positive, and a zero total has unavailable weights and HHI.

A cash-inclusive planner must explicitly select that basis and disclose the
cash amount and its source. Its denominator is the sum of consistently valued
positions plus the explicitly identified total cash amount; it must not reuse
an invested-only denominator or substitute `availableToTrade` for total cash.
The target allocation must explicitly include the cash target, with all target
weights non-negative and summing to one within a documented tolerance. If total
cash is not reliably supplied by the broker, require an explicit caller input
with its currency, valuation time and provenance or reject cash-inclusive
planning. Available cash, reserved cash, and cash in pies must not be added or
interpreted as total cash without an established non-overlapping broker
contract. Neither cash nor a position valuation implies tradability or an
executable price.

### Missing data

Distinguish missing from zero. If a required value, currency or timezone is
missing, or if combining values would require an unstated assumption, raise
`DataUnavailableError` or return a documented unavailable (`None`) metric when
other results remain independently meaningful. Never replace a missing amount
with zero to make an aggregate appear complete. Preserve unknown broker types
and incomplete fields for audit; do not deduplicate records automatically.
Invalid caller arguments raise `ValueError`; exhausted or unsafe pagination
raises `PaginationError`. Document exceptions and unavailable fields per method.

## Consequences and implementation status

Existing allocation and dividend summaries already separate currencies and
preserve Decimal values; division and bounded exact summation use the shared
helpers. Existing portfolio allocation excludes cash. Cash-inclusive planning accepts explicit caller cash valuations; currency
conversion inputs and a uniform missing-data contract across all future modules
are not yet implemented. Each new module must implement and test these decisions;
this ADR is not evidence that every current workflow already enforces them.
