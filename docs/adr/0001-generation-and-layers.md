# ADR 0001: Schema-driven foundation and SDK layers

Status: provisional.

## Context

The upstream API is beta and changes independently. The SDK needs a faithful raw
interface and validated Python objects without adding a heavyweight validation
dependency to the core package.

## Decision

Keep the upstream OpenAPI JSON snapshot in version control. A deterministic
standard-library Python generator emits operation metadata and frozen, slotted,
keyword-only dataclasses. A shared schema-directed parser validates responses.
Raw calls are independent of services; services call raw and must eventually
provide caching, rate limiting, pagination, and safety policy.

## Alternatives

A general-purpose model generator reduces template code but does not by itself
provide the required validated parsing and may introduce runtime dependencies.
Handwritten models would be more vulnerable to upstream drift.

## Consequences and risks

Generation and parser behavior are covered by contract tests. The snapshot has
no required component fields, which limits strictness. Explicit normalization
and a scheduled drift proposal are implemented; additional schema compositions
and constraints must be reviewed before they are supported.
