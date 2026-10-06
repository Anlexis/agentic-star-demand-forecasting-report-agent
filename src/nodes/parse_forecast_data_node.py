"""AgentCore Platform v1.0"""

# RET-C2-281 — ParseForecastDataNode
# Inner domain node 2: compute the demand analytics from the validated history.
#
# Responsibilities:
#   - Compute average / recent / baseline weekly velocity and volatility
#   - Derive trend direction + magnitude and a seasonality flag
#   - Classify the demand tier (high / medium / low)
#   - Project weekly demand across the requested horizon
#   - Compute the inventory math (safety stock, reorder point, recommended buy)
#
# All analytics live here (single source); the generate/format nodes render them.
#
# Inner node — ANONYMOUS trust (review finding 5, corrected 2026-07-02).
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
import math
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

# Service-level → z-score lookup for the safety-stock calculation
# (normal-distribution one-sided quantiles).
_Z_BY_SERVICE_LEVEL = [
    (0.99, 2.33),
    (0.975, 1.96),
    (0.95, 1.65),
    (0.90, 1.28),
    (0.85, 1.04),
    (0.80, 0.84),
    (0.0, 0.67),
]
# Weekly growth applied to the projection is clamped to this band so a single
# volatile period cannot produce an explosive projection.
_MAX_WEEKLY_GROWTH = 0.20


def _z_for_service_level(service_level: float) -> float:
    for threshold, z in _Z_BY_SERVICE_LEVEL:
        if service_level >= threshold:
            return z
    return _Z_BY_SERVICE_LEVEL[-1][1]


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: List[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _classify_demand_tier(avg_velocity: float) -> str:
    if avg_velocity >= 500:
        return "high"
    if avg_velocity >= 100:
        return "medium"
    return "low"


class ParseForecastDataNode(FunctionNode):
    """Compute demand analytics + inventory math for RET-C2-281.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        forecast_input: str  — JSON-serialised validated forecast payload (ADR-005)

    Output state keys (partial dict):
        forecast_input: str  — enriched JSON string (ADR-005), same key updated
        status:         str
        error_log:      list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        forecast_input: Dict[str, Any] = from_json(state.get("forecast_input"), {})

        if not forecast_input:
            logger.error("ParseForecastDataNode: forecast_input is empty or missing")
            emit_trace_event(
                "parse_forecast_data_failed",
                {"reason": "missing_forecast_input"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ParseForecastDataNode: forecast_input missing in state"],
            }

        history: List[Dict[str, Any]] = forecast_input.get("historical_sales", [])
        units = [float(h.get("units", 0)) for h in history]
        n = len(units)
        horizon = int(forecast_input.get("forecast_horizon_weeks", 1))

        # ── Velocity + volatility ─────────────────────────────────────────────
        avg_velocity = round(_mean(units), 2)
        recent_window = min(3, n)
        recent_velocity = round(_mean(units[-recent_window:]), 2) if n else 0.0
        baseline_units = units[:-recent_window] if n > recent_window else units
        baseline_velocity = round(_mean(baseline_units), 2)
        units_stdev = round(_stdev(units, avg_velocity), 2)

        # ── Trend ─────────────────────────────────────────────────────────────
        if baseline_velocity > 0:
            trend_pct = round((recent_velocity - baseline_velocity) / baseline_velocity, 4)
        else:
            trend_pct = 0.0
        if trend_pct > 0.05:
            trend_direction = "up"
        elif trend_pct < -0.05:
            trend_direction = "down"
        else:
            trend_direction = "flat"

        # ── Seasonality (explicit hint or high relative volatility) ───────────
        cov = (units_stdev / avg_velocity) if avg_velocity > 0 else 0.0
        seasonality_flag = bool(forecast_input.get("seasonality_hint")) or cov >= 0.30

        demand_tier = _classify_demand_tier(avg_velocity)

        # ── Weekly projection ─────────────────────────────────────────────────
        weekly_growth = max(-_MAX_WEEKLY_GROWTH, min(_MAX_WEEKLY_GROWTH, trend_pct))
        base = recent_velocity if recent_velocity > 0 else avg_velocity
        projected_weekly_demand: List[float] = [
            round(base * ((1 + weekly_growth) ** week), 2) for week in range(1, horizon + 1)
        ]
        total_projected_demand = round(sum(projected_weekly_demand), 2)

        # ── Inventory math ────────────────────────────────────────────────────
        lead_time_days = int(forecast_input.get("lead_time_days", 0))
        lead_time_weeks = round(lead_time_days / 7.0, 2)
        service_level = float(forecast_input.get("service_level", 0.95))
        z = _z_for_service_level(service_level)
        demand_during_lead_time = round(avg_velocity * lead_time_weeks, 2)
        safety_stock = round(z * units_stdev * math.sqrt(max(lead_time_weeks, 0.0)), 2)
        reorder_point = round(demand_during_lead_time + safety_stock, 2)
        current_inventory = int(forecast_input.get("current_inventory", 0))
        recommended_buy_qty = round(max(0.0, total_projected_demand + safety_stock - current_inventory), 2)

        enriched: Dict[str, Any] = dict(forecast_input)
        enriched.update(
            {
                "avg_weekly_velocity": avg_velocity,
                "recent_velocity": recent_velocity,
                "baseline_velocity": baseline_velocity,
                "units_stdev": units_stdev,
                "coefficient_of_variation": round(cov, 4),
                "trend_direction": trend_direction,
                "trend_pct": trend_pct,
                "seasonality_flag": seasonality_flag,
                "demand_tier": demand_tier,
                "projected_weekly_demand": projected_weekly_demand,
                "total_projected_demand": total_projected_demand,
                "lead_time_weeks": lead_time_weeks,
                "demand_during_lead_time": demand_during_lead_time,
                "safety_stock": safety_stock,
                "reorder_point": reorder_point,
                "recommended_buy_qty": recommended_buy_qty,
                "service_level_z": z,
            }
        )

        logger.info(
            "ParseForecastDataNode: ref=%s tier=%s trend=%s avg_velocity=%.2f buy=%.2f",
            enriched.get("category"),
            demand_tier,
            trend_direction,
            avg_velocity,
            recommended_buy_qty,
        )
        emit_trace_event(
            "parse_forecast_data_complete",
            {
                "forecast_ref": enriched.get("category"),
                "demand_tier": demand_tier,
                "trend_direction": trend_direction,
                "total_projected_demand": total_projected_demand,
                "recommended_buy_qty": recommended_buy_qty,
            },
            state,
        )

        return {
            "forecast_input": to_json(enriched),
            "status": AgentStatus.SUCCESS.value,
        }
