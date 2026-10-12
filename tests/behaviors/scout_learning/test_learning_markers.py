"""0.1.11.10 shipped lessons, slice 5 (core): the marker call that compares a shipped course with the master (``learning_markers``).

1. THE PROMPT holds the catalog's lesson titles, technologies and subtopic keys and titles, and the master's
   ``id | text`` lines through the privacy guard. It holds nothing else of the catalog (no vocabulary phrase, no
   title phrase) and no contact shape. The static privacy pin names the builder.
2. THE CHECKS: a valid answer comes back in the local page's markers format; a quote that is not verbatim, an
   evidence id that does not exist and a key the catalog does not hold never become a marker; a KNOWN from a line
   that has nothing to do with the subtopic is NEW; a SOME survives on its line or on a ``missing`` sentence that
   names the gap; a skills line is SOME at most.
3. THE CALL: one call for a small course, one per module for a big one; a timeout is retried once at once; an
   unusable answer gets one retry that names the problem; the cap and the cancel hook stop before a call; a master
   with no lines makes no call.
4. THE ESTIMATE makes no call.

Everything is SYNTHETIC: a made-up widget course, made-up resume lines and ids. The model is a scripted port: no
real model call, no network, no real home.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from gigai.scout import call_metrics, learning_markers as markers_module
from gigai.scout.learning_corpus import LearningCorpusError
from gigai.scout.learning_course import LONG_CALL_TIMEOUT_SECONDS, LearningCourseStopped
from gigai.scout.learning_markers import (
    CALLS_PER_PART_MAX, PROMPT_CHARS_MAX, LearningMarkersError, catalog_lessons, check_markers, estimate_markers, marker_keys,
    markers_prompt, plan_calls, run_markers, significant_words,
)
from gigai.scout.learning_pathway_local import _marker_entry
from gigai.scout.learning_store import LearningError
from gigai.scout.pipeline.store import StepMetrics

from tests.behaviors.scout_find_jobs import test_resume_privacy_static as privacy_pin

HOME = Path("/nonexistent/home")
TARGET = Path("/nonexistent/target")
ROLE = "Synthetic widget engineer"

#: A made-up master: three experience lines, a skills list, and a line with an email address in it.
MASTER = (
    ("e-widgetco-1", "Ran the Widgetflow scheduler for 40 widget pipelines in production at Widgetco"),
    ("e-widgetco-2", "Built a Gearbox exporter that ships gamma widgets nightly"),
    ("e-acme-1", "Led the on-call rotation for a payments backend"),
    ("s-tools", "Widgetflow, Gearbox, Sprocket"),
    ("e-acme-2", "Wrote to pat@example.com about queue tuning for the dispatcher"),
)
SENT = {
    "e-widgetco-1": MASTER[0][1], "e-widgetco-2": MASTER[1][1], "e-acme-1": MASTER[2][1], "s-tools": MASTER[3][1],
    "e-acme-2": "Wrote to about queue tuning for the dispatcher",
}

NEW = {"marker": "NEW", "evidence": None, "missing": None}
ALL_KEYS = [
    "lesson-alpha/scheduling", "lesson-alpha/retries", "lesson-alpha/queue-tuning",
    "lesson-beta/exporters", "lesson-beta/review-cadence",
    "lesson-gamma/sprocket-clusters", "lesson-gamma/capacity",
    "lesson-delta",
]


def make_catalog() -> dict:
    """A shipped catalog in S1's format (``scout-learning-shipped-catalog:1``); lesson-delta has no subtopic."""

    def lesson(lesson_id: str, title: str, module: str, technologies: list[str], *subtopics: tuple[str, str]) -> dict:
        return {
            "id": lesson_id, "title": title, "module": module, "concept_keys": ["zz-concept-key"], "technologies": technologies,
            "has_practice": False, "subtopics": [{"id": sub_id, "title": sub_title} for sub_id, sub_title in subtopics],
        }

    return {
        "schema_version": "scout-learning-shipped-catalog:1", "role_key": "synthetic-widget-engineer", "role": ROLE,
        "titles": {"phrases": ["zz title phrase"], "deny": ["zzdenyword"]},
        "vocabulary": [{"id": "zz-concept-key", "display": "Zz display", "category": "tool", "phrases": ["zz vocabulary phrase"], "technical": True}],
        "expectations": {"responsibilities": [], "scope_at_staff": []}, "technologies": {"Tools": ["zz-concept-key"]},
        "modules": [{"id": "m1", "title": "Widget foundations"}, {"id": "m2", "title": "Widget operations"}],
        "lessons": [
            lesson("lesson-alpha", "Alpha widgets with Widgetflow", "m1", ["Widgetflow"],
                   ("scheduling", "Scheduling widget pipelines"), ("retries", "Retries and backfills"), ("queue-tuning", "Queue tuning")),
            lesson("lesson-beta", "Beta widget reviews", "m1", ["Gearbox"],
                   ("exporters", "Gearbox exporters"), ("review-cadence", "Weekly review cadence")),
            lesson("lesson-gamma", "Gamma widgets on Sprocket", "m2", ["Sprocket"],
                   ("sprocket-clusters", "Sprocket clusters"), ("capacity", "Multi-tenant capacity planning")),
            lesson("lesson-delta", "Delta widget incident reviews", "m2", []),
        ],
    }


def entry(label: str, line_id: str, quote: str, missing: str | None = None) -> dict:
    return {"marker": label, "evidence": {"id": line_id, "line": quote}, "missing": missing}


class Scripted:
    """A scripted model port: each ``ask`` takes the next answer (a dict or text is returned, an exception raised, a callable asked)."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.items: list[int] = []
        self.timeouts: list[float | None] = []
        self.invalid = 0

    def ask(self, prompt: str, *, items: int = 1, timeout_seconds: float | None = None) -> str:
        self.prompts.append(prompt)
        self.items.append(items)
        self.timeouts.append(timeout_seconds)
        if not self.answers:
            raise AssertionError("the script has no answer left: one call too many")
        found = self.answers.pop(0)
        if callable(found):
            found = found(prompt)
        if isinstance(found, BaseException):
            raise found
        return found if isinstance(found, str) else json.dumps(found)

    def invalid_output(self) -> None:
        self.invalid += 1


def run(port: Scripted, catalog: dict | None = None, master: Any = MASTER, **options: Any):
    return run_markers(HOME, TARGET, catalog if catalog is not None else make_catalog(), master, port, **options)


# ---------------------------------------------------------------------------
# 1. The prompt
# ---------------------------------------------------------------------------


def test_the_prompt_holds_titles_technologies_keys_and_the_guarded_master_lines_and_nothing_else_of_the_catalog() -> None:
    catalog = make_catalog()
    prompt = markers_prompt(role=ROLE, lessons=catalog_lessons(catalog), master_lines=MASTER)

    assert "LESSON lesson-alpha | Alpha widgets with Widgetflow | technologies: Widgetflow" in prompt
    assert "  lesson-alpha/scheduling | Scheduling widget pipelines" in prompt
    assert "LESSON lesson-delta | Delta widget incident reviews | technologies: (none named)\n  lesson-delta | Delta widget incident reviews" in prompt
    for key in ALL_KEYS:
        assert f"  {key} | " in prompt
    for line_id, text in SENT.items():
        assert f"{line_id} | {text}" in prompt
    assert f"THE ROLE the course teaches: {ROLE}" in prompt
    # The contact shape never leaves; what the catalog holds for counting postings is not sent.
    assert "pat@example.com" not in prompt and "@" not in prompt
    for kept_out in ("zz vocabulary phrase", "zz-concept-key", "zz title phrase", "zzdenyword", "Zz display", "has_practice"):
        assert kept_out not in prompt
    # No stranger's text is in this prompt, so there is no untrusted fence; the retry paragraph is absent on a first call.
    assert "UNTRUSTED_POSTING_TEXT" not in prompt and "previous attempt" not in prompt
    assert "{{" not in prompt
    retry = markers_prompt(role=ROLE, lessons=catalog_lessons(catalog), master_lines=MASTER, validation_error="the answer holds no JSON object")
    assert "A previous attempt at this same prompt was rejected by the validator: the answer holds no JSON object." in retry


def test_the_prompt_states_the_zero_invented_rule_and_that_new_is_left_out() -> None:
    prompt = markers_prompt(role=ROLE, lessons=catalog_lessons(make_catalog()), master_lines=MASTER)
    assert "ZERO INVENTED KNOWLEDGE" in prompt and "VERBATIM" in prompt
    assert "turns the marker into NEW" in prompt
    assert "a subtopic you do not return is NEW" in prompt
    assert "skills list alone is SOME at most" in prompt


def test_the_privacy_pin_names_the_builder_and_the_module_calls_no_resume_reader() -> None:
    assert privacy_pin._BUILDERS[("gigai.scout.learning_markers", "markers_prompt")] == frozenset({"guard_private"})
    tree = ast.parse(Path(markers_module.__file__).read_text(encoding="utf-8"))
    called = {privacy_pin._called_name(call) for _scope, call in privacy_pin._calls(tree)}
    assert not called & (privacy_pin._READERS | {"read_record", "load_master", "read_master_lines"})
    assert not [key for key in privacy_pin._READER_ALLOWLIST if key[0] == "gigai.scout.learning_markers"]


def test_a_catalog_whose_ids_cannot_make_a_key_is_refused() -> None:
    for change in (
        lambda catalog: catalog["lessons"][0].update(id="lesson/alpha"),
        lambda catalog: catalog["lessons"][0]["subtopics"][0].update(id="has space"),
        lambda catalog: catalog["lessons"][1].update(id="lesson-alpha"),
        lambda catalog: catalog["lessons"][0]["subtopics"][1].update(id="scheduling"),
        lambda catalog: catalog.pop("lessons"),
    ):
        catalog = make_catalog()
        change(catalog)
        with pytest.raises(LearningMarkersError) as refused:
            catalog_lessons(catalog)
        assert refused.value.code == "invalid_value"
    assert marker_keys(make_catalog()) == ALL_KEYS


# ---------------------------------------------------------------------------
# 2. The checks
# ---------------------------------------------------------------------------


def test_a_valid_answer_comes_back_in_the_local_pages_format() -> None:
    port = Scripted({"markers": {
        "lesson-alpha/scheduling": entry("KNOWN", "e-widgetco-1", "Ran the Widgetflow scheduler for 40 widget pipelines"),
        "lesson-beta/exporters": entry("SOME", "e-widgetco-2", "built a   GEARBOX exporter", "The line shows one exporter, not a fleet of exporters."),
        "lesson-alpha/retries": {"marker": "NEW", "evidence": None, "missing": None},
    }})
    markers, usage, stats = run(port)

    assert list(markers) == ALL_KEYS
    assert markers["lesson-alpha/scheduling"] == {
        "marker": "KNOWN", "evidence": {"id": "e-widgetco-1", "line": "Ran the Widgetflow scheduler for 40 widget pipelines"}, "missing": None,
    }
    # Whitespace and case are folded for the check; the quote is kept as one line.
    assert markers["lesson-beta/exporters"] == {
        "marker": "SOME", "evidence": {"id": "e-widgetco-2", "line": "built a GEARBOX exporter"},
        "missing": "The line shows one exporter, not a fleet of exporters.",
    }
    for key in ALL_KEYS:
        if key not in ("lesson-alpha/scheduling", "lesson-beta/exporters"):
            assert markers[key] == NEW
    assert usage == {"calls": 1, "tokens_in": None, "tokens_out": None}
    assert stats == {"downgraded": 0, "downgraded_evidence": 0, "downgraded_unrelated": 0, "capped_skills": 0, "ignored": 0}
    # One call for the whole course, about every key of it, under the learning calls' long timeout.
    assert port.items == [len(ALL_KEYS)] and port.timeouts == [LONG_CALL_TIMEOUT_SECONDS] and port.invalid == 0
    # The local page accepts every entry as it is.
    for key in ALL_KEYS:
        assert _marker_entry(markers, key) == markers[key]


def test_a_quote_that_is_not_verbatim_or_an_id_that_does_not_exist_is_new() -> None:
    log: list[str] = []
    port = Scripted({"markers": {
        "lesson-alpha/scheduling": entry("KNOWN", "e-widgetco-1", "Operated the Widgetflow scheduler"),  # a paraphrase
        "lesson-alpha/retries": entry("SOME", "e-widgetco-9", "Ran the Widgetflow scheduler", "Retries are not shown."),  # no such line
        "lesson-beta/exporters": entry("KNOWN", "e-widgetco-1", "Built a Gearbox exporter"),  # a real quote of ANOTHER line
        "lesson-alpha/queue-tuning": entry("SOME", "e-acme-2", "pat@example.com about queue tuning", "Tuning under load is not shown."),  # not as sent
        "lesson-beta/review-cadence": {"marker": "KNOWN", "evidence": None, "missing": None},
        "lesson-gamma/capacity": {"marker": "SOME", "evidence": {"id": "e-acme-1", "line": "a"}, "missing": "Capacity planning is not shown."},
        "lesson-gamma/sprocket-clusters": "KNOWN",
    }})
    markers, _usage, stats = run(port, log=log)

    assert all(markers[key] == NEW for key in ALL_KEYS)
    assert stats["downgraded"] == stats["downgraded_evidence"] == 6 and stats["downgraded_unrelated"] == 0
    # The log names keys, never a resume line.
    assert len(log) == 6 and all(" evidence invalid -> NEW" in line for line in log)
    assert not [line for line in log if any(text in line for text in SENT.values())]


def test_a_key_the_catalog_does_not_hold_is_ignored_and_never_becomes_a_marker() -> None:
    good = entry("KNOWN", "e-widgetco-1", "Ran the Widgetflow scheduler")
    port = Scripted({"markers": {
        "lesson-alpha/scheduling": good,
        "lesson-alpha": good,  # a lesson that HAS subtopics is not a key
        "lesson-alpha/no-such-subtopic": good,
        "lesson-omega/scheduling": good,
        "scheduling": good,
        "lesson-delta/anything": good,  # a lesson without subtopics has only its own key
    }})
    markers, _usage, stats = run(port)

    assert list(markers) == ALL_KEYS and stats["ignored"] == 5 and stats["downgraded"] == 0
    assert markers["lesson-alpha/scheduling"]["marker"] == "KNOWN"
    assert [key for key in ALL_KEYS if markers[key]["marker"] != "NEW"] == ["lesson-alpha/scheduling"]


def test_a_known_from_a_line_that_has_nothing_to_do_with_the_subtopic_is_new() -> None:
    log: list[str] = []
    port = Scripted({"markers": {
        # A real line, a verbatim quote, and nothing in common with 'Multi-tenant capacity planning' or Sprocket.
        "lesson-gamma/capacity": entry("KNOWN", "e-acme-1", "Led the on-call rotation for a payments backend"),
        # 'production' is the only shared word of line and title here, and it is a generic word.
        "lesson-beta/review-cadence": entry("KNOWN", "e-widgetco-1", "in production at Widgetco"),
    }})
    catalog = make_catalog()
    catalog["lessons"][1]["subtopics"][1]["title"] = "Production review cadence"
    markers, _usage, stats = run(port, catalog, log=log)

    assert markers["lesson-gamma/capacity"] == NEW and markers["lesson-beta/review-cadence"] == NEW
    assert stats == {"downgraded": 2, "downgraded_evidence": 0, "downgraded_unrelated": 2, "capped_skills": 0, "ignored": 0}
    assert log == [
        "lesson-gamma/capacity: KNOWN from a line that shares no significant word with the subtopic -> NEW",
        "lesson-beta/review-cadence: KNOWN from a line that shares no significant word with the subtopic -> NEW",
    ]


def test_a_some_survives_on_a_missing_sentence_that_names_the_gap_and_not_on_a_vague_one() -> None:
    line = "Led the on-call rotation for a payments backend"
    port = Scripted({"markers": {
        "lesson-gamma/capacity": entry("SOME", "e-acme-1", line, "The line shows on-call work, not multi-tenant capacity planning."),
        "lesson-gamma/sprocket-clusters": entry("SOME", "e-acme-1", line, "Only adjacent work."),
        "lesson-alpha/retries": entry("SOME", "e-acme-1", line, None),
        # A lesson without subtopics is judged on its own title.
        "lesson-delta": entry("SOME", "e-acme-1", "on-call rotation", "The line shows the rotation, not incident reviews."),
        # KNOWN never passes on a sentence: a KNOWN line states the subtopic itself.
        "lesson-beta/exporters": entry("KNOWN", "e-acme-1", line, "Gearbox exporters are not shown."),
    }})
    markers, _usage, stats = run(port)

    assert markers["lesson-gamma/capacity"] == {
        "marker": "SOME", "evidence": {"id": "e-acme-1", "line": line}, "missing": "The line shows on-call work, not multi-tenant capacity planning.",
    }
    assert markers["lesson-delta"]["marker"] == "SOME" and markers["lesson-delta"]["evidence"] == {"id": "e-acme-1", "line": "on-call rotation"}
    assert markers["lesson-gamma/sprocket-clusters"] == NEW and markers["lesson-alpha/retries"] == NEW and markers["lesson-beta/exporters"] == NEW
    assert stats["downgraded"] == stats["downgraded_unrelated"] == 3


def test_a_skills_line_is_some_at_most() -> None:
    port = Scripted({"markers": {"lesson-gamma/sprocket-clusters": entry("KNOWN", "s-tools", "Sprocket")}})
    markers, _usage, stats = run(port)
    assert markers["lesson-gamma/sprocket-clusters"] == {
        "marker": "SOME", "evidence": {"id": "s-tools", "line": "Sprocket"},
        "missing": "Named in the skills list only; no line shows production work on this.",
    }
    assert stats["capped_skills"] == 1 and stats["downgraded"] == 0


def test_significant_words_leave_out_stopwords_generic_words_and_numbers() -> None:
    assert significant_words("Built the data platform for 40 teams in production") == frozenset()
    assert significant_words("CI/CD pipelines with C++ on k8s") == {"ci", "cd", "pipelin", "c++", "k8s"}
    assert significant_words("Deployed") == significant_words("deploying deployments")
    assert significant_words("pipelines") == significant_words("Pipeline")


def test_check_markers_refuses_an_answer_without_a_markers_object() -> None:
    lessons = catalog_lessons(make_catalog())
    for bad in ({}, {"markers": []}, {"markers": "none"}, {"lessons": {}}):
        with pytest.raises(LearningMarkersError) as refused:
            check_markers(bad, lessons, SENT)
        assert refused.value.code == "markers_invalid"
    assert check_markers({"markers": {}}, lessons, SENT) == {key: NEW for key in ALL_KEYS}


# ---------------------------------------------------------------------------
# 3. The call
# ---------------------------------------------------------------------------


def big_catalog(modules: int = 8, lessons: int = 5, subtopics: int = 6) -> dict:
    catalog = make_catalog()
    catalog["modules"] = [{"id": f"m{m}", "title": f"Widget module {m}"} for m in range(1, modules + 1)]
    catalog["lessons"] = [
        {
            "id": f"lesson-{m}-{n}", "title": f"Widget lesson {m}.{n} with Widgetflow and a long descriptive title", "module": f"m{m}",
            "concept_keys": [], "technologies": ["Widgetflow", "Gearbox", "Sprocket", "Cogwheel"], "has_practice": False,
            "subtopics": [
                {"id": f"part-{s}", "title": f"Widget subtopic {m}.{n}.{s}: scheduling, retries and capacity for a very long subtopic title " + "x" * 110}
                for s in range(1, subtopics + 1)
            ],
        }
        for m in range(1, modules + 1) for n in range(1, lessons + 1)
    ]
    return catalog


def test_a_big_course_is_split_by_module_and_every_part_carries_the_whole_master() -> None:
    catalog = big_catalog()
    whole = markers_prompt(role=ROLE, lessons=catalog_lessons(catalog), master_lines=MASTER)
    assert len(whole) > PROMPT_CHARS_MAX
    parts = plan_calls(catalog, MASTER)
    assert [sorted({lesson["module"] for lesson in part}) for part in parts] == [[f"m{m}"] for m in range(1, 9)]
    assert all(len(part) == 5 for part in parts)

    def by_module(prompt: str) -> dict:
        # Answer KNOWN for the first subtopic of the first lesson this prompt lists.
        first = prompt.split("\nLESSON ", 1)[1].split(" | ", 1)[0]
        return {"markers": {f"{first}/part-1": entry("KNOWN", "e-widgetco-1", "Ran the Widgetflow scheduler")}}

    port = Scripted(*[by_module] * 8)
    markers, usage, stats = run(port, catalog)

    assert usage["calls"] == 8 and len(port.prompts) == 8 and port.items == [30] * 8
    for number, prompt in enumerate(port.prompts, 1):
        assert f"LESSON lesson-{number}-1 |" in prompt and f"LESSON lesson-{number}-5 |" in prompt
        other = number % 8 + 1
        assert f"lesson-{other}-1" not in prompt
        for line_id, text in SENT.items():
            assert f"{line_id} | {text}" in prompt
    assert len(markers) == 8 * 5 * 6 and stats["ignored"] == 0
    assert [key for key, value in markers.items() if value["marker"] == "KNOWN"] == [f"lesson-{m}-1/part-1" for m in range(1, 9)]
    assert estimate_markers(catalog, MASTER)["calls"] == 8

    # The small course is one call, and the bound is a parameter of the plan, not a user's setting.
    assert len(plan_calls(make_catalog(), MASTER)) == 1
    assert len(plan_calls(make_catalog(), MASTER, prompt_chars_max=10)) == 2


def timed_out() -> LearningCorpusError:
    return LearningCorpusError("model_unavailable", "CLI model invocation timed out")


def test_a_timeout_is_retried_once_at_once() -> None:
    good = {"markers": {"lesson-alpha/scheduling": entry("KNOWN", "e-widgetco-1", "Ran the Widgetflow scheduler")}}
    port = Scripted(timed_out(), good)
    markers, usage, _stats = run(port)
    assert usage["calls"] == 2 and markers["lesson-alpha/scheduling"]["marker"] == "KNOWN"
    assert port.prompts[0] == port.prompts[1] and port.invalid == 0

    twice = Scripted(timed_out(), timed_out(), good)
    with pytest.raises(LearningError) as failed:
        run(twice)
    assert failed.value.code == "model_unavailable" and len(twice.prompts) == 2

    # Any other model failure is not retried here.
    other = Scripted(LearningCorpusError("model_unavailable", "CLI model process failed with exit code 1"), good)
    with pytest.raises(LearningError) as failed:
        run(other)
    assert failed.value.code == "model_unavailable" and len(other.prompts) == 1


def test_an_unusable_answer_gets_one_retry_that_names_the_problem() -> None:
    good = {"markers": {"lesson-alpha/scheduling": entry("KNOWN", "e-widgetco-1", "Ran the Widgetflow scheduler")}}
    log: list[str] = []
    port = Scripted("I will compare the lessons now.", good)
    markers, usage, stats = run(port, log=log)
    assert usage["calls"] == 2 and port.invalid == 1 and stats["downgraded"] == 0
    assert markers["lesson-alpha/scheduling"]["marker"] == "KNOWN"
    assert "previous attempt" not in port.prompts[0]
    assert "rejected by the validator: the answer holds no JSON object." in port.prompts[1]
    assert log == ["part 1: an answer was not usable (the answer holds no JSON object)"]

    # A JSON object without "markers" is as unusable; what the first answer held is never used.
    wrong = Scripted({"lessons": [{"id": "lesson-alpha"}]}, {"markers": ["lesson-alpha/scheduling"]})
    with pytest.raises(LearningMarkersError) as failed:
        run(wrong)
    assert failed.value.code == "markers_invalid" and len(wrong.prompts) == 2 and wrong.invalid == 2
    assert "rejected by the validator: markers must be an object from KEY to entry." in wrong.prompts[1]


def test_the_cap_and_the_cancel_hook_stop_before_a_call() -> None:
    capped = Scripted("not json", "not json")
    with pytest.raises(LearningMarkersError) as failed:
        run(capped, max_calls=1)
    assert failed.value.code == "cap_reached" and len(capped.prompts) == 1

    # The default cap is the worst case of one part: a timeout, its retry, and the retry of an unusable answer.
    worst = Scripted(timed_out(), "not json", {"markers": {}})
    _markers, usage, _stats = run(worst)
    assert usage["calls"] == CALLS_PER_PART_MAX == 3
    over = Scripted(timed_out(), "not json", timed_out(), {"markers": {}})
    with pytest.raises(LearningMarkersError) as failed:
        run(over)
    assert failed.value.code == "cap_reached" and len(over.prompts) == 3

    stopped = Scripted({"markers": {}})
    with pytest.raises(LearningCourseStopped):
        run(stopped, cancel=lambda: True)
    assert stopped.prompts == []


def test_a_master_with_no_lines_makes_no_call_and_every_subtopic_is_new() -> None:
    for master in ((), {}, None, (("e-empty-1", "   "),), (("e-link-1", "https://example.com/pat"),)):
        port = Scripted()
        markers, usage, stats = run(port, master=master)
        assert markers == {key: NEW for key in ALL_KEYS} and port.prompts == []
        assert usage == {"calls": 0, "tokens_in": None, "tokens_out": None} and stats["downgraded"] == 0
        assert estimate_markers(make_catalog(), master) == {"calls": 0, "tokens_in_estimate": 0, "tokens_out_estimate": 0, "seconds": 0}

    empty = make_catalog()
    empty["lessons"] = []
    port = Scripted()
    assert run(port, empty) == ({}, {"calls": 0, "tokens_in": None, "tokens_out": None}, {"downgraded": 0, "downgraded_evidence": 0, "downgraded_unrelated": 0, "capped_skills": 0, "ignored": 0})
    assert port.prompts == []


def test_the_usage_is_what_the_meter_reported_for_the_calls() -> None:
    class Metered(Scripted):
        def ask(self, prompt: str, *, items: int = 1, timeout_seconds: float | None = None) -> str:
            # What ``CallMeter.invoke`` does for a real call: hand the call's numbers to the open capture block.
            assert call_metrics._CAPTURE.calls is not None
            call_metrics._CAPTURE.calls.append(call_metrics.CapturedCall(StepMetrics(input_tokens=700, output_tokens=90), seconds=1.5, outcome="ok"))
            return super().ask(prompt, items=items, timeout_seconds=timeout_seconds)

    port = Metered("not json", {"markers": {}})
    _markers, usage, _stats = run(port)
    assert usage == {"calls": 2, "tokens_in": 1400, "tokens_out": 180}
    assert call_metrics._CAPTURE.calls is None


def test_without_a_port_the_users_metered_target_is_used_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[Any] = []

    class Fake(Scripted):
        def __init__(self, home_root: Path, target: Path) -> None:
            super().__init__({"markers": {}})
            self.where, self.closed = (home_root, target), False
            made.append(self)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(markers_module, "MeteredCourseModel", Fake)
    markers, usage, _stats = run_markers(HOME, TARGET, make_catalog(), MASTER)
    assert len(made) == 1 and made[0].where == (HOME, TARGET) and made[0].closed and usage["calls"] == 1
    assert markers == {key: NEW for key in ALL_KEYS}

    # A caller's own port is never closed here, and no metered model is built for an empty master.
    made.clear()
    run_markers(HOME, TARGET, make_catalog(), ())
    assert made == []


# ---------------------------------------------------------------------------
# 4. The estimate
# ---------------------------------------------------------------------------


def test_the_estimate_is_arithmetic_on_the_prompt_and_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_model(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the estimate built a model")

    monkeypatch.setattr(markers_module, "MeteredCourseModel", no_model)
    catalog = make_catalog()
    found = estimate_markers(catalog, MASTER)
    prompt = markers_prompt(role=ROLE, lessons=catalog_lessons(catalog), master_lines=MASTER)
    assert found["calls"] == 1
    assert found["tokens_in_estimate"] == -(-len(prompt) // 4) + markers_module.CALL_OVERHEAD_TOKENS_IN
    assert found["tokens_out_estimate"] == len(ALL_KEYS) * markers_module.OUT_TOKENS_PER_SUBTOPIC
    assert found["seconds"] > 0 and set(found) == {"calls", "tokens_in_estimate", "tokens_out_estimate", "seconds"}
