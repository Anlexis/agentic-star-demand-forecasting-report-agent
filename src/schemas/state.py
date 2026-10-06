"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.
#
# RET-C2-281 — Retail Demand Forecasting Report Agent
# Two-layer nested Cat 2 graph: outer backbone (AgentBaseGraph) + inner
# domain workflow (BaseGraph).  Fields below cover both layers.
#
# ADR-005 compliance: all dict/list-valued fields are stored as JSON-
# serialized Optional[str].  Use to_json() / from_json() helpers below
# at every producer and consumer node — one contract end-to-end.
# Never type a dict/list field as a bare dict/list; that causes msgpack
# serialization failures and a CoE Stage-6 state-contract finding.

import json
from typing import Any, NotRequired, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> Optional[str]:
    """Serialize a value to a JSON string for State storage (ADR-005)."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON string from State storage (ADR-005)."""
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class State(AgentState):
    """Flat TypedDict for RET-C2-281.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.

    ADR-005: dict/list fields use JSON-serialized Optional[str].
    formatted_output is NOT re-declared here — it is inherited from AgentState.
    """

    # ------------------------------------------------------------------
    # Outer layer — set by PreProcessNode (pre_process backbone, S-1)
    # ------------------------------------------------------------------

    # Validated and normalised JSON string of the forecast request.
    # Produced by PreProcessNode; consumed by inner InputValidateNode.
    validated_input: NotRequired[Optional[str]]

    # JSON-serialised channel/request metadata dict (ADR-005: stored as str).
    # Shape: {"source": str, "channel": str, "forecast_ref": str}
    enriched_context: NotRequired[Optional[str]]

    # Validated caller channel, taken from the request's input_context. Written
    # by PreProcessNode on the outer layer and carried into the inner graph by
    # src/graph/context_bridge.py, because the framework does not forward
    # input_context across a nested-graph boundary. Always an inert identifier —
    # it is an audit dimension, never rendered into the report.
    request_channel: NotRequired[Optional[str]]

    # Manifest-forwarded runtime config (SR Medium, scaffold issue #12).
    # Seeded by DomainWorkflowGraph._extra_initial_state() from
    # config/agent.yaml llm.system_prompt_template so GenerateForecastSectionsNode
    # can observe the declared prompt on the real .invoke() path (the SDK does
    # not thread graph config into node.execute()). v1 generation is
    # deterministic; this is surfaced for the production LLM layer + audit trace.
    system_prompt_template: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Inner layer — domain nodes (DomainWorkflowGraph)
    # ------------------------------------------------------------------

    # JSON-serialised parsed + enriched forecast payload (ADR-005: stored as str).
    # Written by InputValidateNode, then enriched in place by ParseForecastDataNode.
    # Shape: {category, sku, forecast_horizon_weeks, historical_sales (list),
    #   history_periods (int), current_inventory (int), lead_time_days (int),
    #   unit_cost (float), service_level (float), seasonality_hint (str),
    #   avg_weekly_velocity (float), recent_velocity (float),
    #   baseline_velocity (float), units_stdev (float), trend_direction (str),
    #   trend_pct (float), seasonality_flag (bool), demand_tier (str),
    #   projected_weekly_demand (list), total_projected_demand (float),
    #   lead_time_weeks (float), demand_during_lead_time (float),
    #   safety_stock (float), reorder_point (float), recommended_buy_qty (float)}
    forecast_input: NotRequired[Optional[str]]

    # JSON-serialised report section dict (ADR-005: stored as str).
    # Keys match the 7 report sections:
    #   demand_summary, forecast_table, buy_recommendation, inventory_target,
    #   seasonal_trend_analysis, assumptions_and_method, data_quality_notes
    # Each value is the rendered text for that section.
    report_sections: NotRequired[Optional[str]]

    # JSON-serialised data-sufficiency / business-rule check result
    # (ADR-005: stored as str).
    # Shape: {review_required: bool, confidence_level: str, flags: list[str],
    #   min_history_periods: int, actual_history_periods: int,
    #   coefficient_of_variation: float}
    validation_flags: NotRequired[Optional[str]]

    # True if the forecast needs human review before acting on the buy
    # recommendation (insufficient history, non-positive velocity, or high
    # volatility).
    review_required: NotRequired[Optional[bool]]

    # Final formatted demand-forecast report document (plain text).
    # Assembled by inner OutputFormatNode from report_sections + validation_flags.
    forecast_report: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Outer layer — set by PostProcessNode (post_process backbone, S-3)
    # ------------------------------------------------------------------

    # Primary result surfaced to the caller.
    # Set to the same content as forecast_report after the S-3 gate passes.
    # formatted_output (from AgentState) is also set by PostProcessNode.
    result: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Tracing / audit — framework-managed; do NOT write from node code
    # ------------------------------------------------------------------

    trace_id: NotRequired[Optional[str]]
    correlation_id: NotRequired[Optional[str]]
    # node_history inherited from AgentState
