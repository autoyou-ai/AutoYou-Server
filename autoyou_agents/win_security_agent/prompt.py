# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-425a59663352447455546d73-735f30b6c724956be9143abe

"""Prompt metadata for the private Windows Security Agent."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-425a59663352447455546d73-735f30b6c724956be9143abe"


AGENT_NAME = "autoyou_win_security_agent"
AGENT_DESCRIPTION = "Read-only Windows network observability attributed to local processes."
AGENT_INSTRUCTION = """Use the network snapshot tool for read-only Windows TCP and UDP connection visibility.
Report the process name, PID, executable path, protocol, local endpoint, remote endpoint,
state, source, direction, and whether the current collector is elevated. Use the capability
contract to explain when remote UDP attribution comes from opt-in WFP audit events or the
explicitly configured WinDivert FLOW provider. BIOS data is read-only inventory from native
WMI and never firmware-write authority. Never claim packet-content, person identity, or
remediation visibility that the snapshot does not provide."""
