# Server build validation

Use the platform build guides for [Windows](../servers/windows/README.md),
[macOS](../servers/macos/README.md), and [WSL/Linux](../servers/wsl/README.md).

Local source permissions and free redistribution conditions are in
[LICENSE](../LICENSE). Official release authorization is separate from local
build acknowledgement.

Before sharing an artifact, check the actual build on its target platform,
identify it as unofficial where applicable, verify its version and contents,
and include licenses and notices for the bundled dependency set. A successful
build on one platform is not evidence for another.

For source changes, follow the isolated checks in
[TESTING.md](../docs/contributors/TESTING.md). Use synthetic identifiers and
temporary runtime roots; do not test against a live configuration.

```bash
python scripts/export_public_autoyou_server.py --worktree --check
python scripts/check_release_legal_gates.py --no-generate --artifact-scope server --strict-unknown-license --allow-open-release-blockers
```

The second command checks metadata while leaving explicit release decisions
open. It is not legal or publication approval. An official release requires
the strict gate, the approved artifact scope, and the remaining human review.
