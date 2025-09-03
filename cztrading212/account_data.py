from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .trading212_client import Trading212Client

class AccountData:
    def __init__(self, client: 'Trading212Client'):
        self._client = client

    def fetch_account_cash(self) -> Dict[str, Any]:
        """
        GET /api/v0/equity/account/cash
        """
        return self._client._request("GET", "/api/v0/equity/account/cash")

    def fetch_account_metadata(self) -> Dict[str, Any]:
        """
        GET /api/v0/equity/account/info
        """
        return self._client._request("GET", "/api/v0/equity/account/info")
