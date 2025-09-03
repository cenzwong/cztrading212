from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import json
import time
import requests
from requests.adapters import HTTPAdapter, Retry
from .account_data import AccountData
from .personal_portfolio import PersonalPortfolio
from .pies import Pies
from .instruments_metadata import InstrumentsMetadata
from .equity_orders import EquityOrders

class Trading212Error(RuntimeError):
    """Unified error for Trading212 client."""
    def __init__(self, message: str, *, status_code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass(frozen=True)
class Trading212Config:
    api_key: str
    base_url: str
    environment: str = "live"     # e.g. "live" | "demo"
    timeout_secs: float = 10.0    # per-request timeout


class Trading212Client:
    """
    High-level HTTP client with shared session, retries & error handling.
    Provides sub-clients: account, portfolio, pies.
    """
    def __init__(self, config: Trading212Config):
        self.config = config
        self.session = requests.Session()

        retries = Retry(
            total=3,
            backoff_factor=0.5,              # 0.5s, 1s, 2s
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"])
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        self.account_data = AccountData(self)
        self.equity_order = EquityOrders(self)
        self.personal_portfolio = PersonalPortfolio(self)
        self.pies = Pies(self)
        self.instruments_metadata = InstrumentsMetadata(self)

    # ---------- low-level ----------
    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        base = {"Authorization": self.config.api_key}
        if extra:
            base.update(extra)
        return base

    def _url(self, subpath: str) -> str:
        if not subpath.startswith("/"):
            subpath = "/" + subpath
        return f"{self.config.base_url}{subpath}"

    def _request(
        self,
        method: str,
        subpath: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        url = self._url(subpath)
        try:
            resp = self.session.request(
                method=method.upper(),
                url=url,
                params=params,
                json=json_body,
                headers=self._headers(headers),
                timeout=self.config.timeout_secs,
            )
        except requests.RequestException as e:
            raise Trading212Error(f"Network error calling {url}: {e}") from e

        if not (200 <= resp.status_code < 300):
            # try parse JSON error payload
            try:
                payload = resp.json()
            except ValueError:
                payload = resp.text
            raise Trading212Error(
                f"HTTP {resp.status_code} calling {url}",
                status_code=resp.status_code,
                payload=payload,
            )

        # parse JSON safely
        if resp.content:
            try:
                return resp.json()
            except ValueError as e:
                raise Trading212Error(
                    f"Invalid JSON in response from {url}", payload=resp.text
                ) from e
        return None


# ---------- domain sub-clients ----------






