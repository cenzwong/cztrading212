"""Read a demo account summary using environment-provided credentials.

Run deliberately: TRADING212_API_KEY=... TRADING212_API_SECRET=... python examples/read_account.py
This example performs a broker read when executed.
"""

from cztrading212 import Trading212
from cztrading212.serialization import dumps


def main() -> None:
    with Trading212.from_env(environment="demo") as client:
        result = client.services.call_with_metadata("getAccountSummary")
        print(dumps(result))


if __name__ == "__main__":
    main()
