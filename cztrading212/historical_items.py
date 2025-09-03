from typing import Any, Dict, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from .trading212_client import Trading212Client

class HistoricalItem:
    def __init__(self, client: 'Trading212Client'):
        self._client = client

    def historical_order_data(self, cursor: int, ticker: str, limit: int) -> Dict[str, Any]:
        return self._client._request("GET", f"/api/v0/equity/history/orders?cursor={cursor}&ticker={ticker}&limit={limit}")

    def paid_out_dividends(self, cursor: int, ticker: str, limit: int) -> Dict[str, Any]:
        return self._client._request("GET", f"/api/v0/equity/orders/{id}")

    def exports_list(self) -> Dict[str, Any]:
        return self._client._request("GET", "/api/v0/history/exports")
    
    def export_csv(self, payload: dict) -> Dict[str, Any]:
        return self._client._request("POST", "/api/v0/history/exports", json_body=payload, headers={"Content-Type": "application/json"})

    def transaction_list(self, cursor: int, time: datetime, limit: int) -> Dict[str, Any]:
        return self._client._request("GET", f"/api/v0/history/transactions?cursor={cursor}&time={time}&limit={limit}")