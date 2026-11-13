# Changelog

## Unreleased

- Rebuild the package around the Trading 212 Public API v0 snapshot with sync
  and async raw clients and Decimal-preserving JSON handling.
- Add generated dataclasses, parser entry points, typed raw methods, operation
  catalog, normalization overrides, and a reviewable upstream-sync workflow.
- Add read-only services with TTL caching, pagination, local rate limiting,
  bounded read retries, and freshness metadata.
- Add portfolio, dividend, and history workflows, plus optional read-only CLI
  and MCP adapters.
- Add local order drafts, a durable intent-journal foundation, and scoped cache
  invalidation, and experimental opt-in single-attempt submission and
  observational reconciliation.

This alpha is incomplete; see the README and architecture documentation for
current limitations. Demo and live broker integration remains unverified.
