"""Cache identity and conservative defaults for read-only services."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any, cast

from .. import codec
from .catalog import OPERATIONS

# Cache policies are version-controlled and generated into the operation catalog.
DEFAULT_TTLS: dict[str, float] = {
    operation: float(metadata["cache_policy"]["ttl_seconds"])
    for operation, metadata in OPERATIONS.items()
    if metadata["cache_policy"] is not None
}
CACHE_VERSION = "v2"


def namespace(environment: str, key: str, secret: str) -> str:
    # HMAC avoids storing either credential and avoids a plain hash of the key.
    return hmac.new(
        secret.encode(), (environment + "\0" + key).encode(), hashlib.sha256
    ).hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        obj = cast(dict[str, Any], value)
        return {k: _canonical(obj[k]) for k in sorted(obj)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in cast(list[Any] | tuple[Any, ...], value)]
    return value


def cache_key(
    namespace_value: str, operation: str, arguments: Mapping[str, Any], generation: int = 0
) -> str:
    canonical = codec.dumps(
        _canonical({key: value for key, value in arguments.items() if value is not None})
    )
    identity = json.dumps(
        [CACHE_VERSION, namespace_value, generation, operation, canonical], separators=(",", ":")
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"{CACHE_VERSION}:{namespace_value}:{generation}:{digest}"
