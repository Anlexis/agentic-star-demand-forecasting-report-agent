"""AgentCore Platform v1.0"""

# RET-C2-281 — OutputFormatNode (inner domain node 5, last in DomainWorkflowGraph)
# Assembles the final demand-forecast report document from report_sections and
# validation_flags.  This is the last inner node — it produces the
# forecast_report string that the outer PostProcessNode will S-3-gate.
#
# Inner node — ANONYMOUS trust (review finding 5, corrected 2026-07-02).
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
from typing import Any, ClassVar, Dict, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json

logger = logging.getLogger(__name__)

# Section order for the final report.
_SECTION_ORDER = [
    "demand_summary",
    "forecast_table",
    "buy_recommendation",
    "inventory_target",
    "seasonal_trend_analysis",
    "assumptions_and_method",
    "data_quality_notes",
]

# Human-readable section headers.
_SECTION_HEADERS: Dict[str, str] = {
    "demand_summary": "1. Demand Summary",
    "forecast_table": "2. Weekly Demand Forecast",
    "buy_recommendation": "3. Buy Recommendation",
    "inventory_target": "4. Inventory Target",
    "seasonal_trend_analysis": "5. Seasonal & Trend Analysis",
    "assumptions_and_method": "6. Assumptions & Method",
    "data_quality_notes": "7. Data Quality Notes",
}

_SEPARATOR = "=" * 72
_SUBSEP = "-" * 72


def _assemble_report(
    forecast_ref: str,
    sections: Dict[str, str],
    validation: Dict[str, Any],
) -> str:
    """Assemble the full demand-forecast report from sections + validation."""
    lines = [
        _SEPARATOR,
        "RETAIL DEMAND FORECAST REPORT",
        f"Category / SKU: {forecast_ref}",
        _SEPARATOR,
        "",
    ]

    for key in _SECTION_ORDER:
        header = _SECTION_HEADERS.get(key, key.replace("_", " ").title())
        content = sections.get(key, "(Section not generated)")
        lines.append(header)
        lines.append(_SUBSEP)
        lines.append(content)
        lines.append("")

    # Append review trailer.
    review_required = validation.get("review_required", False)
    confidence = str(validation.get("confidence_level", "unknown")).upper()
    flags = validation.get("flags", [])
    lines += [
        _SEPARATOR,
        "FORECAST GOVERNANCE NOTE",
        _SUBSEP,
        f"  Forecast Confidence:  {confidence}",
        f"  Human Review Required: {'YES' if review_required else 'NO'}",
        f"  Review Triggers:      {', '.join(flags) if flags else 'none'}",
        _SEPARATOR,
    ]

    return "\n".join(lines)


class OutputFormatNode(FunctionNode):
    """Assemble the final demand-forecast report document (inner domain node).

    Reads report_sections and validation_flags from State, renders the full
    forecast report text, and writes it to forecast_report (and result) for
    the outer PostProcessNode.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        report_sections:  str  — JSON-serialised section dict (ADR-005)
        validation_flags: str  — JSON-serialised validation result (ADR-005)
        forecast_input:   str  — JSON-serialised forecast payload (ADR-005)

    Output state keys (partial dict):
        forecast_report: str
        result:          str  (same as forecast_report — backbone convention)
        status:          str
        error_log:       list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        sections: Dict[str, str] = from_json(state.get("report_sections"), {})
        validation: Dict[str, Any] = from_json(state.get("validation_flags"), {})
        forecast_input: Dict[str, Any] = from_json(state.get("forecast_input"), {})

        forecast_ref = forecast_input.get("category", "unknown")

        if not sections:
            logger.error(
                "OutputFormatNode: report_sections missing in state for ref=%s",
                forecast_ref,
            )
            emit_trace_event(
                "output_format_failed",
                {"reason": "missing_report_sections", "forecast_ref": forecast_ref},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"OutputFormatNode: report_sections missing for ref={forecast_ref}"],
            }

        # ── Assemble the report ───────────────────────────────────────────────
        report = _assemble_report(forecast_ref, sections, validation)

        logger.info(
            "OutputFormatNode: ref=%s report_chars=%d review_required=%s",
            forecast_ref,
            len(report),
            validation.get("review_required", False),
        )
        emit_trace_event(
            "output_format_complete",
            {
                "forecast_ref": forecast_ref,
                "report_length": len(report),
                "section_count": len(sections),
                "review_required": validation.get("review_required", False),
            },
            state,
        )

        return {
            "forecast_report": report,
            "result": report,
            "status": AgentStatus.SUCCESS.value,
        }
