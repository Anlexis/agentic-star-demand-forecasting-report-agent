"""AgentCore Platform v1.0"""

# RET-C2-281 — the caller-data contract for a demand-forecast request.
#
# Every value a caller can influence passes through this module exactly once,
# in PreProcessNode, before any domain node sees it. It is the single place the
# template states what a request may contain, so the rules can be read, tested
# and falsified in one file rather than rediscovered per node.
#
# Four properties the contract holds, and why each one is here:
#
#   1. FINITE AND BOUNDED NUMBERS.  ``float("nan")`` and ``float("inf")`` parse
#      cleanly, and Python's ``json`` module accepts the bare literals ``NaN``
#      and ``Infinity`` in a request body. Every comparison against NaN is
#      False, so an unchecked non-finite threshold does not raise — it silently
#      suppresses the decision the template exists to make. Measured on this
#      template before the contract existed: ``unit_cost: Infinity`` produced a
#      report reading ``Estimated Order Spend: inf`` with ``status: success``,
#      and a NaN inside ``historical_sales`` produced
#      ``Avg Weekly Velocity: nan units`` — both shipped to the caller as a
#      successful forecast. Non-finite values now fail CLOSED, naming the field.
#
#   2. INERT IDENTIFIERS FOR EVERYTHING THAT RENDERS.  ``category``, ``sku``
#      and ``seasonality`` are printed into the report document. Free text
#      there is caller-controlled output injection: a value containing newlines
#      forged a complete ``FORECAST GOVERNANCE NOTE`` block claiming
#      ``Human Review Required: NO`` at the top of a real report. Restricting
#      these to a single run of ``[A-Za-z0-9_.-]`` removes every character the
#      forgery needs, and removes the chat-template control tokens
#      (``<|im_start|>``, ``[INST]``, ``<<SYS>>``) in the same stroke.
#      A second, less obvious consequence is deliberate: the platform's input
#      filter masks any two or more consecutive Title Case words as a personal
#      name, so the previous free-text category "Winter Outerwear - Puffer
#      Jackets" reached the renderer as "[MASKED] - [MASKED]" and the report
#      named a redaction sentinel as its subject. The inert alphabet has no
#      whitespace, so that pattern cannot match and a hyphenated label such as
#      "Winter-Outerwear-Puffer-Jackets" survives intact and readable.
#
#   3. STRUCTURAL CAPS.  A request carries a list; a list with no ceiling is a
#      resource contract nobody wrote down. The whole body, the history series
#      and every string are bounded.
#
#   4. FAIL CLOSED, NAMING THE FIELD AND NEVER THE VALUE.  A rejection carries
#      a field name and a code from a closed set. The rejected value is never
#      echoed — not into the error, not into the audit event, not into a log
#      line — because echoing it hands the caller a channel out of the very
#      screen that just refused them.

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, Final, List, Mapping, Tuple

# ── Alphabets and limits ─────────────────────────────────────────────────────

# Everything a caller supplies that is rendered into the report document must
# match this pattern in full. One run, no whitespace, no markup characters.
_INERT_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9_.\-]{1,64}")

# Period labels are carried in state but never rendered; they are still bounded
# so a caller cannot use them as unbounded storage.
_PERIOD_LABEL: Final = re.compile(r"[A-Za-z0-9_.\-]{1,32}")

# Serialized request body ceiling. The platform adapter caps the HTTP body
# separately; this is the template's own limit on what it will parse.
MAX_REQUEST_BYTES: Final[int] = 262_144

# Five years of weekly observations. Longer series do not improve a
# recent-window velocity forecast; they only enlarge the parse.
MAX_HISTORY_ENTRIES: Final[int] = 260

# Largest number of top-level keys accepted before the body is treated as
# malformed rather than merely carrying extras.
MAX_TOP_LEVEL_KEYS: Final[int] = 64

# Declared numeric fields: name -> (low, high, integer?)
_NUMERIC_BOUNDS: Final[Dict[str, Tuple[float, float, bool]]] = {
    "forecast_horizon_weeks": (1, 26, True),
    "current_inventory": (0, 1_000_000_000, True),
    "lead_time_days": (0, 365, True),
    "unit_cost": (0.0, 10_000_000.0, False),
    "service_level": (0.5, 0.9999, False),
}

# Per-observation unit bound, applied to every entry of historical_sales.
_UNITS_BOUNDS: Final[Tuple[float, float]] = (0.0, 1_000_000_000.0)

# Rendered, caller-supplied identifier fields.
_IDENTIFIER_FIELDS: Final[Tuple[str, ...]] = ("category", "sku", "seasonality")

# The complete set of top-level keys this template consumes. Anything else is
# dropped before the payload reaches state — an undeclared key that survives
# into a node result is scanned by the platform output gate and can fail the
# run opaquely, so dropping is both a contract and a safety property.
DECLARED_FIELDS: Final[frozenset[str]] = frozenset({"historical_sales", *_IDENTIFIER_FIELDS, *_NUMERIC_BOUNDS})

# ── Refusal codes (closed set) ───────────────────────────────────────────────

REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "empty_request",
        "request_too_large",
        "malformed_json",
        "not_an_object",
        "too_many_fields",
        "missing_required_field",
        "missing_identifier",
        "not_an_identifier",
        "not_a_number",
        "not_finite",
        "out_of_range",
        "history_empty",
        "history_too_long",
        "history_entry_malformed",
        "disallowed_directive",
    }
)


class RequestContractError(ValueError):
    """A caller request that the contract refuses.

    Carries the offending FIELD and a code from :data:`REASON_CODES`. The
    offending value is deliberately absent: an error message is a caller-visible
    channel, and echoing a rejected value there would defeat the screen that
    produced it.
    """

    def __init__(self, field: str, code: str) -> None:
        if code not in REASON_CODES:  # pragma: no cover - guards a coding error
            raise AssertionError(f"unknown refusal code: {code!r}")
        self.field = field
        self.code = code
        super().__init__(f"{field}: {code}")


# ── Directive screening ──────────────────────────────────────────────────────

# Chat-template control tokens, screened as a CLASS rather than as a list of
# directive phrases. The platform's own input policy blocks ``<|im_start|>`` and
# ``[INST]`` at high confidence but scores ``<<SYS>>`` as no finding at all, so a
# template that relies on the platform alone has a hole exactly where the
# best-known attack sits. Screened here on the raw text and again on the text
# with markup removed, because stripping tags can re-assemble a directive that
# was split by them.
_CONTROL_TOKENS: Final[Tuple[re.Pattern[str], ...]] = (
    re.compile(r"<\s*\|[^|>]{0,64}\|\s*>"),
    re.compile(r"<<\s*/?\s*SYS\s*>>", re.IGNORECASE),
    re.compile(r"\[\s*/?\s*(?:INST|SYS)\s*\]", re.IGNORECASE),
    re.compile(r"<\s*/?\s*(?:system|user|assistant)\s*>", re.IGNORECASE),
    re.compile(r"#{2,}\s*(?:system|instruction)\b", re.IGNORECASE),
)

_DIRECTIVE_PHRASES: Final[Tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"\b(?:ignore|disregard|forget)\s+(?:all\s+|the\s+|any\s+)?"
        r"(?:previous|above|prior|earlier)\s+(?:instruction|prompt|context|rule)",
        re.IGNORECASE,
    ),
    re.compile(r"\byou\s+are\s+now\s+(?:a|an)\b", re.IGNORECASE),
    re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
)

_MARKUP = re.compile(r"<[^>]{0,128}>")


def _screen_text(text: str, field: str) -> None:
    """Refuse a string carrying a chat-template control token or a directive.

    Screened twice: as supplied, and with markup elements removed. The first
    pass sees tokens that a strip would delete; the second sees directives that
    a strip re-assembles out of fragments the caller interleaved with tags.
    """
    stripped = _MARKUP.sub("", text)
    for candidate in (text, stripped):
        for pattern in (*_CONTROL_TOKENS, *_DIRECTIVE_PHRASES):
            if pattern.search(candidate):
                raise RequestContractError(field, "disallowed_directive")


def screen_structure(value: Any, field: str = "request", _depth: int = 0) -> None:
    """Screen a parsed JSON structure depth-first, KEYS included.

    Keys are screened because a JSON body can carry a directive in a key name
    just as easily as in a value, and because ``\\u`` escapes decode during
    parsing — a scan of the raw request text alone would miss both.
    """
    if _depth > 8:
        raise RequestContractError(field, "history_entry_malformed")
    if isinstance(value, str):
        _screen_text(value, field)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                _screen_text(key, field)
            screen_structure(item, field, _depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            screen_structure(item, field, _depth + 1)


# ── Primitive validators ─────────────────────────────────────────────────────


def finite_in_range(value: Any, field: str, low: float, high: float, *, integer: bool = False) -> float:
    """Return *value* as a finite number inside ``[low, high]``, or refuse.

    ``bool`` is rejected before the numeric conversion: ``isinstance(True, int)``
    is True in Python, so ``True`` would otherwise arrive as the quantity 1.
    Strings are accepted because a JSON body may quote its numbers, but the
    literals ``nan``/``inf`` do not survive the finiteness check that follows.
    """
    if isinstance(value, bool):
        raise RequestContractError(field, "not_a_number")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except (TypeError, ValueError):
            raise RequestContractError(field, "not_a_number") from None
    else:
        raise RequestContractError(field, "not_a_number")

    if not math.isfinite(number):
        raise RequestContractError(field, "not_finite")
    if not (low <= number <= high):
        raise RequestContractError(field, "out_of_range")
    if integer:
        return float(int(number))
    return number


def inert_identifier(value: Any, field: str) -> str:
    """Return *value* as an inert identifier, or refuse.

    ``fullmatch``, not ``search``: an anchored match is the difference between
    "this value is an identifier" and "this value contains one somewhere".
    """
    if not isinstance(value, str):
        raise RequestContractError(field, "not_an_identifier")
    candidate = value.strip()
    if not _INERT_IDENTIFIER.fullmatch(candidate):
        raise RequestContractError(field, "not_an_identifier")
    return candidate


# ── The request contract ─────────────────────────────────────────────────────

REQUIRED_FIELDS: Final[Tuple[str, ...]] = ("forecast_horizon_weeks", "historical_sales")


def _validate_history(raw: Any) -> List[Dict[str, Any]]:
    """Normalise ``historical_sales`` into bounded ``{period, units}`` records.

    Accepts either a list of objects or a bare list of numbers; the bare form
    synthesises period labels. Every unit figure goes through the finite parser,
    so a NaN buried at index 40 of a 60-week series refuses the request rather
    than propagating into the mean.
    """
    if not isinstance(raw, list):
        raise RequestContractError("historical_sales", "history_entry_malformed")
    if not raw:
        raise RequestContractError("historical_sales", "history_empty")
    if len(raw) > MAX_HISTORY_ENTRIES:
        raise RequestContractError("historical_sales", "history_too_long")

    records: List[Dict[str, Any]] = []
    for index, entry in enumerate(raw, start=1):
        field = f"historical_sales[{index}]"
        if isinstance(entry, Mapping):
            if "units" not in entry:
                raise RequestContractError(field, "history_entry_malformed")
            units = finite_in_range(entry["units"], f"{field}.units", *_UNITS_BOUNDS)
            label = entry.get("period", f"P{index}")
            if not isinstance(label, str) or not _PERIOD_LABEL.fullmatch(label.strip()):
                raise RequestContractError(f"{field}.period", "not_an_identifier")
            period = label.strip()
        elif isinstance(entry, (int, float, str)) and not isinstance(entry, bool):
            units = finite_in_range(entry, f"{field}.units", *_UNITS_BOUNDS)
            period = f"P{index}"
        else:
            raise RequestContractError(field, "history_entry_malformed")
        records.append({"period": period, "units": units})
    return records


def validate_request(raw_input: Any) -> Dict[str, Any]:
    """Parse and validate a caller forecast request; return the clean payload.

    The returned mapping contains only :data:`DECLARED_FIELDS`, each already
    bounded and inert. Undeclared keys are dropped rather than carried: an
    undeclared value that reaches state is still scanned by the platform's
    output gate on the first node's result, so carrying one turns an unknown
    field into an opaque failure four layers away from the caller.

    :raises RequestContractError: with the offending field and a closed-set code.
    """
    if not isinstance(raw_input, str) or not raw_input.strip():
        raise RequestContractError("input", "empty_request")
    if len(raw_input.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise RequestContractError("input", "request_too_large")

    try:
        parsed = json.loads(raw_input.strip())
    except (ValueError, TypeError):
        # The parser's own message quotes the offending text; it is not carried.
        raise RequestContractError("input", "malformed_json") from None

    if not isinstance(parsed, dict):
        raise RequestContractError("input", "not_an_object")
    if len(parsed) > MAX_TOP_LEVEL_KEYS:
        raise RequestContractError("input", "too_many_fields")

    # Screen the PARSED structure, not the raw text: \u escapes have decoded by
    # now, and keys are visible as keys.
    screen_structure(parsed, "input")

    for required in REQUIRED_FIELDS:
        if required not in parsed:
            raise RequestContractError(required, "missing_required_field")

    payload: Dict[str, Any] = {"historical_sales": _validate_history(parsed["historical_sales"])}

    for field in _IDENTIFIER_FIELDS:
        if field in parsed and parsed[field] not in (None, ""):
            payload[field] = inert_identifier(parsed[field], field)

    if not payload.get("category") and not payload.get("sku"):
        raise RequestContractError("category", "missing_identifier")

    for field, (low, high, integer) in _NUMERIC_BOUNDS.items():
        if field in parsed and parsed[field] is not None:
            number = finite_in_range(parsed[field], field, low, high, integer=integer)
            payload[field] = int(number) if integer else number

    return payload


def dropped_field_count(raw_input: str) -> int:
    """Number of top-level keys the contract will drop. Audit signal only.

    Returns the count, never the names: an undeclared key name is caller data
    and is not echoed anywhere.
    """
    try:
        parsed = json.loads(raw_input.strip())
    except (ValueError, TypeError):
        return 0
    if not isinstance(parsed, dict):
        return 0
    return sum(1 for key in parsed if key not in DECLARED_FIELDS)
