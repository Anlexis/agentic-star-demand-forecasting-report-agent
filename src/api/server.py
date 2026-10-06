"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, AgentGateway calls agent.invoke() directly.

import os
import secrets
from typing import Any, Dict, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets import SecretProvider
from framework.secrets.context import bound_secrets
from framework.security.credential_detector import detect_credentials_in_value
from shared.secrets import InMemoryProvider
from shared.secrets import factory as secrets_factory
from src.graph.graph import RetailDemandForecastingReportAgent, load_runtime_config
from src.services.request_contract import MAX_REQUEST_BYTES

app = FastAPI(title="Agent")

# The agent's identity, exactly as config/agent.yaml declares it. Both values are
# held to the manifest by tests/integration/test_manifest_identity_alignment.py,
# which READS both sides rather than restating either — so these literals cannot
# drift away from the manifest without the suite failing.
#
# `namespace` is lower(industry), not the lowercased template id. `agent_name` is
# the manifest's `name`: the registry's own key for this agent, and the registry
# is what provisions secrets in the platform deployment, so the standalone
# deployment must scope them identically or a secret resolves from a different
# store in each. Note it is the human-readable label and NOT the graph's `name`
# property (the class identifier), which is what /health reports.
#
# They are written as literals at each call site rather than referenced through a
# constant, so the value is readable where it is used — the two call sites below
# are the only places the identity appears in this module.

# Constructed with the declared runtime parameters, so max_retry, the timeout
# and the prompt settings are live in this deployment and not only under the
# registry, which passes config/config.yaml the same way.
agent = RetailDemandForecastingReportAgent(config=load_runtime_config())
agent.compile()
# The secret provider is scoped by the identity the manifest declares, so a
# secret resolves from the same place here and under the registry. The provider
# reads `env/namespaces/{namespace}/…` and `env/agents/{namespace}/{name}/…`, so
# a namespace that disagrees with config/agent.yaml silently splits one agent's
# secrets across two stores — no error at boot, and nothing fails until a secret
# is declared, and then only in one of the two deployments. `namespace` is
# lower(industry) — "ret" — not the lowercased template id.
agent.provision_secrets(secrets_factory(namespace="ret", agent_name="Retail Demand Forecasting Report Agent"))

# Structured caller metadata this adapter accepts. Anything else is DROPPED
# before the request reaches the agent. Dropping rather than ignoring is the
# load-bearing part: an undeclared key that survives into the invocation is
# returned verbatim in the first node's result, where the platform's output gate
# scans every value — so one credential-shaped string in an undeclared field
# fails the run at the first node, before any template code executes, with a
# traceback the caller cannot act on. Declaring a narrow contract does not
# prevent that; only removing the key does.
_ACCEPTED_CONTEXT_KEYS = frozenset({"channel"})


def _entrypoint_secrets() -> SecretProvider:
    """Boot-time SecretProvider for the ENTRY-POINT / DEPLOYMENT credential.

    The caller-auth token INVOKE_AUTH_TOKEN is a *deployment-level* credential:
    it authenticates the /invoke caller at the standalone HTTP boundary, BEFORE
    any InvocationContext (and so before ctx.secrets) exists. That makes it a
    different class of secret from the agent's per-invocation secrets, which are
    resolved through ctx.secrets.require().

    The framework SecretProvider is still the access boundary: this loads the
    deployment credential from its documented source — the process environment
    set by the deploy job, which also presents the same value as a Bearer token
    when it gathers deployment evidence — into an InMemoryProvider once at boot,
    exactly as shared.secrets.factory() loads dotenv values into a
    DotenvProvider. The request handler then reads the token through the
    provider accessor rather than a raw environment read. Boot-safe: an absent
    or empty token yields a provider whose .get() returns None, preserving the
    "no token set → ANONYMOUS callers" contract with no raise at import.

    PATTERN NOTE (for other templates): entry-point/deployment credentials that
    arrive via the process environment are loaded into an InMemoryProvider here;
    agent secrets that arrive via env/*.env files stay on secrets_factory() and
    are read node-side through ctx.secrets.require().
    """
    values = {}
    token = os.environ.get("INVOKE_AUTH_TOKEN")
    if token:
        values["INVOKE_AUTH_TOKEN"] = token
    return InMemoryProvider(values, namespace="ret", agent_name="Retail Demand Forecasting Report Agent")


_ENTRYPOINT_SECRETS = _entrypoint_secrets()


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""
    input_context: Dict[str, Any] = Field(default_factory=dict)


def _accepted_context(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return only the declared context keys, refusing credential-shaped values.

    The screen uses the platform's own detector, the same function the output
    gate calls, so what this adapter refuses and what the platform would block
    are the same set by construction rather than by a local approximation that
    could drift. Fields are iterated one at a time purely so the refusal can
    name the offending field: `detect_credentials_in_value` over a mapping is
    defined as the union over its values, so per-field scanning covers exactly
    the same ground.

    A credential-shaped value here cannot produce a successful request either
    way — the platform gate would fail it at the first node — so refusing it at
    the boundary turns an opaque internal error into a 400 the caller can act
    on. The field NAME is echoed only because it comes from the closed accepted
    set; the value never is.
    """
    accepted: Dict[str, Any] = {}
    for key in sorted(_ACCEPTED_CONTEXT_KEYS):
        if key not in raw:
            continue
        value = raw[key]
        if detect_credentials_in_value(value):
            raise HTTPException(
                status_code=400,
                detail=f"input_context.{key} looks like a credential and was refused. Remove it and retry.",
            )
        accepted[key] = value
    return accepted


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> Dict[str, Any]:
    trust = getattr(request.state, "trust_level", TrustLevel.ANONYMOUS)
    # Standalone caller auth: when INVOKE_AUTH_TOKEN is set on the server
    # environment, callers that no upstream middleware vouched for (still
    # ANONYMOUS) must present it as a Bearer token and run at
    # VERIFIED_EXTERNAL. Middleware-established trust is never demoted. This
    # adapter is the entry-point auth boundary — a deployment-level caller
    # credential, not an agent secret, so ctx.secrets does not apply because no
    # InvocationContext exists before auth.
    #
    # The token is read through the framework SecretProvider accessor
    # (_ENTRYPOINT_SECRETS.get(), built once at boot from the deployment
    # environment), never via a raw environment read in the handler.
    expected = _ENTRYPOINT_SECRETS.get("INVOKE_AUTH_TOKEN")
    if expected and trust is TrustLevel.ANONYMOUS:
        supplied = request.headers.get("authorization", "")
        # Compare bytes: compare_digest raises TypeError on non-ASCII str input
        # (headers decode as latin-1), which would 500 instead of the generic 401.
        if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            # Generic body on purpose — do not leak whether the token was absent,
            # malformed, or wrong.
            raise HTTPException(status_code=401, detail="Token is invalid or expired.")
        trust = TrustLevel.VERIFIED_EXTERNAL

    # Refused here rather than inside the graph so an oversized body is rejected
    # before it is parsed. The graph enforces the same ceiling on its own input,
    # since it is also reachable without this adapter.
    if len(req.input.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"input exceeds the {MAX_REQUEST_BYTES}-byte limit.",
        )

    # 400, not 422: pydantic owns 422 and answers it with a list of error
    # objects, so reusing that status for an application refusal makes client
    # handling ambiguous.
    input_context = _accepted_context(req.input_context)

    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        # The framework wheel ships no type information, so invoke() resolves to
        # Any; the cast records the envelope shape the backbone actually returns
        # rather than letting Any propagate out of this module.
        return cast(Dict[str, Any], agent.invoke(req.input, ctx=ctx, input_context=input_context))


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "agent": agent.name}
