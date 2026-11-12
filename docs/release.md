# Release and versioning

The package is currently an alpha (`0.1.0a1`). Use PEP 440 versions and a tag
named `v` followed by the exact project version. During 0.x development,
incompatible SDK changes should increment the minor version and compatible fixes
the patch version; pre-releases use PEP 440 suffixes such as `a1`, `b1`, or
`rc1`. Any eventual 1.0 release should document compatibility and deprecation
policy before claiming stability. Upstream API changes can still require review
and adaptation, particularly for writes.

A release requires review of the full diff and changelog, successful CI,
reproducible generation and distribution builds, independent core/CLI/MCP
installs, and the relevant demo checks described in [order safety](safety.md).
No real broker integration has been verified yet. The optional demo order test
must only be run with a separately chosen demo account, ticker, quantity,
persistent journal, intent ID, and explicit write gate. Do not substitute unit
mocks for demo evidence or make live orders to validate packaging.

## Publishing configuration

`.github/workflows/publish.yml` is a manually dispatched workflow. It checks out
the supplied tag, requires that the tag version match `pyproject.toml` and that
its commit is reachable from `origin/main`, checks for a clean checkout and
tracked sensitive paths, runs tests and
static checks, regenerates the API, builds twice, compares hashes, validates
archive contents, and runs `twine check --strict`. The default dispatch setting
only validates and uploads a GitHub Actions artifact. If `publish` is explicitly
selected, a separate publishing job downloads that artifact and uses PyPI
Trusted Publishing through the `pypi` GitHub environment. Only that job receives
`id-token: write`; it does not build package code.

Before enabling publication, an administrator must configure the `pypi` GitHub
environment with required reviewers and appropriate deployment restrictions,
configure a matching PyPI Trusted Publisher for this repository and workflow,
and verify project-name ownership or availability. The workflow itself cannot
create those external protections. Its GitHub execution and PyPI trust have not
been verified; no distribution has been published by this project workflow.
GitHub reported `main` as unprotected during this rebuild; repository settings
may change, so verify protection and required checks before merging or releasing.
See [PyPI's Trusted Publishing guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

The `.env` path was tracked in existing history and is removed from the rebuilt
tree; the local file is retained and ignored. The release check still
requires a clean, committed checkout. Review the file and its Git history for
credentials and rotate any exposed credentials before a release. Removing a
file from the current index does not remove it from history; any history rewrite
or remote cleanup requires its own deliberate authorization. Distribution
allowlists and archive checks exclude environment files and local data, but do
not replace secret rotation or Git history review. A package license has not yet
been selected.

The GitHub repository was confirmed public during this rebuild. A local-history
scan of `.env` revisions did not find non-placeholder values under variable names
containing common credential terms, but some assignments were nonempty. That
heuristic does not establish that the history is free of secrets, and it does not
inspect every possible remote ref. The owner must review exposure and rotate any
affected credentials before release.
A public pull request deleting the tracked file shows its old contents in the
diff, so review those contents before opening such a request.

## Local verification

```sh
uv sync --locked --all-extras
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
python scripts/generate.py
uv build
uv run twine check --strict dist/*
```

`scripts/check_release.py --tag vVERSION --source-only` validates tracked paths
and the tag against the checked-out commit and `origin/main`; omit `--source-only`
after building to inspect both archives too. `scripts/check_reproducible.py`
compares two separate output directories. Set `SOURCE_DATE_EPOCH` consistently
before both builds. Matching hashes demonstrate reproducibility for the tested
inputs and build environment, not every platform or future dependency version.

A local `pip install git+file://...` test can verify the Git installation flow
without publishing. The GitHub `pip install git+https://...` URL will install
whatever has actually been pushed to the selected remote ref, not uncommitted
workspace changes. Confirm it after the intended changes are merged and pushed.
