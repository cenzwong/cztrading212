"""Unrelated unit tests avoid real-time throttling; reliability tests inject real policies."""

import pytest

from cztrading212 import client
from cztrading212.core.reliability import RateLimiter


@pytest.fixture(autouse=True)
def no_real_waits_outside_reliability_tests(request, monkeypatch):
    if request.path.name != "test_reliability.py":
        monkeypatch.setattr(client, "RateLimiter", lambda: RateLimiter(policies={}))
