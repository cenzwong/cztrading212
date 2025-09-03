from typing import Any, Dict, TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .trading212_client import Trading212Client

class Pies:
    def __init__(self, client: 'Trading212Client'):
        self._client = client

    def fetch_all_pies(self) -> Dict[str, Any]:
        """
        GET /api/v0/equity/pies
        """
        return self._client._request("GET", "/api/v0/equity/pies")

    def fetch_a_pie(self, pie_id: str) -> Dict[str, Any]:
        """
        GET /api/v0/equity/pies/{id}
        """
        return self._client._request("GET", f"/api/v0/equity/pies/{pie_id}")

    def update_pie(self, pie_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        POST /api/v0/equity/pies/{id}
        """
        return self._client._request(
            "POST",
            f"/api/v0/equity/pies/{pie_id}",
            json_body=payload or {},
            headers={"Content-Type": "application/json"},
        )
    