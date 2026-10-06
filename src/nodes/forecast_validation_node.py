"""AgentCore Platform v1.0"""

# RET-C2-281 — ForecastValidationNode
# Inner domain node 4: data-sufficiency / business-rule check on the forecast.
#
# Determines whether the generated forecast is trustworthy enough to act on
# automatically, or whether it must be flagged for human (merchandising)
# review before the buy recommendation is used.
#
# Review criteria (v1: rule-based):
#   - fewer than 4 historical periods            → low confidence
#   - non-positive average weekly velocity       → unreliable series
#   - coefficient of variation >= 0.75           → high volatility
#   - abs(trend_pct) >= 0.50                      → extreme trend swing
#
# Inner node — ANONYMOUS trust (review finding 5, corrected 2026-07-02).
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

_MIN_HISTORY_PERIODS = 4
_HIGH_VOLATILITY_COV = 0.75
_EXTREME_TREND_PCT = 0.50


def _evaluate_forecast(forecast_input: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate data-sufficiency / business rules; return the flags dict."""
    history_periods = int(forecast_input.get("history_periods", 0))
    avg_velocity = float(forecast_input.get("avg_weekly_velocity", 0.0))
    cov = float(forecast_input.get("coefficient_of_variation", 0.0))
    trend_pct = float(forecast_input.get("trend_pct", 0.0))

    flags: List[str] = []
    if history_periods < _MIN_HISTORY_PERIODS:
        flags.append(f"insufficient_history ({history_periods} < {_MIN_HISTORY_PERIODS} periods)")
    if avg_velocity <= 0:
        flags.append("non_positive_velocity")
    if cov >= _HIGH_VOLATILITY_COV:
        flags.append(f"high_volatility (CoV {cov:.2f} >= {_HIGH_VOLATILITY_COV})")
    if abs(trend_pct) >= _EXTREME_TREND_PCT:
        flags.append(f"extreme_trend ({trend_pct:+.0%})")

    review_required = bool(flags)
    if not flags:
        confidence_level = "high"
    elif len(flags) == 1:
        confidence_level = "medium"
    else:
        confidence_level = "low"

    return {
        "review_required": review_required,
        "confidence_level": confidence_level,
        "flags": flags,
        "min_history_periods": _MIN_HISTORY_PERIODS,
        "actual_history_periods": history_periods,
        "coefficient_of_variation": round(cov, 4),
        "high_volatility_threshold": _HIGH_VOLATILITY_COV,
        "extreme_trend_threshold": _EXTREME_TREND_PCT,
    }


class ForecastValidationNode(FunctionNode):
    """Data-sufficiency / business-rule validation for RET-C2-281.

    Evaluates whether the forecast requires human review and produces a
    validation_flags dict with full traceability for inclusion in the
    report's data-quality section.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        forecast_input: str  — JSON-serialised enriched forecast payload (ADR-005)

    Output state keys (partial dict):
        validation_flags: str   — JSON-serialised validation result dict (ADR-005)
        review_required:  bool  — True if human review is required
        status:           str
        error_log:        list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        forecast_input: Dict[str, Any] = from_json(state.get("forecast_input"), {})

        if not forecast_input:
            logger.error("ForecastValidationNode: forecast_input missing in state")
            emit_trace_event(
                "forecast_validation_failed",
                {"reason": "missing_forecast_input"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ForecastValidationNode: forecast_input missing in state"],
            }

        ref = forecast_input.get("category", "unknown")
        result = _evaluate_forecast(forecast_input)
        review_required = bool(result["review_required"])

        logger.info(
            "ForecastValidationNode: ref=%s confidence=%s review_required=%s flags=%d",
            ref,
            result["confidence_level"],
            review_required,
            len(result["flags"]),
        )
        emit_trace_event(
            "forecast_validation_complete",
            {
                "forecast_ref": ref,
                "confidence_level": result["confidence_level"],
                "review_required": review_required,
                "flag_count": len(result["flags"]),
            },
            state,
        )

        return {
            "validation_flags": to_json(result),
            "review_required": review_required,
            "status": AgentStatus.SUCCESS.value,
        }
