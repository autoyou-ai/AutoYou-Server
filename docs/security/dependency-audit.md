# Dependency review: 2026-10-04 UTC

This review covers the 17 Python requirement manifests in this repository and
selected installation/runtime consumers. It is not a full application security
audit, malware scan, legal opinion, or successful platform build.

## Method and result

The initial static review examined all 217 package records and 3,573 SHA-256
hash lines in the lockfile, plus the optional manifests and their installation
paths. Independent source review found that installers generally use derived
version constraints rather than enforcing the lock's distribution hashes.

A separate [PyPI release API](https://docs.pypi.org/api/json/) check queried
exact pins and declared minimum versions without installing packages. The
final run queried 284 distinct package/version pairs, with no lookup errors.
Two pairs retained advisory matches. Four declarations were not queryable by
this method: two immutable Git sources, an unpinned torchvision requirement,
and the conditional upper-bound-only NumPy requirement.

This is not resolution of every allowed version or transitive dependency.
Advisory aliases may describe the same vulnerability. Empty results do not
establish the absence of unknown vulnerabilities or malicious packages.

## Changes made

| Dependency | Manifest change | Advisory context |
| --- | --- | --- |
| urllib3 | Lock and floors moved from 2.7.0 to 2.8.0 | Upstream HTTP streaming resource-exhaustion and HTTPS proxy fixes |
| LiteLLM | Pin and lock set to 1.89.7 | Patched upstream proxy parameter-validation advisory; AutoYou SDK use did not establish proxy exposure |
| Requests | Minimum raised to 2.33.0 | Excludes the affected earlier minimum; lock was already newer |
| Pydantic | Minimum raised to 2.10.0 | Excludes the affected early 2.x minimum and meets the selected LiteLLM requirement |
| Datasets | Minimum raised to 5.0.1 | Upstream folder-metadata path traversal fix |
| SentencePiece | Minimum raised to 0.2.1 | Excludes the affected 0.2.0 minimum |
| OpenCV | Minimum spelled as published release 4.9.0.80 | Removes an unqueryable abbreviated version; not claimed as a security fix |

The two changed lock entries use hashes returned for the patched releases by
PyPI. A full cross-platform lock regeneration was not performed. No installed
server environment was modified. Rebuild or deliberately update existing
environments for declaration changes to take effect.

The urllib3 finding is source-backed: Requests response consumption can reach
the affected framing/decompression code before application byte limits run.
A malicious fetched origin is a prerequisite. Existing update-origin,
signature, and media-destination controls constrain the attack and remain
necessary. No exploit was executed.

References:
[urllib3 chunk framing](https://osv.dev/vulnerability/GHSA-vxq7-64xx-v4gw),
[urllib3 Deflate handling](https://osv.dev/vulnerability/GHSA-gh4c-6fx4-qh6g),
[urllib3 proxy TLS](https://osv.dev/vulnerability/GHSA-8988-9cw3-xx77),
[LiteLLM proxy validation](https://osv.dev/vulnerability/GHSA-3cv6-jpf6-8222),
[Datasets](https://osv.dev/vulnerability/GHSA-379c-qx7v-6h59).

## Unresolved advisories

- **NLTK 3.10.3, voice stack:** [GHSA-8mgp-746c-j5xp](https://osv.dev/vulnerability/GHSA-8mgp-746c-j5xp)
  lists affected model-artifact import/export APIs and no fixed release.
  The inspected TTS flow uses text, fixed pronunciation resources, POS tagging,
  and CMUDict; no caller-controlled use of those affected artifact APIs was
  established. This is a bounded reachability observation, not a package-wide
  exemption. The isolated
  [English TTS resource test](../../tests/server/media/test_nltk_tts_resource_boundary.py)
  exercised the real frontend, G2p preprocessing, POS tagging, and resource
  loader with traversal-shaped, Windows-path-shaped, and ordinary speech
  text. It used synthetic pronunciation data, prohibited downloads, and
  verified that none of the six affected artifact APIs was called. It passed
  alongside the 24 existing EmotiVoice tests. This does not cover arbitrary
  extensions or repair NLTK itself.
- **Accelerate, optional tuning:** [GHSA-4j2p-28q2-5m79](https://osv.dev/vulnerability/GHSA-4j2p-28q2-5m79)
  lists checkpoint path traversal through 1.14.0 with no fixed release. The
  manifest's minimum is affected. PyPI returned no advisory for 1.15.0, but
  [the tagged 1.15.0 loader](https://github.com/huggingface/accelerate/blob/v1.15.0/src/accelerate/utils/modeling.py)
  still joins checkpoint-index filenames to the model directory without
  checking containment before loading them. An empty advisory result is not
  evidence of a fix, so the floor was not raised merely to remove the match.
  The inspected training flow does not directly call the named Accelerate
  APIs. It does load model repositories through Transformers and permits
  remote model code; those paths prevent a package-wide non-applicability
  conclusion. Use trusted model sources and an isolated training environment.

Neither advisory has been silently suppressed or accepted for release.
Release reviewers must resolve the affected scope or record an explicit,
bounded decision. The advisory command continues to return nonzero while these
matches remain.

## Other limits and follow-up

- Optional Git source contents and their full transitive/build dependency
  closure were not audited by a version-only PyPI query.
- The Python 3.13 lock does not cover every optional or platform dependency.
  Intel macOS voice/full has conflicting NumPy declarations. The selected
  Torch, torchaudio, and Numba release metadata also provides no macOS x86_64
  wheels. Removing the NumPy constraint alone would not validate that profile.
  It needs a supported dependency resolution and a successful native build;
  neither was established in this review.
- Separate build tools, native libraries, Node packages, model files, browser
  downloads, and system packages require their own checks.
- Generate a resolved inventory and SBOM from each actual release artifact,
  then rerun advisories immediately before publication.
- Full repository history, secrets, contributor rights, and release approvals
  remain separate publication checks.

Reproduce with
`python scripts/audit_dependency_advisories.py --all-manifests --include-minimums --output build/dependency-advisories.json`.
See [requirements/README.md](../../requirements/README.md) and
[SECURITY.md](../../SECURITY.md).
