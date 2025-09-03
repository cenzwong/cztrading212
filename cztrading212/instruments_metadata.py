from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .trading212_client import Trading212Client

class InstrumentsMetadata:
    def __init__(self, client: 'Trading212Client'):
        self._client = client

    def exchange_list(self) -> Dict[str, Any]:
        """
        GET /api/v0/equity/metadata/exchanges
        """
        return self._client._request("GET", "/api/v0/equity/metadata/exchanges")

    def instrument_list(self) -> Dict[str, Any]:
        """
        GET /api/v0/equity/metadata/instruments
        """
        return self._client._request("GET", f"/api/v0/equity/metadata/instruments")
