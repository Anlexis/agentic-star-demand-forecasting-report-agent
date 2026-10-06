# PB-7: HITL Interrupt-Propagation Boundary Test
#
# This template does NOT enable cross-boundary HITL interrupt propagation.
# Ships as a skip stub per CoE PB-7 convention (CR-0930/C#14).

import importlib
import pytest


def _hitl_propagation_enabled() -> bool:
    try:
        graph_mod = importlib.import_module("src.graph.graph")
    except Exception:
        return False
    for obj in vars(graph_mod).values():
        if (
            isinstance(obj, type)
            and getattr(obj, "__module__", None) == graph_mod.__name__
            and getattr(obj, "propagate_hitl", False) is True
        ):
            return True
    return False


_HITL_PROPAGATION_ENABLED = _hitl_propagation_enabled()

_PB7_SKIP_REASON = (
    "HITL interrupt-propagation not implemented for this template "
    "(no graph class declares propagate_hitl=True; no cross-boundary "
    "interrupt() checkpoint) — PB-7 skip stub per CoE convention"
)


@pytest.mark.skipif(not _HITL_PROPAGATION_ENABLED, reason=_PB7_SKIP_REASON)
class TestPB7HitlInterruptPropagation:
    """PB-7: a HITL interrupt must propagate across the graph boundary.

    Skipped for this template — cross-boundary HITL propagation is not enabled.
    """

    def test_hitl_interrupt_propagates_to_caller(self):
        raise AssertionError("PB-7 real assertion not yet implemented for a HITL-enabled template")
