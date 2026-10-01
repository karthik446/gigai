"""0110-024 P4 / 0110-025 R4 / 0110-026 S5+F2: the UI models for the sources status lines, the background settings and the search keywords.

``ui/src/sourcesStatusModel.js``, ``sourcesStripModel.js``,
``backgroundSettingsModel.js`` and ``keywordsModel.js`` are pure JavaScript,
so they run under the system ``node`` (LOUD skip without it); what lives in
JSX is pinned by reading the source.

Pinned: the strip's line for a fresh install, a healthy store, a failing
model and automatic checks off; the tags / descriptions / starter-snapshot
lines with their quiet states; the settings PUT body (only what changed),
its address rule and the environment-override notes; the keyword chip limits
(the API's 20 x 100), the run body and the run summary's keywords line.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.background_settings import MAX_MANIFEST_URL_LENGTH
from gigai.scout.find_jobs.contracts import MAX_SEARCH_KEYWORD_LENGTH, MAX_SEARCH_KEYWORDS
from gigai.scout.find_jobs.model_tag import BACKFILL_MODELS

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
NOW = "2026-10-01T12:00:00Z"

SCRIPT = """
const input = JSON.parse(process.argv[1]);
const strip = await import(input.strip);
const lines = await import(input.status);
const settings = await import(input.settings);
const kw = await import(input.keywords);
const now = new Date(input.now).getTime();

const ready = { status: "ready", companies_indexed: 10360, last_checked_at: "2026-10-01T11:48:00Z" };
const refresh = (over) => ({ enabled: true, state: "waiting", in_progress: false, trigger: "auto", last_updated_at: "2026-10-01T11:48:00Z", last_updated_minutes_ago: 12, next_tick_at: "2026-10-01T12:48:00Z", next_tick_in_minutes: 48, ...over });
const lane = (over) => ({ model: null, calls: 0, failures: 0, consecutive_failures: 0, last_error: null, retry_after: null, ...over });
const tags = (over) => ({ available: true, titles: 5200, tagged_by_rules: 4400, tagged_by_model: 600, model_other: 50, awaiting_model: 150,
  setting: { model_enabled: true, backfill_enabled: false, tag_backfill_model: "configured", source: "default" },
  models: { demand: "ollama_local", backfill: "ollama_local" }, queue: { state: "idle", demand: lane(), backfill: lane() }, ...over });
const text = { available: true, postings_with_text: 6100, unchecked: 2900 };
const snap = (over) => ({ enabled: true, setting_source: "default", manifest_url: "https://example.invalid/manifest.json", as_of: "2026-09-30T06:00:00Z", source: "https://example.invalid/manifest.json", kind: "full", last_result: "up_to_date", last_reason: null, last_message: "The stored snapshot is the published one.", ...over });
const status = (index, blocks) => ({ running: false, update: null, index, ...blocks });

const scenarios = {
  freshInstall: status({ status: "empty", companies_indexed: 0, last_checked_at: null }, {
    refresh: refresh({ state: "needs_first_update", trigger: null, last_updated_at: null, last_updated_minutes_ago: null, next_tick_at: null, next_tick_in_minutes: null }),
    tags: { available: false, titles: 0, tagged_by_rules: 0, tagged_by_model: 0, model_other: 0, awaiting_model: 0, setting: tags().setting, models: {}, queue: null },
    text_index: { available: false, postings_with_text: 0, unchecked: 0 },
    snapshot: snap({ as_of: null, source: null, kind: null, last_result: "skipped", last_reason: "not_published", last_message: "No snapshot is published at the configured address." }),
  }),
  healthy: status(ready, { refresh: refresh(), tags: tags(), text_index: text, snapshot: snap() }),
  failingModel: status(ready, { refresh: refresh(), text_index: text, snapshot: snap(),
    tags: tags({ queue: { state: "backoff", demand: lane({ model: "ollama_local:llama3.1", failures: 3, consecutive_failures: 3, last_error: "ConnectError: connection refused\\n  at 127.0.0.1:11434", retry_after: "2026-10-01T12:10:00Z" }), backfill: lane() } }) }),
  autoRefreshOff: status(ready, { refresh: refresh({ enabled: false, state: "disabled", next_tick_at: null, next_tick_in_minutes: null }), tags: tags(), text_index: text, snapshot: snap() }),
  olderServer: status(ready, {}),
};

const body = (settingsOver, effectiveOver, readable = true) => ({
  schema_version: "scout-background-settings:1", readable,
  settings: { sources: { auto_refresh: true }, tagging: { model_enabled: true, backfill_enabled: false, tag_backfill_model: "configured" }, snapshot: { enabled: true, manifest_url: "https://example.invalid/default/manifest.json" }, ...settingsOver },
  effective: { sources: { auto_refresh: true, source: "default" }, tagging: { model_enabled: true, backfill_enabled: false, tag_backfill_model: "configured", source: "default" }, snapshot: { enabled: true, manifest_url: "https://example.invalid/default/manifest.json", source: "default" }, ...effectiveOver },
});
const loaded = body();
const draft = settings.draftFromResponse(loaded);
const forced = body({}, { sources: { auto_refresh: false, source: "environment" }, tagging: { model_enabled: false, backfill_enabled: false, tag_backfill_model: "configured", source: "environment" }, snapshot: { enabled: false, manifest_url: "https://example.invalid/other.json", source: "environment" } });
const openai = body({ tagging: { model_enabled: true, backfill_enabled: true, tag_backfill_model: "openai" } }, { tagging: { model_enabled: true, backfill_enabled: true, tag_backfill_model: "openai", source: "setting" } });

const many = Array.from({ length: 25 }, (_unused, index) => `kw${index}`);
const out = {
  strips: Object.fromEntries(Object.entries(scenarios).map(([name, value]) => [name, strip.sourcesStrip(value, { now })])),
  healthyNoRun: strip.sourcesStrip(scenarios.healthy, { now, hasRun: false }),
  settingsLines: lines.statusLines(scenarios.healthy, { withRefresh: true }),
  refresh: {
    updating: lines.refreshLine({ refresh: refresh({ in_progress: true, state: "running", last_updated_minutes_ago: null }) }),
    dueNow: lines.refreshLine({ refresh: refresh({ last_updated_minutes_ago: 0, next_tick_in_minutes: 0 }) }),
    hours: lines.refreshLine({ refresh: refresh({ last_updated_minutes_ago: 185, next_tick_in_minutes: 60 }) }),
    days: lines.refreshLine({ refresh: refresh({ last_updated_minutes_ago: 4320, next_tick_in_minutes: null, state: "inactive" }) }),
    none: lines.refreshLine({}),
  },
  tags: {
    recovered: lines.tagsLines({ tags: tags({ queue: { demand: lane({ last_error: "boom", consecutive_failures: 0 }), backfill: lane() } }) }),
    backfillFailing: lines.tagsLines({ tags: tags({ queue: { demand: lane(), backfill: lane({ model: "claude_cli:haiku", last_error: "x".repeat(400), consecutive_failures: 1 }) } }) }),
    modelOff: lines.tagsLines({ refresh: refresh(), tags: tags({ setting: { model_enabled: false } }) }),
    noThread: lines.tagsLines({ tags: tags({ queue: null, awaiting_model: 0 }) }),
    noTitles: lines.tagsLines({ tags: tags({ titles: 0 }) }),
  },
  text: { one: lines.textLine({ text_index: { available: true, postings_with_text: 1, unchecked: 0 } }), empty: lines.textLine({ text_index: { available: true, postings_with_text: 0, unchecked: 0 } }) },
  snapshot: {
    offlineFirst: lines.snapshotLine({ snapshot: snap({ as_of: null, last_result: "skipped", last_reason: "offline" }) }),
    offlineLater: lines.snapshotLine({ snapshot: snap({ last_result: "skipped", last_reason: "offline" }) }),
    notPublishedLater: lines.snapshotLine({ snapshot: snap({ last_result: "skipped", last_reason: "not_published" }) }),
    never: lines.snapshotLine({ snapshot: snap({ as_of: null, last_result: null, last_reason: null }) }),
    off: lines.snapshotLine({ snapshot: snap({ enabled: false, as_of: null, last_result: "skipped", last_reason: "disabled" }) }),
    offWithData: lines.snapshotLine({ snapshot: snap({ enabled: false }) }),
    refused: lines.snapshotLine({ snapshot: snap({ as_of: null, last_result: "refused", last_reason: "digest_mismatch" }) }),
    refusedLater: lines.snapshotLine({ snapshot: snap({ last_result: "failed", last_reason: "write_failed" }) }),
    imported: lines.snapshotLine({ snapshot: snap({ last_result: "imported" }) }),
    oddStamp: lines.asOfLabel("build 42"),
    none: lines.snapshotLine({}),
  },
  note: [lines.DATA_SOURCE_NOTE, lines.DATA_SOURCE_DOCS_URL],
  settings: {
    draft,
    unchanged: settings.buildPatch(draft, loaded),
    one: settings.buildPatch({ ...draft, autoRefresh: false }, loaded),
    all: settings.buildPatch({ autoRefresh: false, modelEnabled: false, backfillEnabled: true, backfillModel: "haiku", snapshotEnabled: false, manifestUrl: "  https://mirror.example/manifest.json " }, loaded),
    urlCleared: settings.buildPatch({ ...draft, manifestUrl: "  " }, loaded),
    urlSameAfterTrim: settings.buildPatch({ ...draft, manifestUrl: ` ${draft.manifestUrl} ` }, loaded),
    urlErrors: ["", "https://a.example/m.json", "http://localhost:8000/m.json", "ftp://a.example/m.json", "not a url", "https://", "https://a.example/" + "x".repeat(input.maxUrl)].map((value) => settings.manifestUrlError(value) === ""),
    formError: settings.formError({ ...draft, manifestUrl: "nope" }),
    options: settings.backfillModelOptions(loaded, draft).map((item) => item.value),
    optionsOpenai: settings.backfillModelOptions(openai, settings.draftFromResponse(openai)).map((item) => item.value),
    labels: Object.keys(settings.BACKFILL_MODEL_LABELS),
    notesNone: settings.overrideNotes(loaded),
    notesForced: settings.overrideNotes(forced),
    forcedDraft: settings.draftFromResponse(forced),
    unreadable: [settings.isUnreadable(body({}, {}, false)), settings.isUnreadable(loaded), settings.isUnreadable(null)],
    paused: [settings.taggingPausedNote(draft, loaded), settings.taggingPausedNote({ ...draft, autoRefresh: false }, loaded), settings.taggingPausedNote(draft, forced)],
    errors: [settings.saveErrorText({ code: "settings_unreadable", message: "generic" }), settings.saveErrorText({ code: "invalid_value", message: "generic", detail: "snapshot.manifest_url must be an http(s) URL" }), settings.saveErrorText({ code: "target_unavailable" }), settings.saveErrorText(null)],
    unreadableWarning: settings.UNREADABLE_WARNING,
    help: [settings.AUTO_REFRESH_HELP, settings.BACKFILL_HELP, settings.SNAPSHOT_HELP],
  },
  keywords: {
    limits: [kw.MAX_KEYWORDS, kw.MAX_KEYWORD_LENGTH],
    typed: kw.addKeywords([], "  rust ,  payments   platform\\nRUST, "),
    repeat: kw.addKeywords(["Rust"], "rust"),
    punctuation: kw.addKeywords(["a"], "+++"),
    tooLong: kw.addKeywords([], "x".repeat(input.maxLength + 1)),
    longest: kw.addKeywords([], "x".repeat(input.maxLength)).values.length,
    tooMany: kw.addKeywords([], many.join(",")),
    full: kw.addKeywords(many.slice(0, input.maxKeywords), "one more"),
    removed: kw.removeKeyword(["a", "b", "c"], 1),
    bodyNone: kw.runBodyWithKeywords({ config_digest: "d" }, []),
    bodyUndefined: kw.runBodyWithKeywords({ config_digest: "d" }, undefined),
    body: kw.runBodyWithKeywords({ config_digest: "d" }, ["rust", " Rust ", "payments platform"]),
    bodyCapped: kw.runBodyWithKeywords({}, many).keywords.length,
    lineNone: kw.keywordsLine({ source: "index" }),
    lineNull: kw.keywordsLine(null),
    lineApplied: kw.keywordsLine({ keywords: { terms: ["rust", "payments"], applied: true, reason: null, message: null, matched: 40, dropped: 310, text_not_checked: 1200 } }),
    lineAllChecked: kw.keywordsLine({ keywords: { terms: ["rust"], applied: true, matched: 4, dropped: 3, text_not_checked: 0 } }),
    lineOne: kw.keywordsLine({ keywords: { terms: ["rust"], applied: true, text_not_checked: 1 } }),
    lineIgnored: kw.keywordsLine({ keywords: { terms: ["rust"], applied: false, reason: "no_text_index", message: "Keywords were not applied: no posting text is indexed on this machine yet. Run Update sources, then search again." } }),
    lineIgnoredBare: kw.keywordsLine({ keywords: { terms: [], applied: false, reason: "bad_query", message: null } }),
  },
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the status, settings and keywords models were NOT run")
    payload = {
        "strip": (UI_SRC / "sourcesStripModel.js").as_uri(),
        "status": (UI_SRC / "sourcesStatusModel.js").as_uri(),
        "settings": (UI_SRC / "backgroundSettingsModel.js").as_uri(),
        "keywords": (UI_SRC / "keywordsModel.js").as_uri(),
        "now": NOW,
        "maxUrl": MAX_MANIFEST_URL_LENGTH,
        "maxLength": MAX_SEARCH_KEYWORD_LENGTH,
        "maxKeywords": MAX_SEARCH_KEYWORDS,
    }
    done = subprocess.run([node, "--input-type=module", "-e", SCRIPT, json.dumps(payload)], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _details(strip: dict) -> dict[str, str]:
    assert all(item["tone"] == "quiet" for item in strip["details"])
    return {item["role"]: item["text"] for item in strip["details"]}


# --- the sources strip -------------------------------------------------


def test_fresh_install_says_not_updated_yet_and_no_snapshot_published_quietly(out: dict) -> None:
    fresh = out["strips"]["freshInstall"]
    assert fresh["kind"] == "empty" and fresh["steps"] is not None and fresh["amber"] is False
    assert _details(fresh) == {
        "refresh": "Not updated yet · automatic checks start after the first update",
        "snapshot": "Starter data: none is published yet",
    }


def test_healthy_store_reads_updated_and_next_check_and_keeps_up_to_date(out: dict) -> None:
    healthy = out["strips"]["healthy"]
    assert healthy["line"] == "Company postings: 10,360 companies stored · updated 12 min ago · next check in 48 min · up to date"
    assert _details(healthy) == {
        "tags": "Titles tagged: 4,400 by rules, 600 by model, 150 waiting",
        "text": "Descriptions checked for 6,100 postings, 2,900 not yet",
        "snapshot": "Starter data: as of 2026-09-30",
    }
    assert healthy["amber"] is False and healthy["runBlocked"] == ""


def test_failing_model_adds_one_quiet_line_with_the_last_error(out: dict) -> None:
    failing = out["strips"]["failingModel"]
    details = _details(failing)
    assert details["tags-error"] == "The model (ollama_local:llama3.1) could not tag titles: ConnectError: connection refused at 127.0.0.1:11434. It is tried again by itself."
    assert details["tags"] == "Titles tagged: 4,400 by rules, 600 by model, 150 waiting"
    assert failing["amber"] is False and failing["kind"] == "fresh" and failing["runBlocked"] == "", "a failing model is never an error state of the strip"


def test_auto_refresh_off_says_so_and_that_tagging_is_paused(out: dict) -> None:
    off = out["strips"]["autoRefreshOff"]
    assert off["line"] == "Company postings: 10,360 companies stored · updated 12 min ago · automatic checks are off · up to date"
    assert _details(off)["tags"] == "Titles tagged: 4,400 by rules, 600 by model, 150 waiting (paused: automatic updates are off)"


def test_a_server_without_the_blocks_reads_as_before(out: dict) -> None:
    older = out["strips"]["olderServer"]
    assert older["line"] == "Company postings: 10,360 companies stored · updated 12 minutes ago · up to date"
    assert older["details"] == []
    assert out["strips"]["healthy"]["details"] != [] and out["refresh"]["none"] == ""


def test_the_stepper_and_settings_carry_the_refresh_sentence_as_a_detail(out: dict) -> None:
    assert _details(out["healthyNoRun"])["refresh"] == "Updated 12 min ago · next check in 48 min"
    assert [item["role"] for item in out["settingsLines"]] == ["refresh", "tags", "text", "snapshot"]
    assert "refresh" not in _details(out["strips"]["healthy"]), "the strip's own line already says it"


def test_refresh_sentence_states(out: dict) -> None:
    assert out["refresh"] == {
        "updating": "Not updated yet · updating now",
        "dueNow": "Updated just now · next check due now",
        "hours": "Updated 3 hr ago · next check in 1 hr",
        "days": "Updated 3 days ago",
        "none": "",
    }


def test_tags_line_quiet_states(out: dict) -> None:
    tags = out["tags"]
    assert tags["recovered"]["error"] == "", "a last_error left from before the lane recovered is not shown"
    error = tags["backfillFailing"]["error"]
    assert error.startswith("The model (claude_cli:haiku) could not tag titles: xxx") and error.endswith("… It is tried again by itself.") and len(error) < 260
    assert tags["modelOff"]["line"].endswith("150 waiting (tagging with the model is off)")
    # A server from before 0110-028 (no queued count): the line as it was, and no tagging line.
    assert tags["noThread"] == {"line": "Titles tagged: 4,400 by rules, 600 by model, 0 waiting", "error": "", "tagging": ""}
    assert tags["noTitles"] is None
    assert out["text"] == {"one": "Descriptions checked for 1 posting, 0 not yet", "empty": ""}


def test_snapshot_line_is_never_an_error(out: dict) -> None:
    assert out["snapshot"] == {
        "offlineFirst": "Starter data: it could not be reached, and is tried again at the next update",
        "offlineLater": "Starter data: as of 2026-09-30 · a newer one could not be reached",
        "notPublishedLater": "Starter data: as of 2026-09-30",
        "never": "Starter data: not downloaded yet",
        "off": "Starter data: downloads are off",
        "offWithData": "Starter data: as of 2026-09-30 · downloads are off",
        "refused": "Starter data: the last download was not used; your own updates fill the store",
        "refusedLater": "Starter data: as of 2026-09-30 · the last download was not used",
        "imported": "Starter data: as of 2026-09-30",
        "oddStamp": "build 42",
        "none": "",
    }
    for text in out["snapshot"].values():
        assert "error" not in text.lower() and "failed" not in text.lower()


def test_the_data_source_note_is_one_short_line_with_a_docs_link(out: dict) -> None:
    note, url = out["note"]
    assert note.startswith("Company and title data comes from public job boards;") and len(note) < 100
    assert url.startswith("https://karthik446.github.io/gigai/latest/scout/sources/")
    docs = Path(__file__).resolve().parents[2] / "gigai-docs" / "src" / "content" / "docs" / "scout" / "sources.md"
    assert "## Where the data comes from" in docs.read_text(encoding="utf-8"), "the link's anchor exists on the docs page"


def test_the_strip_starts_nothing_by_itself() -> None:
    model = (UI_SRC / "sourcesStatusModel.js").read_text(encoding="utf-8")
    lines = (UI_SRC / "components" / "SourcesStatusLines.jsx").read_text(encoding="utf-8")
    for source in (model, lines):
        assert "fetch(" not in source and "api.js" not in source and "useEffect" not in source
    strip = (UI_SRC / "components" / "SourcesStrip.jsx").read_text(encoding="utf-8")
    assert "<SourcesStatusLines lines={strip.details} />" in strip
    assert strip.count("startSourcesUpdate(") == 1 and "useEffect" not in strip, "an update starts from the two buttons only"
    panel = (UI_SRC / "components" / "SourcesUpdatePanel.jsx").read_text(encoding="utf-8")
    assert "statusLines(status, { withRefresh: true })" in panel


# --- Settings > Background updates ---------------------------------------


def test_the_draft_is_what_the_file_says_never_the_override(out: dict) -> None:
    settings = out["settings"]
    expected = {"autoRefresh": True, "modelEnabled": True, "backfillEnabled": False, "backfillModel": "configured", "snapshotEnabled": True, "manifestUrl": "https://example.invalid/default/manifest.json"}
    assert settings["draft"] == expected
    assert settings["forcedDraft"] == expected, "an environment override is shown as a note, not written into the form"


def test_the_put_body_names_only_what_changed(out: dict) -> None:
    settings = out["settings"]
    assert settings["unchanged"] is None and settings["urlSameAfterTrim"] is None
    assert settings["one"] == {"sources": {"auto_refresh": False}}
    assert settings["all"] == {
        "sources": {"auto_refresh": False},
        "tagging": {"model_enabled": False, "backfill_enabled": True, "tag_backfill_model": "haiku"},
        "snapshot": {"enabled": False, "manifest_url": "https://mirror.example/manifest.json"},
    }
    assert settings["urlCleared"] == {"snapshot": {"manifest_url": None}}, "an empty address puts the default back"


def test_the_put_body_is_one_the_server_accepts(out: dict) -> None:
    from gigai.scout.find_jobs.background_settings import validate_patch

    for name in ("one", "all", "urlCleared"):
        validate_patch(out["settings"][name])


def test_manifest_address_rule_matches_the_server(out: dict) -> None:
    assert out["settings"]["urlErrors"] == [True, True, True, False, False, False, False]
    assert "http(s) URL of at most 2,048 characters" in out["settings"]["formError"]


def test_backfill_model_choices(out: dict) -> None:
    settings = out["settings"]
    assert settings["options"] == ["configured", "haiku"]
    assert settings["optionsOpenai"] == ["configured", "haiku", "openai"], "openai only when it is already the saved or running choice"
    assert sorted(settings["labels"]) == sorted(BACKFILL_MODELS)


def test_override_notes_and_the_unreadable_warning(out: dict) -> None:
    settings = out["settings"]
    assert settings["notesNone"] == {"sources": "", "tagging": "", "snapshot": ""}
    forced = settings["notesForced"]
    assert forced["sources"] == "Set by the environment (GIGAI_SCOUT_AUTO_REFRESH): automatic checks are off now, whatever is saved here."
    assert forced["tagging"] == "Set by the environment (GIGAI_SCOUT_MODEL_TAGS): tagging with the model is off now, whatever is saved here."
    assert "the starter file is off now and its address is https://example.invalid/other.json" in forced["snapshot"]
    assert settings["unreadable"] == [True, False, False]
    assert "cannot be read" in settings["unreadableWarning"] and "nothing can be saved" in settings["unreadableWarning"]
    assert settings["errors"] == [
        settings["unreadableWarning"],
        "snapshot.manifest_url must be an http(s) URL",
        "No Scout project is set up on this machine yet, so there are no background settings to save.",
        "The settings could not be saved.",
    ]


def test_turning_auto_refresh_off_says_it_pauses_model_tagging(out: dict) -> None:
    settings = out["settings"]
    assert "also pauses tagging titles with the model" in settings["help"][0]
    assert settings["paused"] == ["", "Paused while automatic checks are off.", "Paused while automatic checks are off."]
    assert "Off unless you turn it on" in settings["help"][1] and "no descriptions" in settings["help"][2]


def test_the_environment_names_are_the_servers() -> None:
    from gigai.scout.find_jobs import model_tag, refresh_tick, snapshot

    model = (UI_SRC / "backgroundSettingsModel.js").read_text(encoding="utf-8")
    for name in (refresh_tick.AUTO_REFRESH_ENV, model_tag.MODEL_TAGS_ENV, snapshot.SNAPSHOT_ENV, snapshot.MANIFEST_URL_ENV):
        assert name in model


def test_settings_panel_wiring_saves_only_on_the_button() -> None:
    panel = (UI_SRC / "components" / "BackgroundUpdatesPanel.jsx").read_text(encoding="utf-8")
    assert panel.count("putBackgroundSettings(") == 1 and "async function save()" in panel
    assert "onClick={save}" in panel and "disabled={off || !patch || Boolean(invalid)}" in panel
    assert panel.count("useEffect(") == 1, "the one effect is the first read; no autosave"
    assert '<details className="background-advanced">' in panel, "the manifest address is collapsed"
    assert "<h2>Background updates</h2>" in panel
    for role in ("override-sources", "override-tagging", "override-snapshot", "background-unreadable"):
        assert role in panel
    view = (UI_SRC / "views" / "SettingsView.jsx").read_text(encoding="utf-8")
    assert "<BackgroundUpdatesPanel />" in view
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    assert 'request("GET", "/api/settings/background")' in api and 'request("PUT", "/api/settings/background", patch)' in api


# --- search keywords -------------------------------------------------------


def test_keyword_limits_are_the_api_limits(out: dict) -> None:
    assert out["keywords"]["limits"] == [MAX_SEARCH_KEYWORDS, MAX_SEARCH_KEYWORD_LENGTH] == [20, 100]


def test_chips_are_trimmed_split_and_never_repeated(out: dict) -> None:
    keywords = out["keywords"]
    assert keywords["typed"] == {"values": ["rust", "payments platform"], "error": ""}
    assert keywords["repeat"] == {"values": ["Rust"], "error": ""}
    assert keywords["removed"] == ["a", "c"]


def test_a_chip_the_server_would_refuse_is_not_added(out: dict) -> None:
    keywords = out["keywords"]
    assert keywords["punctuation"] == {"values": ["a"], "error": "A keyword needs a letter or a digit."}
    assert keywords["tooLong"] == {"values": [], "error": "A keyword can be at most 100 characters."}
    assert keywords["longest"] == 1
    assert len(keywords["tooMany"]["values"]) == 20 and keywords["tooMany"]["error"] == "At most 20 keywords."
    assert len(keywords["full"]["values"]) == 20 and "one more" not in keywords["full"]["values"]


def test_the_run_body_carries_keywords_only_when_there_are_some(out: dict) -> None:
    from gigai.scout.find_jobs.contracts import search_keywords

    keywords = out["keywords"]
    assert keywords["bodyNone"] == {"config_digest": "d"} and keywords["bodyUndefined"] == {"config_digest": "d"}
    assert keywords["body"] == {"config_digest": "d", "keywords": ["rust", "payments platform"]}
    assert search_keywords(keywords["body"]["keywords"]) == ("rust", "payments platform")
    assert keywords["bodyCapped"] == 20


def test_the_run_summary_keywords_line(out: dict) -> None:
    keywords = out["keywords"]
    assert keywords["lineNone"] is None and keywords["lineNull"] is None
    assert keywords["lineApplied"] == {"text": "Keywords: rust, payments · text not checked for 1,200 postings", "ignored": False}
    assert keywords["lineAllChecked"] == {"text": "Keywords: rust", "ignored": False}
    assert keywords["lineOne"]["text"] == "Keywords: rust · text not checked for 1 posting"
    assert keywords["lineIgnored"] == {
        "text": "Keywords: rust · Keywords were not applied: no posting text is indexed on this machine yet. Run Update sources, then search again.",
        "ignored": True,
    }
    assert keywords["lineIgnoredBare"] == {"text": "Keywords were not applied to this search.", "ignored": True}


def test_keywords_wiring() -> None:
    dialog = (UI_SRC / "components" / "RunConfirmDialog.jsx").read_text(encoding="utf-8")
    assert '<KeywordsInput id="run-keywords"' in dialog and "onConfirm({ selectionCap, modelTarget, keywords })" in dialog
    jobs = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    assert "runBodyWithKeywords(buildRunRequest({ configDigest: config.config_digest, selectionCap, modelTarget }), keywords)" in jobs
    assert "keywordsLine(progress?.boards)" in jobs and 'data-role="run-keywords"' in jobs
    status = (UI_SRC / "components" / "NodeStatusList.jsx").read_text(encoding="utf-8")
    assert "keywordsLine(boards)" in status and 'className="muted keywords-line"' in status, "the line is quiet, never a callout"
    field = (UI_SRC / "components" / "KeywordsInput.jsx").read_text(encoding="utf-8")
    assert "addKeywords(values, text)" in field and "tag-input" in field
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    assert "keywords" not in api.split("export function buildRunRequest")[1].split("}\n")[0], "the frozen run request is unchanged"


def test_new_styles_use_the_design_tokens_only() -> None:
    import re

    css = (UI_SRC / "styles.css").read_text(encoding="utf-8")
    new = css[css.index("/* 0110-024/025/026: the quiet status lines") : css.index("/* 0110-017: Resume display spacing control")]
    assert ".sources-status-lines" in new and ".background-setting" in new and ".keywords-line" in new
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", new), "no raw colours: tokens only"
