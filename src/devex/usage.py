"""Token usage events: one record per model call or agent step, from any source.

The schema is deliberately small and source-neutral. Adapters map three shapes onto it:
a generic JSONL or CSV export with the schema's own field names, and OpenTelemetry GenAI
spans (OTLP JSON). GitHub Copilot reports, provider usage/cost exports and LLM gateways
map onto the generic shape (see docs/09-token-economics.md). Engineers are pseudonymised
on ingest, as in the engineer ranking. Cost is computed here, from events and a dated
price table, never fetched.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from devex import engineers, routing

__all__ = [
    "FIELDS",
    "OUTCOMES",
    "SURFACES",
    "TASK_CLASSES",
    "TASK_TYPES",
    "Costed",
    "Price",
    "PriceTable",
    "UsageEvent",
    "cost",
    "event_cost",
    "from_otel",
    "from_record",
    "identity",
    "load_events",
    "load_prices",
    "price_on",
    "to_record",
    "unpriced",
    "write_events",
]

SURFACES = ("ide_chat", "completion", "coding_agent", "code_review", "ci", "sre", "other")
TASK_TYPES = ("pr", "review", "incident", "agent_session", "chat")
OUTCOMES = ("merged", "reverted", "abandoned", "resolved")
TASK_CLASSES = tuple(c.value for c in routing.TaskClass)
UNATTRIBUTED = "unattributed"
_PSEUDONYM = re.compile(r"eng-[0-9a-f]{6}")


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """``input_tokens`` counts every input token, cached ones included;
    ``cached_input_tokens`` is the part served from the provider's prompt cache.
    ``cost_usd``, when a source reports billed cost, overrides the price table."""

    timestamp: datetime
    team: str
    engineer: str
    surface: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    org: str = ""
    task_id: str = ""
    task_type: str = "chat"
    repo: str = ""
    pr: int | None = None
    outcome: str | None = None
    task_class: str | None = None
    cost_usd: float | None = None

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


FIELDS = tuple(f.name for f in fields(UsageEvent))
_DEFAULTS = {f.name: f.default for f in fields(UsageEvent)}


def identity(user: str, *, reveal: bool = False) -> str:
    """Stable pseudonym for a user id; existing pseudonyms pass through unchanged."""
    if not user:
        return UNATTRIBUTED
    return user if reveal or _PSEUDONYM.fullmatch(user) else engineers.pseudonym(user)


def _timestamp(value: object) -> datetime:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def _blank(value: object) -> bool:
    return value is None or value == ""


def _int(value: object) -> int:
    return 0 if _blank(value) else int(value)  # type: ignore[arg-type]


def _check(name: str, value: str | None, allowed: tuple[str, ...]) -> str | None:
    if value is not None and value not in allowed:
        raise ValueError(f"{name} {value!r} is not one of {', '.join(allowed)}")
    return value


def from_record(record: Mapping[str, object], *, reveal: bool = False) -> UsageEvent:
    """Generic shape: the schema's field names; blanks mean "not reported"."""
    text = {k: str(v) for k, v in record.items() if not _blank(v)}
    event = UsageEvent(
        timestamp=_timestamp(record["timestamp"]),
        team=text.get("team", UNATTRIBUTED),
        engineer=identity(text.get("engineer", ""), reveal=reveal),
        surface=_check("surface", text.get("surface", "other"), SURFACES) or "other",
        provider=text.get("provider", ""),
        model=text["model"],
        input_tokens=_int(record.get("input_tokens")),
        output_tokens=_int(record.get("output_tokens")),
        cached_input_tokens=_int(record.get("cached_input_tokens")),
        org=text.get("org", ""),
        task_id=text.get("task_id", ""),
        task_type=_check("task_type", text.get("task_type", "chat"), TASK_TYPES) or "chat",
        repo=text.get("repo", ""),
        pr=None if "pr" not in text else int(text["pr"]),
        outcome=_check("outcome", text.get("outcome"), OUTCOMES),
        task_class=_check("task_class", text.get("task_class"), TASK_CLASSES),
        cost_usd=None if "cost_usd" not in text else float(text["cost_usd"]),
    )
    if event.cached_input_tokens > event.input_tokens:
        raise ValueError("cached_input_tokens cannot exceed input_tokens (which includes them)")
    return event


def to_record(event: UsageEvent) -> dict[str, object]:
    """Schema-named record without the fields left at their default, so files stay small."""
    values = {name: getattr(event, name) for name in FIELDS}
    stamp = event.timestamp.isoformat(timespec="seconds") + "Z"
    return {
        name: stamp if name == "timestamp" else value
        for name, value in values.items()
        if name in ("timestamp", "model") or value != _DEFAULTS[name]
    }


# Schema field -> OpenTelemetry attributes, first present wins. gen_ai.* follows the GenAI
# semantic conventions; devex.* are the attributes a gateway or agent adds for attribution.
OTEL_ATTRIBUTES: Mapping[str, tuple[str, ...]] = {
    "model": ("gen_ai.response.model", "gen_ai.request.model"),
    "provider": ("gen_ai.provider.name", "gen_ai.system"),
    "input_tokens": ("gen_ai.usage.input_tokens",),
    "output_tokens": ("gen_ai.usage.output_tokens",),
    "cached_input_tokens": (
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.cache_read_input_tokens",
    ),
    "engineer": ("user.id", "enduser.id"),
    "task_id": ("devex.task.id", "gen_ai.conversation.id"),
    "team": ("devex.team",),
    "org": ("devex.org",),
    "surface": ("devex.surface",),
    "task_type": ("devex.task.type",),
    "repo": ("devex.repo",),
    "pr": ("devex.pr",),
    "outcome": ("devex.outcome",),
    "task_class": ("devex.task.class",),
    "cost_usd": ("devex.cost_usd",),
}
_OTEL_USAGE = ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens")


def _otel_value(value: Mapping[str, object]) -> object:
    kinds = ("stringValue", "intValue", "doubleValue", "boolValue")
    found = next((k for k in kinds if k in value), None)
    if found is None:
        return None
    return int(value[found]) if found == "intValue" else value[found]  # type: ignore[arg-type]


def _attributes(items: Iterable[Mapping[str, object]] | None) -> dict[str, object]:
    return {str(a["key"]): _otel_value(a.get("value") or {}) for a in items or ()}  # type: ignore[arg-type]


def _otel_event(attrs: Mapping[str, object], start_ns: int, *, reveal: bool) -> UsageEvent:
    record = {
        name: next((attrs[k] for k in keys if not _blank(attrs.get(k))), None)
        for name, keys in OTEL_ATTRIBUTES.items()
    }
    moment = datetime(1970, 1, 1) + timedelta(microseconds=start_ns // 1000)
    return from_record({**record, "timestamp": moment}, reveal=reveal)


def from_otel(document: Mapping[str, object], *, reveal: bool = False) -> tuple[UsageEvent, ...]:
    """OTLP JSON traces: every span carrying gen_ai.usage tokens becomes one event; resource
    attributes (e.g. devex.team set per service) apply to all of its spans."""
    return tuple(
        _otel_event(attrs, int(span["startTimeUnixNano"]), reveal=reveal)
        for rs in document.get("resourceSpans", ())  # type: ignore[union-attr]
        for resource in (_attributes((rs.get("resource") or {}).get("attributes")),)
        for ss in rs.get("scopeSpans", ())
        for span in ss.get("spans", ())
        for attrs in ({**resource, **_attributes(span.get("attributes"))},)
        if any(k in attrs for k in _OTEL_USAGE)
    )


def load_events(path: str | Path, *, reveal: bool = False) -> tuple[UsageEvent, ...]:
    """``.jsonl``/``.ndjson`` and ``.csv`` use the generic shape; ``.json`` is either a list of
    generic records or an OTLP JSON trace export (``resourceSpans``)."""
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix == ".csv":
        return tuple(from_record(r, reveal=reveal) for r in csv.DictReader(text.splitlines()))
    if source.suffix in (".jsonl", ".ndjson"):
        lines = (line for line in text.splitlines() if line.strip())
        return tuple(from_record(json.loads(line), reveal=reveal) for line in lines)
    document = json.loads(text)
    if isinstance(document, Mapping) and "resourceSpans" in document:
        return from_otel(document, reveal=reveal)
    return tuple(from_record(r, reveal=reveal) for r in document)


def write_events(events: Iterable[UsageEvent], path: str | Path) -> None:
    target = Path(path)
    records = tuple(map(to_record, events))
    if target.suffix == ".csv":
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(records)
        return
    lines = (json.dumps(r, separators=(",", ":")) for r in records)
    target.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens. ``tier`` names the routing catalog tier the model plays."""

    provider: str
    model: str
    tier: str
    effective_from: date
    input: float
    output: float
    cached_input: float


@dataclass(frozen=True, slots=True)
class PriceTable:
    note: str
    prices: tuple[Price, ...]


def load_prices(path: str | Path) -> PriceTable:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    return PriceTable(
        note=document.get("note", ""),
        prices=tuple(
            Price(
                provider=p["provider"],
                model=p["model"],
                tier=p["tier"],
                effective_from=date.fromisoformat(p["effective_from"]),
                input=float(p["input"]),
                output=float(p["output"]),
                cached_input=float(p.get("cached_input", p["input"])),
            )
            for p in document["prices"]
        ),
    )


def price_on(table: PriceTable, model: str, day: date) -> Price | None:
    """The price in effect for ``model`` on ``day``: the latest one that started on or before."""
    valid = (p for p in table.prices if p.model == model and p.effective_from <= day)
    return max(valid, key=lambda p: p.effective_from, default=None)


def event_cost(event: UsageEvent, price: Price) -> float:
    uncached = event.input_tokens - event.cached_input_tokens
    return (
        uncached * price.input
        + event.cached_input_tokens * price.cached_input
        + event.output_tokens * price.output
    ) / 1_000_000


@dataclass(frozen=True, slots=True)
class Costed:
    event: UsageEvent
    usd: float
    tier: str


def unpriced(events: Iterable[UsageEvent], table: PriceTable) -> tuple[str, ...]:
    """Models with neither a price in effect on the event's date nor a billed cost."""
    return tuple(
        sorted(
            {
                e.model
                for e in events
                if e.cost_usd is None and price_on(table, e.model, e.timestamp.date()) is None
            }
        )
    )


def cost(events: Iterable[UsageEvent], table: PriceTable) -> tuple[Costed, ...]:
    items = tuple(events)
    missing = unpriced(items, table)
    if missing:
        raise ValueError(f"no price in effect for: {', '.join(missing)}; add them to the table")

    def one(event: UsageEvent) -> Costed:
        price = price_on(table, event.model, event.timestamp.date())
        usd = event.cost_usd if event.cost_usd is not None else event_cost(event, price)  # type: ignore[arg-type]
        return Costed(event, usd, price.tier if price else "unknown")

    return tuple(map(one, items))
