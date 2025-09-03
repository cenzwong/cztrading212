from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .trading212_client import Trading212Client

class PersonalPortfolio:
    def __init__(self, client: 'Trading212Client'):
        self._client = client

    def positions(self) -> Dict[str, Any]:
        """
        GET /api/v0/equity/portfolio
        """
        return self._client._request("GET", "/api/v0/equity/portfolio")

    def position(self, ticker: str) -> Dict[str, Any]:
        """
        GET /api/v0/equity/portfolio/{ticker}
        """
        return self._client._request("GET", f"/api/v0/equity/portfolio/{ticker}")

    def find_by_ticker(self, ticker: str) -> Dict[str, Any]:
        """
        POST /api/v0/equity/portfolio/ticker
        body: {"ticker": "..."}
        """
        return self._client._request(
            "POST",
            "/api/v0/equity/portfolio/ticker",
            json_body={"ticker": ticker},
            headers={"Content-Type": "application/json"},
        )