"""AgentCore Platform v1.0"""

# RET-C2-281 — DomainWorkflowGraph (inner BaseGraph)
#
# This is the INNER graph for the Cat 2 two-layer nested architecture.
# It encapsulates the retail demand-forecast report generation pipeline:
#
#   START
#     → input_validate            (InputValidateNode)
#     → parse_forecast_data       (ParseForecastDataNode)
#     → generate_forecast_sections (GenerateForecastSectionsNode)
#     → forecast_validation       (ForecastValidationNode)
#     → output_format             (OutputFormatNode)
#     → END
#
# Called by ForecastReportGraphNode.get_subgraph() (graph.py).
# get_output() shapes the sub_result dict consumed by merge_output() there.
#
# Rules enforced:
#   ✅ Inherits BaseGraph (fully custom topology — no forced backbone)
#   ✅ Implements all 7 BaseGraph ABC methods
#   ✅ register_nodes() does NOT call super() (abstract in BaseGraph)
#   ✅ Does NOT register initialize / finalize (outer backbone concerns)
#   ✅ All inner nodes declare required_trust_level = TrustLevel.ANONYMOUS
#   ✅ get_output() designed together with ForecastReportGraphNode.merge_output()
#   ✅ All inner node ctors are empty-parens (no constructor args — CoE no-arg rule)
#   ❌ No Level-0 platform SDK imports
#   ❌ Not placed under src/subagents/

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import inner_seed
from src.nodes.forecast_validation_node import ForecastValidationNode
from src.nodes.generate_forecast_sections_node import GenerateForecastSectionsNode
from src.nodes.input_validate_node import InputValidateNode
from src.nodes.output_format_node import OutputFormatNode
from src.nodes.parse_forecast_data_node import ParseForecastDataNode
from src.schemas.state import State


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for RET-C2-281.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by ForecastReportGraphNode.get_subgraph() in graph.py.

    Pipeline (linear):
        START
          → input_validate             (InputValidateNode)
          → parse_forecast_data        (ParseForecastDataNode)
          → generate_forecast_sections (GenerateForecastSectionsNode)
          → forecast_validation        (ForecastValidationNode)
          → output_format              (OutputFormatNode)
          → END

    All nodes are FunctionNode subclasses with ANONYMOUS trust_level.
    initialize / finalize are outer backbone concerns — not registered here.
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "ret_c2_281_demand_forecast_workflow"

    @property
    def state_schema(self) -> type:
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """No mandatory config for v1 rule-based inner graph."""
        pass

    # ── Runtime config seeding ────────────────────────────────────────────────

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the declared runtime parameters and caller channel into inner state.

        Two things cross the nested-graph boundary here, and neither crosses it
        on its own:

        * The declared runtime parameters. The framework does not thread a
          graph's ``self.config`` into ``node.execute()``'s ``config`` argument
          on the real ``.invoke()`` path — that argument is only populated by a
          direct unit-level call. The outer GraphNode forwards the declared
          settings via ``_parent_config()`` into this graph's ``self.config``;
          copying ``system_prompt_template`` into state is what makes
          GenerateForecastSectionsNode observe it end to end.
        * The caller channel. ``GraphNode.execute()`` invokes this graph without
          an ``input_context`` argument, so anything the caller supplied there
          is absent from the inner state unless it is bridged
          (src/graph/context_bridge.py).

        Absent values are omitted rather than seeded as ``None``, so a consumer
        keeps its own declared default instead of special-casing a null.
        """
        seed: Dict[str, Any] = {}
        configurable = (self.config or {}).get("configurable", {}) or {}
        template = configurable.get("system_prompt_template")
        if template:
            seed["system_prompt_template"] = template
        seed.update(inner_seed())
        return seed

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all 5 domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.
        Every key registered here is referenced in add_edges().
        All nodes are instantiated with empty-parens (no ctor args) —
        CoE no-arg ctor rule: SDK v1 FunctionNode subclasses take no arguments.
        """
        self._nodes["input_validate"] = InputValidateNode()
        self._nodes["parse_forecast_data"] = ParseForecastDataNode()
        self._nodes["generate_forecast_sections"] = GenerateForecastSectionsNode()
        self._nodes["forecast_validation"] = ForecastValidationNode()
        self._nodes["output_format"] = OutputFormatNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear demand-forecast generation topology.

        Linear flow:
            input_validate → parse_forecast_data → generate_forecast_sections
            → forecast_validation → output_format → END.

        No conditional branching — all paths through the report pipeline are
        linear in v1.  route() satisfies the ABC but is not used at runtime.
        """
        self._sg.add_edge(START, "input_validate")
        self._sg.add_edge("input_validate", "parse_forecast_data")
        self._sg.add_edge("parse_forecast_data", "generate_forecast_sections")
        self._sg.add_edge("generate_forecast_sections", "forecast_validation")
        self._sg.add_edge("forecast_validation", "output_format")
        self._sg.add_edge("output_format", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: AgentState) -> str:
        """Conditional routing — required by BaseGraph ABC.

        Linear topology; add_conditional_edges() is not used, so this method
        is never called at runtime.  Returns END on error so an unexpected
        invocation does not re-enter a processing node.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "output_format"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the output dict returned to the outer graph as sub_result.

        This dict is received by ForecastReportGraphNode.merge_output()
        in graph.py as the `sub_result` argument.  Both methods are designed
        together to guarantee field-name consistency:

            Inner get_output() emits:   "forecast_report", "report_sections",
                                        "validation_flags", "review_required", "status"
            Outer merge_output() reads: sub_result.get(...) for each key above.
        """
        return {
            "forecast_report": state.get("forecast_report"),
            "report_sections": state.get("report_sections"),
            "validation_flags": state.get("validation_flags"),
            "review_required": state.get("review_required", False),
            "status": state.get("status"),
            "node_history": state.get("node_history", []),
            "correlation_id": state.get("correlation_id"),
        }
