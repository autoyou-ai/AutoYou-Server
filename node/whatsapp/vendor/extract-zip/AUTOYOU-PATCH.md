Based on extract-zip 2.0.1 (BSD-2-Clause), published by max-mapper/extract-zip.
The local version 2.0.2-autoyou.1 is an AutoYou patch, not an upstream release.

GHSA-jmr9-qjv8-65gv permits archive symlinks to point outside the destination.
This patch validates relative and resolved link targets and destination parents
before creating directories. It rejects absolute links and uses exclusive file
creation to prevent writes through pre-existing destination links.

Browser downloads extract into fresh directories. Re-extracting over existing
files now fails; completed Puppeteer cache entries are reused without extraction.
Internal relative framework symlinks remain supported.

Keep the upstream LICENSE. Replace this local patch with a verified upstream
fix when available; preserve the archive-boundary regression check.
