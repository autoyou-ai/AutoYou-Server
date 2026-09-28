# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from shared.runtime_module_loader import import_autoyou_shared_tools_module as _import_autoyou_shared_tools_module

_SMC_MOD = "autoyou_agents.shared_tools.scheduler_mission_control"

_smc = _import_autoyou_shared_tools_module(_SMC_MOD, anchor=__file__)
create_scheduler_mission_control_app = _smc.create_scheduler_mission_control_app

app = create_scheduler_mission_control_app("notify")
