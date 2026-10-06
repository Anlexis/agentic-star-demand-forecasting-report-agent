"""AgentCore Platform v1.0"""

# AgentRegistry entry-point re-export (SR High fix, scaffold issue #12).
# config/agent.yaml declares `module: "src.graph"` + `class:
# "RetailDemandForecastingReportAgent"`. AgentRegistry does
# importlib.import_module("src.graph") then getattr(module, "<class>"), so the
# agent class MUST be importable from the PACKAGE, not only from
# src.graph.graph. Previously this file exported nothing but a docstring, so
# manifest-based discovery failed with AttributeError. Re-export the agent class
# (and the Graph alias server.py imports) here.
from src.graph.graph import Graph, RetailDemandForecastingReportAgent

__all__ = ["Graph", "RetailDemandForecastingReportAgent"]
