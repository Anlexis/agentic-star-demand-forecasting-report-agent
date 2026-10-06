# Demand Forecasting Report Agent

AI agent for forecasting retail demand and producing order recommendation reports, built with Agentic Star.

> **Category**: Cat 2 (a domain-specific pipeline for one job-to-be-done)
> **Industry**: Retail
> **Template ID**: RET-C2-281

## Overview

A buyer deciding how much of a line to order next is usually working from a
spreadsheet of weekly sell-through and a rule of thumb. The arithmetic is not
hard, but it is fiddly and easy to get subtly wrong: how much of last month's
lift is trend and how much is noise, how much cover the lead time needs, how
much safety stock a given service level actually implies.

This agent takes a category or SKU, a weekly sales history and the replenishment
parameters, and returns a structured **demand forecast report**: projected weekly
demand across the horizon, a buy recommendation with its estimated spend, the
reorder point and safety stock implied by the requested service level, a trend
and seasonality read, and a governance note saying whether the forecast is firm
enough to act on without a human look.

The forecast is deterministic and rule-based — a recent-window velocity grown by
the recent-versus-baseline trend, clamped so one volatile week cannot produce a
runaway projection, with safety stock from a normal-demand model. Every figure
in the report can be traced back to the arithmetic in `src/nodes/`, which is the
point: a buyer signing off on an order needs to be able to see why the number is
what it is.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails at graph
compile / start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Sending a request

The forecast request is a JSON object, sent as the request string:

```json
{
  "input": "{\"category\": \"Winter-Outerwear-Puffer-Jackets\", \"sku\": \"RET-WO-PUFFER-XL-2026\", \"forecast_horizon_weeks\": 6, \"historical_sales\": [{\"period\": \"2026-W01\", \"units\": 320}, {\"period\": \"2026-W02\", \"units\": 345}], \"current_inventory\": 640, \"lead_time_days\": 21, \"unit_cost\": 38.5, \"service_level\": 0.95, \"seasonality\": \"winter_demand_peak\"}"
}
```

`forecast_horizon_weeks` and `historical_sales` are required, along with at least
one of `category` or `sku`. `historical_sales` accepts either `{period, units}`
objects or a bare list of numbers.

Two constraints are worth knowing before you send real data:

- **Values that appear in the report are inert identifiers.** `category`, `sku`
  and `seasonality` must each be a single run of `A-Z a-z 0-9 _ . -`, at most 64
  characters, with no spaces. Use `Winter-Outerwear-Puffer-Jackets`, not
  `Winter Outerwear - Puffer Jackets`. The report is a line-oriented document, so
  free text there would let a caller write report structure; and the platform's
  input filter rewrites two or more consecutive title-case words as a personal
  name, which would put a redaction sentinel where the report's subject should be.
- **Numbers must be finite and in range.** `NaN` and `Infinity` are valid JSON
  literals to most parsers and compare False against every threshold, so they are
  refused rather than carried into the arithmetic.

A refused request names the field that failed and the reason, and never repeats
the value that was refused. `docs/02_design.md` carries the full contract,
including the bounds on each field; `src/services/request_contract.py` is where
they are declared.

## Project Structure

```
src/          agent implementation (nodes, graphs, services, schemas)
tests/        unit, boundary and integration tests
config/       agent manifest and runtime parameters
deploy/       local deployment recipe and a smoke payload
docs/         design and operational documentation
```

`docs/` holds the design (`02_design.md`) and the test specification
(`03_test_spec.md`).

## Customising

1. Adjust `config/config.yaml` for your own environment and policies.
2. Change the business guardrails in `src/services/request_contract.py` — the
   horizon ceiling, the history cap and the per-field bounds are all declared
   there in one place.
3. Review the forecast arithmetic in `src/nodes/parse_forecast_data_node.py` and
   the report sections in `src/nodes/generate_forecast_sections_node.py`.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
