---
title: Third-Party Attributions
description: Where to find SBOMs, NOTICE files, and third-party license notices.
---

# Third-Party Attributions

The optional EmotiVoice local speech runtime and Cognee memory integration are
also listed on the public [AutoYou attributions page](https://www.autoyou.me/attributions/).
They are independent open-source projects; the integrations do not imply a
formal partnership or endorsement.

The native multilingual speech integration uses the MIT-licensed
[Supertonic SDK](https://github.com/supertone-oss-archive/supertonic-py), native
ONNX Runtime, and the Apache-2.0-licensed Lingua language detector. The optional
downloaded Supertonic 3 weights retain their separate
[OpenRAIL-M model license](https://huggingface.co/supertone-oss-archive/supertonic-3/blob/aafc6e32416a594460b32413efc49d7fe4ce6d46/LICENSE).
That license is downloaded and verified alongside the weights. SDK and native
library notices belong in each build's generated legal bundle; model data is
kept in the user's writable cache.

Every distributed app build should include its own legal bundle:

- `NOTICE.txt`
- `sbom.cdx.json`
- `LICENSE`
- `THIRD-PARTY-NOTICES.md`
- `release-profile.json` where applicable

Desktop packages place these files under the app or package `Legal` directory. Mobile apps include the generated legal files as app resources/assets.

The repository-wide notice index is available in `THIRD-PARTY-NOTICES.md`.
