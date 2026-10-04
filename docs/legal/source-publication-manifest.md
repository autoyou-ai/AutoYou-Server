# Public source boundary

This repository is the standalone AutoYou Server source distribution. Its
[LICENSE](../../LICENSE) and separately identified component licenses govern
use, modification, building, and sharing.

## Publishable Source Scope

The intended public tree contains server runtime modules, the local
administration interface, built-in agents, optional integration adapters,
dependency manifests, platform server build tools, synthetic test fixtures,
and the documentation needed to work on these components.

The reviewed inference subset in `vendor/emotivoice/` retains its own license.
Model checkpoints and installed dependencies are not supplied merely because
their integration code is present.

Client applications and hosted services are separately distributed products.
Ordinary server development must be possible using this repository without
access to another workspace, private account, or release infrastructure.

## Do Not Publish In Source Releases

Do not publish production secrets, API keys, certificates, signing keys,
other credentials, provisioning material, personal data,
runtime configuration, keystores, databases, device captures, private operational
records, unpublished security reports, or material belonging to other projects.
Ignored files, local environments, caches, generated installers, and build
outputs are not source-release inputs.

Public examples and screenshots must use reviewed synthetic values. A file
being tracked by Git is not evidence that it is safe to publish.

## Checks before publication

Run the current-tree check from this repository:

```bash
python scripts/export_public_autoyou_server.py --worktree --check
```

The checker enforces an allowlist and recognizes selected sensitive paths and
content patterns. It does not prove the absence of secrets or inspect every
historical object. Review the final tree, history, tags, attachments, and
release artifacts before changing repository visibility. A clean snapshot does
not make its previous history safe.

Local source builds and free unofficial sharing are governed by LICENSE;
official signing and hosted service credentials are not part of the public
source grant. Maintainer review records are kept outside this repository.

Before an official release, run the server release legal gate with `python scripts/check_release_legal_gates.py --artifact-scope server --strict-unknown-license`.
Supply the operator's external review file through `AUTOYOU_RELEASE_CHECKLIST`
or `--release-checklist`. Contributor checks may use
`--allow-open-release-blockers` for metadata validation without that private input.

Publication of this source set does not publish or license OpenStorey-hosted services.
Those services and separately distributed client applications have their own
terms and release processes.

## Notices and SBOMs

Keep attribution and component-license information public where appropriate.
Generate release inventories from the actual resolved artifact. Label
manifest-derived inventories as declarations rather than proof of shipped
contents. Inspect generated documents for credentials, machine paths, personal
identifiers, and unrelated product information before publishing them.

An SBOM helps identify affected versions. It is neither a security certificate
nor a substitute for required third-party notices or source obligations.
