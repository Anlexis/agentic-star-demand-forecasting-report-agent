# What the caller receives when the output gate refuses a report.
#
# The backbone returns `formatted_output or result` and returns it on an error
# status too, so a gate that merely raises, or that returns an error without
# clearing, still ships the un-gated text through the fallback. Withholding is
# therefore a property of the state the node returns, not of the exception it
# throws, and the only place it can honestly be measured is the caller channel.
#
# Every path that can return a non-success status is enumerated here rather than
# only the one a scan would name, because a gate is exactly as good as its
# least-examined exit.

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext, TrustLevel

from src.graph.graph import Graph
from src.nodes.post_process_node import VIOLATION_CODES, PostProcessNode, _contain

_SECRET = "sk-abcdefghij0123456789ABCDEF"
_LEAKY_REPORT = (
    "========================================================================\n"
    "RETAIL DEMAND FORECAST REPORT\n"
    "Category / SKU: widgets\n"
    f"supplier token={_SECRET}\n"
)

_REQUEST = json.dumps(
    {
        "category": "widgets",
        "forecast_horizon_weeks": 3,
        "historical_sales": [100, 110, 120, 130],
    }
)


def _envelope(state_delta: dict) -> dict:
    """The caller-visible envelope the backbone would build from a state delta."""
    return {
        "output": state_delta.get("formatted_output") or state_delta.get("result"),
        "status": state_delta.get("status"),
    }


class TestWithheldReportReachesNoOne:
    def test_no_released_text_survives_on_any_caller_field(self) -> None:
        delta = PostProcessNode()(
            {
                "caller_trust_level": TrustLevel.ANONYMOUS.value,
                "forecast_report": _LEAKY_REPORT,
                "report_sections": json.dumps({"demand_summary": _LEAKY_REPORT}),
                "validation_flags": json.dumps({"review_required": True}),
            }
        )
        assert delta["status"] == AgentStatus.ERROR.value
        assert _SECRET not in json.dumps(delta, default=str)
        assert _envelope(delta)["output"] is not None
        assert _SECRET not in _envelope(delta)["output"]

    def test_the_fallback_channel_is_closed(self) -> None:
        """`result` is the live fallback, so clearing `formatted_output` alone is
        not containment — and a FALSY replacement re-opens it."""
        delta = _contain("api_key_pattern")
        assert delta["result"] is None
        assert delta["formatted_output"], "a falsy replacement re-opens the `or result` fallback"
        assert _envelope(delta)["output"] == delta["formatted_output"]

    def test_the_notice_is_built_from_a_closed_set(self) -> None:
        for code in VIOLATION_CODES:
            notice = _contain(code)["formatted_output"]
            assert code in notice
            assert "Traceback" not in notice
            assert "/src/" not in notice

    def test_an_unknown_code_cannot_reach_the_caller(self) -> None:
        """The notice is assembled from a code, so the code set is the contract.

        Without this guard a future caller of `_contain` could pass an exception
        message and it would be published as a reason.
        """
        try:
            _contain("arbitrary text from an exception")
        except AssertionError:
            return
        raise AssertionError("_contain accepted a code outside the closed set")


class TestEveryNonSuccessExit:
    """Enumerate the exits, and check the envelope at each one."""

    def _invoke(self, request: str, trust: TrustLevel = TrustLevel.VERIFIED_EXTERNAL) -> dict:
        agent = Graph()
        agent.compile()
        return agent.invoke(request, ctx=InvocationContext(caller_trust_level=trust))

    def test_trust_refusal_publishes_no_report(self) -> None:
        result = self._invoke(_REQUEST, TrustLevel.ANONYMOUS)
        assert result["status"] == AgentStatus.ERROR.value
        assert "RETAIL DEMAND FORECAST REPORT" not in str(result.get("output") or "")

    def test_contract_refusal_publishes_a_reason_and_no_report(self) -> None:
        result = self._invoke(json.dumps({"forecast_horizon_weeks": 4, "historical_sales": []}))
        assert result["status"] == AgentStatus.ERROR.value
        assert "history_empty" in str(result["output"])
        assert "RETAIL DEMAND FORECAST REPORT" not in str(result["output"])

    def test_inner_pipeline_failure_publishes_no_traceback(self) -> None:
        """An inner-graph failure surfaces through the subgraph error path, whose
        default message carries the inner log and a traceback. None of it may
        reach the caller envelope."""
        result = self._invoke(json.dumps({"forecast_horizon_weeks": 4, "historical_sales": [1]}))
        envelope = json.dumps(result, default=str)
        assert "Traceback" not in envelope
        assert "site-packages" not in envelope
        assert "/src/" not in envelope

    def test_success_still_publishes_the_report(self) -> None:
        """The control. Without it, a gate that withheld everything would pass
        every assertion above."""
        result = self._invoke(_REQUEST)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "RETAIL DEMAND FORECAST REPORT" in result["output"]
