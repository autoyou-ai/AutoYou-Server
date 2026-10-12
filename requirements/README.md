# Dependency profiles and advisory checks

The `.txt` files select dependencies. They do not certify security or determine
the license of third-party packages. Installation executes code from selected
package sources with the installer's permissions.

## What the files establish

- `base.txt` supplies core runtime requirements.
- `full.txt` composes runtime profiles; `training-full.txt` adds optional tuning.
- `binary-default.txt` selects the normal native package profile.
- `test.txt` and `e2e.txt` are development dependencies.
- `cognee.txt` includes optional upstream Git sources pinned to full commit IDs.
- `realtimestt-runtime.txt` describes the separately installed RealtimeSTT
  package. Its installer uses `--no-deps` and adjusts installed dependency metadata.
- `locked.txt` contains exact releases and hashes from a Python 3.13 resolution.
  It is not a complete lock for every operating system and optional profile.

Normal bootstrap/build paths derive version constraints from the lock. They
discard its hashes, extras, and markers, so those installs do not enforce
`--require-hashes`. Build tools, some optional packages, and separate installers
have additional dependency paths. An operator can also override lock usage.

The root launchers request the internet component in addition to the selected
bootstrap profile. Profile names alone should not be used as an inventory of
what was installed.

## Reproduce the manifest advisory check

This standard-library script queries PyPI's release-specific advisory API.
It sends public package names and versions, installs no packages, and writes
the requested report:

```bash
python scripts/audit_dependency_advisories.py --all-manifests --include-minimums --output build/dependency-advisories.json
```

Run it from the repository root. Without `--all-manifests`, only the lock is
checked. A nonzero exit indicates an advisory or lookup error. A minimum-version
check examines the declared floor, not every release allowed by a range.
Unpinned requirements and Git sources are reported as skipped, not clean.

The dated [dependency review](../docs/security/dependency-audit.md) records
scope and unresolved items. Advisory information changes; rerun at release time.

## Release and source integrity

For each supported platform/profile, resolve dependencies in an isolated build
environment, retain the exact resolved inventory and artifact hashes, review
advisories and upstream changes, and inspect the resulting package. Generate
an artifact SBOM from that resolved inventory. A manifest-derived SBOM is a
declaration of intended dependencies, not proof of bundled versions.

Keep publishing credentials and production secrets out of dependency-install
jobs. Use minimally privileged CI, review dependency changes, and verify
upstream sources. Hashes identify artifacts but do not prove that an upstream
artifact is benign. Public source and SBOMs aid inspection without granting
repository write access.

## Known scope limits

The shared lock does not cover every optional or platform-only dependency.
In particular, the Intel macOS voice/full NumPy constraint conflicts with the
shared lock, and the selected Torch, torchaudio, and Numba versions have no
macOS x86_64 wheels in the reviewed PyPI metadata. That optional profile is
not validated for Intel macOS; removing one constraint does not resolve its
native dependency requirements. See the dated review for the exact scope.
No successful full platform build or complete optional dependency closure is
implied by a manifest advisory check.

Report suspected vulnerabilities privately under [SECURITY.md](../SECURITY.md).
Third-party notice, source-offer, and relinking duties remain separate from
dependency security.
