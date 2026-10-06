# The identity the entry point provisions secrets under, against the identity
# the manifest declares.
#
# The manifest is the source of truth for who this agent is, and the secret
# provider is scoped by that identity: it reads `env/namespaces/{namespace}/…`
# and `env/agents/{namespace}/{agent_name}/…`. Under the registry the scope
# comes from config/agent.yaml; standalone it comes from whatever
# src/api/server.py passes to the factory. When those disagree, one agent's
# secrets live in two stores and a key provisioned for one deployment is simply
# absent in the other — with no error at boot, because a missing tier file is
# ignored by design. Nothing fails until a secret is declared, and then it fails
# in only one of the two places.
#
# Both sides are READ here, never restated: the manifest is parsed from
# config/agent.yaml, and the entry point's identity is taken from the provider
# the module actually bound. A test that spelled the expected namespace out
# twice would keep passing through exactly the drift it exists to catch.
#
# The DIRECTION of the alignment is pinned too. `namespace:` is
# lower(industry_code) — the manifest's own `industry` field, lowercased — so
# re-aligning the wrong way round (moving the manifest onto the entry point's
# value instead of the reverse) fails here rather than passing as a fix.
#
# Deterministic: no model, no network, no filesystem beyond the manifest.

from pathlib import Path

from framework.utils.config_loader import load_config

from src.api.server import agent

# tests/integration/<this file> -> parents[2] is the repository root.
_MANIFEST_PATH = Path(__file__).resolve().parents[2] / "config" / "agent.yaml"
_MANIFEST = load_config(str(_MANIFEST_PATH))

# The provider the entry point bound at import time — the identity that is live
# in a standalone deployment, not a re-derivation of it.
_PROVISIONED = agent._secrets_provider


def test_manifest_declares_the_identity_fields() -> None:
    """The fields every comparison below rests on must actually be present.

    Without this, a manifest that lost `namespace:` would make each comparison
    `None == None` and the file would pass while asserting nothing.
    """
    assert _MANIFEST.get("namespace"), f"{_MANIFEST_PATH} declares no namespace"
    assert _MANIFEST.get("name"), f"{_MANIFEST_PATH} declares no name"
    assert _MANIFEST.get("industry"), f"{_MANIFEST_PATH} declares no industry"


def test_provisioned_namespace_matches_the_manifest() -> None:
    assert _PROVISIONED._namespace == _MANIFEST["namespace"], (
        "src/api/server.py provisions secrets under namespace "
        f"{_PROVISIONED._namespace!r}, but config/agent.yaml declares "
        f"{_MANIFEST['namespace']!r}. A secret would resolve from a different "
        "store standalone than under the registry."
    )


def test_provisioned_agent_name_matches_the_manifest() -> None:
    assert _PROVISIONED._agent_name == _MANIFEST["name"], (
        "src/api/server.py provisions secrets for agent name "
        f"{_PROVISIONED._agent_name!r}, but config/agent.yaml declares "
        f"{_MANIFEST['name']!r}."
    )


def test_manifest_namespace_is_lower_industry() -> None:
    """`namespace:` is lower(industry_code) — the fleet-wide convention.

    Checked against the manifest's own `industry` field, which pins which of the
    two values is the correct one to align on without hard-coding either.
    """
    assert _MANIFEST["namespace"] == _MANIFEST["industry"].lower(), (
        f"config/agent.yaml declares namespace {_MANIFEST['namespace']!r}; the "
        f"convention is lower(industry) = {_MANIFEST['industry'].lower()!r}."
    )


def test_entrypoint_class_matches_the_manifest() -> None:
    """The manifest's dotted entry point must name the class the server binds."""
    declared = _MANIFEST["class"].rsplit(".", 1)[-1]
    assert type(agent).__name__ == declared


def test_the_runtime_name_is_distinct_from_the_registry_name() -> None:
    """Two names exist here, and each is used for something different.

    `config/agent.yaml`'s `name` is the registry's key and the secret scope; the
    graph's `name` property is the runtime identity the framework reports and
    `/health` echoes. They are not the same string in this template, which is
    easy to mistake for a defect — it is pinned rather than corrected so that if
    either is normalised later, this test says so instead of a deployment
    quietly reading from a new secret store.
    """
    assert agent.name == "RetailDemandForecastingReportAgent"
    assert _MANIFEST["name"] == "Retail Demand Forecasting Report Agent"
