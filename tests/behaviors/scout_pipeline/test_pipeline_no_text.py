"""PL1 / DESIGN 9: ``pipeline.sqlite`` holds no text, ever.

Three checks, each independent of the store's own validators:

1. the schema: every table's columns are exactly the reviewed list below, and
   no column is a text payload (each TEXT column is an id, a job identity, a
   digest, a code, a store path key, a timestamp or a day);
2. the values: after a scenario that touches every table, every stored
   value is a number or matches its column's shape, and nothing in the file
   is contact-shaped (an email, a phone number) or carries whitespace;
3. the writes: a sentence, an email, a resume line, an absolute path or a
   URL with an email in it is refused before it reaches the file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sqlite3

import pytest

from gigai.scout.pipeline.store import COLUMN_KINDS, PipelineStore, PipelineStoreError, PostingBuild, PostingRecord, StepMetrics

#: The reviewed schema: (column, kind). A new column fails this test until it is added here with a non-text kind.
_REVIEWED: dict[str, dict[str, str]] = {
    "step": {
        "profile_id": "id", "job": "job", "name": "code", "lane": "lane", "model_target": "id", "state": "code",
        "input_digest": "digest", "done_digest": "digest", "output_ref": "ref", "output_digest": "digest",
        "generation": "integer", "attempts": "integer", "not_before": "real", "error_code": "code",
        "lease_owner": "owner", "lease_pid": "integer", "lease_until": "real", "claimed_at": "real",
        "cancel_requested": "integer", "approval_id": "id", "trigger": "code", "enqueued_at": "timestamp",
        "updated_at": "timestamp",
    },
    "step_run": {
        "id": "integer", "profile_id": "id", "job": "job", "name": "code", "attempt": "integer", "owner": "owner",
        "lane": "lane", "adapter": "id", "model": "model", "input_tokens": "integer", "output_tokens": "integer",
        "cached_tokens": "integer", "cost_usd": "real", "cost_status": "code", "started_at": "timestamp",
        "seconds": "real", "outcome": "code", "error_code": "code", "input_digest": "digest",
    },
    # 0.1.10.7 E: a model call outside a step; step_run's metrics columns, keyed by the call's kind.
    "model_call": {
        "id": "integer", "kind": "code", "profile_id": "id", "job": "job", "items": "integer",
        "lane": "lane", "adapter": "id", "model": "model", "input_tokens": "integer", "output_tokens": "integer",
        "cached_tokens": "integer", "cost_usd": "real", "cost_status": "code", "started_at": "timestamp",
        "seconds": "real", "outcome": "code", "error_code": "code", "input_digest": "digest",
    },
    "lane": {"lane": "lane", "not_before": "real", "backoff_seconds": "real", "error_code": "code", "updated_at": "timestamp"},
    "approval": {
        "id": "id", "profile_id": "id", "trigger": "code", "jobs": "integer", "est_calls": "integer", "est_tokens": "integer",
        "state": "code", "created_at": "timestamp", "decided_at": "timestamp", "decided_by": "code",
    },
    "anchor": {"scope": "code", "last_checked_at": "timestamp", "set_by": "code"},
    "cap_counter": {"cap": "code", "day": "day", "used": "integer"},
    # 0.1.10.7 PL6: the per-(posting, profile) read model. A posting's title, company and text are never kept here.
    "posting": {
        "job": "job", "profile_id": "id", "board": "board", "first_seen": "timestamp", "published_at": "timestamp",
        "removed_at": "timestamp", "listing_digest": "digest", "listing_known": "integer", "rank_score": "integer",
        "match_rank": "integer", "state": "code", "stale_code": "code", "assessed_at": "timestamp", "reqs_met": "integer",
        "reqs_total": "integer", "open_questions": "integer", "tailored": "integer", "label": "code",
        "ats_score": "integer", "pinned_digest": "digest", "settings_digest": "digest", "updated_at": "timestamp",
    },
    "posting_build": {
        "profile_id": "id", "match_digest": "digest", "facts_digest": "digest", "pinned_digest": "digest",
        "settings_digest": "digest", "row_count": "integer", "built_at": "timestamp",
    },
}
_NUMERIC = {"integer": ("INTEGER",), "real": ("REAL",)}
#: Kept separate from the store's own shapes on purpose: this is the test's reading of "no text".
_SHAPE = {
    "id": r"[A-Za-z0-9][A-Za-z0-9_.:-]*",
    "job": r"https://jobs\.example\.test/[a-z0-9/]+|text:sha256:[0-9a-f]{64}",
    "digest": r"sha256:[0-9a-f]{64}",
    "code": r"[a-z][a-z0-9_]*",
    "lane": r"claude_cli|codex_cli|ollama|local|api:[a-z0-9_.-]+",
    "model": r"[A-Za-z0-9][A-Za-z0-9_.:/+-]*",
    "ref": r"[a-z_]+/[A-Za-z0-9_]+/[0-9a-f]+\.json",
    "owner": r"[0-9a-f]{32}:[A-Za-z0-9_-]+",
    "timestamp": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z",
    "day": r"\d{4}-\d{2}-\d{2}",
    "board": r"(greenhouse|lever|ashby):[a-z0-9-]+",
}
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_PAYLOAD_WORDS = re.compile(r"text|message|note|body|content|title|answer|story|resume|posting|summary|reason|detail|name_text")

_P = "profile_7f3c"
_JOB = "https://jobs.example.test/acme/42"
_PASTED = "text:sha256:" + "ab" * 32


def _digest(*parts: str) -> str:
    return "sha256:" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def test_the_schema_has_exactly_the_reviewed_columns_and_no_text_payload_column(tmp_path: Path) -> None:
    store = PipelineStore(tmp_path / "pipeline.sqlite")
    connection = sqlite3.connect(store.path)
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    assert sorted(tables) == sorted(_REVIEWED)
    for table in tables:
        columns = {row[1]: row[2] for row in connection.execute(f"PRAGMA table_info({table})")}
        assert set(columns) == set(_REVIEWED[table]), table
        for column, declared in columns.items():
            kind = _REVIEWED[table][column]
            assert kind in _NUMERIC or kind in _SHAPE, (table, column)
            if kind in _NUMERIC:
                assert declared in _NUMERIC[kind], (table, column, declared)
            assert not _PAYLOAD_WORDS.search(column), (table, column)
    assert {table: dict(kinds) for table, kinds in COLUMN_KINDS.items()} == _REVIEWED  # the store documents the same


def _scenario(store: PipelineStore) -> None:
    """Touch every table and every column that can be written."""

    approval = store.create_approval(profile_id=_P, trigger="story_saved", jobs=2, est_calls=6, est_tokens=500_000)
    store.enqueue(_P, _JOB, "tailor", input_digest=_digest("t1"), trigger="answer_saved", lane="claude_cli", model_target="claude_cli",
                  downstream_lanes={"reassess": ("codex_cli", "codex_cli")})
    store.enqueue(_P, _PASTED, "tailor", input_digest=_digest("t2"), trigger="story_saved", lane="api:openrouter-main",
                  model_target="openrouter-main", approval_id=approval)
    store.decide_approval(approval, approved=True, decided_by="agent")
    metrics = StepMetrics(adapter="claude_cli", model="claude-opus-5-5", input_tokens=1000, output_tokens=200, cached_tokens=600,
                          cost_usd=0.01, cost_status="provider_reported")
    tailor = store.claim(worker="w1")
    store.finish(tailor, output_ref=f"resumes/{_P}/{'0f' * 32}.json", output_digest=_digest("tailored.md"), metrics=metrics)
    other = store.claim(worker="w2", lanes=["api:openrouter-main"])
    store.fail(other, "model_target_unavailable", metrics=StepMetrics(adapter="openrouter", cost_status="unavailable"))
    reassess = store.claim(worker="w1", lanes=["codex_cli"])
    store.renew(reassess)
    store.fail(reassess, "assess_timeout")
    ats = store.claim(worker="w3", lanes=["local"])
    store.finish(ats, input_digest=_digest("ats-in"), output_ref=f"ats/{_P}/{'1e' * 32}.json", output_digest=_digest("ats"))
    store.cancel(_P, _PASTED)
    store.advance_anchor("2026-10-02T14:02:00Z", set_by="scout_new")
    store.spend("pipeline_calls", "2026-10-02", 3, limit=40)
    store.spend("rank_calls", "2026-10-02", 50)
    store.record_call(kind="assess", lane="codex_cli", seconds=11.2, metrics=metrics, profile_id=_P, job=_JOB, input_digest=_digest("p"))
    store.record_call(kind="assess", lane="codex_cli", seconds=0.4, outcome="error", error_code="model_timeout", job=_PASTED)
    answered = store.record_call(kind="rank", lane="api:openrouter-main", seconds=3.0, items=50, metrics=StepMetrics(adapter="openrouter_api"))
    store.fail_call(answered, "model_output_invalid")
    store.replace_postings(_build(), [_posting_row(), _posting_row(job=_JOB + "/2", removed_at="2026-10-02T09:00:00.000000Z", state="needs_answers")])
    store.set_match_ranks([(2, _JOB, _P)])


def _build() -> PostingBuild:
    return PostingBuild(_P, _digest("m"), _digest("f"), _digest("r"), _digest("s"), 0, "2026-10-02T14:02:00.000000Z")


def _posting_row(**changes: object) -> PostingRecord:
    values: dict[str, object] = dict(
        job=_JOB, profile_id=_P, board="lever:acme", first_seen="2026-10-01T08:00:00.000000Z",
        published_at="2026-09-30T00:00:00.000000Z", removed_at=None, listing_digest=_digest("c"), listing_known=True,
        rank_score=82, match_rank=1, state="matched", stale_code="posting_changed", assessed_at="2026-10-01T09:00:00.000000Z",
        reqs_met=3, reqs_total=4, open_questions=1, tailored=True, label="needs_attention", ats_score=71,
        pinned_digest=_digest("r"), settings_digest=_digest("s"), updated_at="2026-10-02T14:02:00.000000Z",
    )
    values.update(changes)
    return PostingRecord(**values)  # type: ignore[arg-type]


def test_every_stored_value_is_an_id_a_digest_a_code_or_a_number_and_nothing_is_contact_shaped(tmp_path: Path) -> None:
    store = PipelineStore(tmp_path / "pipeline.sqlite")
    _scenario(store)
    connection = sqlite3.connect(store.path)
    seen: dict[str, int] = {}
    for table, kinds in _REVIEWED.items():
        rows = connection.execute(f"SELECT {', '.join(kinds)} FROM {table}").fetchall()
        assert rows, table  # the scenario reached every table
        seen[table] = len(rows)
        for row in rows:
            for (column, kind), value in zip(kinds.items(), row):
                if value is None:
                    continue
                if kind in _NUMERIC:
                    assert type(value) in (int, float), (table, column, value)
                    continue
                assert type(value) is str and re.fullmatch(_SHAPE[kind], value), (table, column, value)
                assert not re.search(r"\s", value) and not _EMAIL.search(value), (table, column, value)
                if kind not in ("timestamp", "day"):  # dates are digit runs by design; so are long hex ids and digests
                    assert not _PHONE.search(re.sub(r"[0-9a-f]{16,}", "", value)), (table, column, value)
    assert seen["step_run"] >= 4 and seen["approval"] == 1 and seen["anchor"] == 1 and seen["cap_counter"] == 2
    assert seen["model_call"] == 3
    assert seen["posting"] == 2 and seen["posting_build"] == 1
    # And the raw file as a whole: no email shape anywhere in its bytes.
    connection.close()
    store.close()
    raw = b"".join(path.read_bytes() for path in tmp_path.iterdir() if path.name.startswith("pipeline.sqlite"))
    assert not _EMAIL.search(raw.decode("latin-1"))


@pytest.mark.parametrize(
    ("call", "bad"),
    [
        ("job", "https://jobs.example.test/apply?ref=jane.doe@example.com"),
        ("job", "Senior engineer at Acme, remote"),
        ("profile_id", "Jane Doe"),
        ("digest", "sha256:not a digest"),
        ("model_target", "call me at 555 123 4567"),
        ("error_code", "Traceback: model said jane.doe@example.com"),
        ("error_code", "The model timed out"),
        ("output_ref", "/Users/jane/resume.md"),
        ("output_ref", "../../home/resume.md"),
        ("metrics_model", "jane.doe@example.com"),
        ("metrics_adapter", "my adapter"),
        ("anchor", "last tuesday"),
        ("cap", "Pipeline Calls"),
        ("call_kind", "Assess this job"),
        ("call_job", "Senior engineer at Acme, remote"),
        ("call_profile_id", "Jane Doe"),
        ("call_error_code", "Traceback: model said jane.doe@example.com"),
        ("call_input_digest", "You are assessing a resume"),
        ("call_model", "jane.doe@example.com"),
        ("call_lane", "my laptop"),
        ("call_fail", "The answer was not JSON"),
        ("posting_job", "Staff Engineer at Acme, remote"),
        ("posting_job", "https://jobs.example.test/apply?ref=jane.doe@example.com"),
        ("posting_board", "Acme Corp"),
        ("posting_board", "lever:jane.doe@example.com"),
        ("posting_state", "Needs your answers"),
        ("posting_stale_code", "The posting changed: now asks for Terraform"),
        ("posting_label", "Recommended, apply today"),
        ("posting_first_seen", "last tuesday"),
        ("posting_listing_digest", "Own the Python inference services"),
        ("posting_pinned_digest", "Jane Doe resume v3"),
        ("posting_since", "yesterday"),
    ],
)
def test_text_is_refused_before_it_reaches_the_file(tmp_path: Path, call: str, bad: str) -> None:
    store = PipelineStore(tmp_path / "pipeline.sqlite")
    good = dict(profile_id=_P, job=_JOB, input_digest=_digest("x"), model_target="claude_cli")
    with pytest.raises(PipelineStoreError):
        if call in ("job", "profile_id", "digest", "model_target"):
            args = {**good, {"digest": "input_digest"}.get(call, call): bad}
            store.enqueue(args["profile_id"], args["job"], "tailor", input_digest=args["input_digest"], trigger="process_now",
                          lane="claude_cli", model_target=args["model_target"])
        elif call == "posting_since":
            store.postings(since=bad)
        elif call.startswith("posting_"):
            store.replace_postings(_build(), [_posting_row(), _posting_row(**{"job": _JOB + "/2", call.removeprefix("posting_"): bad})])
        elif call == "call_fail":
            store.fail_call(store.record_call(kind="assess", lane="codex_cli", seconds=1.0), bad)
        elif call.startswith("call_"):
            values: dict[str, object] = dict(kind="assess", lane="codex_cli", seconds=1.0, outcome="error")
            if call == "call_model":
                values["metrics"] = StepMetrics(model=bad)
            else:
                values[call.removeprefix("call_")] = bad
            store.record_call(**values)  # type: ignore[arg-type]
        elif call == "anchor":
            store.advance_anchor(bad, set_by="scout_new")
        elif call == "cap":
            store.spend(bad, "2026-10-02")
        else:
            store.enqueue(_P, _JOB, "tailor", input_digest=_digest("x"), trigger="process_now", lane="claude_cli")
            claim = store.claim()
            if call == "error_code":
                store.fail(claim, bad)
            elif call == "output_ref":
                store.finish(claim, output_ref=bad)
            elif call == "metrics_model":
                store.finish(claim, metrics=StepMetrics(model=bad))
            else:
                store.finish(claim, metrics=StepMetrics(adapter=bad))
    dump = "\n".join(sqlite3.connect(store.path).iterdump())
    assert bad not in dump
