"""0110-8-05: a KNOWN function tag vetoes a generic title's whole-word rule. Synthetic only.

The case (ANALYSIS-1 Q3, reproduced on synthetic data): a profile lists the
generic "Staff Engineer" next to "Staff Software Engineer" and "Staff AI
Engineer". The generic title's rule needs only the words ``staff`` and
``engineer``, so it passed "Staff Security Engineer" and "Staff Technical
Program Manager, Engineering Onboarding", though the tag store knew their
functions (security, operations).

0.1.11.3 (packet 14): the rule now reads the role as a whole and rejects that
second title by itself (other words sit between "staff" and "engineering"), so
the operations example here is "Staff Engineer, Technical Program Management":
the rule passes it (the role, then its area) and the tag still says operations.

(a) Those two do NOT match that profile: in the posting read model, in
    ``scout new``, in the rank lane's demand set and in the tag queue's. A
    posting the store has no tag for still matches by the generic rule and is
    flagged ``tag_pending``. A profile with ONLY the generic title names no
    function, so it matches as it always did.
(b) ``gigai scout profile list`` warns with the live count of what the generic
    title alone matches.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import postings, profile_records
from gigai.scout.find_jobs import model_tag
from gigai.scout.find_jobs.ats_board_clients import matches_roles
from gigai.scout.find_jobs.posting_tags import default_store, normalize_title, tag_new_titles
from gigai.scout.find_jobs.title_query import TitleMatcher
from gigai.scout.pipeline.store import PipelineStore, pipeline_path

from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job
from tests.support.scout_profile_fixtures import uuids

MIXED_TITLES = ("Staff Engineer", "Staff Software Engineer", "Staff AI Engineer")
SECURITY = "Staff Security Engineer"
TPM = "Staff Engineer, Technical Program Management"
SOFTWARE = "Staff Software Engineer"
GENERIC_SOFTWARE = "Staff Engineer, World Model Development"
#: Never put in the tag store: "not tagged yet".
UNTAGGED = "Staff Reliability Engineer"
TITLES = {1: SECURITY, 2: TPM, 3: SOFTWARE, 4: GENERIC_SOFTWARE, 5: UNTAGGED}
TAGGED = (SECURITY, TPM, SOFTWARE, GENERIC_SOFTWARE)


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[PostingsFixture, str]:
    """The postings fixture (its second profile is ``staff engineer`` ALONE) plus a "mixed" profile; the tag store knows four titles."""

    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    resolved = fx.base.gig.resolved
    default = next(item for item in profile_records.list_profiles(resolved) if item.profile_id == fx.default_profile_id)
    mixed = profile_records.create_profile(
        resolved, label="Mixed", titles=MIXED_TITLES, titles_to_avoid=(), queries=MIXED_TITLES, resume_ref=default.resume_ref,
        uuid_factory=uuids(60),
    )
    fx.seed("acme", [lever_job("acme", n, title=title) for n, title in TITLES.items()], seen_at=days_ago(1))
    tag_new_titles(default_store(fx.home_root), TAGGED)
    return fx, mixed.profile_id


def _jobs(*numbers: int) -> set[str]:
    return {job_url("acme", n) for n in numbers}


def _model_rows(fx: PostingsFixture, profile_id: str) -> set[str]:
    postings.refresh(fx.home_root, fx.target, now=NOW)
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        return {row.job for row in store.postings(profile_id=profile_id)}
    finally:
        store.close()


def test_which_titles_are_generic() -> None:
    from gigai.scout.find_jobs.title_query import is_generic_role

    assert [is_generic_role(role) for role in ("Staff Engineer", "staff engineer", "Senior Staff Engineer", "Engineering Manager", "Director of Engineering")] == [True] * 5
    assert not any(is_generic_role(role) for role in ("Staff Software Engineer", "Staff AI Engineer", "Staff Security Engineer", "Product Manager", "Staff", ""))


def test_the_store_knows_the_functions_the_rule_ignores(tmp_path: Path) -> None:
    """The synthetic repro of Q3: the rule passes both titles on the generic title, and the store knows better."""

    store = default_store(tmp_path)
    tag_new_titles(store, TAGGED)
    assert matches_roles(SECURITY, MIXED_TITLES) and matches_roles(TPM, MIXED_TITLES)
    assert not matches_roles(SECURITY, MIXED_TITLES[1:]) and not matches_roles(TPM, MIXED_TITLES[1:])  # the generic title alone
    assert store.get(normalize_title(SECURITY)).function == "security_it"  # type: ignore[union-attr]
    assert store.get(normalize_title(TPM)).function == "operations"  # type: ignore[union-attr]


def test_a_known_function_tag_vetoes_the_generic_title(tmp_path: Path) -> None:
    store = default_store(tmp_path)
    tag_new_titles(store, TAGGED)
    matcher = TitleMatcher(MIXED_TITLES, store)

    assert matcher.matches(SECURITY) is False and matcher.matches(TPM) is False
    assert matcher.matches(SOFTWARE) is True and matcher.matches(GENERIC_SOFTWARE) is True
    pending = matcher.decide(UNTAGGED)
    assert pending.matched is True and pending.tag_pending is True
    assert matcher.decide(SECURITY).by == "vetoed" and matcher.decide(GENERIC_SOFTWARE).tag_pending is False
    assert matcher.counts.to_json() == {
        "matched_by_rule": 4, "matched_by_tag": 0, "untagged_fallback": 0, "vetoed_by_tag": 3, "tag_pending": 1,
    }
    # A function-specific title's own match is never vetoed, whatever the rules tagged it.
    tag_new_titles(store, ["Staff Software Engineer, Product Experiences"])
    assert store.get(normalize_title("Staff Software Engineer, Product Experiences")).function == "product"  # type: ignore[union-attr]
    assert matcher.matches("Staff Software Engineer, Product Experiences") is True

    # The tag arrives: a function of the profile matches and is no longer pending; any other is vetoed.
    key = normalize_title(UNTAGGED)
    tag_new_titles(store, [UNTAGGED])
    assert TitleMatcher(MIXED_TITLES, store).decide(UNTAGGED) == TitleMatcher(MIXED_TITLES, store).decide(GENERIC_SOFTWARE)
    store.set_model_function(key, "security_it", model="m", prompt_version="tag-v1")
    assert TitleMatcher(MIXED_TITLES, store).matches(UNTAGGED) is False


def test_only_generic_titles_and_no_store_match_as_before(tmp_path: Path) -> None:
    store = default_store(tmp_path)
    tag_new_titles(store, TAGGED)
    alone = TitleMatcher(("Staff Engineer",), store)
    assert all(alone.matches(title) for title in TITLES.values())  # no function named: nothing to veto by
    assert alone.generic_roles == () and alone.decide(UNTAGGED).tag_pending is False
    plain = TitleMatcher(MIXED_TITLES, None)
    assert all(plain.matches(title) is matches_roles(title, MIXED_TITLES) for title in TITLES.values())


def test_a_the_read_model_and_the_rank_lane_demand_set_leave_the_vetoed_postings_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, mixed = _fixture(tmp_path, monkeypatch)

    assert _model_rows(fx, mixed) == _jobs(3, 4, 5)
    # The second profile has the generic title ALONE: it keeps every posting with its words.
    assert _model_rows(fx, fx.second_profile_id) == _jobs(1, 2, 3, 4, 5)

    # The rank lane ranks ``profile_posting_rows``: the same matcher, the same set.
    _resolved, views = postings.active_profiles(fx.home_root, fx.target)
    view = next(item for item in views if item.profile_id == mixed)
    assert {row.normalized_url for row in postings.profile_posting_rows(view, fx.home_root, fx.target, NOW)} == _jobs(3, 4, 5)  # type: ignore[attr-defined]


def test_a_scout_new_leaves_them_out_and_flags_the_untagged_posting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, mixed = _fixture(tmp_path, monkeypatch)

    result = CliRunner().invoke(cli, [*fx.cli("--peek", "--profile", mixed), "--json"])
    assert result.exit_code == 0, result.output
    rows = {row["job_identity"]: row for row in json.loads(result.output)["postings"]["rows"]}
    assert set(rows) == _jobs(3, 4, 5)
    assert {job: row["tag_pending"] for job, row in rows.items()} == {job_url("acme", 3): False, job_url("acme", 4): False, job_url("acme", 5): True}

    everything = CliRunner().invoke(cli, [*fx.cli("--peek"), "--json"])
    assert everything.exit_code == 0, everything.output
    by_job = {row["job_identity"]: row for row in json.loads(everything.output)["postings"]["rows"]}
    for vetoed in _jobs(1, 2):
        assert mixed not in [item["profile_id"] for item in by_job[vetoed]["profiles"]]

    table = CliRunner().invoke(cli, fx.cli("--peek", "--profile", mixed))
    assert table.exit_code == 0 and table.output.count("tag pending") == 1, table.output


def test_a_the_tag_queue_demand_set_is_the_same_roles_and_never_a_vetoed_title(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, _mixed = _fixture(tmp_path, monkeypatch)
    store = default_store(fx.home_root)
    tag_new_titles(store, ["Staff Wizard of Things"])  # staff level, no function found: this is what waits for a model

    demand = model_tag.load_demand(fx.home_root, fx.target)
    assert set(MIXED_TITLES) <= set(demand.roles) and demand.levels == ("principal", "staff")
    waiting = set(store.titles_awaiting_model(100, levels=demand.levels))
    assert waiting == {normalize_title("Staff Wizard of Things")}
    assert normalize_title(SECURITY) not in waiting and normalize_title(TPM) not in waiting  # known functions: nothing to ask
    # One matcher: what the queue's answer changes is exactly what the search matches by.
    matcher = TitleMatcher(MIXED_TITLES, store)
    assert matcher.matches("Staff Wizard of Things") is False  # no generic rule hit, no tag: not a match
    # 0.1.11.5 (TITLE-01): only the software family adds a match by its tag; ai_ml is a wide family, rule only.
    store.set_model_function(normalize_title("Staff Wizard of Things"), "ai_ml", model="m", prompt_version="tag-v1")
    assert TitleMatcher(MIXED_TITLES, store).matches("Staff Wizard of Things") is False
    store.set_model_function(normalize_title("Staff Wizard of Things"), "software", model="m", prompt_version="tag-v1")
    assert TitleMatcher(MIXED_TITLES, store).matches("Staff Wizard of Things") is True


def test_b_profile_list_warns_with_the_live_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, mixed = _fixture(tmp_path, monkeypatch)
    args = ["scout", "profile", "list", "--home", str(fx.home_root), "--target", str(fx.target)]

    text = CliRunner().invoke(cli, args)
    assert text.exit_code == 0, text.output
    # Five postings carry the words "staff" and "engineer": what the generic title ALONE matches, for both profiles that have it.
    assert text.output.count("warning: title 'Staff Engineer' alone matches 5 postings") == 1, text.output
    assert text.output.count("warning: title 'staff engineer' alone matches 5 postings") == 1, text.output

    listed = CliRunner().invoke(cli, [*args, "--json"])
    assert listed.exit_code == 0, listed.output
    by_id = {item["profile_id"]: item for item in json.loads(listed.output)["profiles"]}
    (warning,) = by_id[mixed]["title_warnings"]
    assert (warning["code"], warning["title"], warning["matches"]) == ("generic_title", "Staff Engineer", 5)
    assert by_id[fx.default_profile_id]["title_warnings"] == []  # "staff ai engineer", ...: no generic title, no warning
    assert [item["matches"] for item in by_id[fx.second_profile_id]["title_warnings"]] == [5]

    # The count is live: one more posting with the words, one more match.
    fx.seed("acme", [lever_job("acme", n, title=title) for n, title in {**TITLES, 6: "Staff Data Engineer"}.items()], seen_at=days_ago(0.5))
    again = json.loads(CliRunner().invoke(cli, [*args, "--json"]).output)
    assert next(item for item in again["profiles"] if item["profile_id"] == mixed)["title_warnings"][0]["matches"] == 6
