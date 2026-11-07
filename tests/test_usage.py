import json
from datetime import date, datetime

import pytest

from devex import engineers, synthetic_usage, usage
from devex.cli import PRICES, USAGE_EVENTS
from devex.usage import Price, PriceTable, UsageEvent

T0 = datetime(2026, 5, 1, 9, 30)
SONNET = Price("anthropic", "claude-sonnet-4", "mid-tier", date(2025, 5, 22), 3.0, 15.0, 0.3)
TABLE = PriceTable("illustrative", (SONNET,))


def _record(**overrides: object) -> dict[str, object]:
    return {
        "timestamp": "2026-05-01T09:30:00Z",
        "team": "payments",
        "engineer": "alice",
        "surface": "coding_agent",
        "provider": "anthropic",
        "model": "claude-sonnet-4",
        "input_tokens": 1000,
        "output_tokens": 200,
        **overrides,
    }


def test_from_record_pseudonymizes_and_normalizes_time():
    event = usage.from_record(_record())
    assert event.timestamp == T0
    assert event.engineer == engineers.pseudonym("alice") != "alice"
    assert event.task_type == "chat" and event.outcome is None and event.pr is None


def test_identity_passes_pseudonyms_through_and_reveals_on_request():
    assert usage.identity("eng-0a1b2c") == "eng-0a1b2c"
    assert usage.identity("") == "unattributed"
    assert usage.identity("alice", reveal=True) == "alice"
    assert usage.from_record(_record(engineer="")).engineer == "unattributed"


@pytest.mark.parametrize(
    "overrides",
    [
        {"surface": "fax"},
        {"task_type": "meeting"},
        {"outcome": "shipped"},
        {"task_class": "epic"},
        {"input_tokens": 10, "cached_input_tokens": 11},
    ],
)
def test_from_record_rejects_values_outside_the_schema(overrides):
    with pytest.raises(ValueError):
        usage.from_record(_record(**overrides))


def test_record_round_trip_drops_defaults():
    event = usage.from_record(_record(task_id="t1", task_type="pr", pr="42", outcome="merged"))
    record = usage.to_record(event)
    assert "cached_input_tokens" not in record and "cost_usd" not in record
    assert record["timestamp"] == "2026-05-01T09:30:00Z"
    assert usage.from_record(record) == event


@pytest.mark.parametrize("suffix", [".jsonl", ".csv"])
def test_write_and_load_events_round_trip(tmp_path, suffix):
    events = synthetic_usage.generate()[:50]
    path = tmp_path / f"events{suffix}"
    usage.write_events(events, path)
    assert usage.load_events(path) == events


def test_load_events_accepts_a_json_list(tmp_path):
    path = tmp_path / "events.json"
    path.write_text(json.dumps([_record(), _record(model="claude-sonnet-4", team="data")]))
    assert [e.team for e in usage.load_events(path)] == ["payments", "data"]


def _attr(key: str, value: object) -> dict[str, object]:
    kind = "intValue" if isinstance(value, int) else "stringValue"
    return {"key": key, "value": {kind: str(value) if kind == "intValue" else value}}


OTLP = {
    "resourceSpans": [
        {
            "resource": {"attributes": [_attr("devex.team", "platform")]},
            "scopeSpans": [
                {
                    "spans": [
                        {
                            "name": "chat claude-sonnet-4",
                            "startTimeUnixNano": "1777627800000000000",
                            "attributes": [
                                _attr("gen_ai.system", "anthropic"),
                                _attr("gen_ai.request.model", "claude-sonnet-4"),
                                _attr("gen_ai.usage.input_tokens", 5000),
                                _attr("gen_ai.usage.output_tokens", 400),
                                _attr("gen_ai.usage.cache_read.input_tokens", 3000),
                                _attr("user.id", "bob"),
                                _attr("devex.surface", "code_review"),
                                _attr("devex.task.type", "review"),
                                _attr("devex.task.id", "rev-1"),
                            ],
                        },
                        {"name": "http GET", "startTimeUnixNano": "1", "attributes": []},
                    ]
                }
            ],
        }
    ]
}


def test_from_otel_maps_genai_spans_and_skips_others():
    (event,) = usage.from_otel(OTLP)
    assert event.timestamp == T0
    assert (event.team, event.surface, event.task_type, event.task_id) == (
        "platform",
        "code_review",
        "review",
        "rev-1",
    )
    assert (event.input_tokens, event.output_tokens, event.cached_input_tokens) == (5000, 400, 3000)
    assert event.provider == "anthropic" and event.engineer == engineers.pseudonym("bob")


def test_load_events_detects_an_otlp_export(tmp_path):
    path = tmp_path / "traces.json"
    path.write_text(json.dumps(OTLP))
    assert len(usage.load_events(path)) == 1


def test_price_on_picks_the_price_in_effect_that_day():
    table = usage.load_prices(PRICES)
    assert usage.price_on(table, "o3", date(2025, 5, 1)).input == 10.0
    assert usage.price_on(table, "o3", date(2025, 7, 1)).input == 2.0
    assert usage.price_on(table, "o3", date(2025, 1, 1)) is None
    assert usage.price_on(table, "unknown-model", date(2026, 1, 1)) is None
    assert "verify" in table.note.lower()


def test_event_cost_bills_cached_input_at_the_cached_price():
    event = UsageEvent(
        T0, "t", "e", "ide_chat", "anthropic", "claude-sonnet-4", 1_000_000, 100_000, 400_000
    )
    assert usage.event_cost(event, SONNET) == pytest.approx(0.6 * 3 + 0.4 * 0.3 + 0.1 * 15)


def test_cost_honours_billed_cost_and_rejects_unpriced_models():
    billed = usage.from_record(_record(model="copilot-premium", cost_usd="0.04"))
    priced = usage.from_record(_record())
    costed = usage.cost((billed, priced), TABLE)
    assert costed[0].usd == 0.04 and costed[0].tier == "unknown"
    assert costed[1].tier == "mid-tier"
    stray = usage.from_record(_record(model="mystery"))
    assert usage.unpriced((stray, priced), TABLE) == ("mystery",)
    with pytest.raises(ValueError, match="mystery"):
        usage.cost((stray,), TABLE)


def test_committed_events_are_the_seeded_generator_output():
    assert usage.load_events(USAGE_EVENTS) == synthetic_usage.generate()


def test_synthetic_events_carry_no_real_identities():
    events = synthetic_usage.generate()
    assert all(e.engineer.startswith("eng-") or e.engineer == "unattributed" for e in events)
    assert all(e.timestamp.date() <= synthetic_usage.AS_OF for e in events)
    assert {e.surface for e in events} <= set(usage.SURFACES)
