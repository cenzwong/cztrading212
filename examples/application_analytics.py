"""Read-only analytics example; configure credentials as described in the README."""

from decimal import Decimal

from cztrading212 import Trading212

with Trading212.from_env() as client:
    concentration = client.app.risk.concentration()
    print("Position value:", concentration.positions_value, concentration.snapshot.currency)
    print("Concentration HHI:", concentration.hhi)

    income = client.app.dividends.income_report(max_pages=20)
    for total in income.by_currency:
        print("Dividend income:", total.currency, total.amount, total.count)

    matches = client.app.instruments.search("Apple", limit=5)
    for match in matches.matches:
        print("Instrument:", match.instrument.ticker, match.instrument.isin)

    # Replace these illustrative identifiers with tickers in your account and
    # broker metadata. This creates value suggestions, never order drafts.
    # plan = client.app.rebalancing.plan(target_weights={
    #     "AAPL_US_EQ": Decimal("0.4"), "VUSA_GB_EQ": Decimal("0.6")
    # })
    # print(plan.entries)
    _ = Decimal  # Used in the illustrative target above.
