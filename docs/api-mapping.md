# API operation mapping

The [human-readable API reference](api-reference.md) lists every operation.
The machine-readable catalog at
`src/cztrading212/_generated/operations.json` maps every snapshotted operation
ID to its HTTP method and endpoint, generated raw group/method, parameters,
request and response schemas, authentication, documented endpoint rate policy,
reviewed retry/cache policy, and mutation risk. Deprecated pies remain in the
catalog because they are present in the official snapshot.

The raw entry point is `client.raw.<group>.<method>(...)`; the corresponding
`client.raw.call(operation_id, ...)` provides dynamic access. Raw request
arguments use upstream names and JSON bodies use `body=`. Raw responses keep
upstream keys and Decimal numeric values. Services apply generated parsers and
read policies; not every raw operation has a dedicated convenience facade.
Consult [generation](generation.md) before changing mappings or policies.
