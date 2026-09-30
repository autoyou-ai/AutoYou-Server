#!/bin/bash
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-e4da2d17ede9e69a1d74ba0b

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

AUTOYOU_BUILD_VERSION="81.0.0"
if [ -f "$SCRIPT_DIR/VERSION" ]; then
    AUTOYOU_BUILD_VERSION="$(head -n 1 "$SCRIPT_DIR/VERSION" | tr -d '\r\n')"
fi
echo "[INFO] AutoYou v${AUTOYOU_BUILD_VERSION} Launcher"

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

PYTHON_CMD=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command_exists "$cmd" && "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        PYTHON_CMD="$cmd"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "[ERROR] Python 3.10+ is required."
    if [ "$(uname -s)" = "Darwin" ]; then
        echo "Install with: brew install python@3.11"
    else
        echo "Install with: sudo apt-get install python3.10 python3-pip python3-venv"
    fi
    exit 1
fi

# Keep the bootstrap as the shell's process so the server's parent-watchdog
# chain remains intact when the terminal or shell is closed.
# Keep the ordinary launcher browser-capable. An explicit --without internet
# still removes the component during bootstrap.
exec "$PYTHON_CMD" scripts/bootstrap_autoyou.py --with internet "$@"
