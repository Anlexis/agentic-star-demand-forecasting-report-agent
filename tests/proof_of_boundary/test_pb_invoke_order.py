# PB-6: Invoke Execution Order Verification
# Verifies BaseNode.__call__() enforces: S-1 trust gate -> S-4 node_start ->
# S-2 _security_gate_input() -> execute() -> S-3 _security_gate_output() ->
# S-4 node_complete, for every concrete node under src/nodes/.
#
# Also verifies the full backbone invoke order for the outer
# RetailDemandForecastingReportAgent (Cat 2 two-layer nested graph):
#   InitializeNode -> PreProcessNode (pre_process) -> ForecastReportGraphNode (main)
#   -> PostProcessNode (post_process) -> FinalizeNode
#
# PB-6 invoke uses VERIFIED_EXTERNAL caller trust (the real external path) — NEVER
# for_internal(). A VERIFIED_EXTERNAL InvocationContext exercises the same code path a
# real STG caller uses: it clears the outer PreProcessNode S-1 gate
# (required_trust_level = VERIFIED_EXTERNAL) AND passes through the inner ANONYMOUS
# domain nodes. for_internal() (INTERNAL) would not represent a real external caller,
# so it is deliberately not used.

import importlib
import inspect
import json
import pkgutil
from pathlib import Path

import pytest

# ── Template-specific constants ───────────────────────────────────────────────

# Class name of the node in the `main` backbone slot.
_MAIN_SLOT_NODE = "ForecastReportGraphNode"

# A SUCCESS-yielding retail demand-forecast payload for the backbone invoke test.
# 8 weeks of history (>= 4 periods) with a mild upward trend, a positive lead time,
# and current inventory set below projected demand so a buy is recommended. All
# PreProcessNode required fields present (forecast_horizon_weeks, historical_sales)
# plus a category/SKU identifier for the inner InputValidateNode.
#
# CONTRACT (reference_newgen_stg_deploy): deploy/invoke_payload.json["input"]
# MUST equal this exact string — the Stage-5 deploy-stg evidence invoke and the
# PB-6 test must exercise the identical payload. test_invoke_payload_matches_pb6
# below asserts that equality so the two can never drift.
#
# Identifier fields carry no whitespace: the request contract restricts
# everything that renders into the report to a single inert run of
# [A-Za-z0-9_.-]. A hyphenated label is therefore the readable form, and it also
# survives the platform's input filter, which masks two or more consecutive
# Title Case words as a personal name — the earlier "Winter Outerwear - Puffer
# Jackets" reached the renderer as "[MASKED] - [MASKED]" and this payload
# produced a report whose subject was a redaction sentinel.
_VALID_PAYLOAD = json.dumps(
    {
        "category": "Winter-Outerwear-Puffer-Jackets",
        "sku": "RET-WO-PUFFER-XL-2026",
        "forecast_horizon_weeks": 6,
        "historical_sales": [
            {"period": "2026-W01", "units": 320},
            {"period": "2026-W02", "units": 345},
            {"period": "2026-W03", "units": 360},
            {"period": "2026-W04", "units": 402},
            {"period": "2026-W05", "units": 455},
            {"period": "2026-W06", "units": 498},
            {"period": "2026-W07", "units": 512},
            {"period": "2026-W08", "units": 560},
        ],
        "current_inventory": 640,
        "lead_time_days": 21,
        "unit_cost": 38.5,
        "service_level": 0.95,
        "seasonality": "winter_demand_peak",
    }
)

# ─────────────────────────────────────────────────────────────────────────────


def _discover_node_classes() -> list[type]:
    """Import every module under src/nodes/ and collect concrete BaseNode subclasses."""
    from framework.nodes.base_node import BaseNode

    try:
        pkg = importlib.import_module("src.nodes")
    except ImportError:
        return []

    discovered = []
    for _, modname, _ in pkgutil.walk_packages(pkg.__path__, prefix="src.nodes."):
        module = importlib.import_module(modname)
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseNode)
                and attr is not BaseNode
                and attr.__module__ == modname
                and not inspect.isabstract(attr)
            ):
                discovered.append(attr)
    return discovered


def _patch_domain_emit(monkeypatch):
    """Patch emit_trace_event in every domain node module (avoids audit-backend calls).

    Patched at the node MODULE level (not via a sys.modules stub, which would break
    the real `shared` package the framework loads at import time).
    """
    for mod_suffix in (
        "pre_process_node",
        "input_validate_node",
        "parse_forecast_data_node",
        "generate_forecast_sections_node",
        "forecast_validation_node",
        "output_format_node",
        "post_process_node",
        "main_node",
    ):
        try:
            monkeypatch.setattr(
                f"src.nodes.{mod_suffix}.emit_trace_event",
                lambda *a, **k: None,
            )
        except AttributeError:
            pass  # module not yet imported / no emit symbol; fine


class TestInvokeOrder:
    """PB-6: __call__ must run S-1 -> node_start -> S-2 -> execute() -> S-3 -> node_complete."""

    def test_call_order_for_every_node(self, monkeypatch):
        _patch_domain_emit(monkeypatch)

        node_classes = _discover_node_classes()
        if not node_classes:
            pytest.skip("no concrete BaseNode subclasses found under src/nodes/")

        import framework.nodes.base_node as base_node_module

        failures: list[str] = []
        for node_cls in node_classes:
            order: list[str] = []
            monkeypatch.setattr(
                base_node_module,
                "emit_trace_event",
                lambda event_type, _payload, _state, _o=order: _o.append(f"event:{event_type}"),
            )

            for method_name, label in (
                ("_security_gate_input", "security_gate_input"),
                ("execute", "execute"),
                ("_security_gate_output", "security_gate_output"),
            ):
                original = getattr(node_cls, method_name)

                def spy(self, arg, _o=order, _label=label, _orig=original):
                    _o.append(_label)
                    return _orig(self, arg)

                monkeypatch.setattr(node_cls, method_name, spy)

            instance = node_cls()
            # caller trust == the node's required level so the S-1 gate always passes here;
            # the gate-denial branch is asserted separately in TestS1TrustGate.
            state = {
                "caller_trust_level": node_cls.required_trust_level.value,
                "correlation_id": "pb6-invoke-order-test",
            }
            instance(state)

            expected = [
                "event:node_start",
                "security_gate_input",
                "execute",
                "security_gate_output",
                "event:node_complete",
            ]
            if order != expected:
                failures.append(
                    f"{node_cls.__name__}: invoke order violation.\n" f"expected: {expected}\nactual:   {order}"
                )

        assert not failures, "\n\n".join(failures)


class TestS1TrustGate:
    """PB-6 S-1 (NODE level): the trust gate in BaseNode.__call__ runs BEFORE execute()
    and denies a caller whose trust is below the node's required_trust_level."""

    def test_pre_process_denies_anonymous_caller(self, monkeypatch):
        """PreProcessNode (required VERIFIED_EXTERNAL) must refuse an ANONYMOUS caller."""
        _patch_domain_emit(monkeypatch)
        from framework.schemas.agent_status import AgentStatus
        from framework.schemas.invocation_context import TrustLevel
        from src.nodes.pre_process_node import PreProcessNode

        node = PreProcessNode()
        result = node(
            {
                "caller_trust_level": TrustLevel.ANONYMOUS.value,
                "user_input": _VALID_PAYLOAD,
                "correlation_id": "pb6-s1-denial",
            }
        )
        assert result["status"] == AgentStatus.ERROR.value
        assert any(
            "trust gate" in (e or "").lower() for e in result.get("error_log", [])
        ), f"expected an S-1 trust-gate denial, got error_log={result.get('error_log')}"

    def test_pre_process_admits_verified_external_caller(self, monkeypatch):
        """The same node admits a VERIFIED_EXTERNAL caller and runs execute() to SUCCESS."""
        _patch_domain_emit(monkeypatch)
        from framework.schemas.agent_status import AgentStatus
        from framework.schemas.invocation_context import TrustLevel
        from src.nodes.pre_process_node import PreProcessNode

        node = PreProcessNode()
        result = node(
            {
                "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
                "user_input": _VALID_PAYLOAD,
                "input_context": {},
                "correlation_id": "pb6-s1-admit",
            }
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"] is not None


class TestBackboneInvokeOrder:
    """PB-6 backbone: a full Graph().invoke() runs the 5-node backbone in order.

    Backbone order: InitializeNode -> PreProcessNode (pre_process) ->
                    ForecastReportGraphNode (main) ->
                    PostProcessNode (post_process) -> FinalizeNode

    Uses VERIFIED_EXTERNAL caller trust — the real external path.
    InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL) is mandatory;
    NEVER use for_internal(), which would not represent a real external caller.
    """

    def _invoke(self, monkeypatch):
        _patch_domain_emit(monkeypatch)
        from framework.schemas.invocation_context import InvocationContext, TrustLevel
        from src.graph.graph import Graph

        agent = Graph()
        agent.compile()
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        return agent.invoke(_VALID_PAYLOAD, ctx=ctx)

    def test_backbone_invoke_succeeds_and_returns_output(self, monkeypatch):
        from framework.schemas.agent_status import AgentStatus

        result = self._invoke(monkeypatch)
        assert result.get("status") == AgentStatus.SUCCESS.value, (
            f"Expected status={AgentStatus.SUCCESS.value!r}, got: {result.get('status')!r}\n"
            f"error_log: {result.get('error_log')}"
        )
        assert result.get("output") is not None, "output must be set after a successful invoke"
        # The assembled report banner + governance trailer must be in the surfaced output.
        assert "RETAIL DEMAND FORECAST REPORT" in result["output"]
        assert "FORECAST GOVERNANCE NOTE" in result["output"]
        # The caller's own identifier reaches the report intact. Anchoring only on
        # the template-literal headers would pass on a report whose subject had
        # been replaced by a redaction sentinel, which is exactly what this
        # payload produced before the identifier fields were made inert.
        assert "Winter-Outerwear-Puffer-Jackets" in result["output"]
        assert "[MASKED]" not in result["output"]
        # A forecast is a set of numbers; a non-finite one reports nothing while
        # claiming success. Checked with the gate's own token pattern rather than
        # a substring search, which would match "governance".
        from src.nodes.post_process_node import _NON_FINITE_TOKEN

        assert _NON_FINITE_TOKEN.search(result["output"]) is None

    def test_backbone_node_history_matches_expected_order(self, monkeypatch):
        result = self._invoke(monkeypatch)
        history = result.get("node_history", [])
        assert history == [
            "InitializeNode",
            "PreProcessNode",
            "ForecastReportGraphNode",
            "PostProcessNode",
            "FinalizeNode",
        ], f"unexpected backbone node_history: {history}"

    def test_main_slot_is_forecast_report_graph_node(self):
        """The `main` backbone slot must be ForecastReportGraphNode (a GraphNode — Cat 2)."""
        from framework.nodes.graph_node import GraphNode
        from src.graph.graph import ForecastReportGraphNode, RetailDemandForecastingReportAgent

        agent = RetailDemandForecastingReportAgent()
        agent.compile()
        main_node = agent._nodes.get("main")
        assert main_node is not None, "main slot must be registered"
        assert isinstance(
            main_node, ForecastReportGraphNode
        ), f"main slot must be ForecastReportGraphNode, got {type(main_node).__name__}"
        assert isinstance(main_node, GraphNode), "main slot node must subclass GraphNode (Cat 2 contract)"
        assert main_node.__class__.__name__ == _MAIN_SLOT_NODE

    def test_invoke_payload_matches_pb6(self):
        """deploy/invoke_payload.json["input"] MUST equal _VALID_PAYLOAD (Stage-5 alignment).

        The deploy-stg evidence invoke (stg_invoke_evidence.py POSTs invoke_payload.json
        as the request body) must exercise the same payload PB-6 asserts yields SUCCESS.
        """
        repo_root = Path(__file__).resolve().parents[2]
        payload_file = repo_root / "deploy" / "invoke_payload.json"
        assert payload_file.exists(), "deploy/invoke_payload.json is required for deploy-stg"
        body = json.loads(payload_file.read_text())
        assert body.get("input") == _VALID_PAYLOAD, (
            "deploy/invoke_payload.json['input'] must equal the PB-6 _VALID_PAYLOAD "
            "(reference_newgen_stg_deploy contract)"
        )
        # And the payload the STG server forwards to agent.invoke() must itself be a
        # valid, PreProcessNode-parseable forecast JSON object.
        forecast = json.loads(body["input"])
        for required in ("forecast_horizon_weeks", "historical_sales"):
            assert required in forecast, f"invoke_payload input missing required field: {required}"
        assert forecast.get("category") or forecast.get(
            "sku"
        ), "invoke_payload input must carry a category or sku identifier"
