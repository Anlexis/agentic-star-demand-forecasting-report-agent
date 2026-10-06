# Template Design Specification — RET-C2-281 Retail Demand Forecasting Report Agent

## Position in AgentCore Architecture

- **Agent Class**: RetailDemandForecastingReportAgent
- **L1 Base (framework base class)**: AgentBaseGraph — direct framework inheritance
- **Pattern**: Cat 2 — DocGenerationAgent (two-layer nested workflow)
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible); ADR-005 JSON-serialised strings for all dict/list fields
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution; Cat 2 nested via `GraphNode`)

> **Scope note.** Despite the "Swarm Intelligence" wording in the source idea, this template is a **single DocGenerationAgent** that produces a **read-only demand-forecast report**. It is NOT a multi-agent / autonomous (Cat 3) system — there is no reasoning loop, no tool execution, and no automatic purchasing. Buy figures are *recommendations only*.

## Domain Context

Demand-forecast report generator for retail merchandise planners and category managers (Japan grocery / fashion / GMD retailers, FPT ASEAN retail). Generates structured weekly/monthly demand-forecast reports with buy recommendations, inventory-target calculations, and seasonal-trend analysis from a category/SKU's historical sales series.

**Business driver**: Japan 2025年問題 planning-role labor shortage — automating the forecast-report drafting saves an estimated 10–15 hrs/week per planner (McKinsey "The Automation Curve in Agentic Commerce", May 2026; Deloitte "Retailers clear the shelves for agentic AI", 2026).

## Architecture Overview

### Backbone (outer AgentBaseGraph — fixed 5-node pipeline)

```
START → initialize → pre_process → main(GraphNode) → post_process → finalize → END
                                         ↓ (retry, max 3)
                                       pre_process
```

### Inner Domain Workflow (DomainWorkflowGraph — linear 5-node pipeline)

```
START → input_validate → parse_forecast_data → generate_forecast_sections
          → forecast_validation → output_format → END
```

### Node Configuration

| Node | Class | File | Trust | Responsibility | Input Keys | Output Keys |
|------|-------|------|-------|---------------|------------|-------------|
| initialize | InitializeNode | framework | — | session init | — | session_id, schema_version |
| pre_process | PreProcessNode | src/nodes/pre_process_node.py | VERIFIED_EXTERNAL | S-1 trust + the caller-data contract (bounds, inert identifiers, caps, directive screen) | user_input, input_context | validated_input, enriched_context, request_channel |
| main | ForecastReportGraphNode | src/graph/graph.py | — | delegates to DomainWorkflowGraph | validated_input | forecast_report, report_sections, validation_flags, review_required |
| post_process | PostProcessNode | src/nodes/post_process_node.py | ANONYMOUS | S-3 output gate; publishes the report, or withholds it and clears every output-bearing field | forecast_report | formatted_output, result |
| finalize | FinalizeNode | framework | — | response metadata | — | response_metadata, total_time_ms |
| input_validate (inner) | InputValidateNode | src/nodes/input_validate_node.py | ANONYMOUS | domain field validation + history normalisation | validated_input | forecast_input |
| parse_forecast_data (inner) | ParseForecastDataNode | src/nodes/parse_forecast_data_node.py | ANONYMOUS | velocity/trend/seasonality + inventory math | forecast_input | forecast_input (enriched) |
| generate_forecast_sections (inner) | GenerateForecastSectionsNode | src/nodes/generate_forecast_sections_node.py | ANONYMOUS | render the 7 report sections from the computed analytics (deterministic) | forecast_input | report_sections |
| forecast_validation (inner) | ForecastValidationNode | src/nodes/forecast_validation_node.py | ANONYMOUS | data-sufficiency / business-rule check | forecast_input | validation_flags, review_required |
| output_format (inner) | OutputFormatNode | src/nodes/output_format_node.py | ANONYMOUS | assemble final report document | report_sections, validation_flags | forecast_report, result |

### Data Flow

```
user_input (JSON forecast request)
    │
    ▼ PreProcessNode (VERIFIED_EXTERNAL, S-1)
validated_input (normalised JSON string)
enriched_context (JSON string — ADR-005)
    │
    ▼ ForecastReportGraphNode → DomainWorkflowGraph
    │   InputValidateNode            → forecast_input (JSON string — ADR-005)
    │   ParseForecastDataNode        → forecast_input (enriched analytics, ADR-005)
    │   GenerateForecastSectionsNode → report_sections (JSON string — ADR-005)
    │   ForecastValidationNode       → validation_flags (JSON string), review_required (bool)
    │   OutputFormatNode             → forecast_report (str), result (str)
    ▼ merge_output
forecast_report, report_sections, validation_flags, review_required → outer state
    │
    ▼ PostProcessNode (ANONYMOUS, S-3)
formatted_output (S-3-gated forecast_report), result
```

### State Definition

| Field | Type | Purpose | Producer |
|-------|------|---------|----------|
| validated_input | NotRequired[Optional[str]] | Normalised forecast-request JSON string | PreProcessNode |
| enriched_context | NotRequired[Optional[str]] | JSON: {source, channel, forecast_ref} | PreProcessNode |
| forecast_input | NotRequired[Optional[str]] | JSON: validated + enriched forecast payload | InputValidateNode / ParseForecastDataNode |
| report_sections | NotRequired[Optional[str]] | JSON: {section_name: text, ...} × 7 sections | GenerateForecastSectionsNode |
| validation_flags | NotRequired[Optional[str]] | JSON: data-sufficiency / business-rule result | ForecastValidationNode |
| review_required | NotRequired[Optional[bool]] | True if human review required | ForecastValidationNode |
| forecast_report | NotRequired[Optional[str]] | Final formatted forecast report text | OutputFormatNode |
| result | NotRequired[Optional[str]] | Same as forecast_report (backbone convention) | OutputFormatNode / PostProcessNode |
| request_channel | NotRequired[Optional[str]] | Validated caller channel from `input_context`; audit dimension, never rendered | PreProcessNode (bridged into the inner graph) |

**ADR-005 constraint**: all dict/list-valued fields use JSON-serialised `Optional[str]`. `to_json()` / `from_json()` helpers are defined in `src/schemas/state.py` and used at every producer/consumer boundary — one contract end-to-end.

**Prohibited**: re-declaring `formatted_output` (inherited from AgentState), credentials in State, Pydantic models.

### Input Payload Schema (user_input JSON)

```json
{
  "category": "Summer-2026-Apparel",
  "sku": "APP-SUM-0042",
  "forecast_horizon_weeks": 4,
  "historical_sales": [
    {"period": "2026-W18", "units": 120},
    {"period": "2026-W19", "units": 135},
    {"period": "2026-W20", "units": 128},
    {"period": "2026-W21", "units": 151}
  ],
  "current_inventory": 350,
  "lead_time_days": 21,
  "unit_cost": 12.5,
  "service_level": 0.95,
  "seasonality": "summer_peak"
}
```

### Caller-Data Contract

Declared once in `src/services/request_contract.py` and applied once, in
PreProcessNode. Nothing reaches a domain node that has not passed it:
`ForecastReportGraphNode.extract_input()` forwards only `validated_input`, with
no fallback to the raw request, because the backbone edge pre_process → main is
unconditional and every inner node runs at ANONYMOUS.

| Field | Required | Rule |
|---|---|---|
| `forecast_horizon_weeks` | yes | integer, 1–26 (a longer request is capped, not refused, at the 26-week business ceiling) |
| `historical_sales` | yes | 1–260 entries; `{period, units}` objects or bare numbers; each `units` finite, 0–1,000,000,000 |
| `category` / `sku` | one of the two | inert identifier |
| `seasonality` | no | inert identifier |
| `current_inventory` | no | integer, 0–1,000,000,000 |
| `lead_time_days` | no | integer, 0–365 |
| `unit_cost` | no | finite, 0–10,000,000 |
| `service_level` | no | finite, 0.5–0.9999 (default 0.95 when absent; an out-of-range value is refused, never silently replaced) |
| whole body | — | ≤ 256 KB, ≤ 64 top-level keys; undeclared keys are dropped |

**Inert identifier** = a single run of `[A-Za-z0-9_.-]`, 1–64 characters, no
whitespace. Every value in that class is rendered into the report document, and
the report is line-oriented, so free text there would let a caller write report
structure — a value containing newlines forges a governance block. The same
restriction removes chat-template control tokens (`<|…|>`, `[INST]`, `<<SYS>>`),
and it keeps values clear of the platform input filter's personal-name
heuristic, which rewrites two or more consecutive title-case words and would
otherwise put a redaction sentinel where the report's subject belongs.

**Every caller number** goes through a finite, bounded parser. `NaN` and
`Infinity` are accepted by most JSON parsers and compare False against every
threshold, so an unchecked one does not raise — it agrees with whatever the code
asks. Booleans are rejected before conversion, because `isinstance(True, int)`
is True in Python.

**Directive screen**: the parsed structure is walked depth-first, keys included,
both as supplied and with markup elements removed. Keys are screened because a
directive travels in a key name as easily as in a value, and `\u` escapes have
decoded by the time the structure exists.

**Refusals** name the field and a code from a closed set
(`not_finite`, `out_of_range`, `not_an_identifier`, `disallowed_directive`,
`history_too_long`, …) and never repeat the value that was refused.

### Structured caller context (`input_context`)

`POST /invoke` accepts an `input_context` object carrying exactly one declared
key, `channel` (an inert identifier). Every other key is **dropped at the
adapter** rather than ignored: an undeclared value that survives into the
invocation is returned verbatim in the first node's result, where the platform's
output gate scans it, so a credential-shaped string in an unknown field fails the
run before any template code executes. The adapter screens the declared field
with the platform's own credential detector and answers 400 naming the field.

The framework does not forward `input_context` across a nested-graph boundary —
`GraphNode.execute()` invokes the subgraph with a single string — so the
validated channel is carried into the inner graph by
`src/graph/context_bridge.py` and seeded through
`DomainWorkflowGraph._extra_initial_state()`.

### Output Contract

The report is published only if it satisfies both of the following; otherwise it
is withheld in full.

1. **No credential material.** Scanned with the **union** of the platform's
   credential detector and this template's local patterns. Neither alone closes
   the set: the platform's patterns describe credential formats (`sk_live_`,
   `AKIA…`, JWTs, database URIs) and match nothing of the shape `password=…`,
   which the local set catches; the local set misses the formats. Narrowing to
   either side is a bypass.
2. **Every rendered number is finite.** A report printing `nan` or `inf` states
   nothing while reporting success.

There is deliberately **no monetary rounding grid** here. Every figure the report
prints is an exact unit count or an exact derived spend, and a buyer places an
order against those numbers; snapping them to a coarse grid would make the
document wrong rather than safer. The two properties above are the invariant
this template holds in its place, and the output gate enforces them rather than
trusting the pipeline to produce them.

**Withholding clears state, it does not raise.** The backbone surfaces
`formatted_output or result`, and it does so on an error status too, so a gate
that merely refuses still ships the un-gated report through the fallback. On a
violation PostProcessNode returns ERROR, clears `result`, `forecast_report`,
`report_sections` and `validation_flags`, and replaces the output with a fixed
notice built from a closed-set code — non-empty, so the `or result` fallback
cannot reopen.

### Output Report Sections

1. **Demand Summary** — tier, trend, average/recent velocity, total projected demand
2. **Weekly Demand Forecast** — per-week projected units across the horizon
3. **Buy Recommendation** — recommended order qty, estimated spend, action
4. **Inventory Target** — safety stock, reorder point, service-level rationale
5. **Seasonal & Trend Analysis** — seasonality signal, trend, volatility
6. **Assumptions & Method** — model assumptions + computation method
7. **Data Quality Notes** — history coverage, volatility, confidence caveats

## Security Configuration

| Layer | Gate | Implementation |
|-------|------|---------------|
| S-1 | Trust enforcement | PreProcessNode `required_trust_level = VERIFIED_EXTERNAL`; the entry point maps a valid bearer credential to VERIFIED_EXTERNAL and never demotes middleware-established trust |
| S-2 | Input validation | The caller-data contract in PreProcessNode (see *Caller-Data Contract* above), plus the adapter's credential screen on `input_context`. InputValidateNode applies domain shaping only, not a second copy of the contract — two screens enforcing one rule make each other unfalsifiable |
| S-3 | Output gate | PostProcessNode, module-level `_run_output_gate()` called inline from `execute()`. The agent class deliberately defines no `_security_gate_output`: the framework's is `@final` and enforced at class-definition time |
| S-4 | Audit logging | `emit_trace_event()` in every node's `execute()` (positional call) |
| S-5 | Credential handling | No credentials in State; agent secrets via InvocationContext only. The deployment's caller credential is a different class — it authenticates the HTTP caller before any InvocationContext exists — and is loaded once at boot through a framework SecretProvider rather than read from the environment per request |

**Configuration** — identity and runtime parameters live in two files:

`config/agent.yaml` is the registry manifest, read at ROOT level with no `agent:`
block. It carries `id`, `name`, `namespace`, `version`, `category`, `industry`,
`generation_mode`, the single dotted `class` entry point, `required_trust_level`
and the `requires.secrets` / `requires.extras` declarations (both empty — this
template calls no `ctx.secrets.require()` and constructs no model client).

`config/config.yaml` carries the runtime parameters:

```yaml
max_retry: 3
timeout_s: 30
llm:
  system_prompt_template: prompts/demand_forecast_report.j2
  temperature: 0.0
  max_tokens: 4000
security:
  s3_gate_enabled: true
```

`namespace` is `lower(industry)` — `ret` — and scopes the secret store. The
entry point provisions under the same identity the manifest declares;
`tests/integration/test_manifest_identity_alignment.py` reads both sides rather
than restating either, so a divergence fails the suite instead of silently
splitting one agent's secrets across two stores.

## Framework Utilization

### Shared Components Used
- [x] InvocationContext — session id, correlation id, caller trust level
- [x] S-3: module-level `_run_output_gate()` in `post_process_node.py`, called inline from `execute()`; scans with the union of the platform credential detector and the local patterns, and additionally refuses a non-finite figure
- [x] `framework.security.credential_detector` — the same function on both boundaries: the adapter's `input_context` screen and the output gate's credential floor
- [x] S-4: `emit_trace_event()` — at least one domain-specific event per node `execute()`
- [x] `to_json()` / `from_json()` helpers in `src/schemas/state.py` — ADR-005 serialisation contract

### Composition Pattern

- **Pattern**: Cat 2 nested two-layer — GraphNode wrapping inner BaseGraph
- **Outer graph**: `RetailDemandForecastingReportAgent(AgentBaseGraph)` — fixed 5-node backbone
- **Inner graph**: `DomainWorkflowGraph(BaseGraph)` — 5-node linear domain pipeline
- **Error propagation**: propagate (SubgraphError on inner failure; outer backbone retries pre_process)

## Import Isolation Confirmation
- [x] Template does not import the Level-0 platform SDK
- [x] Import targets: `framework/` and `shared/` only (no Level-0 SDK)

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | AgentBaseGraph | Fixed sequential report pipeline; no LLM reasoning loop (single DocGen, not "swarm"/autonomous) |
| Composition pattern | Cat 1 (flat) | Cat 2 (nested GraphNode) | Cat 2 nested | 5 sequential domain steps; DocGenerationAgent pattern |
| Analytics placement | Inline in generate | Separate ParseForecastData node | Separate node | Single-responsibility; one analytics source consumed by generate + format |
| Buy governance | Auto-execute buy | Recommendation + review flag | Recommendation only | Read-only report; ForecastValidationNode sets review_required |
| State dict fields | bare dict | JSON-serialised str | JSON-serialised str | ADR-005: msgpack serialisation safety |
| Report generation | Model-written narrative | Deterministic rendering of computed figures | Deterministic | Every figure traces to arithmetic in `src/nodes/`, which is what a buyer signing off an order needs. The declared prompt settings are surfaced to the rendering node and recorded in the audit trace so a narrative layer can be wired over the same figures without changing this contract |
| Contract enforcement | One screen per node | One screen, in the node that owns the caller contract | One screen | Two screens enforcing the same rule mask each other: remove either and the suite stays green, so neither is falsifiable |
| Output invariant | Monetary precision grid | No grid; finite-and-clean enforcement | No grid | The report's figures are exact unit counts and an exact derived spend; rounding them to a grid would make the document wrong rather than safer |
