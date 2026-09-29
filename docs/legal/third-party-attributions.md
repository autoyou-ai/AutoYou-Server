---
title: Third-Party Attributions
description: Where to find SBOMs, NOTICE files, and third-party license notices.
---

# Third-Party Attributions

The optional EmotiVoice local speech runtime and Cognee memory integration are
also listed on the public [AutoYou attributions page](https://www.autoyou.me/attributions/).
They are independent open-source projects; the integrations do not imply a
formal partnership or endorsement.

Every distributed app build should include its own legal bundle:

- `NOTICE.txt`
- `sbom.cdx.json`
- `LICENSE`
- `THIRD-PARTY-NOTICES.md`
- `release-profile.json` where applicable

Desktop packages place these files under the app or package `Legal` directory. Mobile apps include the generated legal files as app resources/assets.

The repository-wide notice index is available in `THIRD-PARTY-NOTICES.md`.
