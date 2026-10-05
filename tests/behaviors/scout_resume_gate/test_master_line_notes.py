"""0.1.11 N1b: line notes in the master resume's format (``master_resume``; SPEC 5.4).

A line or an entry heading may carry one note, in a second trailing comment::

    - Built the durable runtime ... <!-- id:b-4d2a91 --> <!-- note: agentic roles: lead with this -->

The END outcomes, on the pure format (no file, no journal, no model):

* a note is read whole, BEFORE any word of a comment is read, and the canonical markdown writes it back;
* a note that a 0.1.10 reader would misread is refused with the line number, and never quoted: a word that starts
  with ``id:``, ``tags:``, ``backed:`` or ``gigai-master:``, a comment mark, more than 300 characters;
* a 0.1.10 reader (its ``_split``, reproduced below word for word) reads a 0.1.11 master with notes exactly as it
  reads the same master without them: the notes are dropped and nothing else moves. ``MASTER_FORMAT`` stays 1;
* a note is never printed (``markdown(ids=False)``, the PDF's markdown parser, the assessment's evidence view and
  the lines an assessment's evidence is traced to) and never part of a line's ``mark``; it IS part of the stored
  markdown, so a note edit is a change of that line (a new revision).

Everything is synthetic: an invented master written here. Nothing reads a home.
"""

from __future__ import annotations

from datetime import date

import pytest

from gigai.scout import assess_master, master_migration, master_resume, resume_pdf
from gigai.scout.master_resume import MASTER_FORMAT, MASTER_MARKER, NOTE_MAX_CHARS, MasterResumeError, compare, note_of, note_rule, parse_master

LEAD = "agentic roles: lead with this"
SHORTER = "shorter version: keep the first two bullets"
#: Awkward on purpose: colons inside words, a reserved name in the middle of a word and in brackets, an arrow.
AWKWARD = "valid: yes; paid:3 hashtags:x (id:b-9) Android: ok, mid:way -> then"

PLAIN = """<!-- gigai-master:1 -->

## Summary

- Platform engineer with 9 years of experience in payment systems. <!-- id:sum-platform tags:backend -->

## Experience

### Edge Lab <!-- id:r-edge -->
Founder | Jan 2024 - Present
- Built the durable runtime that resumes an agent run after a crash. <!-- id:b-4d2a91 tags:ai,agents backed:story:runtime -->
- Ran the on-call rotation for 4 teams. <!-- id:b-oncall -->

### Northwind Labs <!-- id:r-north -->
Senior Engineer | Mar 2014 - May 2019
- Built the billing export used by 30 finance analysts. <!-- id:b-billing -->

## Skills

- Cloud: Kubernetes, Docker <!-- id:s-cloud -->
"""
NOTED = (
    PLAIN.replace("<!-- id:r-edge -->", f"<!-- id:r-edge --> <!-- note: {SHORTER} -->")
    .replace("backed:story:runtime -->", f"backed:story:runtime --> <!-- note: {LEAD} -->")
    .replace("<!-- id:s-cloud -->", f"<!-- id:s-cloud --> <!-- note: {AWKWARD} -->")
)


def _facts(master: master_resume.Master) -> tuple[object, ...]:
    """Everything a 0.1.10 master holds: the sections, every entry and every line (no notes)."""

    return (
        master.sections,
        [(entry.id, entry.section, entry.heading, entry.sublines, entry.bullets, entry.order) for entry in master.entries.values()],
        [(item.id, item.section, item.kind, item.text, item.tags, item.backed, item.entry_id, item.order, item.mark) for item in master.items.values()],
    )


def _refused(markdown: str) -> MasterResumeError:
    with pytest.raises(MasterResumeError) as raised:
        parse_master(markdown)
    return raised.value


# --- reading and writing ------------------------------------------------------------------------------


def test_a_note_is_a_second_trailing_comment_on_a_line_or_an_entry_heading_and_is_written_back() -> None:
    master = parse_master(NOTED)
    assert {key: item.note for key, item in master.items.items()} == {
        "sum-platform": None, "b-4d2a91": LEAD, "b-oncall": None, "b-billing": None, "s-cloud": AWKWARD,
    }
    assert {key: entry.note for key, entry in master.entries.items()} == {"r-edge": SHORTER, "r-north": None}
    # The note is taken whole: the line's own id, tags and evidence are what its first comment says, whatever the note holds.
    runtime = master.items["b-4d2a91"]
    assert (runtime.text, runtime.tags, runtime.backed) == ("Built the durable runtime that resumes an agent run after a crash.", ("ai", "agents"), ("story:runtime",))
    assert (master.items["s-cloud"].text, master.items["s-cloud"].tags, master.items["s-cloud"].backed) == ("Cloud: Kubernetes, Docker", (), ())
    # The canonical markdown is the file itself, and reads back as the same master.
    assert master.markdown() == NOTED
    assert parse_master(master.markdown()).markdown() == NOTED and compare(master, parse_master(master.markdown())) == master_resume.MasterChange()
    # What a prompt or a brief reads (N3, N5), and what the JSON of a line and an entry says.
    assert (note_of(runtime), note_of(master.entries["r-edge"]), note_of(master.items["b-oncall"]), note_of(master.entries["r-north"])) == (LEAD, SHORTER, None, None)
    assert (runtime.to_json()["note"], master.entries["r-edge"].to_json()["note"], master.items["b-oncall"].to_json()["note"]) == (LEAD, SHORTER, None)


def test_the_format_version_stays_1() -> None:
    assert (MASTER_FORMAT, MASTER_MARKER) == (1, "<!-- gigai-master:1 -->")
    assert parse_master(NOTED).markdown().splitlines()[0] == "<!-- gigai-master:1 -->"


@pytest.mark.parametrize(
    "line",
    [
        f"- Ran the on-call rotation for 4 teams. <!-- note: {LEAD} --> <!-- id:b-oncall -->",  # typed before the id
        f"- Ran the on-call rotation for 4 teams. <!-- id:b-oncall --><!--note:{LEAD}-->",  # typed tight
        f"- Ran the on-call rotation for 4 teams. <!-- id:b-oncall -->   <!--   note:   agentic roles:   lead with this   -->  ",  # spacing
        f"- Ran the on-call rotation\n  for 4 teams. <!-- id:b-oncall --> <!-- note: {LEAD} -->",  # on the wrapped line's second half
    ],
)
def test_a_typed_note_is_read_wherever_it_stands_among_the_trailing_comments_and_stored_one_way(line: str) -> None:
    typed = PLAIN.replace("- Ran the on-call rotation for 4 teams. <!-- id:b-oncall -->", line)
    assert typed != PLAIN
    master = parse_master(typed)
    assert (master.items["b-oncall"].note, master.items["b-oncall"].text) == (LEAD, "Ran the on-call rotation for 4 teams.")
    assert f"- Ran the on-call rotation for 4 teams. <!-- id:b-oncall --> <!-- note: {LEAD} -->" in master.markdown().splitlines()


def test_an_empty_note_comment_is_no_note() -> None:
    typed = PLAIN.replace("<!-- id:b-oncall -->", "<!-- id:b-oncall --> <!-- note:  -->")
    assert parse_master(typed).items["b-oncall"].note is None and parse_master(typed).markdown() == PLAIN


# --- refused, with the line number ------------------------------------------------------------------------

ONCALL_LINE = 12  # the line of PLAIN that holds b-oncall
_ONCALL = "- Ran the on-call rotation for 4 teams. <!-- id:b-oncall -->"
_SECRET = "zebra-quartz"  # a word of every refused note: no refusal may quote the note


@pytest.mark.parametrize(
    ("typed", "rule"),
    [
        # What the 0.1.10 reader would take as the line's own id, tags, evidence or format.
        (f"{_ONCALL} <!-- note: {_SECRET} see id:b-billing -->", "id:, tags:, backed: or gigai-master:"),
        (f"{_ONCALL} <!-- note: {_SECRET} tags:ai -->", "id:, tags:, backed: or gigai-master:"),
        (f"{_ONCALL} <!-- note: backed:story:runtime {_SECRET} -->", "id:, tags:, backed: or gigai-master:"),
        (f"{_ONCALL} <!-- note: {_SECRET} gigai-master:2 -->", "id:, tags:, backed: or gigai-master:"),
        (f"{_ONCALL} <!-- note: id: {_SECRET} -->", "id:, tags:, backed: or gigai-master:"),
        # A comment mark: the note ends at its own "-->".
        (f"{_ONCALL} <!-- note: {_SECRET} --> and more -->", "ends at its '-->'"),
        (f"{_ONCALL} <!-- note: {_SECRET} --> and more", "a comment belongs at the end of the line"),
        (f"{_ONCALL} <!-- note: {_SECRET} <!-- inner -->", "comment mark"),
        # One note, at most 300 characters, in a comment of its own.
        (f"{_ONCALL} <!-- note: {_SECRET} {'x' * NOTE_MAX_CHARS} -->", f"at most {NOTE_MAX_CHARS} characters"),
        (f"{_ONCALL} <!-- note: {_SECRET} --> <!-- note: again -->", "two notes"),
        (f"- Ran the on-call rotation for 4 teams. <!-- id:b-oncall note: {_SECRET} -->", "a comment of its own"),
        (f"- Ran the on-call rotation for 4 teams. <!-- id:b-oncall <!-- note: {_SECRET} -->", "a comment of its own"),
    ],
)
def test_a_note_a_0_1_10_reader_would_misread_is_refused_with_the_line_number_and_never_quoted(typed: str, rule: str) -> None:
    error = _refused(PLAIN.replace(_ONCALL, typed))
    assert error.code == "master_markdown_invalid" and str(error).startswith(f"line {ONCALL_LINE}: ") and rule in str(error), str(error)
    assert _SECRET not in str(error)


def test_a_note_of_exactly_300_characters_is_kept() -> None:
    note = "x" * NOTE_MAX_CHARS
    assert parse_master(PLAIN.replace(_ONCALL, f"{_ONCALL} <!-- note: {note} -->")).items["b-oncall"].note == note
    assert note_rule(note) is None and "at most 300" in str(note_rule(note + "x"))


@pytest.mark.parametrize(
    ("old", "new", "line", "rule"),
    [
        ("## Experience", f"## Experience <!-- note: {_SECRET} -->", 7, "a section heading takes no note"),
        ("Founder | Jan 2024 - Present", f"Founder | Jan 2024 - Present <!-- note: {_SECRET} -->", 10, "a heading line takes no note"),
        (_ONCALL, f"{_ONCALL}\n<!-- note: {_SECRET} -->", 13, "not on a line of its own"),
        (_ONCALL, f"- Ran the on-call rotation <!-- note: {_SECRET} -->\n  for 4 teams. <!-- id:b-oncall --> <!-- note: again -->", 13, "two notes"),
    ],
)
def test_a_note_where_no_line_or_entry_can_hold_it_is_refused_not_dropped(old: str, new: str, line: int, rule: str) -> None:
    error = _refused(PLAIN.replace(old, new))
    assert str(error).startswith(f"line {line}: ") and rule in str(error) and _SECRET not in str(error), str(error)


# --- the 0.1.10 reader ---------------------------------------------------------------------------------------


def _split_0_1_10(raw: str, number: int) -> tuple[str, master_resume._Fields]:  # noqa: SLF001 - the reader under test
    """``master_resume._split`` as GigAI 0.1.10.9 to 0.1.10.11 ship it, word for word (it knows no note)."""

    fields = master_resume._Fields()  # noqa: SLF001
    line = raw
    while True:
        found = master_resume._COMMENT.search(line)  # noqa: SLF001
        if found is None:
            break
        line = line[: found.start()]
        for word in found.group(1).split():
            key, colon, value = word.partition(":")
            if not colon:
                continue  # a note's word
            if key == "id":
                if not master_resume._ID.fullmatch(value):  # noqa: SLF001
                    master_resume._bad(number, "an id holds letters, digits, '-' and '_' (at most 64) and starts with a letter or digit")  # noqa: SLF001
                if fields.id is not None and fields.id != value:
                    master_resume._bad(number, "one line has two ids")  # noqa: SLF001
                fields.id = value
            elif key == "tags":
                tags = tuple(tag for tag in value.split(",") if tag)
                if not tags or not all(master_resume._TAG.fullmatch(tag) for tag in tags):  # noqa: SLF001
                    master_resume._bad(number, "tags are comma-separated words of letters, digits and + # . - _ (tags:ai,llm)")  # noqa: SLF001
                fields.tags += tuple(tag for tag in tags if tag not in fields.tags)
            elif key == "backed":
                refs = tuple(ref for ref in value.split(",") if ref)
                if not refs or not all(master_resume._BACKED.fullmatch(ref) for ref in refs):  # noqa: SLF001
                    master_resume._bad(number, "backed names a story or an answer (backed:story:<id> or backed:answer:<question_id>)")  # noqa: SLF001
                fields.backed += tuple(ref for ref in refs if ref not in fields.backed)
            elif master_resume._MARKER.fullmatch(word):  # noqa: SLF001
                fields.marker = int(master_resume._MARKER.fullmatch(word).group(1))  # type: ignore[union-attr]  # noqa: SLF001
    return line.strip(), fields


#: Notes the rule accepts, each awkward in its own way for a reader that scans every word of a comment.
ACCEPTED_NOTES = (
    LEAD, SHORTER, AWKWARD,
    "valid: only for platform roles", "Android: lead; iOS: skip", "paid:yes kid:no hashtags:none", "(id:b-1) [tags:ai] 'backed:story:x'",
    "note: a note about notes", "ids: three; tag: none; back: later", "use for id-heavy roles (identity, tags, backing)",
    "ID:upper TAGS:upper BACKED:upper", "x" * NOTE_MAX_CHARS, "- starts with a dash", "# starts with a hash", "50% of roles: 2024 -> 2025",
)


@pytest.mark.parametrize("note", ACCEPTED_NOTES)
def test_a_0_1_10_reader_drops_the_note_and_misreads_nothing(note: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert note_rule(note) is None
    # A 0.1.11 master as GigAI stores it: the note on a line that has tags and evidence, on an entry and on a plain line.
    stored = parse_master(
        PLAIN.replace("<!-- id:r-edge -->", f"<!-- id:r-edge --> <!-- note: {note} -->")
        .replace("backed:story:runtime -->", f"backed:story:runtime --> <!-- note: {note} -->")
        .replace("<!-- id:b-billing -->", f"<!-- id:b-billing --> <!-- note: {note} -->")
    ).markdown()
    assert stored.count(f"<!-- note: {note} -->") == 3
    monkeypatch.setattr(master_resume, "_split", _split_0_1_10)
    old = parse_master(stored)
    # Exactly the master without the notes: every id, text, tag, evidence link, mark and place.
    monkeypatch.undo()
    assert _facts(old) == _facts(parse_master(PLAIN))
    assert all(item.note is None for item in old.items.values()) and all(entry.note is None for entry in old.entries.values())
    assert old.markdown() == PLAIN  # what a 0.1.10 binary would write back: the notes are gone, nothing else moved


@pytest.mark.parametrize(
    ("note", "what_0_1_10_does"),
    [
        ("see id:b-billing", "one line has two ids"),  # the line would be read under another id, or refused whole
        ("id: the short one", "an id holds letters"),
        ("tags:c++/java first", "tags are comma-separated words"),
        ("backed:nothing", "backed names a story or an answer"),
    ],
)
def test_the_refused_notes_are_the_ones_a_0_1_10_reader_would_misread(note: str, what_0_1_10_does: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Why the rule is what it is: these notes, were they stored, would stop a 0.1.10 binary from reading the master at all."""

    assert note_rule(note) is not None
    monkeypatch.setattr(master_resume, "_split", _split_0_1_10)
    assert what_0_1_10_does in str(_refused(PLAIN.replace(_ONCALL, f"{_ONCALL} <!-- note: {note} -->")))


def test_a_0_1_10_reader_would_take_a_reserved_word_of_a_note_as_the_lines_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """The silent misread the rule prevents: a note's ``tags:`` and ``backed:`` words become the line's tags and evidence."""

    assert note_rule("tags:leadership backed:story:invented") is not None
    monkeypatch.setattr(master_resume, "_split", _split_0_1_10)
    old = parse_master(PLAIN.replace(_ONCALL, f"{_ONCALL} <!-- note: tags:leadership backed:story:invented -->"))
    assert (old.items["b-oncall"].tags, old.items["b-oncall"].backed, old.items["b-oncall"].strength) == (("leadership",), ("story:invented",), "backed")


# --- never printed, never in the mark, part of the content -----------------------------------------------------


def test_a_note_is_never_printed_and_never_part_of_a_mark() -> None:
    plain, noted = parse_master(PLAIN), parse_master(NOTED)
    notes = (LEAD, SHORTER, AWKWARD, "note:")
    # The mark of every line: a note edit makes no assessment and no profile's resume stale.
    assert {key: item.mark for key, item in noted.items.items()} == {key: item.mark for key, item in plain.items.items()}
    assert {key: item.strength for key, item in noted.items.items()} == {key: item.strength for key, item in plain.items.items()}
    # The text a resume is printed from, and what the PDF's parser makes of the stored file itself.
    assert noted.markdown(ids=False) == plain.markdown(ids=False) and not any(note in noted.markdown(ids=False) for note in notes)
    printed = str(resume_pdf.parse_resume_markdown(noted.markdown()))
    assert printed == str(resume_pdf.parse_resume_markdown(plain.markdown())) and not any(note in printed for note in notes)
    # The lines an assessment's evidence is traced to (what makes an assessment stale), and the evidence view (no notes yet: N3).
    assert assess_master.master_lines(noted) == assess_master.master_lines(plain)
    prior = assess_master.profile_prior(titles=("staff platform engineer",), item_ids=None)
    posting = {"title": "Staff Platform Engineer", "posting_text": "We run agents on Kubernetes. You will own the durable runtime and the on-call rotation."}
    view = assess_master.evidence_text(noted, prior, today=date(2026, 10, 5), **posting)
    assert view.markdown == assess_master.evidence_text(plain, prior, today=date(2026, 10, 5), **posting).markdown
    assert "runtime" in view.markdown and not any(note in view.markdown for note in notes)


def test_a_note_edit_is_a_change_of_that_line_and_of_the_stored_content() -> None:
    plain, noted = parse_master(PLAIN), parse_master(NOTED)
    assert noted.markdown() != plain.markdown()  # the stored bytes: a new content digest, so a new revision
    assert compare(plain, noted).to_json() == {"added": 0, "removed": 0, "changed": 3}  # two lines and one entry
    reworded = parse_master(NOTED.replace(LEAD, "agentic roles: lead with this one"))
    assert compare(noted, reworded).to_json() == {"added": 0, "removed": 0, "changed": 1}
    assert reworded.items["b-4d2a91"].mark == noted.items["b-4d2a91"].mark


def test_a_merge_on_top_of_a_stored_master_keeps_its_notes() -> None:
    """``master init`` again (the migration, with the stored master as its base) reads the base as a draft: the notes stay."""

    plan = master_migration.plan_migration([], base=parse_master(NOTED))
    assert plan.master.markdown() == NOTED
