# Contributing

Setup and running the tests are covered in [TESTING.md](TESTING.md). This page
covers what every pull request must include, and how versions and releases
work.

## Every pull request

- **Bump the version.** Each pull request merged to `main` changes
  `__version__` in [`src/cddl_verifier/_version.py`](src/cddl_verifier/_version.py)
  exactly once (see [Versioning](#versioning)).
- **Update the docs** (README, `docs/`, `TESTING.md`) in the same pull request
  as the code they describe.
- **Describe the change in the pull request.** There is no changelog file; the
  pull request description is the record of what changed and why. Start any
  change that users can notice (different results, newly rejected input,
  removed or renamed options) with **Behaviour change:**, so it can be found
  when release notes are written.
- **Close the issues it finishes.** Put `Closes #N` in the pull request
  description, one per line, for each issue the pull request completes, so
  GitHub links them and closes them when it merges. Naming the issue (`#N`)
  in the title, or anywhere without a closing keyword, does not close it. A
  closing keyword in a commit message does close it on merge, but does not
  link the pull request, so use the description. Use `Part of #N` or
  `Refs #N` for an issue the pull request only advances.
- **Gaps you find but don't fix** get an issue of their own, linked from the
  pull request.

## Versioning

The project follows [Semantic Versioning 2.0.0](https://semver.org/). The
version moves with `main`: every merged pull request gets its own version, and
releases are cut from `main` whenever it is stable enough (see
[Releasing](#releasing)). A version is never held back waiting for a release.

- The version is defined once, as `__version__` in `_version.py`.
  `pyproject.toml` reads it, `cddl_verifier.__version__` exposes it, and
  `cddl-verify --version` prints it. Don't repeat the number elsewhere.
- Bump it once per pull request, against the version on `main`, not once per
  commit. If `main` moves to the same version before you merge, bump again on
  top of it.
- CI enforces this. The *Version bumped* job checks that the pull request's
  version is exactly one step above the version on `main`: patch + 1, minor + 1
  with patch 0, or major + 1 with the rest 0. It reads the `main` version from
  the first parent of GitHub's test merge commit, so it needs no stored copy
  and no extra fetch. Whether the step is the right one is left to review.
- GitHub computes that merge commit when the check runs and does not re-run the
  check when `main` moves. To stop two pull requests from merging with the same
  version, turn on branch protection for `main` with *Require status checks to
  pass* (including *Version bumped*) and *Require branches to be up to date
  before merging*.
- Pick the part to bump by the largest change in the pull request:

  | Bump | When | Example |
  |------|------|---------|
  | Patch (`0.2.0` → `0.2.1`) | Bug fixes, docs, tests, internal refactors, tooling and CI | Fix a crash on a malformed schema |
  | Minor (`0.2.1` → `0.3.0`) | A new feature or capability: a new public function or option, newly supported CDDL or CBOR | Support a new control operator |
  | Major (`0.x` → `1.0.0`, `1.x` → `2.0.0`) | An incompatible change to the public API contract | Rename or remove a public function or CLI option |

- Get the maintainer's agreement before a major bump.
- While the major version is `0`, a change that breaks compatibility is a minor
  bump, as SemVer allows. One example is rejecting input that used to be
  accepted.
- The public API is `cddl_verifier`, `cddl_verifier.cbor`,
  `cddl_verifier.json_codec` and the `cddl-verify` CLI. Modules whose names
  start with `_` are internal and can change in any version.
- Most versions are never published. PyPI only gets the versions that are
  released, so gaps between published versions are expected.

## Releasing

A release publishes whatever version `main` is at. Releases go to PyPI through
[`publish_pypi.yml`](.github/workflows/publish_pypi.yml), which uses PyPI Trusted
Publishing (no API tokens are stored).

1. Pick a commit on `main` that is green and stable. Its `_version.py` is the
   version released, so no version change is needed.
2. Optional dry run: manual runs of the workflow (Actions → *Publish to PyPI /
   TestPyPI* → *Run workflow*) upload only to TestPyPI, but a version can be
   uploaded only once per index, and a release fails if its version is already
   on TestPyPI. So never run it on `main` itself. Instead, push a temporary
   branch from the chosen commit that changes `_version.py` to a pre-release of
   the version (`0.2.1` → `0.2.1rc1`, then `rc2`, …), run the workflow on that
   branch, and delete the branch afterwards without merging it. The version
   check runs only on pull requests, so the branch needs no pull request.
3. Publish a GitHub release with tag `vX.Y.Z` matching the version, on that
   commit. Use *Generate release notes* (since the previous `v` tag) and put
   the **Behaviour change:** items from the merged pull requests at the top.
   The workflow builds and checks the distributions, tests the wheel and sdist,
   uploads to TestPyPI, installs from there, and then uploads to PyPI.

The release notes on GitHub are the history of what each release contains. A
version maps to the commit that set it (`git log --oneline -S'__version__ = "0.2.1"' --
src/cddl_verifier/_version.py`), and every commit on `main` links to its pull
request.

One-time setup: on pypi.org and test.pypi.org, add a (pending) trusted publisher
for repository `sahebbiswas/cddl_verifier`, workflow `publish_pypi.yml`, and
environment `pypi` / `testpypi` respectively (or "any" environment). GitHub
creates the environments on first use; add a required reviewer to `pypi` to gate
production uploads.
