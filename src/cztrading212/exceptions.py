"""Errors raised by the SDK, with no response bodies or credentials in messages."""


class Trading212Error(Exception):
    """Base SDK exception."""


class APIError(Trading212Error):
    def __init__(
        self, status_code: int, operation: str, *, headers: dict[str, str] | None = None
    ) -> None:
        self.status_code = status_code
        self.operation = operation
        self.headers = headers or {}
        super().__init__(f"Trading 212 returned HTTP {status_code} for {operation}")


class ResponseValidationError(Trading212Error):
    """An upstream response does not match the documented schema."""


class PaginationError(Trading212Error):
    """Pagination is unsafe, malformed, cyclic, or exceeds a configured bound."""


class NetworkError(Trading212Error):
    """A request failed at the transport boundary; a write's outcome may be unknown."""


class RateLimitError(Trading212Error):
    """A local or broker rate limit cannot be waited out within the configured bound."""

    def __init__(
        self, operation: str, retry_after: float, *, status_code: int | None = None
    ) -> None:
        self.operation = operation
        self.retry_after = retry_after
        self.status_code = status_code
        super().__init__(f"Rate limit for {operation}; retry after {retry_after:.2f} seconds")


class DataUnavailableError(Trading212Error):
    """A workflow cannot proceed without a required value or compatible currency."""


class JournalStateError(Trading212Error):
    """The durable order journal is unavailable or in an incompatible state."""


class DuplicateIntentError(Trading212Error):
    """An order intent has already been recorded or claimed."""


class IntentConflictError(Trading212Error):
    """An intent ID was reused with a different order payload."""


class TradingDisabledError(Trading212Error):
    """The guarded submission service has not been explicitly enabled."""


class CacheInvalidationError(Trading212Error):
    """Cache invalidation failed; inspect the attached outcome before taking action."""

    def __init__(self, *, intent_id: str, state: str, order_id: int | None = None) -> None:
        self.intent_id = intent_id
        self.state = state
        self.order_id = order_id
        super().__init__(
            "Order cache invalidation failed; inspect the journal and broker before taking further action"
        )


class OrderOutcomeUnknownError(Trading212Error):
    """The broker may have received an order; automatic resubmission is unsafe."""

    def __init__(
        self,
        *,
        intent_id: str,
        status_code: int | None = None,
        observed_order_id: int | None = None,
        cache_invalidated: bool = True,
    ) -> None:
        self.intent_id = intent_id
        self.status_code = status_code
        self.observed_order_id = observed_order_id
        self.cache_invalidated = cache_invalidated
        super().__init__(
            "Order outcome is unknown; inspect the journal and broker before taking further action"
        )
