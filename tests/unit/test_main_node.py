# RET-C2-281 — Unit Tests: Main Node

from src.nodes.main_node import MainNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel


class TestMainNode:
    """Unit tests for the main business logic node.

    Nodes are invoked via ``node(state)`` (BaseNode.__call__), not
    ``node.execute(state)`` directly, so the mandatory S-1 trust gate runs on
    every call. MainNode is ANONYMOUS, so the caller_trust_level is ANONYMOUS.
    """

    def setup_method(self):
        self.node = MainNode()

    def test_success_path(self):
        """TC: Main node processes valid input and returns SUCCESS."""
        state = {
            "validated_input": "test input",
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "node_history": [],
            "error_log": [],
        }
        result = self.node(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        # CoE R2 #15 regression guard: State status must be the serialized
        # string, never the bare enum member. AgentStatus is a str-Enum, so
        # isinstance(..., str) alone cannot catch a bare-enum regression —
        # the exact-type check can.
        assert type(result["status"]) is str, (  # noqa: E721 — see comment above
            f"status must be AgentStatus.SUCCESS.value (str), " f"got {type(result['status']).__name__}"
        )
        assert result["result"] is not None

    def test_empty_input(self):
        """TC: Main node handles empty input gracefully."""
        state = {
            "validated_input": "",
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "node_history": [],
            "error_log": [],
        }
        result = self.node(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        # Regression guard (see test_success_path): an exact-type check, because
        # AgentStatus is a str-Enum and isinstance(..., str) cannot tell a bare
        # enum member from its serialized value.
        assert type(result["status"]) is str  # noqa: E721

    def test_execute_method_signature(self):
        """Node contract: Node must implement execute(state) not _invoke_impl.

        Canonical contract (the review criteria §2-2):
          - Override: execute(self, state: AgentState) -> dict
          - PROHIBITED: _invoke_impl(), process() override
        """
        import inspect

        # Must have execute() defined on the concrete class (not just inherited stub)
        assert hasattr(MainNode, "execute"), "MainNode must implement execute()"

        sig = inspect.signature(MainNode.execute)
        params = list(sig.parameters.keys())
        # execute(self, state) — at minimum two parameters
        assert len(params) >= 2, f"execute() must accept (self, state), got params: {params}"
        assert params[1] == "state", f"Second parameter must be 'state', got '{params[1]}'"

        # Must NOT define _invoke_impl at the domain level
        assert (
            "_invoke_impl" not in MainNode.__dict__
        ), "_invoke_impl() must not be defined in MainNode — use execute() instead"
