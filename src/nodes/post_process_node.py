"""AgentCore Platform v1.0"""

# RET-C2-281 — PostProcessNode
# Outer backbone post_process slot: the output gate, and the only node that
# publishes the demand-forecast report to the caller.
#
# The gate enforces two properties on the assembled report, both stated in
# docs/02_design.md as the output contract:
#
#   1. NO CREDENTIAL MATERIAL.  Scanned with the UNION of the platform's own
#      credential detector and this template's local patterns. The union is the
#      point. Delegating entirely to the platform detector would look like a
#      tightening and be a widening of what gets through: its patterns describe
#      credential FORMATS (`sk_live_`, `AKIA…`, JWTs, database URIs) and match
#      nothing of the shape `password=…`, which the local set catches. Keeping
#      only the local set is the mirror-image bypass — measured on this
#      template before the union: an `AKIA…` string riding in a caller field
#      passed the domain gate, and containment came from the platform raising
#      inside its own output gate afterwards, which DISCARDS this node's result
#      dict wholesale and with it every field this node cleared. A detector gap
#      on either side is a containment bypass.
#
#   2. EVERY RENDERED NUMBER IS FINITE.  The report exists to state unit counts
#      and a spend figure, so `nan` or `inf` in it is not a cosmetic defect —
#      it is a forecast that says nothing while reporting success. The request
#      contract already rejects non-finite caller input, so this is the
#      backstop that would catch an arithmetic path introduced later.
#
# There is no monetary rounding grid here, and that is a deliberate reading of
# the report rather than an omission: every figure it prints is an exact unit
# count or an exact derived spend, and rounding those to a coarse grid would
# make the document wrong rather than safer. docs/02_design.md records the
# invariant this template does hold in the grid's place.
#
# ON A VIOLATION THE NODE CLEARS EVERY OUTPUT-BEARING FIELD.  Raising is not
# containment: the backbone's get_output() returns
# `formatted_output or result`, and it does so on an error status too, so a
# gate that merely refuses still ships the un-gated text through the fallback.
# The caller-visible replacement is a fixed notice carrying a code from a
# closed set — not the error log, not the scanner's finding, not the matched
# text. An error envelope is a caller-visible channel; node-authored text in it
# is a channel out of the gate that just closed.
#
# The domain gate is a module-level function called inline from execute(). It is
# deliberately NOT named `_security_gate_output` and NOT an instance hook:
# FunctionNode._security_gate_output is @final and enforced at class-definition
# time, and the `_extra_security_gate_*` hooks are auto-wrapped into the graph
# chain by the platform.
#
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
import re
from typing import Any, ClassVar, Dict, Final, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from framework.security.credential_detector import detect_credentials
from shared.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

# Local credential patterns. Every entry here catches something the platform
# detector does not, which is the only reason to keep a local set at all:
#   - the `(sk|pk|ak)-` family at 16 characters, below the platform's 20
#   - `Bearer <token>` at 8 characters, below the platform's 16
#   - `password=` / `secret:` / `api_key=` assignments, which the platform's
#     format-based patterns do not describe at any length
_LOCAL_CREDENTIAL_PATTERNS: Final[List[Tuple[str, str]]] = [
    (r"(?:sk|pk|ak)-[A-Za-z0-9]{16,}", "api_key_pattern"),
    (r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "jwt_pattern"),
    (r"Bearer\s+[A-Za-z0-9_\-\.]{8,}", "bearer_token"),
    (
        r"(?:password|passwd|secret|api_key|token|access_key|private_key)" r"\s*[:=]\s*\S{8,}",
        "credential_assignment",
    ),
]

# A standalone non-finite numeric token. The guards keep it from firing inside a
# longer word or identifier, so "information" and "Infrastructure" are not
# matches while a rendered `inf`, `-inf` or `nan` is.
_NON_FINITE_TOKEN: Final = re.compile(
    r"(?<![A-Za-z0-9_.\-])[+-]?(?:nan|inf(?:inity)?)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

# Every code the caller can ever see in a withheld-report notice. The set is
# closed by construction and asserted before the notice is built, so no
# node-authored text, scanner finding or matched value can reach the caller
# through this channel.
VIOLATION_CODES: Final[frozenset[str]] = frozenset(
    {
        "api_key_pattern",
        "jwt_pattern",
        "bearer_token",
        "credential_assignment",
        "platform_credential_pattern",
        "non_finite_value",
    }
)


def _run_output_gate(content: str) -> Optional[str]:
    """Return the violated property's code, or None when the report is clean.

    The credential scan is the UNION of the platform detector and the local
    patterns; the local patterns run first only so that the more specific code
    is reported when both match.
    """
    for pattern, code in _LOCAL_CREDENTIAL_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return code
    if detect_credentials(content):
        return "platform_credential_pattern"
    if _NON_FINITE_TOKEN.search(content):
        return "non_finite_value"
    return None


def _contain(code: str) -> Dict[str, Any]:
    """Return the state delta that withholds a violating report.

    Every output-bearing field is cleared, and ``formatted_output`` carries a
    fixed notice built from *code* alone. It is deliberately non-empty: the
    backbone falls back to ``result`` whenever ``formatted_output`` is falsy,
    so a truthy replacement closes that fallback even if some later change
    starts writing ``result`` on this path.
    """
    if code not in VIOLATION_CODES:  # pragma: no cover - guards a coding error
        raise AssertionError(f"unknown violation code: {code!r}")
    notice = (
        f"[Demand forecast report withheld — the assembled report failed the "
        f"output policy check ({code}). No report content is released. Contact "
        f"the merchandising analytics owner to retrieve it through the "
        f"reviewed channel.]"
    )
    return {
        "formatted_output": notice,
        "result": None,
        "forecast_report": None,
        "report_sections": None,
        "validation_flags": None,
        "status": AgentStatus.ERROR.value,
        "error_log": [f"PostProcessNode: output policy violation — {code}"],
    }


class PostProcessNode(FunctionNode):
    """Apply the output gate and publish the demand-forecast report.

    Outer backbone post_process slot. Declared ANONYMOUS — trust was enforced at
    PreProcessNode (VERIFIED_EXTERNAL) and the backbone reaches this slot only
    after a successful main.

    Input state keys:
        forecast_report: str   — assembled report from the inner OutputFormatNode
        review_required: bool  — human-review flag

    Output state keys (partial dict):
        formatted_output: str
        result:           str | None
        status:           str
        error_log:        list[str]  (only on ERROR)
        forecast_report / report_sections / validation_flags — cleared on a
        violation only
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        forecast_report: str = state.get("forecast_report") or ""
        review_required: bool = bool(state.get("review_required"))

        if not forecast_report.strip():
            logger.warning("PostProcessNode: forecast_report is empty — using fallback message")
            forecast_report = (
                "[Demand Forecast Report] No report content generated. "
                "Check the run's audit trail for upstream failures."
            )

        violation = _run_output_gate(forecast_report)
        if violation:
            logger.error("PostProcessNode: output policy violation — %s", violation)
            emit_trace_event("post_process_output_violation", {"violation": violation}, state)
            return _contain(violation)

        logger.info(
            "PostProcessNode: output gate passed — length=%d review_required=%s",
            len(forecast_report),
            review_required,
        )
        emit_trace_event(
            "post_process_complete",
            {
                "output_length": len(forecast_report),
                "review_required": review_required,
            },
            state,
        )

        return {
            "formatted_output": forecast_report,
            "result": forecast_report,
            "status": AgentStatus.SUCCESS.value,
        }
