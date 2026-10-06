"""AgentCore Platform v1.0"""

# RET-C2-281 — InputValidateNode
# Inner domain node 1: shape the validated request into the forecast payload.
#
# Division of labour with PreProcessNode, which matters for reading the rest of
# the pipeline: PreProcessNode owns the CALLER CONTRACT — bounds, finiteness,
# inert identifiers, structural caps, the directive screen — and nothing reaches
# this node that has not passed it (ForecastReportGraphNode.extract_input()
# forwards only ``validated_input``). This node owns the DOMAIN SHAPE: which
# identifier names the forecast, the business ceiling on the horizon, the
# derived series length, and the defaults for parameters the caller omitted.
#
# It is deliberately not a second copy of the contract. Two screens enforcing
# the same rule make each other unfalsifiable — remove either and the suite
# stays green — so the rules live in one place and this node fails closed if it
# is handed something the contract would not have produced.
#
# Inner node — ANONYMOUS trust. The outer PreProcessNode already enforced
# VERIFIED_EXTERNAL; inner nodes must be ANONYMOUS so the outer
# InvocationContext passes through the nested-graph boundary.
#
# Returns ONLY the state keys this node writes (partial-dict contract).

import json
import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import to_json
from src.services.request_contract import RequestContractError, finite_in_range

logger = logging.getLogger(__name__)

# Business ceiling on how far ahead a recent-window velocity model is projected.
# Beyond roughly two quarters the growth term dominates and the figure stops
# describing demand, so the horizon is capped rather than refused — a caller
# asking for more gets the longest defensible forecast, not an error.
_MAX_HORIZON_WEEKS = 26

_DEFAULT_SERVICE_LEVEL = 0.95


def _shape_history(raw_history: Any) -> List[Dict[str, Any]]:
    """Return the history as ``[{period, units}]`` records with float units.

    The contract has already bounded the series and every unit figure, so this
    only fixes the record shape. A record that does not fit the shape raises —
    it means something upstream changed, and computing a mean over it would be
    worse than stopping.
    """
    if not isinstance(raw_history, list) or not raw_history:
        raise RequestContractError("historical_sales", "history_empty")
    shaped: List[Dict[str, Any]] = []
    for index, entry in enumerate(raw_history, start=1):
        field = f"historical_sales[{index}]"
        if isinstance(entry, dict):
            units = finite_in_range(entry.get("units"), f"{field}.units", 0, 1_000_000_000)
            period = str(entry.get("period", f"P{index}"))
        else:
            units = finite_in_range(entry, f"{field}.units", 0, 1_000_000_000)
            period = f"P{index}"
        shaped.append({"period": period, "units": units})
    return shaped


class InputValidateNode(FunctionNode):
    """Shape the validated forecast request into the domain payload.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        validated_input: str  — the clean payload from PreProcessNode. The inner
                                graph receives it as its own ``user_input``
                                (GraphNode invokes the subgraph with that
                                string), so both keys are read, in that order.

    Output state keys (partial dict):
        forecast_input: str   — JSON-serialised domain payload (ADR-005)
        status:         str
        error_log:      list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raw = state.get("validated_input") or state.get("user_input", "")

        try:
            payload = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
        except (json.JSONDecodeError, ValueError):
            payload = None

        if not isinstance(payload, dict):
            emit_trace_event(
                "input_validate_failed",
                {"field": "validated_input", "reason": "not_an_object"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputValidateNode: validated_input: not_an_object"],
            }

        try:
            category = str(payload.get("category", "")).strip()
            sku = str(payload.get("sku", "")).strip()
            if not category and not sku:
                raise RequestContractError("category", "missing_identifier")

            horizon = int(
                finite_in_range(
                    payload.get("forecast_horizon_weeks"),
                    "forecast_horizon_weeks",
                    1,
                    10_000,
                    integer=True,
                )
            )
            horizon = min(horizon, _MAX_HORIZON_WEEKS)

            history = _shape_history(payload.get("historical_sales"))

            service_level = _DEFAULT_SERVICE_LEVEL
            if payload.get("service_level") is not None:
                service_level = finite_in_range(payload["service_level"], "service_level", 0.5, 0.9999)

            forecast_input: Dict[str, Any] = {
                "category": category or sku,
                "sku": sku,
                "forecast_horizon_weeks": horizon,
                "historical_sales": history,
                "history_periods": len(history),
                "current_inventory": int(
                    finite_in_range(
                        payload.get("current_inventory", 0),
                        "current_inventory",
                        0,
                        1_000_000_000,
                        integer=True,
                    )
                ),
                "lead_time_days": int(
                    finite_in_range(payload.get("lead_time_days", 0), "lead_time_days", 0, 365, integer=True)
                ),
                "unit_cost": finite_in_range(payload.get("unit_cost", 0.0), "unit_cost", 0.0, 10_000_000.0),
                "service_level": service_level,
                "seasonality_hint": str(payload.get("seasonality", "")).strip(),
            }
        except RequestContractError as refusal:
            logger.error("InputValidateNode: refused — field=%s code=%s", refusal.field, refusal.code)
            emit_trace_event(
                "input_validate_failed",
                {"field": refusal.field, "reason": refusal.code},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"InputValidateNode: refused — {refusal.field}: {refusal.code}"],
            }

        logger.info(
            "InputValidateNode: ref=%s horizon=%dw history=%d periods",
            forecast_input["category"],
            horizon,
            len(history),
        )
        emit_trace_event(
            "input_validate_complete",
            {
                "forecast_ref": forecast_input["category"],
                "horizon_weeks": horizon,
                "history_periods": len(history),
                "channel": state.get("request_channel", "unspecified"),
            },
            state,
        )

        return {
            "forecast_input": to_json(forecast_input),
            "status": AgentStatus.SUCCESS.value,
        }
