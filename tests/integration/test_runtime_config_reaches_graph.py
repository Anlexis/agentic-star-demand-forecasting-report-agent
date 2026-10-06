# A declared runtime value must reach the node that consumes it.
#
# Configuration that is declared and never read is the failure mode this repo
# has already had once: the graph's loader read a block that the flat registry
# manifest no longer contains, so it returned an empty mapping and every
# declared value silently fell back to a node default. Nothing raised, the
# manifest validated, and the suite stayed green, because no test connected a
# declared value to an observable effect.
#
# These tests make that connection. They read config/config.yaml — the file a
# deployment reads — and follow one declared value all the way to the inner
# graph's state, through the real invoke path rather than by calling the loader.

import json
from pathlib import Path

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext, TrustLevel
from framework.utils.config_loader import load_config

from src.graph.graph import Graph, load_runtime_config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "config" / "config.yaml"

_REQUEST = json.dumps(
    {
        "category": "widgets",
        "forecast_horizon_weeks": 4,
        "historical_sales": [120, 130, 140, 150, 160, 170],
    }
)


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in (
        "pre_process_node",
        "input_validate_node",
        "parse_forecast_data_node",
        "generate_forecast_sections_node",
        "forecast_validation_node",
        "output_format_node",
        "post_process_node",
    ):
        monkeypatch.setattr(f"src.nodes.{module}.emit_trace_event", lambda *a, **k: None)


def test_runtime_config_file_exists_and_is_read() -> None:
    """The loader reads config/config.yaml, and that file declares something.

    An empty result here would make every assertion below vacuous, which is
    precisely how the previous breakage stayed invisible.
    """
    assert _CONFIG_PATH.exists(), f"{_CONFIG_PATH} is required"
    on_disk = load_config(str(_CONFIG_PATH))
    assert on_disk, "config/config.yaml declares nothing"
    assert load_runtime_config() == on_disk


def test_the_loader_does_not_read_the_registry_manifest() -> None:
    """Runtime parameters and registry identity live in different files.

    The manifest carries no runtime block at all, so a loader pointed at it
    returns an empty mapping — which is the exact shape of the defect, and is
    indistinguishable from "nothing was declared" unless it is asserted.
    """
    manifest = load_config(str(_REPO_ROOT / "config" / "agent.yaml"))
    assert "agent" not in manifest, "the registry manifest must be flat"
    assert "max_retry" not in manifest
    assert "max_retry" in load_runtime_config()


def test_declared_prompt_template_reaches_the_inner_graph_state() -> None:
    """Follow one declared value to the node that observes it, on the real path.

    The framework does not thread a graph's config into node.execute()'s config
    argument on the .invoke() path — that argument is only populated by a direct
    unit-level call — so a node-level assertion would pass on a graph where the
    value never arrives.
    """
    declared = load_runtime_config()["llm"]["system_prompt_template"]
    observed: dict = {}

    import src.nodes.generate_forecast_sections_node as gen

    original = gen.GenerateForecastSectionsNode.execute

    def _record(self, state, config=None):  # type: ignore[no-untyped-def]
        observed["system_prompt_template"] = state.get("system_prompt_template")
        return original(self, state, config)

    gen.GenerateForecastSectionsNode.execute = _record  # type: ignore[method-assign]
    try:
        agent = Graph()
        agent.compile()
        result = agent.invoke(_REQUEST, ctx=InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL))
    finally:
        gen.GenerateForecastSectionsNode.execute = original  # type: ignore[method-assign]

    assert result["status"] == AgentStatus.SUCCESS.value, result
    assert observed["system_prompt_template"] == declared


def test_a_changed_declaration_changes_what_the_inner_graph_observes() -> None:
    """The value is read, not merely present.

    Asserting only that the observed value equals the declared one would also
    pass if both happened to equal a hard-coded default, so the declaration is
    moved and the observation must move with it.
    """
    observed: dict = {}

    import src.nodes.generate_forecast_sections_node as gen

    original = gen.GenerateForecastSectionsNode.execute

    def _record(self, state, config=None):  # type: ignore[no-untyped-def]
        observed["system_prompt_template"] = state.get("system_prompt_template")
        return original(self, state, config)

    gen.GenerateForecastSectionsNode.execute = _record  # type: ignore[method-assign]
    try:
        agent = Graph(config={"llm": {"system_prompt_template": "prompts/other.j2"}})
        agent.compile()
        agent.invoke(_REQUEST, ctx=InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL))
    finally:
        gen.GenerateForecastSectionsNode.execute = original  # type: ignore[method-assign]

    assert observed["system_prompt_template"] == "prompts/other.j2"


def test_declared_prompt_file_exists() -> None:
    """A declared prompt path that names no file is a dead declaration."""
    declared = load_runtime_config()["llm"]["system_prompt_template"]
    assert (_REPO_ROOT / declared).exists(), f"{declared} is declared but absent"
