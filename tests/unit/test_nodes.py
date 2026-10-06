# RET-C2-281 — Unit Tests: domain nodes + graph wiring
#
# Real, non-stub unit tests. They import the REAL modules merged to develop in
# Wave-1 and assert real behaviour (report content, demand analytics, inventory
# math, forecast-governance review flags, S-1 trust levels, the S-3 output gate,
# and the Cat 2 two-layer graph composition).
#
# Nodes are invoked via BaseNode.__call__ (`node(state)` / the _call helper),
# NOT via node.execute() directly, so the mandatory S-1 trust gate — and the
# @final S-2 input gate — run on every call, exactly as they do on the real
# .invoke() path. Each state therefore carries an explicit caller_trust_level:
# VERIFIED_EXTERNAL for PreProcessNode (its required_trust_level), ANONYMOUS for
# every other (inner-domain and post_process) node. Positive-path payloads are
# kept PII-free so the default S-2 PII scan does not mask/alter them.
#
# S-4 audit events are patched at the node MODULE level (not via a sys.modules
# stub, which would break the real `shared` package the framework loads at import
# time). Patch pattern per node:
#     monkeypatch.setattr("src.nodes.<mod>.emit_trace_event", lambda *a, **k: None)

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from src.schemas.state import from_json, to_json


def _call(node, state: dict, trust: str = TrustLevel.ANONYMOUS.value) -> dict:
    """Invoke a node through BaseNode.__call__ with an explicit caller trust.

    Routing through __call__ (rather than execute() directly) runs the S-1
    trust gate and the @final S-2 input gate first, matching the real
    .invoke() path. caller_trust_level is injected without mutating the
    caller's dict.
    """
    return node({**state, "caller_trust_level": trust})


# ── Shared fixtures / helpers ─────────────────────────────────────────────────


def _raw_payload(**overrides) -> dict:
    """A complete, valid raw forecast request payload (as a caller would POST).

    Every identifier field is a single inert run of [A-Za-z0-9_.-], which is what
    the request contract accepts and, as a consequence, also what the platform's
    input filter leaves alone: that filter masks two or more consecutive Title
    Case words as a personal name, so a value with whitespace would arrive at the
    renderer as "[MASKED]".
    """
    payload = {
        "category": "WinterOuterwear",
        "sku": "SKU-TEST-1",
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
    payload.update(overrides)
    return payload


VALID_PAYLOAD = json.dumps(_raw_payload())


def _validated_input(
    units, horizon=4, lead_time_days=14, current_inventory=0, service_level=0.95, seasonality_hint=""
) -> dict:
    """The normalised forecast_input dict shape produced by InputValidateNode
    (i.e. the input the downstream ParseForecastDataNode consumes)."""
    history = [{"period": f"W{i}", "units": float(u)} for i, u in enumerate(units, start=1)]
    return {
        "category": "Test Category",
        "sku": "SKU-1",
        "forecast_horizon_weeks": horizon,
        "historical_sales": history,
        "history_periods": len(history),
        "current_inventory": current_inventory,
        "lead_time_days": lead_time_days,
        "unit_cost": 10.0,
        "service_level": service_level,
        "seasonality_hint": seasonality_hint,
    }


def _enriched_forecast_input(**overrides) -> dict:
    """A forecast_input enriched with the analytics ParseForecastDataNode adds
    (the input the Generate / Validation / OutputFormat nodes consume)."""
    data = _validated_input([320, 345, 360, 402, 455, 498, 512, 560], horizon=6)
    data.update(
        {
            "avg_weekly_velocity": 435.0,
            "recent_velocity": 523.33,
            "baseline_velocity": 356.75,
            "units_stdev": 84.5,
            "coefficient_of_variation": 0.19,
            "trend_direction": "up",
            "trend_pct": 0.4671,
            "seasonality_flag": True,
            "demand_tier": "medium",
            "projected_weekly_demand": [530.0, 540.0, 550.0, 560.0, 570.0, 580.0],
            "total_projected_demand": 3330.0,
            "lead_time_weeks": 3.0,
            "demand_during_lead_time": 1305.0,
            "safety_stock": 240.0,
            "reorder_point": 1545.0,
            "recommended_buy_qty": 2930.0,
            "service_level_z": 1.65,
        }
    )
    data.update(overrides)
    return data


# ── PreProcessNode (outer pre_process, S-1 VERIFIED_EXTERNAL) ──────────────────


class TestPreProcessNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.pre_process_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.pre_process_node import PreProcessNode

        self.node = PreProcessNode()

    # PreProcessNode is VERIFIED_EXTERNAL, so every call injects that trust so
    # the S-1 gate in __call__ passes and execute() runs (see _call default).
    _EXT = TrustLevel.VERIFIED_EXTERNAL.value

    def test_valid_payload_returns_success(self):
        result = _call(self.node, {"user_input": VALID_PAYLOAD, "input_context": {}}, self._EXT)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"] is not None
        assert json.loads(result["validated_input"])["forecast_horizon_weeks"] == 6

    def test_enriched_context_carries_forecast_ref(self):
        result = _call(self.node, {"user_input": VALID_PAYLOAD, "input_context": {"channel": "merch"}}, self._EXT)
        ctx = from_json(result["enriched_context"])
        assert ctx["forecast_ref"] == "SKU-TEST-1"
        assert ctx["channel"] == "merch"
        assert ctx["source"] == "RetailDemandForecastingReportAgent"

    def test_empty_input_returns_error(self):
        result = _call(self.node, {"user_input": "", "input_context": {}}, self._EXT)
        assert result["status"] == AgentStatus.ERROR.value
        assert any("empty" in e for e in result["error_log"])

    def test_invalid_json_returns_error(self):
        result = _call(self.node, {"user_input": "{not valid json}", "input_context": {}}, self._EXT)
        assert result["status"] == AgentStatus.ERROR.value
        assert any("JSON" in e or "json" in e for e in result["error_log"])

    def test_non_object_json_returns_error(self):
        result = _call(self.node, {"user_input": "[1, 2, 3]", "input_context": {}}, self._EXT)
        assert result["status"] == AgentStatus.ERROR.value
        assert any("object" in e for e in result["error_log"])

    def test_missing_required_field_returns_error(self):
        payload = {"sku": "X", "historical_sales": [10, 20, 30, 40]}  # no forecast_horizon_weeks
        result = _call(self.node, {"user_input": json.dumps(payload), "input_context": {}}, self._EXT)
        assert result["status"] == AgentStatus.ERROR.value
        assert any("forecast_horizon_weeks" in e for e in result["error_log"])

    def test_trust_level_is_verified_external(self):
        assert self.node.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_execute_signature_is_state_first(self):
        import inspect
        from src.nodes.pre_process_node import PreProcessNode

        params = list(inspect.signature(PreProcessNode.execute).parameters.keys())
        assert params[0] == "self" and params[1] == "state"
        assert "_invoke_impl" not in PreProcessNode.__dict__


# ── S-1 trust gate (PreProcessNode via BaseNode.__call__) ──────────────────────


class TestS1TrustGate:
    """The mandatory S-1 trust gate (BaseNode.__call__) is enforced BEFORE
    execute() runs. PreProcessNode requires VERIFIED_EXTERNAL, so an ANONYMOUS
    caller is denied at the gate and execute() never runs; a VERIFIED_EXTERNAL
    caller passes through to validation.

    Regression coverage for CoE Stage-6 C3/C13-TRUST-GATE: unit tests must
    invoke nodes via node(state) (routing through the trust gate), not
    node.execute() directly (which bypasses it).
    """

    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.pre_process_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.pre_process_node import PreProcessNode

        self.node = PreProcessNode()

    def test_anonymous_caller_denied_at_gate(self):
        """ANONYMOUS < VERIFIED_EXTERNAL → __call__ RETURNS an error dict
        (no exception) and execute() is never reached (no validated_input)."""
        result = self.node(
            {
                "user_input": VALID_PAYLOAD,
                "input_context": {},
                "caller_trust_level": TrustLevel.ANONYMOUS.value,
            }
        )
        assert result["status"] == AgentStatus.ERROR.value
        assert "trust gate denied" in " ".join(result["error_log"]).lower()
        # Gate short-circuits before execute(): no validation output produced.
        assert "validated_input" not in result

    def test_verified_external_caller_passes_gate(self):
        """VERIFIED_EXTERNAL caller clears the S-1 gate and execute() runs to SUCCESS."""
        result = self.node(
            {
                "user_input": VALID_PAYLOAD,
                "input_context": {},
                "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
            }
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"] is not None


# ── InputValidateNode (inner domain node 1, ANONYMOUS) ─────────────────────────


class TestInputValidateNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.input_validate_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.input_validate_node import InputValidateNode

        self.node = InputValidateNode()

    def test_valid_input_builds_forecast_input(self):
        result = _call(self.node, {"validated_input": VALID_PAYLOAD})
        assert result["status"] == AgentStatus.SUCCESS.value
        data = from_json(result["forecast_input"])
        assert data["category"] == "WinterOuterwear"
        assert data["history_periods"] == 8
        assert data["forecast_horizon_weeks"] == 6

    def test_reads_the_inner_graphs_user_input(self):
        """The inner graph receives the validated payload as its own user_input.

        GraphNode invokes a subgraph with a single string, which the framework
        seeds as `user_input`, so `validated_input` is absent inside the inner
        graph and this key is the real production source.
        """
        result = _call(self.node, {"user_input": VALID_PAYLOAD})
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_missing_identifier_returns_error(self):
        payload = {"forecast_horizon_weeks": 6, "historical_sales": [10, 20, 30, 40]}
        result = _call(self.node, {"validated_input": json.dumps(payload)})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("category" in e or "sku" in e for e in result["error_log"])

    def test_invalid_horizon_returns_error(self):
        result = _call(self.node, {"validated_input": json.dumps(_raw_payload(forecast_horizon_weeks=0))})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("forecast_horizon_weeks" in e for e in result["error_log"])

    def test_empty_history_returns_error(self):
        result = _call(self.node, {"validated_input": json.dumps(_raw_payload(historical_sales=[]))})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("historical_sales" in e for e in result["error_log"])

    def test_horizon_capped_at_max(self):
        result = _call(self.node, {"validated_input": json.dumps(_raw_payload(forecast_horizon_weeks=52))})
        data = from_json(result["forecast_input"])
        assert data["forecast_horizon_weeks"] == 26  # clamped to _MAX_HORIZON_WEEKS

    def test_bare_number_history_is_normalised(self):
        payload = _raw_payload(historical_sales=[100, 200, 300, 400])
        result = _call(self.node, {"validated_input": json.dumps(payload)})
        data = from_json(result["forecast_input"])
        assert data["history_periods"] == 4
        assert data["historical_sales"][0] == {"period": "P1", "units": 100.0}

    def test_out_of_range_service_level_is_refused(self):
        """An out-of-range service level fails closed; it is not quietly replaced.

        Substituting the default used to look harmless and was not: the caller
        asked for a specific stockout risk, the safety-stock figure was computed
        at a different one, and nothing in the report said so.
        """
        result = _call(self.node, {"validated_input": json.dumps(_raw_payload(service_level=2.0))})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("service_level: out_of_range" in e for e in result["error_log"])

    def test_absent_service_level_uses_the_declared_default(self):
        payload = _raw_payload()
        payload.pop("service_level")
        result = _call(self.node, {"validated_input": json.dumps(payload)})
        data = from_json(result["forecast_input"])
        assert data["service_level"] == 0.95

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── ParseForecastDataNode (inner domain node 2, ANONYMOUS) ─────────────────────


class TestParseForecastDataNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.parse_forecast_data_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.parse_forecast_data_node import ParseForecastDataNode

        self.node = ParseForecastDataNode()

    def _run(self, units, **kw):
        state = {"forecast_input": to_json(_validated_input(units, **kw))}
        return from_json(_call(self.node, state)["forecast_input"])

    def test_computes_velocity_and_projection(self):
        """Flat series of 100 over 6 weeks, 4-week horizon → deterministic analytics."""
        d = self._run([100] * 6, horizon=4)
        assert d["avg_weekly_velocity"] == 100.0
        assert d["recent_velocity"] == 100.0
        assert d["baseline_velocity"] == 100.0
        assert d["total_projected_demand"] == 400.0
        assert d["projected_weekly_demand"] == [100.0, 100.0, 100.0, 100.0]

    def test_flat_trend_direction(self):
        d = self._run([100] * 6, horizon=4)
        assert d["trend_direction"] == "flat"

    def test_upward_trend_direction(self):
        d = self._run([100, 100, 100, 200, 200, 200], horizon=4)
        assert d["trend_direction"] == "up"

    def test_demand_tier_high(self):
        d = self._run([500] * 6, horizon=4)
        assert d["demand_tier"] == "high"

    def test_demand_tier_low(self):
        d = self._run([50] * 6, horizon=4)
        assert d["demand_tier"] == "low"

    def test_recommended_buy_never_negative(self):
        """Ample current inventory → recommended buy floors at 0."""
        d = self._run([100] * 6, horizon=4, current_inventory=100000)
        assert d["recommended_buy_qty"] == 0

    def test_missing_forecast_input_returns_error(self):
        result = _call(self.node, {})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── GenerateForecastSectionsNode (inner domain node 3, ANONYMOUS) ──────────────


class TestGenerateForecastSectionsNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.generate_forecast_sections_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.generate_forecast_sections_node import GenerateForecastSectionsNode

        self.node = GenerateForecastSectionsNode()

    def test_generates_all_seven_sections(self):
        state = {"forecast_input": to_json(_enriched_forecast_input())}
        result = _call(self.node, state)
        assert result["status"] == AgentStatus.SUCCESS.value
        sections = from_json(result["report_sections"])
        assert set(sections.keys()) == {
            "demand_summary",
            "forecast_table",
            "buy_recommendation",
            "inventory_target",
            "seasonal_trend_analysis",
            "assumptions_and_method",
            "data_quality_notes",
        }

    def test_summary_reflects_forecast_fields(self):
        state = {"forecast_input": to_json(_enriched_forecast_input())}
        sections = from_json(_call(self.node, state)["report_sections"])
        assert "Test Category" in sections["demand_summary"]
        assert "MEDIUM" in sections["demand_summary"]  # demand_tier upper-cased

    def test_forecast_table_has_total_row(self):
        state = {"forecast_input": to_json(_enriched_forecast_input())}
        sections = from_json(_call(self.node, state)["report_sections"])
        assert "TOTAL" in sections["forecast_table"]

    def test_buy_recommendation_place_order_when_positive(self):
        state = {"forecast_input": to_json(_enriched_forecast_input(recommended_buy_qty=2930.0))}
        sections = from_json(_call(self.node, state)["report_sections"])
        assert "PLACE REPLENISHMENT ORDER" in sections["buy_recommendation"]

    def test_buy_recommendation_no_buy_when_zero(self):
        state = {"forecast_input": to_json(_enriched_forecast_input(recommended_buy_qty=0))}
        sections = from_json(_call(self.node, state)["report_sections"])
        assert "NO BUY REQUIRED" in sections["buy_recommendation"]

    def test_missing_forecast_input_returns_error(self):
        result = _call(self.node, {})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── ForecastValidationNode (inner domain node 4, ANONYMOUS) ────────────────────


class TestForecastValidationNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.forecast_validation_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.forecast_validation_node import ForecastValidationNode

        self.node = ForecastValidationNode()

    def _flags(self, **overrides):
        state = {"forecast_input": to_json(_enriched_forecast_input(**overrides))}
        result = _call(self.node, state)
        return result, from_json(result["validation_flags"])

    def test_high_confidence_no_flags(self):
        result, flags = self._flags()  # 8 periods, avg 435, cov 0.19, trend 0.47 → clean
        assert result["review_required"] is False
        assert flags["review_required"] is False
        assert flags["confidence_level"] == "high"
        assert flags["flags"] == []

    def test_insufficient_history_flags_review(self):
        result, flags = self._flags(history_periods=2)
        assert result["review_required"] is True
        assert any("insufficient_history" in f for f in flags["flags"])
        assert flags["confidence_level"] == "medium"  # single flag

    def test_non_positive_velocity_flags_review(self):
        result, flags = self._flags(avg_weekly_velocity=0.0)
        assert result["review_required"] is True
        assert any("non_positive_velocity" in f for f in flags["flags"])

    def test_high_volatility_flags_review(self):
        result, flags = self._flags(coefficient_of_variation=0.90)
        assert result["review_required"] is True
        assert any("high_volatility" in f for f in flags["flags"])

    def test_extreme_trend_flags_review(self):
        result, flags = self._flags(trend_pct=0.80)
        assert result["review_required"] is True
        assert any("extreme_trend" in f for f in flags["flags"])

    def test_multiple_flags_low_confidence(self):
        result, flags = self._flags(history_periods=2, coefficient_of_variation=0.90)
        assert result["review_required"] is True
        assert flags["confidence_level"] == "low"
        assert len(flags["flags"]) >= 2

    def test_validation_flags_carry_thresholds(self):
        _result, flags = self._flags()
        assert flags["min_history_periods"] == 4
        assert flags["high_volatility_threshold"] == 0.75
        assert flags["extreme_trend_threshold"] == 0.50

    def test_missing_forecast_input_returns_error(self):
        result = _call(self.node, {})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── OutputFormatNode (inner domain node 5, ANONYMOUS) ──────────────────────────


class TestOutputFormatNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.output_format_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.output_format_node import OutputFormatNode

        self.node = OutputFormatNode()

    def _state(self, review_required=True, confidence="medium"):
        sections = {
            "demand_summary": "Category / SKU: Test Category\nDemand Tier: MEDIUM",
            "forecast_table": "  Week   Projected Units\n  W1        100\n  TOTAL     400",
            "buy_recommendation": "  Recommended Buy Qty: 400 units",
            "inventory_target": "  Reorder Point: 200 units",
            "seasonal_trend_analysis": "  Seasonality Detected: NO",
            "assumptions_and_method": "  - Projection base is the recent-window velocity.",
            "data_quality_notes": "  Historical Periods Used: 6",
        }
        validation = {
            "review_required": review_required,
            "confidence_level": confidence,
            "flags": ["insufficient_history (2 < 4 periods)"] if review_required else [],
        }
        return {
            "report_sections": to_json(sections),
            "validation_flags": to_json(validation),
            "forecast_input": to_json(_validated_input([100] * 6)),
        }

    def test_assembles_full_report(self):
        result = _call(self.node, self._state())
        assert result["status"] == AgentStatus.SUCCESS.value
        report = result["forecast_report"]
        assert result["result"] == report
        assert "RETAIL DEMAND FORECAST REPORT" in report
        assert "Category / SKU: Test Category" in report
        for header in (
            "1. Demand Summary",
            "2. Weekly Demand Forecast",
            "3. Buy Recommendation",
            "4. Inventory Target",
            "5. Seasonal & Trend Analysis",
            "6. Assumptions & Method",
            "7. Data Quality Notes",
            "FORECAST GOVERNANCE NOTE",
        ):
            assert header in report, f"missing section header: {header}"

    def test_review_required_note_yes(self):
        report = _call(self.node, self._state(review_required=True))["forecast_report"]
        assert "Human Review Required: YES" in report

    def test_review_not_required_note_no(self):
        report = _call(self.node, self._state(review_required=False))["forecast_report"]
        assert "Human Review Required: NO" in report

    def test_confidence_surfaced_in_governance(self):
        report = _call(self.node, self._state(confidence="high"))["forecast_report"]
        assert "Forecast Confidence" in report
        assert "HIGH" in report

    def test_missing_sections_returns_error(self):
        result = _call(self.node, {"forecast_input": to_json(_validated_input([100] * 6))})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── PostProcessNode (outer post_process, S-3 output gate, ANONYMOUS) ───────────


class TestPostProcessNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.post_process_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.post_process_node import PostProcessNode

        self.node = PostProcessNode()

    def test_clean_report_passes_gate(self):
        report = "RETAIL DEMAND FORECAST REPORT\nCategory / SKU: WinterOuterwear\nAll clear."
        result = _call(self.node, {"forecast_report": report, "review_required": True})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == report
        assert result["result"] == report

    def test_empty_report_uses_fallback(self):
        result = _call(self.node, {"forecast_report": "", "review_required": False})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "No report content generated" in result["formatted_output"]

    def test_violating_report_is_withheld_and_every_field_cleared(self):
        leaky = "RETAIL DEMAND FORECAST REPORT\ntoken=sk-abcdefghij0123456789ABCDEF"
        result = _call(
            self.node,
            {
                "forecast_report": leaky,
                "report_sections": '{"demand_summary": "x"}',
                "validation_flags": '{"review_required": false}',
                "review_required": True,
            },
        )
        assert result["status"] == AgentStatus.ERROR.value
        # Nothing from the violating report is released on ANY caller-visible
        # field. `result` matters as much as `formatted_output`: the backbone
        # returns `formatted_output or result`, on an error status too.
        assert leaky not in str(result)
        assert "sk-abcdefghij0123456789ABCDEF" not in str(result)
        assert result["result"] is None
        assert result["forecast_report"] is None
        assert result["report_sections"] is None
        assert result["validation_flags"] is None
        # The replacement is truthy, so the `or result` fallback cannot reopen.
        assert result["formatted_output"]
        assert "withheld" in result["formatted_output"]

    def test_withheld_notice_carries_only_a_closed_set_code(self):
        from src.nodes.post_process_node import VIOLATION_CODES

        leaky = "RETAIL DEMAND FORECAST REPORT\npassword = supersecret123"
        result = _call(self.node, {"forecast_report": leaky, "review_required": True})
        notice = result["formatted_output"]
        assert any(f"({code})" in notice for code in VIOLATION_CODES), notice
        # No scanner finding, matched text, path or log line reaches the caller.
        assert "Traceback" not in notice
        assert "src/" not in notice

    def test_output_gate_is_the_union_of_the_local_and_platform_detectors(self):
        """Neither detector alone closes the set; the gate must run both.

        The first three are local-only shapes (below the platform's length
        thresholds, or an assignment form the platform's format patterns do not
        describe). The last two are platform-only shapes the local patterns miss.
        """
        from src.nodes.post_process_node import _run_output_gate

        local_only = [
            "ak-abcdefghij012345",  # 16 chars, under the platform's 20
            "Bearer abcdefgh",  # 8 chars, under the platform's 16
            "password = supersecret123",  # an assignment, not a format
        ]
        platform_only = [
            "AKIAABCDEFGHIJKLMNOP",
            # No inline credential in the fixture: the platform pattern matches
            # the connection-string SHAPE, so the host and path are enough.
            "postgresql://reporting-db.internal:5432/forecasts",
        ]
        for probe in local_only + platform_only:
            assert _run_output_gate(probe) is not None, probe
        assert _run_output_gate("A perfectly clean demand forecast report.") is None

    def test_output_gate_rejects_a_non_finite_figure(self):
        """A forecast that prints `nan` or `inf` says nothing while reporting success."""
        from src.nodes.post_process_node import _run_output_gate

        assert _run_output_gate("  Estimated Order Spend: inf") == "non_finite_value"
        assert _run_output_gate("  Avg Weekly Velocity:   nan units") == "non_finite_value"
        assert _run_output_gate("  Recommended Buy Qty:   -Infinity") == "non_finite_value"
        # Ordinary report prose containing those letters is untouched.
        assert _run_output_gate("Seasonal information: infrastructure nanometre") is None

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── Graph wiring: outer AgentBaseGraph + inner BaseGraph (Cat 2 nested) ─────────


class TestOuterGraphComposition:
    def test_registers_five_backbone_slots(self):
        from src.graph.graph import (
            ForecastReportGraphNode,
            RetailDemandForecastingReportAgent,
        )
        from src.nodes.post_process_node import PostProcessNode
        from src.nodes.pre_process_node import PreProcessNode

        agent = RetailDemandForecastingReportAgent()
        agent.compile()
        assert set(agent._nodes.keys()) == {
            "initialize",
            "pre_process",
            "main",
            "post_process",
            "finalize",
        }
        assert isinstance(agent._nodes["pre_process"], PreProcessNode)
        assert isinstance(agent._nodes["main"], ForecastReportGraphNode)
        assert isinstance(agent._nodes["post_process"], PostProcessNode)

    def test_name_and_state_schema(self):
        from src.schemas.state import State
        from src.graph.graph import RetailDemandForecastingReportAgent

        agent = RetailDemandForecastingReportAgent()
        assert agent.name == "RetailDemandForecastingReportAgent"
        assert agent.state_schema is State

    def test_graph_alias_matches_real_class(self):
        from src.graph.graph import Graph, RetailDemandForecastingReportAgent

        assert Graph is RetailDemandForecastingReportAgent

    def test_main_slot_graphnode_contracts(self):
        from src.graph.graph import ForecastReportGraphNode

        node = ForecastReportGraphNode()
        assert node.error_strategy == "propagate"
        assert node.propagate_hitl is False
        # extract_input forwards ONLY validated_input, never the raw user_input.
        # The property is asserted here rather than end to end because it is not
        # observable end to end: the framework skips execute() on a node whose
        # incoming state already carries an error status, so the old fallback was
        # unreachable today — and held shut by a framework detail rather than by
        # anything in this template.
        assert node.extract_input({"validated_input": "V", "user_input": "U"}) == "V"
        assert node.extract_input({"user_input": "U"}) == ""
        assert node.extract_input({}) == ""

    def test_merge_output_maps_subresult_keys(self):
        from src.graph.graph import ForecastReportGraphNode

        node = ForecastReportGraphNode()
        sub_result = {
            "forecast_report": "REPORT",
            "report_sections": "{}",
            "validation_flags": "{}",
            "review_required": True,
            "status": AgentStatus.SUCCESS.value,
            "node_history": ["x"],  # not forwarded by merge_output
        }
        delta = node.merge_output({}, sub_result)
        assert delta["forecast_report"] == "REPORT"
        assert delta["review_required"] is True
        assert delta["status"] == AgentStatus.SUCCESS.value
        assert set(delta.keys()) == {
            "forecast_report",
            "report_sections",
            "validation_flags",
            "review_required",
            "status",
        }

    def test_agent_defines_no_security_gate_output_override(self):
        # The agent class must NOT define a `_security_gate_output` method:
        # FunctionNode._security_gate_output is @final and enforced at
        # class-definition time. The domain output gate for this template lives
        # inline in PostProcessNode.execute() via the module-level
        # _run_output_gate() helper.
        from src.graph import graph as graph_mod
        from src.nodes.post_process_node import _run_output_gate

        assert "_security_gate_output" not in graph_mod.RetailDemandForecastingReportAgent.__dict__
        # The domain gate is reachable at module level, not on the agent class.
        assert _run_output_gate("sk-abcdefghij0123456789ABCDEF") is not None
        assert _run_output_gate("A perfectly clean report.") is None


class TestInnerDomainGraph:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        for mod in (
            "input_validate_node",
            "parse_forecast_data_node",
            "generate_forecast_sections_node",
            "forecast_validation_node",
            "output_format_node",
        ):
            monkeypatch.setattr(f"src.nodes.{mod}.emit_trace_event", lambda *a, **k: None)

    def test_registers_five_domain_nodes(self):
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        g = DomainWorkflowGraph()
        g.register_nodes()
        assert set(g._nodes.keys()) == {
            "input_validate",
            "parse_forecast_data",
            "generate_forecast_sections",
            "forecast_validation",
            "output_format",
        }

    def test_name_and_state_schema(self):
        from src.schemas.state import State
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        g = DomainWorkflowGraph()
        assert g.name == "ret_c2_281_demand_forecast_workflow"
        assert g.state_schema is State

    def test_inner_graph_invoke_produces_report(self):
        """Standalone inner-graph invoke (ANONYMOUS caller) runs the linear pipeline
        and shapes the get_output() dict consumed by the outer merge_output()."""
        from framework.schemas.invocation_context import InvocationContext, TrustLevel
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        g = DomainWorkflowGraph()
        g.compile()
        ctx = InvocationContext(caller_trust_level=TrustLevel.ANONYMOUS)
        result = g.invoke(VALID_PAYLOAD, ctx=ctx)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["forecast_report"] is not None
        assert "RETAIL DEMAND FORECAST REPORT" in result["forecast_report"]
