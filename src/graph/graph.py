"""AgentCore Platform v1.0"""

# RET-C2-281 — Outer graph (AgentBaseGraph; Cat 2 two-layer nested architecture)
#
# Architecture (Cat 2):
#
#   Outer backbone (fixed — identical to Cat 1, do NOT override add_edges()):
#     START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                             ↓ (RETRY, max 3)
#                                          pre_process
#
#   `main` slot is a GraphNode subclass (ForecastReportGraphNode) that delegates
#   the full domain workflow to DomainWorkflowGraph (inner BaseGraph).
#
#   Domain complexity is fully encapsulated inside the inner graph. The outer
#   backbone is never modified.
#
# Directory layout:
#   src/graph/graph.py                 ← outer graph (this file)
#   src/graph/domain_workflow_graph.py ← inner graph (multi-step topology)
#
# Rules enforced:
#   ✅ RetailDemandForecastingReportAgent inherits AgentBaseGraph (L1 Base)
#   ✅ super().register_nodes() called first (fills initialize + finalize)
#   ✅ ForecastReportGraphNode assigned to self._nodes["main"]
#   ✅ PreProcessNode (VERIFIED_EXTERNAL) in pre_process slot (S-1 gate)
#   ✅ PostProcessNode (ANONYMOUS) in post_process slot (S-3 output gate)
#   ✅ S-3 domain gate is inline in PostProcessNode.execute() (_run_s3_domain_gate);
#      the agent class defines NO _security_gate_output (FunctionNode's is @final)
#   ✅ merge_output() returns only changed keys
#   ✅ class name matches config/agent.yaml class: field exactly
#   ❌ add_edges() NOT overridden on the outer graph
#   ❌ No Level-0 platform SDK imports

import os
from typing import TYPE_CHECKING, Any, ClassVar, Dict

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from src.graph.context_bridge import stash_channel
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State

if TYPE_CHECKING:  # import-time cycle; needed for annotations only
    from src.graph.domain_workflow_graph import DomainWorkflowGraph

# Runtime parameters live in config/config.yaml, three levels up from this file
# (src/graph/graph.py → src/graph → src → <repo root>). config/agent.yaml next
# to it is the static registry manifest — identity, entry point, trust level —
# and carries no runtime block at all.
#
# Reading the right file is load-bearing rather than cosmetic. This function
# previously read `agent.config` out of config/agent.yaml, which was correct
# while the manifest nested everything under an `agent:` key. The registry
# manifest is flat, so that read returns an empty mapping: the declared prompt
# template, temperature and retry ceiling silently stop reaching the graph and
# every consumer falls back to its own default. Nothing raises and no test that
# only asserts "a report came out" notices.
# tests/integration/test_runtime_config_reaches_graph.py holds a declared value
# to its observable effect end to end so the same substitution fails loudly.
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "config.yaml",
)


def load_runtime_config() -> Dict[str, Any]:
    """Return the runtime parameters declared in config/config.yaml.

    Best-effort: a missing or unparseable file yields ``{}`` so graph
    construction never breaks. PyYAML is loaded on demand — it is a framework
    runtime dependency, so importing it here avoids a module-load coupling for
    callers that never read the file.
    """
    try:
        import yaml

        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


class ForecastReportGraphNode(GraphNode):
    """GraphNode subclass assigned to the `main` slot of RetailDemandForecastingReportAgent.

    Wraps DomainWorkflowGraph (inner Cat 2 BaseGraph).
    Called by AgentBaseGraph backbone after pre_process and before post_process.

    Contracts:
      get_subgraph()  — instantiate and return DomainWorkflowGraph
      extract_input() — pull validated_input (S-1 output) from outer state
      merge_output()  — map sub_result fields into outer state delta (changed keys only)
      error_strategy  — "propagate": re-raise inner errors as SubgraphError (fail-fast)
    """

    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    # Runtime parameters handed down by the outer graph. Bound after
    # construction by bind_runtime_config() because template nodes take no
    # constructor arguments; the class-level default keeps a directly
    # instantiated node working in a unit test.
    # Not a ClassVar: bind_runtime_config() assigns a fresh mapping to the
    # INSTANCE, so the empty class-level default is never mutated.
    _parent_runtime_config: Dict[str, Any] = {}

    def bind_runtime_config(self, config: Dict[str, Any]) -> None:
        """Attach the outer graph's declared runtime parameters to this node."""
        self._parent_runtime_config = dict(config or {})

    def get_subgraph(self) -> "DomainWorkflowGraph":
        """Instantiate and return the inner domain workflow graph.

        DomainWorkflowGraph is imported lazily (inside the method) to avoid
        circular-import risk at module load time and to match the Cat 2 pattern.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Return the string input passed into inner_graph.invoke().

        Only ``validated_input`` — the payload PreProcessNode produced after the
        trust gate and the request contract — is ever forwarded. There is
        deliberately NO fallback to the raw ``user_input``.

        Why that matters, stated precisely rather than dramatically. The backbone
        edge pre_process → main is unconditional, so ``main`` is scheduled even
        after pre_process refused a caller. It does not currently RUN on that
        path: ``BaseNode.__call__`` treats an incoming state that already carries
        an error status as a rejection and skips ``execute()`` entirely, which was
        measured rather than assumed — under an anonymous caller this method is
        never reached, though the node still appears in ``node_history``.

        So the old fallback to the raw ``user_input`` was unreachable today, and
        was one framework detail away from being reachable: nothing in this
        template was holding it shut. Forwarding only ``validated_input`` makes
        the property explicit and local — the inner pipeline, whose nodes all run
        at ANONYMOUS, can only ever see a payload that cleared the trust gate and
        the request contract.

        The already-validated caller channel is stashed here on the way in.
        ``GraphNode.execute()`` calls ``subgraph.invoke()`` without an
        ``input_context`` argument, so this is the only point at which the outer
        state and the inner graph's construction are both in scope
        (src/graph/context_bridge.py explains the mechanism).
        """
        stash_channel(state.get("request_channel"))
        return state.get("validated_input") or ""

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map inner graph sub_result back into the outer state delta.

        sub_result is the dict returned by DomainWorkflowGraph.get_output().
        Returns ONLY changed keys — never the full state.

        Key coupling (designed together with DomainWorkflowGraph.get_output()):
          Inner get_output() emits  → "forecast_report", "report_sections",
                                       "validation_flags", "review_required", "status"
          This merge_output() reads → sub_result.get(...) for each of these keys.

        PostProcessNode (outer post_process) reads forecast_report + review_required
        from state to apply the S-3 gate and set formatted_output.
        """
        return {
            "forecast_report": sub_result.get("forecast_report"),
            "report_sections": sub_result.get("report_sections"),
            "validation_flags": sub_result.get("validation_flags"),
            "review_required": sub_result.get("review_required", False),
            "status": sub_result.get("status"),
        }

    def _parent_config(self) -> Dict[str, Any]:
        """Forward the declared runtime parameters to the inner graph.

        The framework does not thread a graph's ``self.config`` into
        ``node.execute()``'s ``config`` argument on the real ``.invoke()`` path —
        that argument is only populated by a direct unit-level call — so the
        declared values are handed to the inner graph as its own config and
        seeded into inner state by
        ``DomainWorkflowGraph._extra_initial_state()``. Only keys that are
        actually declared are forwarded; an absent key leaves the consumer on
        its own default.

        The values are read from the graph's own config when the agent was
        constructed with one (the deployment path, where the entry point passes
        config/config.yaml), and otherwise from the file directly, so a bare
        ``Graph()`` used in a test observes the same declared values a
        deployment does.

        Report generation is deterministic; the prompt/temperature/token
        settings are surfaced for the narrative layer and recorded in the audit
        trace rather than faked as a model call here.
        """
        cfg = self._parent_runtime_config or load_runtime_config()
        llm = cfg.get("llm", {}) or {}
        declared = {
            "system_prompt_template": llm.get("system_prompt_template"),
            "temperature": llm.get("temperature"),
            "max_tokens": llm.get("max_tokens"),
            "max_retry": cfg.get("max_retry"),
            "timeout_s": cfg.get("timeout_s"),
        }
        return {"configurable": {k: v for k, v in declared.items() if v is not None}}


class RetailDemandForecastingReportAgent(AgentBaseGraph):
    """Outer graph for RET-C2-281 (Cat 2 — DocGenerationAgent).

    Inherits AgentBaseGraph directly (L1 Base). Domain logic is fully
    encapsulated in ForecastReportGraphNode (main slot), which delegates to
    DomainWorkflowGraph (inner BaseGraph).

    Backbone (fixed — identical to Cat 1):
        START → initialize → pre_process → main → post_process → finalize → END

    register_nodes() is the ONLY override:
      - super().register_nodes() fills: initialize, finalize (framework defaults)
      - pre_process: PreProcessNode    (VERIFIED_EXTERNAL — S-1 trust gate)
      - main:        ForecastReportGraphNode (delegates to DomainWorkflowGraph)
      - post_process: PostProcessNode  (ANONYMOUS — S-3 output gate)

    add_edges() is NOT overridden — backbone wiring belongs to the framework.

    get_output() is NOT overridden — the caller-facing forecast report is
    already surfaced in the base ``output`` envelope (PostProcessNode writes
    formatted_output/result, which the backbone maps to ``output``). The PB-6
    backbone-invoke test asserts the full assembled report is present in
    ``result["output"]``, so no additional surfacing is required.

    Class name MUST match config/agent.yaml `class:` field exactly.
    server.py imports this as `Graph` via the alias below.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with AgentRegistry."""
        return "RetailDemandForecastingReportAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default InitializeNode (sets schema_version, session_id,
        trust_level) and FinalizeNode (builds response_metadata, total_time_ms).
        """
        super().register_nodes()  # fills: initialize, finalize

        main = ForecastReportGraphNode()
        main.bind_runtime_config(self.config or load_runtime_config())

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = main
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.
    #
    # S-3 output gate: the agent class deliberately does NOT define a
    # `_security_gate_output` method. FunctionNode._security_gate_output is
    # @final (it provides the default credential scan); the domain S-3 gate for
    # this template lives inline in PostProcessNode.execute() via the
    # module-level _run_s3_domain_gate() helper in src/nodes/post_process_node.py
    # (the CoE-green reference pattern — mirrors a peer template).


# Alias for backward compat (server.py imports Graph).
# Class name RetailDemandForecastingReportAgent matches config/agent.yaml class: field.
Graph = RetailDemandForecastingReportAgent
