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

## Resolved since the review (2026-10-30 UTC)

- **Zod, WhatsApp helper:** [GHSA-mrhx-c7xc-m3w5](https://osv.dev/vulnerability/GHSA-mrhx-c7xc-m3w5)
  (memory exhaustion while validating large arrays) affects Zod through 4.6.5.
  Puppeteer 24.38.0, which whatsapp-web.js pins, pulls chromium-bidi 14 with
  Zod 3. Puppeteer 25 moves to a fixed Zod but removes `Browser.isConnected()`,
  which whatsapp-web.js still calls on logout, so `node/whatsapp` overrides Zod
  to 4.6.7 instead. Puppeteer passes no protocol parser to chromium-bidi, so
  Zod is not on the CDP path the client uses; the override was checked with
  Chromium in both CDP and WebDriver BiDi modes and the client reached the
  WhatsApp QR code.
- **deepmerge, Tunnelmole helper:** [GHSA-gcqx-w48m-w2h4](https://osv.dev/vulnerability/GHSA-gcqx-w48m-w2h4)
  affects every deepmerge release through 4.3.1 and has no upstream fix.
  `node/tunnelmole/vendor/deepmerge` is 4.3.1 with own `__proto__` keys
  dropped (see its `AUTOYOU-PATCH.md`); `security.test.js` checks the fix.
- **glob and minimatch, WhatsApp helper (2026-11-05):**
  [GHSA-3g3m-2fjr-6pw4](https://github.com/advisories/GHSA-3g3m-2fjr-6pw4)
  (glob 9.0.0 through 14.0.1 keeps walking after an AbortSignal fires) and
  [GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249)
  (minimatch 7.0.0 to 11.0.0 glob-part amplification) reached `node/whatsapp`
  through whatsapp-web.js, archiver 7.0.1 and archiver-utils 5.0.2, which asks
  for glob ^10 (10.5.0, minimatch 9.0.9). No 10.x release is fixed, so
  `node/whatsapp` overrides glob to 14.0.3, which brings minimatch 11.0.4.
  archiver-utils only calls `require('glob').sync`, which glob 14 still
  exports; archiving a directory and a glob pattern with archiver 7.0.1 and the
  override produced the expected zip entries. readdir-glob keeps minimatch
  5.1.9, outside the affected range. `npm audit` reports 0 vulnerabilities.
- **multidict 7.2.1 / aiohttp 3.14.5 / soupsieve 3.0.4:** taken from the
  Dependabot updates. soupsieve 3.0.4 fixes quadratic `:dir()` matching.

## Reviewed decisions

- **Accelerate, optional tuning:** [GHSA-4j2p-28q2-5m79](https://osv.dev/vulnerability/GHSA-4j2p-28q2-5m79)
  lists checkpoint path traversal through 1.14.0 with no fixed release. The
  manifest's minimum is affected. PyPI returned no advisory for 1.15.0, but
  [the tagged 1.15.0 loader](https://github.com/huggingface/accelerate/blob/v1.15.0/src/accelerate/utils/modeling.py)
  still joins checkpoint-index filenames to the model directory without
  checking containment before loading them. An empty advisory result is not
  evidence of a fix, so the floor was not raised merely to remove the match.
  The affected functions are `load_checkpoint_in_model` and
  `load_checkpoint_and_dispatch`. **Mitigated in AutoYou 2026-11-07:**
  `train_adapter` installs `shared.accelerate_checkpoint_guard` before importing
  PEFT or Transformers. The guard covers the public loader aliases and the
  dispatch loader, rejects absolute/traversing paths, external symlinks,
  Windows alternate data streams and non-regular files (including named pipes),
  and bounds the JSON index to 32 MiB. Upstream receives a private index of
  validated paths, so replacing the original index cannot bypass validation.
  Normal Hugging Face snapshot-to-blob links and safetensors filenames remain
  supported. Temporary files honor `AUTOYOU_TEST_ROOT` and are removed even
  after a loader failure. Real Accelerate 0.33.0 and 1.15.0 loaders passed
  synthetic sharded-weight tests on Windows; WSL tests cover POSIX symlinks
  and named pipes. This is scoped to AutoYou's training process, not arbitrary
  third-party scripts importing Accelerate, and does not sandbox trusted model
  Python code or a concurrently malicious local account. Tuning remains
  optional and excluded from default server releases.

This decision is recorded in
[requirements/advisory-decisions.json](../../requirements/advisory-decisions.json)
as `inline_mitigations_already_exist`, scoped to Accelerate 0.33.0 in
`requirements/tuning.txt` and to these advisory ids, with a review-by date of
2026-11-21. The advisory command reports it under `reviewed_advisories` and
returns nonzero again for any other match, a changed floor or manifest, or once
the review date passes.

- **OpenTelemetry API, server:** [GHSA-xcq7-p3pf-p88h](https://osv.dev/vulnerability/GHSA-xcq7-p3pf-p88h)
  (CVE-2026-114603) is a quadratic regular-expression split in
  `TraceState.from_header`, fixed in 1.45.1. **Resolved 2026-11-07:**
  [ADK 2.14.0](https://github.com/google/adk-python/releases/tag/v2.14.0)
  permits the patched OpenTelemetry line. Base and macOS manifests now pin
  ADK 2.14.0, its required Google GenAI 2.30.0, and OpenTelemetry 1.45.2 /
  semantic conventions 0.66b2. The full lock was regenerated, including the
  new exporter dependencies. The advisory exception and Dependabot ignore
  rules were removed. Isolated tests cover long whitespace tracestate input,
  synthetic streaming and non-streaming LiteLLM/ADK responses, tool routing
  and session behavior. Linux base and macOS ARM64 metadata resolutions pass;
  these do not replace native release builds or device testing.
- **setuptools, voice-clone runtime:** [PYSEC-2026-3447](https://osv.dev/vulnerability/PYSEC-2026-3447)
  and [GHSA-xmj3-9gf7-h52w](https://osv.dev/vulnerability/GHSA-xmj3-9gf7-h52w)
  affect setuptools 81.0.0. **Resolved 2026-11-07:** the separate voice runtime
  now pins 84.2.1. Its installer backports only Perth 1.0.1's model-directory
  lookup from `pkg_resources` to `pathlib`, preserving the bundled model and
  watermarker. The patch requires the exact reviewed module source, operates
  only in the explicitly selected voice venv, and records before/after hashes
  in `AUTOYOU-PATCHES.json` and the wheel RECORD. Repeated installs are
  idempotent. The real bundled Perth model produced bit-identical 64,000-sample
  synthetic watermarked audio before and after the patch/setuptools upgrade.
  When borrowing a Torch environment, the installer creates its own Perth
  copy without uninstalling or modifying the donor's package. A two-venv test
  verified the local import and unchanged hashes for all 22 donor source/model
  files. Patch writes are atomic, and installer scratch files honor the test root.
  The setuptools advisory exception was removed. Existing installed voice
  environments receive this fix when the runtime installer is run again;
  the review did not modify the operator's installed runtime.

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
