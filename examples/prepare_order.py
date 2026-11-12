"""Prepare an order locally; this example does not submit an order or use credentials."""

from decimal import Decimal

from cztrading212.application.trading import Trading
from cztrading212.serialization import dumps


def main() -> None:
    draft = Trading().prepare_limit(
        ticker="AAPL_US_EQ", quantity=Decimal("-0.1"), limit_price=Decimal("150.00")
    )
    print(dumps({"operation_id": draft.operation_id, "body": draft.request_body()}))


if __name__ == "__main__":
    main()
