# Test Specification — RET-C2-281 Retail Demand Forecasting Report Agent

## 1. Test Strategy

- **Agent:** RET-C2-281 — Retail Demand Forecasting Report Agent (Cat 2,
  DocGeneration pattern, two-layer nested graph: outer `AgentBaseGraph` backbone
  + inner `DomainWorkflowGraph` `BaseGraph`). Read-only report generator — it
  synthesises a demand-forecast document from a caller-supplied sales history; it
  is not multi-agent despite the "Swarm"-style naming elsewhere.
- **Coverage target:** ≥ 90% of `src/nodes/` + `src/graph/` branches.
- **Test types:** Unit (per node + graph wiring) · Proof-of-Boundary (framework
  security/serialization contracts) · Backbone invoke (full `Graph().invoke()`).
- **Framework provisioning:** `framework` (agenticstar-agentcore) is supplied by
  CI — the CI stub package on `PYTHONPATH` for the CI stub package arm, or the wheel from the
  package registry for the wheel arm. Tests import the REAL Wave-1 modules on
  `develop`; there are no stub nodes.
- **S-4 audit:** `emit_trace_event` is patched at the node module level in unit
  tests to avoid audit-backend calls, never via a `sys.modules` stub (which would
  break the real `shared` package the framework loads at import time).
- **S-2 input gate:** the contract tests call `node.execute(state)` directly,
  with no framework wrapper in front. That is deliberate rather than a
  convenience. The platform's input policy refuses some of the same shapes before
  `execute()` ever runs, so an end-to-end refusal shows only that *something*
  refused — a template leaning on the platform for its guarantees has none of its
  own where that policy is absent or configured off, and none at all when the
  graph is called as a library. The end-to-end tests assert refusal
  *behaviourally* (error status, nothing published) and leave the attribution to
  the direct-call tests.

### Test file map

| File | Scope |
|------|-------|
| `tests/unit/test_nodes.py` | All 7 domain/backbone nodes + outer & inner graph wiring |
| `tests/unit/test_request_contract.py` | The caller-data contract, called directly: finite/bounded numbers, inert identifiers, the directive screen, structural caps, the closed refusal-code set, and PreProcessNode's own refusals |
| `tests/unit/test_main_node.py` | Deprecated `MainNode` stub — `execute()` contract kept green |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | TC-06/TC-07 — overriding a `@final` platform gate raises at class definition |
| `tests/integration/test_invoke_contract.py` | End to end through the real ASGI `/invoke` with bearer auth: happy path, the non-finite matrix, range/boolean cases, identifier and directive refusals, structural caps, the `input_context` channel, and the deployment payload |
| `tests/integration/test_manifest_identity_alignment.py` | The secret identity the entry point provisions against the identity the manifest declares — both sides read, neither restated |
| `tests/integration/test_runtime_config_reaches_graph.py` | A declared value in `config/config.yaml` followed into the inner graph on the real invoke path, and moved to prove it is read |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | PB-6 per-node + backbone invoke order (VERIFIED_EXTERNAL) + S-1 gate + payload alignment |
| `tests/proof_of_boundary/test_output_containment.py` | Every exit that can return a non-success status, measured at the caller envelope, with the success path as the control |
| `tests/proof_of_boundary/test_import_isolation.py` | PB-4 Level-0 import isolation (AST scan) |
| `tests/proof_of_boundary/test_state_safety.py` | PB-2/PB-5 State msgpack/credential safety (AST scan) |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | PB-7 HITL interrupt-propagation (skip stub — no cross-boundary HITL) |

### Canonical valid payload (PB-6 `_VALID_PAYLOAD`)

The retail demand-forecast request used by the backbone invoke test and by
`deploy/invoke_payload.json` (the two MUST stay identical — asserted by
`test_invoke_payload_matches_pb6`):

```json
{
  "category": "Winter-Outerwear-Puffer-Jackets",
  "sku": "RET-WO-PUFFER-XL-2026",
  "forecast_horizon_weeks": 6,
  "historical_sales": [
    {"period": "2026-W01", "units": 320}, {"period": "2026-W02", "units": 345},
    {"period": "2026-W03", "units": 360}, {"period": "2026-W04", "units": 402},
    {"period": "2026-W05", "units": 455}, {"period": "2026-W06", "units": 498},
    {"period": "2026-W07", "units": 512}, {"period": "2026-W08", "units": 560}
  ],
  "current_inventory": 640,
  "lead_time_days": 21,
  "unit_cost": 38.5,
  "service_level": 0.95,
  "seasonality": "winter_demand_peak"
}
```

Eight weeks of history (≥ 4 periods) with a mild upward trend; a positive lead
time and current inventory below projected demand ⇒ a replenishment buy is
recommended and a complete 7-section report is produced.

`category` and `seasonality` are hyphen/underscore forms rather than free text.
That is the contract's inert-identifier rule, and it is also what keeps the
report's subject out of the platform input filter's personal-name heuristic: the
earlier `"Winter Outerwear - Puffer Jackets"` reached the renderer as
`"[MASKED] - [MASKED]"`, so the deployment's own smoke invoke produced a report
about a redaction sentinel and reported success.
`test_the_deploy_payload_carries_nothing_the_input_filter_would_mask` asserts the
committed payload is clear of every shape that filter recognises.

## 2. Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Where |
|-------|------|----------------|-------|
| TC-01 | State contract: flat `TypedDict`, domain fields `NotRequired`, no Pydantic/dataclass | AST scan: 0 violations | `test_state_safety.py` |
| TC-02 | Invalid/empty/non-JSON input rejected at PreProcessNode | `status=error`, error_log populated | `TestPreProcessNode` |
| TC-03 | No JWT/credential in State | CI `gate-credential-scan`: 0 violations | CI + `test_state_safety.py` |
| TC-04 | `execute(self, state)` contract — no `_invoke_impl` | Signature `(self, state)`, `_invoke_impl` absent | `test_execute_signature_is_state_first`, `test_main_node.py` |
| TC-05 | S-4: `emit_trace_event()` called inside each node `execute()` | ≥1 domain event per node (positional form) | verified by CoE preflight S-4/#3 |
| TC-08 | S-1: `required_trust_level` enforced in `__call__` before `execute()` | ANONYMOUS caller → refused; VERIFIED_EXTERNAL → admitted | `TestS1TrustGate` |
| TC-08a | Outer `PreProcessNode` = VERIFIED_EXTERNAL; inner nodes + post_process = ANONYMOUS | trust levels asserted per node | `test_trust_level_*` |
| TC-11 | S-3 output gate on post_process | credential pattern or non-finite figure → report withheld, every output-bearing field cleared, `status=error`; clean → published | `TestPostProcessNode`, `test_output_containment.py` |
| TC-12 | Caller-data contract | every numeric field finite and in range; rendered values inert; caps enforced; refusals name the field and a closed-set code, never the value | `test_request_contract.py`, `TestNumericContract`, `TestRenderedValuesAreInert`, `TestStructuralCaps` |
| TC-13 | The deployed agent serves a request | `POST /invoke` with the deployment bearer credential returns a real report containing the caller's own identifier | `TestTheAgentServesRequests` |
| TC-14 | Declared runtime config reaches the graph | a value declared in `config/config.yaml` is observed by the inner rendering node on the real invoke path, and moves when the declaration moves | `test_runtime_config_reaches_graph.py` |
| TC-15 | Secret identity alignment | the namespace and agent name the entry point provisions equal the manifest's, and `namespace == lower(industry)` | `test_manifest_identity_alignment.py` |

## 3. Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Where |
|-------|----------|------|----------------|-------|
| PB-2 | State serialization | AST scan of `src/schemas/state.py` | primitives only; no Pydantic/dataclass | `test_state_safety.py` |
| PB-4 | Import isolation | AST scan of `src/` | 0 Level-0 (`agenticstar` / platform) imports | `test_import_isolation.py` |
| PB-5 | Checkpoint safety | no credential-named fields / prohibited types in State | inspection pass | `test_state_safety.py` |
| PB-6 | Invoke execution order (per node) | `__call__`: S-1 gate → S-4 node_start → S-2 input gate → `execute()` → S-3 output gate → S-4 node_complete | order verified for every `src/nodes/` class | `TestInvokeOrder` |
| PB-6b | Backbone invoke order | full `Graph().invoke(_VALID_PAYLOAD, ctx=VERIFIED_EXTERNAL)` | `status=success`; node_history = `[Initialize, PreProcess, ForecastReportGraphNode, PostProcess, Finalize]` | `TestBackboneInvokeOrder` |
| PB-6c | Real external caller | `InvocationContext(caller_trust_level=VERIFIED_EXTERNAL)` — **never** `for_internal()` | inner ANONYMOUS nodes accept the passthrough trust; SUCCESS end-to-end | `TestBackboneInvokeOrder` |
| PB-6d | S-1 denial (NODE level) | `PreProcessNode({caller_trust_level: ANONYMOUS})` | `status=error`, "trust gate" in error_log; VERIFIED_EXTERNAL admitted | `TestS1TrustGate` |
| PB-6e | Payload alignment | `deploy/invoke_payload.json["input"] == _VALID_PAYLOAD` | Stage-5 deploy-stg invoke exercises the PB-6 payload | `test_invoke_payload_matches_pb6` |
| PB-7 | HITL interrupt propagation | skip stub — `propagate_hitl=False`, no cross-boundary interrupt() checkpoint | skipped with reason (real assertion when HITL wired) | `test_pb7_hitl_interrupt_propagation.py` |

## 4. Business Logic Tests

| BL-ID | Test | Input | Expected Result | Where |
|-------|------|-------|----------------|-------|
| BL-01 | Happy-path report generation | `_VALID_PAYLOAD` | 7-section report; `RETAIL DEMAND FORECAST REPORT` + `FORECAST GOVERNANCE NOTE` present | `test_backbone_invoke_succeeds_and_returns_output`, `TestInnerDomainGraph` |
| BL-02 | Forecast-request normalisation | horizon 52; bare-number history | horizon clamped to 26; `[100,200,…]` → `[{period:P1,units:…}]` | `test_horizon_capped_at_max`, `test_bare_number_history_is_normalised` |
| BL-03 | Demand velocity + weekly projection | flat 100/wk × 6, horizon 4 | avg/recent/baseline velocity = 100; projection = `[100,100,100,100]`; total 400 | `test_computes_velocity_and_projection` |
| BL-04 | Trend classification | flat vs rising series | `flat` vs `up` trend_direction | `test_flat_trend_direction`, `test_upward_trend_direction` |
| BL-05 | Demand-tier classification | avg ≥ 500 / < 100 | `high` / `low` tier | `test_demand_tier_high`, `test_demand_tier_low` |
| BL-06 | Inventory-math floor | ample current inventory | recommended buy floors at 0 (never negative) | `test_recommended_buy_never_negative` |
| BL-07 | 7-section generation | enriched forecast_input | all 7 section keys; summary reflects tier; buy line PLACE / NO-BUY | `TestGenerateForecastSectionsNode` |
| BL-08 | Forecast-governance review flags | insufficient history / non-positive velocity / high volatility / extreme trend | `review_required=True`, flag present; confidence high/medium/low by flag count | `TestForecastValidationNode` |
| BL-09 | Report assembly | 7 rendered sections + validation flags | all 7 headers + `FORECAST GOVERNANCE NOTE`; `Human Review Required: YES/NO` reflects flag | `TestOutputFormatNode` |
| BL-10 | Graph key coupling | inner `get_output` ↔ outer `merge_output` | 5 coupled keys mapped; `merge_output` returns changed keys only | `TestOuterGraphComposition`, `TestInnerDomainGraph` |

### Negative / boundary cases

| Case | Node | Expected |
|------|------|----------|
| empty `user_input` | PreProcessNode | `status=error`, `input: empty_request` |
| invalid JSON | PreProcessNode | `status=error`, `input: malformed_json` — the parser's message, which quotes the offending text, is not carried |
| JSON root not an object | PreProcessNode | `status=error`, `input: not_an_object` |
| missing `forecast_horizon_weeks` | PreProcessNode | `status=error`, `missing_required_field`, field named |
| missing category & sku | PreProcessNode | `status=error`, `missing_identifier` |
| `NaN` / `Infinity` in any numeric field | PreProcessNode | `status=error`, `not_finite`, field named — including an index deep inside `historical_sales` |
| out-of-range numeric | PreProcessNode | `status=error`, `out_of_range` |
| boolean where a number belongs | PreProcessNode | `status=error`, `not_a_number` |
| whitespace, newline or markup in a rendered value | PreProcessNode | `status=error`, `not_an_identifier` |
| `<\|im_start\|>` / `[INST]` / `<<SYS>>`, in a value or a key | PreProcessNode | `status=error`, `disallowed_directive` |
| `historical_sales` empty or over 260 entries | PreProcessNode | `status=error`, `history_empty` / `history_too_long` |
| request body over 256 KB | entry point | HTTP 400 |
| credential-shaped `input_context` value | entry point | HTTP 400 naming the field, never the value |
| undeclared `input_context` key | entry point | dropped before the invocation |
| unauthenticated caller | entry point / PreProcessNode | HTTP 401; with the adapter credential removed, the trust gate refuses and no report is produced |
| missing `forecast_input` | Parse / Generate / Validation | `status=error` |
| missing `report_sections` | OutputFormatNode | `status=error` |
| empty `forecast_report` | PostProcessNode | fallback message, `status=success` |
| credential or non-finite figure in the report | PostProcessNode | report withheld in full, every output-bearing field cleared, `status=error` |

## 5. Test Execution Summary

- Execution: `pytest tests/`, against the published framework wheel rather than
  an import shim — a pass under a stub is not a pass.
- Total: 233 tests — **232 passed, 1 skipped** (PB-7 skip stub, by design).
  - `test_request_contract.py` 79 · `test_nodes.py` 66 ·
    `test_invoke_contract.py` 54 · `test_output_containment.py` 8 ·
    `test_pb_invoke_order.py` 7 · `test_manifest_identity_alignment.py` 6 ·
    `test_runtime_config_reaches_graph.py` 5 · `test_main_node.py` 3 ·
    `test_framework_compliance_tc06_tc07.py` 2 · `test_import_isolation.py` 1 ·
    `test_state_safety.py` 1 · `test_pb7…` 1 (skip).
- Gates: `gate-dep-pinning`, `gate-stub-check`, `gate-cat-consistency`,
  import-isolation, composition, invoke-chain, credential-scan, trust-level,
  scaffold-integrity — all PASS.
- Coverage: node + graph modules exercised on both success and error paths.
