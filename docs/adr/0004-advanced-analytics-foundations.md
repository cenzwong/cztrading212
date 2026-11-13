# ADR 0004: Advanced analytics require explicit historical data

Status: proposed design; no advanced analytics implementation is implied.

## Context

Current account and position responses are independent point-in-time views.
History endpoints do not by themselves establish a complete dated series of
portfolio valuations, external flows, benchmark prices or declared dividends.
Producing historical returns from the current snapshot would fabricate evidence.

## Decision

Keep any future `historical_performance`, `benchmarking`, `advanced_risk` and
`forecasting` modules within the application layer. Add an extension point only
when a concrete consumer and optional data source require it. No external data
provider becomes a mandatory runtime dependency. Inputs must carry the source,
currency, valuation time, retrieval time and completeness described by
[ADR 0003](0003-application-data-provenance.md); calculations follow
[ADR 0002](0002-financial-calculation-semantics.md). Missing or inconsistent
inputs produce an unavailable result or an explicit error, not a plausible
substitute.

| Future capability | Required evidence and conventions |
| --- | --- |
| Historical portfolio performance | Dated, currency-consistent valuations across the entire requested period, with source and missing-interval policy. Current positions alone are insufficient. |
| Time-weighted return | Valuations around external cash flows, identified and dated flows, subperiod and compounding conventions, plus treatment of missing valuations. |
| Money-weighted return / XIRR | Accurate dated external cash flows and opening/closing valuations, numerical solver conventions, and explicit handling of absent or multiple solutions. |
| Benchmark comparison | Optional external benchmark price or total-return series, currency and FX inputs, matching dates, reinvestment and alignment policies. |
| Volatility and Sharpe ratio | Historical return series, observation frequency, annualisation, risk-free rate, missing-data and currency conventions. |
| Maximum drawdown | A sufficiently complete ordered portfolio valuation series and clear peak, trough and recovery conventions. |
| Dividend forecast | Explicit external declarations or caller-provided assumptions, dates and currencies; historical cash payments are not declarations. |

## Consequences

Future data ingestion, persistence, correction and provider interfaces require
separate design and validation. Historical snapshots must record their actual
valuation and provenance, not merely report assembly time. Forecasts must
identify assumptions and must not be presented as broker guarantees. This ADR
creates no analytics, data source, automated trading or order-execution feature.
