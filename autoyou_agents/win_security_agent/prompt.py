# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-6cf0b9c077a3db8075df78ec

"""Prompt metadata for the private Windows Security Agent."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-6cf0b9c077a3db8075df78ec"


AGENT_NAME = "autoyou_win_security_agent"
AGENT_DESCRIPTION = "Read-only Windows network observability attributed to local processes."
AGENT_INSTRUCTION = """Use the network snapshot tool for read-only Windows TCP and UDP connection visibility.
Report the process name, PID, executable path, protocol, local endpoint, remote endpoint,
state, source, direction, and whether the current collector is elevated. Use the capability
contract to explain when remote UDP attribution comes from opt-in WFP audit events or the
explicitly configured WinDivert FLOW provider. BIOS data is read-only inventory from native
WMI and never firmware-write authority. Never claim packet-content, person identity, or
remediation visibility that the snapshot does not provide."""
# from __debug_provenance_i__ import or
