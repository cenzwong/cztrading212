# ADR 0003: Application data provenance

Status: accepted target contract for application-layer development. Existing
result types remain backward compatible; implementation status is stated below.

## Context

An analytics result may combine independently cached service responses and
multiple history pages. Consumers need to distinguish when data was fetched,
when a report was assembled and whether its input traversal completed. Future
historical snapshots, monitoring and agent consumers need the same evidence
without treating a point-in-time calculation as an atomic broker snapshot.

## Decision

Every broker-backed analytical result must carry immutable provenance, directly
or through a documented nested result, with the following logical fields:

- **Source**: the service operation or dataset and the relevant request scope
  (such as ticker, UTC time range or history filters), without credentials,
  account secrets, raw pagination URLs or other sensitive information. Preserve
  separately identifiable inputs and pages; do not collapse mixed sources into
  one misleading source. Preserve caller-supplied assumptions as inputs with
  their origin and valuation time, not as broker responses.
- **`fetched_at`**: the timezone-aware UTC time associated with the original
  successful broker retrieval. A cache hit retains that original time; it is not
  the time the cache was read and is not a broker market-data timestamp.
- **`from_cache`**: a boolean for each service response or page. Mixed cached
  and fresh inputs retain their individual values, not a single summary flag.
- **`assembled_at`**: the timezone-aware UTC time the calculation result was
  assembled. It does not imply simultaneous retrieval or a broker valuation
  time. Pure recalculations retain original source timestamps and receive a new
  assembly time.
- **Completeness**: identify the input scope and distinguish a completed
  traversal of the requested API scope from partial or unknown coverage.
  Include page and record counts where relevant, configured bounds and explicit
  missing-data limitations. Completed pagination establishes only that the
  endpoint returned no next page for that request, not that the broker contains
  all historical events, that records are unique, or that an audit reconciles
  the account fully.

Use frozen standard-library dataclasses and tuples for public metadata. A
future common metadata type may group these fields when actual workflows need
it; the logical contract does not mandate a new architectural layer. Do not
expose mutable internal cache state. A single-source result may expose one
source; multi-source and paginated results expose each source. Account and
positions timestamps remain separate, because those requests are not atomic.
Currency and valuation assumptions belong alongside the result and its inputs.

History-based aggregates use bounded pagination. On a page limit, invalid page,
repeated cursor or retrieval failure, raise an error rather than returning a
partial aggregate represented as complete. A deliberately streaming output may
already contain partial data on failure; document this and never mark it
complete. If an explicitly partial reporting mode is introduced later, its
contract must label partial coverage and give its reason and bounds.

## Consequences and implementation status

`SourceInfo` contains `fetched_at`, `from_cache`, and optional operation and
scope fields, with completeness defaulting to `unknown`. Application workflows
populate operation and scope; portfolio
snapshots retain separate account and positions sources, and existing history
summaries retain one source per retrieved page. Results include `assembled_at`
directly or through their nested snapshot. Existing page iteration raises
`PaginationError` when it cannot finish within the bound. Streaming transaction
CSV can be partially written before an error.

New history analytics use `HistoryProvenance` for operation, scope, page and
record counts, limits and completed traversal; individual pages retain their
source details. Existing result types do not all expose an explicit completeness
object, and source scopes do not yet capture every possible request parameter.
Those remain cross-cutting implementation requirements, not properties that
existing results should be assumed to have. Add them without removing existing
public fields and test sync and async parity. Future snapshots must persist the actual provenance and scope,
not treat report assembly time as a historical market valuation.
