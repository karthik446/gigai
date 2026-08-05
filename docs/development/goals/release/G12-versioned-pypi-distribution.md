# G12 — Versioned PyPI Distribution

- Status: Approved
- Depends on: Phase 1 completion (G10)
- Unblocks: clean-machine installation and a future convenience-installer goal

## Outcome

Deliberately publish GigAI `0.1.0` as the first public pre-alpha release: an
installable PyPI distribution and matching GitHub Release that a user can
install on a clean machine without cloning this repository.

The package metadata, annotated Git tag `v0.1.0`, PyPI release, GitHub Release,
and `gigai --version` must all identify the same version. This explicitly
declares the first public release and activates ADR 0003's immutable/additive
schema-versioning regime.

## In scope

- The static `[project].version` field in `pyproject.toml` as the only package
  version source. The CLI derives its version from installed distribution
  metadata and must not introduce a second hard-coded version value.
- An explicit release order: confirm PyPI name ownership before the release
  commit or tag; set the static project version; regenerate `uv.lock` with
  `uv lock` from that source of truth rather than editing the lockfile by hand;
  pass the exact-tag CI gate; build; publish and install through TestPyPI; then
  authorize the production PyPI and GitHub Release.
- Release checks that reject a mismatch among the static project version, the
  release tag, built artifacts, lockfile, and CLI version output. Every build
  job clears `dist/`, parses that version, and resolves exactly one matching
  wheel and source distribution; no CI, container, documentation, or release
  step may name a literal versioned artifact filename.
- A release workflow that builds the wheel and source distribution from the
  exact annotated release tag, verifies both in fresh environments without a
  source checkout, and requires the complete existing CI gate for that exact
  tag commit.
- PyPI publication through a GitHub-to-PyPI Trusted Publisher using OIDC and a
  protected release environment; no long-lived PyPI API token is stored in the
  repository, workflow, or release evidence.
- A GitHub Release for the same tag containing the wheel, source distribution,
  `SHA256SUMS`, and verifiable provenance attestations.
- Clean-machine operator evidence on one supported macOS machine and one
  supported Linux machine. Each installs the exact PyPI version with
  `uv tool install "gigai==0.1.0"`, then proves `gigai --version`,
  `gigai --help`, and an offline setup/doctor path without a repository clone
  or provider credential.
- Public installation and upgrade documentation that uses the published,
  versioned package rather than a local editable checkout, and source-build
  documentation that resolves built artifacts without a literal version.
- A release manifest and completion audit recording the tag commit, artifact
  digests, attestation verification, PyPI and GitHub Release URLs, CI evidence,
  and redacted clean-machine proofs.

## Out of scope

- Example Gigs, a template gallery, sample workpads, or any claim that a Gig
  can run. Those are a later, separately reviewed product goal.
- A `curl | sh` installer, custom installation domain, package-manager
  bootstrap, silent PATH mutation, or auto-update behavior. A later
  convenience-installer goal may wrap this PyPI distribution only after its
  security boundary is specified.
- Native binaries, standalone platform installers, Homebrew, npm, or other
  package registries.
- Run scheduling or execution, target mutation, provider changes, automatic
  fallback, or network activity outside the explicit release publication path.
- Changes to the eight packaged schemas, canonical golden vectors, V14 product
  behavior, or completed Phase 1 goal contracts.

## Acceptance criteria

1. Before creating the release commit or annotated `v0.1.0` tag, GigAI's
   publisher confirms that the `gigai` PyPI project name is available or
   already owned by it. A name-ownership failure stops the release.
2. The release commit sets the static `pyproject.toml [project].version` to
   `0.1.0` and regenerates `uv.lock` with `uv lock`. At the exact annotated
   `v0.1.0` tag commit, the lockfile project entry, built-artifact metadata,
   and `gigai --version` output all match that version. The release evidence
   records the `uv lock` refresh and a subsequent `uv lock --locked` check; a
   dirty checkout or any version disagreement fails the release gate.
3. The exact tag commit passes the complete cross-platform CI suite, built-wheel
   verifiers, and the Debian offline-container lane before publication; all
   `--locked` commands succeed from that commit.
4. The release workflow builds a wheel and source distribution with `uv build
   --no-sources`. The flag is retained as a forward guard against future
   `tool.uv.sources` entries. From a clean artifact directory, each built
   distribution installs and passes its release smoke checks in a fresh
   environment that has neither the repository checkout nor editable GigAI
   installation available.
5. A disposable TestPyPI publication dry run completes before production
   publication is authorized, proving the tagged artifacts can be uploaded and
   installed without using the local checkout.
6. PyPI publication uses a configured Trusted Publisher and GitHub OIDC under
   a protected release environment. No credential value, token-shaped value,
   or workstation path appears in committed material, workflow logs, or
   durable evidence.
7. The GitHub Release, PyPI release, release manifest, and `SHA256SUMS` agree
   on the exact artifact digests, and the published provenance attestation is
   independently verified.
8. Sanitized clean-machine macOS and Linux proofs install the exact PyPI
   version with `uv tool install "gigai==0.1.0"` and pass the stated offline
   command checks. They do not clone this repository, rely on a local wheel,
   or use provider credentials.
9. The README and cheat sheet show the supported no-clone installation command,
   state the version/update policy, and accurately retain the Phase 1 boundary:
   GigAI can manage Gig proposals but cannot execute Runs. Source-build
   instructions and every CI/release artifact reference resolve the built
   package from parsed metadata; none contains a literal versioned filename.
10. All eight schema-resource hashes and canonical-vector bytes remain unchanged
    from the Phase 1 baseline. The release documentation records that this first
    public release activates ADR 0003 for later contract evolution.

## Verification and evidence

- TestPyPI dry-run publication and fresh TestPyPI installation record.
- Tag-to-metadata-to-CLI version mismatch tests, including a dirty-tree
  rejection test.
- Fresh wheel and source-distribution installation checks, including the
  installed CLI, offline setup, and offline doctor.
- Hosted exact-tag CI, protected-environment, Trusted Publisher, checksum, and
  provenance-attestation evidence.
- Sanitized macOS and Linux clean-machine installation records.
- `docs/development/evidence/release/G12/completion-audit.md`,
  `terminal-handoff.md`, and a machine-readable release manifest.

## Stop boundary

Stop before publishing if PyPI name ownership, Trusted Publisher setup,
protected-environment approval, exact-tag CI, artifact verification,
attestation verification, TestPyPI publication, or either clean-machine proof
is missing or fails.
Do not substitute a Git source install, a local wheel, a credential-bearing
workflow, or a curl installer for the published-package proof. Route example
Gigs and every execution-oriented surface to a later goal.
