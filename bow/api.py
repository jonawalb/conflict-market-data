"""HTTP client for the Polymarket Gamma, CLOB, and data-api endpoints.

All calls are read-only GETs against public endpoints. No authentication is
required or used.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from .config import CLOB, DATA_API, GAMMA, USER_AGENT, Config

logger = logging.getLogger(__name__)


class ApiError(RuntimeError):
    """Raised when an endpoint fails after all retries."""


class PolymarketClient:
    """Thin retrying JSON client. One instance per collector run."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self.calls = 0
        self.failures = 0

    def _get(self, url: str) -> Any:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        last_error: Optional[Exception] = None

        for attempt in range(self._cfg.max_retries):
            try:
                with urllib.request.urlopen(
                    request, timeout=self._cfg.request_timeout
                ) as response:
                    self.calls += 1
                    time.sleep(self._cfg.inter_request_sleep)
                    return json.loads(response.read())
            except urllib.error.HTTPError as exc:
                last_error = exc
                # 400 means we walked past a pagination cap; retrying will not help.
                if exc.code in (400, 404):
                    raise ApiError(f"{exc.code} on {url}") from exc
                logger.debug("HTTP %s on %s (attempt %d)", exc.code, url, attempt + 1)
            except (OSError, json.JSONDecodeError) as exc:
                # OSError covers URLError, TimeoutError, and — the one that
                # actually killed a scheduled run — ConnectionResetError, which
                # is raised mid-read and is not a URLError subclass.
                last_error = exc
                logger.debug("transient error on %s: %s", url, exc)
            time.sleep(self._cfg.retry_backoff * (attempt + 1))

        self.failures += 1
        raise ApiError(f"failed after {self._cfg.max_retries} attempts: {url}") from last_error

    # -- Gamma ------------------------------------------------------------

    def search_events(self, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Full-text search returning events with nested markets."""
        url = f"{GAMMA}/public-search?q={urllib.parse.quote(query)}&limit_per_type={limit}"
        payload = self._get(url)
        return payload.get("events", []) or []

    def open_events(self, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        """Currently open events, ordered by 24h volume."""
        url = (
            f"{GAMMA}/events?closed=false&limit={limit}&offset={offset}"
            "&order=volume24hr&ascending=false"
        )
        return self._get(url) or []

    def market_by_id(self, market_id: str) -> Optional[Dict[str, Any]]:
        payload = self._get(f"{GAMMA}/markets?id={market_id}")
        return payload[0] if payload else None

    # -- CLOB -------------------------------------------------------------

    def price_history(
        self, token_id: str, interval: str = "1d", fidelity: int = 1
    ) -> List[Dict[str, Any]]:
        """Price bars for a token.

        Note: this endpoint returns an empty history once a market resolves, which
        is precisely why this collector exists.
        """
        url = (
            f"{CLOB}/prices-history?market={token_id}"
            f"&interval={interval}&fidelity={fidelity}"
        )
        try:
            return self._get(url).get("history", []) or []
        except ApiError:
            return []

    def book(self, token_id: str) -> Optional[Dict[str, Any]]:
        """Full order book snapshot. Never recoverable retrospectively."""
        try:
            return self._get(f"{CLOB}/book?token_id={token_id}")
        except ApiError:
            return None

    # -- data-api ---------------------------------------------------------

    def trades(self, condition_id: str, limit: int, offset: int) -> List[Dict[str, Any]]:
        """Trade tape with wallet addresses. Paging caps near offset 10000."""
        url = f"{DATA_API}/trades?market={condition_id}&limit={limit}&offset={offset}"
        try:
            payload = self._get(url)
            return payload if isinstance(payload, list) else []
        except ApiError:
            return []
