"""AgentCore Platform v1.0"""

# RET-C2-281 — PreProcessNode
# Outer backbone pre_process slot: the trust gate plus the caller-data contract.
#
# Responsibilities:
#   - Enforce VERIFIED_EXTERNAL trust (required_trust_level)
#   - Apply the request contract (src/services/request_contract.py) to the whole
#     caller payload: bounded, finite numbers; inert identifiers for everything
#     that renders; structural caps; a directive screen over the parsed body
#   - Write validated_input (the clean payload, re-serialised) plus the audit
#     context to State
#   - Emit an audit event for every validation decision
#
# This node owns the caller contract, so it is where refusal is enforced. The
# platform's own input gate runs before execute() and blocks some of the same
# shapes, but it is configuration the template does not control — a template
# that relies on it refuses nothing of its own where that gate is absent or
# configured off. The contract tests therefore call execute() DIRECTLY, with no
# framework wrapper in front, so what is being measured is this node's refusal
# and not the platform's.
#
# Returns ONLY the state keys this node writes (partial-dict contract).

import json
import logging
from typing import Any, ClassVar, Dict, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import to_json
from src.services.request_contract import (
    RequestContractError,
    dropped_field_count,
    inert_identifier,
    validate_request,
)

logger = logging.getLogger(__name__)


def _refusal(field: str, code: str) -> Dict[str, Any]:
    """State delta for a refused request.

    ``formatted_output`` is set here rather than left to the backbone default.
    The backbone surfaces ``formatted_output or result`` and projects neither
    ``error_log`` nor the status reason, so without this a caller whose request
    was refused receives a null output and no way to tell a rejected field from
    an internal failure. The notice carries only the field name and the reason
    code — both drawn from sets this template authors, never from caller text —
    so it is actionable without being a channel back out of the screen.
    """
    return {
        "formatted_output": (
            f"[Request refused — {field}: {code}. No forecast was produced. " f"Correct the named field and retry.]"
        ),
        "status": AgentStatus.ERROR.value,
        "error_log": [f"PreProcessNode: request refused — {field}: {code}"],
    }


class PreProcessNode(FunctionNode):
    """Trust gate and caller-data contract for RET-C2-281.

    This is the outer backbone's pre_process slot and the only node that
    requires VERIFIED_EXTERNAL, so an anonymous or unauthenticated caller is
    refused here. The inner domain nodes all run at ANONYMOUS and never see raw
    caller text: ForecastReportGraphNode.extract_input() forwards only
    ``validated_input``, which exists solely because this node produced it.

    Input state keys:
        user_input:     str   — caller-supplied JSON forecast request
        input_context:  dict  — optional structured caller metadata

    Output state keys (partial dict):
        validated_input:  str        — the clean payload, re-serialised
        enriched_context: str        — JSON-serialised audit metadata (ADR-005)
        request_channel:  str        — validated channel, bridged to the inner graph
        status:           str
        error_log:        list[str]  — set only on ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        user_input = state.get("user_input", "")
        input_context = state.get("input_context", {}) or {}

        try:
            payload = validate_request(user_input)
        except RequestContractError as refusal:
            # The field name and the code are the whole message. The rejected
            # value never appears here, in the audit event, or in the log line:
            # an error is a caller-visible channel, and echoing what was refused
            # would hand back a way through the screen that refused it.
            logger.warning(
                "PreProcessNode: request refused — field=%s code=%s",
                refusal.field,
                refusal.code,
            )
            emit_trace_event(
                "pre_process_validation_failed",
                {"field": refusal.field, "reason": refusal.code},
                state,
            )
            return _refusal(refusal.field, refusal.code)

        # The caller channel is metadata, not forecast data, so it is validated
        # separately and held to the same inert alphabet. An absent or
        # unrecognised channel degrades to the declared default rather than
        # failing a request that is otherwise well formed.
        channel = "unspecified"
        raw_channel = input_context.get("channel") if isinstance(input_context, dict) else None
        if raw_channel:
            try:
                channel = inert_identifier(raw_channel, "input_context.channel")
            except RequestContractError as refusal:
                emit_trace_event(
                    "pre_process_validation_failed",
                    {"field": refusal.field, "reason": refusal.code},
                    state,
                )
                return _refusal(refusal.field, refusal.code)

        forecast_ref = payload.get("sku") or payload.get("category") or "unknown"
        normalised_json = json.dumps(payload, ensure_ascii=False)
        dropped = dropped_field_count(user_input)

        logger.info(
            "PreProcessNode: validated forecast_ref=%s fields=%d dropped=%d",
            forecast_ref,
            len(payload),
            dropped,
        )
        emit_trace_event(
            "pre_process_validated",
            {
                "forecast_ref": forecast_ref,
                "declared_fields": sorted(payload.keys()),
                "undeclared_fields_dropped": dropped,
                "channel": channel,
            },
            state,
        )

        return {
            "validated_input": normalised_json,
            "enriched_context": to_json(
                {
                    "source": "RetailDemandForecastingReportAgent",
                    "channel": channel,
                    "forecast_ref": forecast_ref,
                }
            ),
            "request_channel": channel,
            "status": AgentStatus.SUCCESS.value,
        }
