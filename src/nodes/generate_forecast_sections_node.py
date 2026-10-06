"""AgentCore Platform v1.0"""

# RET-C2-281 — GenerateForecastSectionsNode
# Inner domain node 3: generate all 7 demand-forecast report sections.
#
# v1 implementation: DETERMINISTIC template-based generation from the analytics
# computed in ParseForecastDataNode — there is NO LLM or Jinja render in SDK v1
# (no framework.services.llm_client). The manifest-declared
# llm.system_prompt_template (config/agent.yaml) is surfaced to this node —
# seeded into state by DomainWorkflowGraph._extra_initial_state() and recorded in
# the audit trace — so the production LLM narrative layer can later be wired over
# the SAME computed figures without changing this contract. It is documented
# here, never faked as an LLM call.
#
# Sections produced:
#   1. demand_summary
#   2. forecast_table
#   3. buy_recommendation
#   4. inventory_target
#   5. seasonal_trend_analysis
#   6. assumptions_and_method
#   7. data_quality_notes
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


def _section_demand_summary(d: Dict[str, Any]) -> str:
    ref = d.get("category", "N/A")
    horizon = d.get("forecast_horizon_weeks", 0)
    tier = str(d.get("demand_tier", "unknown")).upper()
    trend = str(d.get("trend_direction", "flat")).upper()
    lines = [
        f"Category / SKU:        {ref}",
        f"Forecast Horizon:      {horizon} week(s)",
        f"Demand Tier:           {tier}",
        f"Avg Weekly Velocity:   {d.get('avg_weekly_velocity', 0)} units",
        f"Recent Velocity:       {d.get('recent_velocity', 0)} units/week",
        f"Trend:                 {trend} ({d.get('trend_pct', 0):+.1%})",
        f"Total Projected Demand: {d.get('total_projected_demand', 0)} units over horizon",
    ]
    return "\n".join(lines)


def _section_forecast_table(d: Dict[str, Any]) -> str:
    projected: List[float] = d.get("projected_weekly_demand", [])
    if not projected:
        return "No forecast could be projected (insufficient data)."
    lines = ["  Week   Projected Units"]
    for week, units in enumerate(projected, start=1):
        lines.append(f"  W{week:<5} {units:>14,.0f}")
    lines.append(f"  {'TOTAL':<6} {d.get('total_projected_demand', 0):>14,.0f}")
    return "\n".join(lines)


def _section_buy_recommendation(d: Dict[str, Any]) -> str:
    buy = d.get("recommended_buy_qty", 0)
    current = d.get("current_inventory", 0)
    unit_cost = d.get("unit_cost", 0.0)
    est_spend = round(float(buy) * float(unit_cost), 2)
    action = "PLACE REPLENISHMENT ORDER" if buy > 0 else "NO BUY REQUIRED — current cover is sufficient"
    lines = [
        f"  Recommended Buy Qty:   {buy:,.0f} units",
        f"  Current Inventory:     {current:,} units",
        f"  Estimated Order Spend: {est_spend:,.2f} (at unit cost {unit_cost})",
        f"  Recommended Action:    {action}",
    ]
    return "\n".join(lines)


def _section_inventory_target(d: Dict[str, Any]) -> str:
    lines = [
        f"  Service Level:            {d.get('service_level', 0):.0%} (z={d.get('service_level_z', 0)})",
        f"  Lead Time:                {d.get('lead_time_days', 0)} days ({d.get('lead_time_weeks', 0)} weeks)",
        f"  Demand During Lead Time:  {d.get('demand_during_lead_time', 0):,.0f} units",
        f"  Safety Stock:             {d.get('safety_stock', 0):,.0f} units",
        f"  Reorder Point:            {d.get('reorder_point', 0):,.0f} units",
    ]
    return "\n".join(lines)


def _section_seasonal_trend(d: Dict[str, Any]) -> str:
    seasonal = d.get("seasonality_flag", False)
    hint = d.get("seasonality_hint") or "none provided"
    trend = str(d.get("trend_direction", "flat"))
    cov = d.get("coefficient_of_variation", 0.0)
    lines = [
        f"  Seasonality Detected:  {'YES' if seasonal else 'NO'} (hint: {hint})",
        f"  Demand Trend:          {trend} ({d.get('trend_pct', 0):+.1%} recent vs baseline)",
        f"  Baseline Velocity:     {d.get('baseline_velocity', 0)} units/week",
        f"  Recent Velocity:       {d.get('recent_velocity', 0)} units/week",
        f"  Volatility (CoV):      {cov:.2f}",
    ]
    if seasonal:
        lines.append(
            "  Note: seasonal pattern present — align buy timing with the demand peak "
            "and reassess safety stock ahead of the season."
        )
    return "\n".join(lines)


def _section_assumptions(d: Dict[str, Any]) -> str:
    return (
        "  - Projection base is the recent-window velocity, grown by the recent-vs-baseline\n"
        "    trend (clamped to +/-20% per week to avoid runaway extrapolation).\n"
        "  - Safety stock uses a normal-demand model: z * sigma * sqrt(lead_time_weeks).\n"
        "  - Reorder point = expected demand during lead time + safety stock.\n"
        "  - Recommended buy = projected horizon demand + safety stock - current inventory.\n"
        "  - v1 is a deterministic rule-based forecast; production wires an LLM narrative\n"
        "    layer via prompts/demand_forecast_report.j2 over the same computed figures."
    )


def _section_data_quality(d: Dict[str, Any]) -> str:
    periods = d.get("history_periods", 0)
    cov = d.get("coefficient_of_variation", 0.0)
    lines = [
        f"  Historical Periods Used: {periods}",
        f"  Demand Volatility (CoV): {cov:.2f}",
    ]
    if periods < 4:
        lines.append("  WARNING: fewer than 4 historical periods — low forecast confidence.")
    if cov >= 0.75:
        lines.append("  WARNING: high demand volatility — treat projections as indicative only.")
    if d.get("avg_weekly_velocity", 0) <= 0:
        lines.append("  WARNING: non-positive average velocity — verify the input series.")
    return "\n".join(lines)


class GenerateForecastSectionsNode(FunctionNode):
    """Generate all 7 demand-forecast report sections.

    v1: deterministic template-based synthesis over the ParseForecastDataNode
    analytics — no LLM in SDK v1.
    Production: the manifest-declared llm.system_prompt_template drives an LLM
    narrative over the same computed figures (surfaced here via state/config;
    documented, not faked).

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        forecast_input:         str  — JSON-serialised enriched forecast payload (ADR-005)
        system_prompt_template: str  — declared prompt path, seeded by
                                       DomainWorkflowGraph._extra_initial_state()
                                       (optional; inert in the deterministic v1 path)

    Output state keys (partial dict):
        report_sections: str  — JSON-serialised section dict (ADR-005)
        status:          str
        error_log:       list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        forecast_input: Dict[str, Any] = from_json(state.get("forecast_input"), {})

        # Resolve the declared prompt template (config/agent.yaml
        # llm.system_prompt_template). It is seeded into state by
        # DomainWorkflowGraph._extra_initial_state() (the SDK does not thread the
        # graph config into execute()'s `config` arg on the real .invoke() path),
        # with a fallback to the direct `config` arg used by unit-level calls.
        # v1 is DETERMINISTIC: the sections below are synthesised from the
        # ParseForecastDataNode analytics — there is no LLM/Jinja render in SDK
        # v1. The declared template path is surfaced (and recorded in the audit
        # trace) so the production LLM layer can wire over the SAME figures; it
        # is documented here, never faked as an LLM call.
        configurable = (config or {}).get("configurable", {})
        prompt_template = state.get("system_prompt_template") or configurable.get("system_prompt_template") or ""

        if not forecast_input:
            logger.error("GenerateForecastSectionsNode: forecast_input missing in state")
            emit_trace_event(
                "generate_forecast_sections_failed",
                {"reason": "missing_forecast_input"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["GenerateForecastSectionsNode: forecast_input missing in state"],
            }

        ref = forecast_input.get("category", "unknown")

        sections: Dict[str, str] = {
            "demand_summary": _section_demand_summary(forecast_input),
            "forecast_table": _section_forecast_table(forecast_input),
            "buy_recommendation": _section_buy_recommendation(forecast_input),
            "inventory_target": _section_inventory_target(forecast_input),
            "seasonal_trend_analysis": _section_seasonal_trend(forecast_input),
            "assumptions_and_method": _section_assumptions(forecast_input),
            "data_quality_notes": _section_data_quality(forecast_input),
        }

        logger.info(
            "GenerateForecastSectionsNode: ref=%s sections=%d",
            ref,
            len(sections),
        )
        emit_trace_event(
            "generate_forecast_sections_complete",
            {
                "forecast_ref": ref,
                "section_count": len(sections),
                "section_keys": sorted(sections.keys()),
                "prompt_template": prompt_template,
            },
            state,
        )

        return {
            "report_sections": to_json(sections),
            "status": AgentStatus.SUCCESS.value,
        }
