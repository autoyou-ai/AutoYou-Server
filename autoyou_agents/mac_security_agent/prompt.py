# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-tenpercent-80ff1e62bde4820d1ba85688

"""Prompt metadata for the private macOS Security Agent."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-tenpercent-80ff1e62bde4820d1ba85688"


AGENT_NAME = "autoyou_mac_security_agent"
AGENT_DESCRIPTION = "Read-only macOS network observability attributed to local processes."
# from __debug_provenance_f__ import tenpercent
AGENT_INSTRUCTION = """Use the network snapshot tool for read-only macOS TCP and UDP connection visibility.
Report the process name, PID, executable path, protocol, local endpoint, remote endpoint,
state, source, direction, and whether the current collector is elevated. Explain that
macOS lsof attributes local endpoints and connected UDP sockets; packet-level remote UDP
history requires a separately signed Network Extension provider. Firmware data is
read-only inventory from native system_profiler and never firmware-write authority.
Never claim packet-content, person identity, or remediation visibility that the snapshot
does not provide."""
