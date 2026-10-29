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

## Resolved since the review (2026-10-21 UTC)

- **NLTK, voice stack:** [GHSA-8mgp-746c-j5xp](https://osv.dev/vulnerability/GHSA-8mgp-746c-j5xp)
  (CVE-2026-81726) affects NLTK through 3.10.3. NLTK 3.10.4 routes the six
  named model-artifact APIs (`TransitionParser.train`/`parse`,
  `AveragedPerceptron.save`/`load`, `PerceptronTagger.save_to_json` and
  `save_maxent_params`) through `nltk.pathsec`, as confirmed in the published
  3.10.4 source distribution. The voice floor and the lock now require 3.10.4
  with the PyPI-published hashes. With real tagger and CMUDict resources
  installed by the 3.10.4 downloader, the EmotiVoice English frontend produced
  byte-identical phonemes, raw G2p output, out-of-vocabulary predictions and
  POS tags compared with 3.10.3. NLTK 3.10.4 refuses a data root that another
  account can write to; the default per-user model directory is unaffected.
  The [English TTS resource test](../../tests/server/media/test_nltk_tts_resource_boundary.py)
  still checks that speech text never reaches artifact persistence APIs or
  downloads.

## Reviewed decisions

- **Accelerate, optional tuning:** [GHSA-4j2p-28q2-5m79](https://osv.dev/vulnerability/GHSA-4j2p-28q2-5m79)
  lists checkpoint path traversal through 1.14.0 with no fixed release. The
  manifest's minimum is affected. PyPI returned no advisory for 1.15.0, but
  [the tagged 1.15.0 loader](https://github.com/huggingface/accelerate/blob/v1.15.0/src/accelerate/utils/modeling.py)
  still joins checkpoint-index filenames to the model directory without
  checking containment before loading them. An empty advisory result is not
  evidence of a fix, so the floor was not raised merely to remove the match.
  The affected functions are `load_checkpoint_in_model` and
  `load_checkpoint_and_dispatch`. No AutoYou source calls them, and the locked
  Transformers 5.12.1 and current PEFT 0.21.2 wheels do not call them either
  (checked 2026-10-21). The tuning profile is optional: none of the generated
  release SBOMs include Accelerate or PEFT, and WSL builds add it only with
  `AUTOYOU_INCLUDE_TUNING`. The training flow loads remote model code, so model
  sources are already fully trusted; this advisory does not widen that
  boundary. Use trusted model sources and an isolated training environment.

This decision is recorded in
[requirements/advisory-decisions.json](../../requirements/advisory-decisions.json)
as `vulnerable_code_not_in_execute_path`, scoped to Accelerate 0.33.0 in
`requirements/tuning.txt` and to these advisory ids, with a review-by date of
2026-11-21. The advisory command reports it under `reviewed_advisories` and
returns nonzero again for any other match, a changed floor or manifest, or once
the review date passes.

- **OpenTelemetry API, server:** [GHSA-xcq7-p3pf-p88h](https://osv.dev/vulnerability/GHSA-xcq7-p3pf-p88h)
  (CVE-2026-114603) is a quadratic regular-expression split in
  `TraceState.from_header`, fixed in 1.45.1. The lock cannot move there:
  google-adk 2.11.0 caps `opentelemetry-sdk` at 1.42.1, and google-adk 2.13.0
  (latest, checked 2026-10-29) still caps the API and SDK at 1.44. The parser
  runs only when a propagator reads an inbound `tracestate` header. AutoYou
  installs no OpenTelemetry instrumentation and never calls
  `propagate.extract`. ADK's one extraction, `get_propagated_context` in
  `google/adk/telemetry/_agent_engine.py`, passes a carrier holding only a
  `traceparent` key, so no `tracestate` value reaches the parser. Dependabot
  ignores newer OpenTelemetry releases until ADK allows 1.45.1.
- **setuptools, voice-clone runtime:** [PYSEC-2026-3447](https://osv.dev/vulnerability/PYSEC-2026-3447)
  and [GHSA-xmj3-9gf7-h52w](https://osv.dev/vulnerability/GHSA-xmj3-9gf7-h52w)
  affect setuptools 81.0.0. The separate voice-clone environment keeps it
  because resemble-perth 1.0.1, the latest release, imports `pkg_resources`,
  which setuptools 82 removed. The advisories cover `MANIFEST.in` exclusion
  while building a source distribution and the legacy Windows
  `Wheel.install_as_egg` path. That environment only installs published wheels
  with pip, so neither path runs. The server environment already requires
  setuptools 84.2.1 or later.

Both are recorded as `vulnerable_code_not_in_execute_path`, scoped to the
listed versions and manifests, with a review-by date of 2026-11-29.

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
