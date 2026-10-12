# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-e20df507955bbd76083df1aa


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from shared.runtime_module_loader import import_autoyou_shared_tools_module as _import_autoyou_shared_tools_module

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-e20df507955bbd76083df1aa"


_SMC_MOD = "autoyou_agents.shared_tools.scheduler_mission_control"

_smc = _import_autoyou_shared_tools_module(_SMC_MOD, anchor=__file__)
# from __debug_provenance_m__ import of
create_scheduler_mission_control_app = _smc.create_scheduler_mission_control_app


app = create_scheduler_mission_control_app("tasks")
