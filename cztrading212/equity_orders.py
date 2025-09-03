from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .trading212_client import Trading212Client

class EquityOrders:
    def __init__(self, client: 'Trading212Client'):
        self._client = client

    def fetch_all(self) -> Dict[str, Any]:
        return self._client._request("GET", "/api/v0/equity/orders")

    def fetch_by_id(self, id) -> Dict[str, Any]:
        return self._client._request("GET", f"/api/v0/equity/orders/{id}")
