"""AgentCore Platform v1.0"""

# RET-C2-281 — DEPRECATED: MainNode
#
# This file is superseded by the Cat 2 nested architecture introduced in
# Wave-1 (issues #1-#8).  The `main` slot of RetailDemandForecastingReportAgent
# is now filled by ForecastReportGraphNode (src/graph/graph.py), which
# delegates the domain workflow to DomainWorkflowGraph (domain nodes in
# src/nodes/).
#
# This stub remains to avoid import errors in any code that may reference
# this module path.  It is NOT imported by graph.py or any other template code.
#
# DO NOT USE -- schedule for removal in Wave-2 cleanup.

from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

_DEPRECATION_MSG = (
    "DEPRECATED: MainNode is superseded by the Cat 2 DomainWorkflowGraph pipeline "
    "(ForecastReportGraphNode -> DomainWorkflowGraph). "
    "This stub is retained for import compatibility only."
)


class MainNode(FunctionNode):
    """DEPRECATED -- superseded by Cat 2 domain nodes in DomainWorkflowGraph."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        # S-4 audit required in every execute() (CoE calibration 2026-07-06).
        emit_trace_event(
            "main_node_deprecated_called",
            {
                "warning": "MainNode is deprecated and not part of the Cat 2 pipeline",
                "replacement": "ForecastReportGraphNode + DomainWorkflowGraph",
            },
            state,
        )
        return {
            "status": AgentStatus.SUCCESS.value,
            "result": _DEPRECATION_MSG,
        }
