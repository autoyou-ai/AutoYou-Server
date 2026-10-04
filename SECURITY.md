# Security policy

## Report privately

Email **security@autoyou.me** with the subject `AutoYou Server security report`.
If private vulnerability reporting is enabled on this repository, you may
instead use GitHub's **Security > Report a vulnerability**. Do not use public
issues, discussions, or pull requests for undisclosed vulnerabilities.

Include:

- Affected version or commit, operating system, and dependency/profile details.
- The affected component, necessary configuration, and expected trust boundary.
- Reproduction steps using synthetic data on an installation you control.
- Expected and observed behavior, likely impact, and a minimal proposed fix
  if available.

Do not send passwords, tokens, private keys, personal messages, production
databases, or other people's information. Redact logs and screenshots. Ask for
a suitable transfer method before sending sensitive supporting material.

## Review and disclosure

Maintainers will assess impact, request information where needed, and coordinate
remediation and disclosure with the reporter and affected upstream projects.
Response and repair times depend on severity, available maintainers, and
upstream fixes; this policy is not a response-time commitment or paid support
agreement. Do not assume a report was received without acknowledgement.

We welcome good-faith research performed lawfully on systems and data you own
or are explicitly authorized to test. Avoid service disruption, persistence,
social engineering, data extraction, and access beyond what is necessary to
demonstrate the issue. Stop and report if unexpected personal data is exposed.
This policy does not grant permission to test third-party systems or waive
another party's rights. No bounty or payment is promised by a report.

## Scope and support

Relevant reports include authentication and pairing failures, unauthorized
agent execution, credential or private-data exposure, unsafe update or build
paths, and exploitable dependency vulnerabilities.

Start with the current source on the default branch or identify the exact
affected release. No long-term-support branch or fixed patch schedule is
promised here. Reports affecting older releases remain useful; a fix may be
provided only in a newer version. Unofficial builds and modifications should
be identified so maintainers can distinguish upstream behavior.

Ordinary defects, configuration help, and feature proposals belong in
[SUPPORT.md](SUPPORT.md). A suspected security issue should still be reported
privately even if its impact is uncertain.

## Dependency and release security

Dependency manifests, advisories, an SBOM, and source review cover different
things. None certifies an installation as vulnerability-free. See
[requirements/README.md](requirements/README.md) for the audit method and limits.

Treat dependency installation and agent execution as code execution with the
permissions of that process. Keep build environments separate from production
credentials. Review upstream changes and immutable dependency identifiers,
minimize CI permissions, and protect publishing credentials. Publishing source
or an SBOM does not grant permission to alter this repository or its releases.

Distribute licenses, notices, and an SBOM appropriate to the actual artifact.
A source-manifest inventory must not be presented as the resolved inventory of
a compiled release. Report suspected compromised packages or signing material
privately and follow the relevant upstream incident process.

## Operator controls

Use unique credentials, current dependencies, backups, and the narrowest
necessary network and agent permissions. Verify downloaded artifacts against
their published provenance where available. Review exposure before enabling
remote access, tunnels, browser automation, messaging, or third-party models.

Transport encryption protects particular connections. Endpoints, provider
integrations, and connection metadata have separate trust boundaries.
Neither local operation nor encrypted transport makes arbitrary agent code
safe or guarantees confidentiality across every integration.
