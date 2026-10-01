"""0110-026b: snapshot export (S1). Synthetic cache directories only; no network, no real data."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.scout.find_jobs.company_index import CompanyIndex, CompanyIndexEntry, IndexedPosting
from gigai.scout.find_jobs.posting_tags import TAGGER_VERSION, tag_new_titles
from gigai.scout.find_jobs.snapshot import (
    ROLE_INDEX,
    SnapshotError,
    export_snapshot,
    file_name,
    load_manifest,
    read_lines,
    scan_for_private_content,
    verify_files,
)
from gigai.scout.find_jobs.tag_store import TagStore
from gigai.scout.scout_cli import scout_group

T1 = "2026-09-27T10:00:00.000Z"
DAY1 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
DAY2 = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)


def _posting(pid: str, title: str) -> IndexedPosting:
    return IndexedPosting(
        posting_id=pid, title=title, location="Denver, CO", url=f"https://boards.example/jobs/{pid}",
        updated_at="2026-09-20T00:00:00Z", content_sha256="ab" * 32, first_seen=T1, last_seen=T1,
        published_at="2026-09-19T00:00:00Z", countries=("US",),
    )


def _board(ats: str, slug: str, ids: dict[str, str], etag: str = "etag-1") -> CompanyIndexEntry:
    return CompanyIndexEntry(
        company=slug.title(), ats=ats, slug=slug, checked_at=T1, etag=etag, body_sha256="cd" * 32,
        postings={pid: _posting(pid, title) for pid, title in ids.items()}, last_modified="Mon, 01 Sep 2026 00:00:00 GMT",
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    index = CompanyIndex.for_home(root)
    index.write(_board("greenhouse", "acme", {"1": "Senior Software Engineer", "2": "Product Designer"}))
    index.write(_board("lever", "globex", {"a": "Staff Data Scientist"}))
    index.write(_board("ashby", "initech", {"x": "Engineering Manager", "y": "Recruiter"}))
    store = TagStore.for_home(root, tagger_version=TAGGER_VERSION)
    tag_new_titles(store, ["Senior Software Engineer", "Product Designer", "Staff Data Scientist", "Engineering Manager", "Recruiter"])
    store.close()
    return root


def _digests(out: Path) -> dict[str, str]:
    return {name: meta["sha256"] for name, meta in load_manifest(out / "manifest.json")["files"].items()}


def test_round_trip_export_then_read_back_verifies(home: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = export_snapshot(home, out, as_of=DAY1)
    manifest = load_manifest(result.manifest_path)
    assert manifest["counts"] == {"boards": 3, "postings": 5, "tags": 5}
    assert manifest["tagger_version"] == TAGGER_VERSION
    assert manifest["as_of"] == "2026-10-01T06:00:00Z"
    assert verify_files(manifest, out) == {name: meta["rows"] for name, meta in manifest["files"].items()}
    rows = list(read_lines(out / file_name(ROLE_INDEX, "2026-10-01")))
    assert {(r["ats"], r["slug"], r["id"]) for r in rows} >= {("greenhouse", "acme", "1")}
    boards = {r["slug"]: r for r in read_lines(out / file_name("boards", "2026-10-01"))}
    assert boards["acme"]["etag"] == "etag-1" and boards["acme"]["last_modified"].startswith("Mon")
    # the commands are text only, data first and manifest last
    assert result.gh_commands[0].startswith("gh release create") and "--clobber" in result.gh_commands[-1]
    assert result.gh_commands[-1].endswith('manifest.json"')


def test_corrupted_file_fails_the_digest_check(home: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    export_snapshot(home, out, as_of=DAY1)
    victim = out / file_name("boards", "2026-10-01")
    data = bytearray(victim.read_bytes())
    data[len(data) // 2] ^= 0xFF
    victim.write_bytes(bytes(data))
    with pytest.raises(SnapshotError) as caught:
        verify_files(load_manifest(out / "manifest.json"), out)
    assert caught.value.code == "digest_mismatch"
    (out / file_name("tags", "2026-10-01")).unlink()
    with pytest.raises(SnapshotError) as missing:
        verify_files(load_manifest(out / "manifest.json"), out)
    assert missing.value.code in {"digest_mismatch", "file_missing"}


def test_unknown_manifest_version_is_refused(home: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    export_snapshot(home, out, as_of=DAY1)
    doc = json.loads((out / "manifest.json").read_text())
    doc["format_version"] = 99
    (out / "manifest.json").write_text(json.dumps(doc))
    with pytest.raises(SnapshotError) as caught:
        load_manifest(out / "manifest.json")
    assert caught.value.code == "manifest_version"


def test_removed_board_and_posting_are_in_the_removal_list_not_the_delta(home: Path, tmp_path: Path) -> None:
    first = tmp_path / "day1"
    export_snapshot(home, first, as_of=DAY1)
    index = CompanyIndex.for_home(home)
    assert index.delete("lever", "globex")  # the board is gone
    index.write(_board("greenhouse", "acme", {"1": "Senior Software Engineer"}, etag="etag-2"))  # posting 2 gone, etag moved
    index.write(_board("ashby", "initech", {"x": "Engineering Manager", "y": "Recruiter", "z": "Designer"}))  # one new posting
    second = tmp_path / "day2"
    result = export_snapshot(home, second, base_manifest=first / "manifest.json", as_of=DAY2)
    manifest = result.manifest
    assert manifest["removals"] == {"boards": [["lever", "globex"]], "postings_count": 1}
    assert manifest["delta"]["base"]["as_of"] == "2026-10-01T06:00:00Z"
    lines = list(read_lines(second / manifest["delta"]["file"]))
    assert not any(line["op"] == "put" and line["row"].get("slug") == "globex" for line in lines)
    assert not any(line["op"] == "del" and line["key"][:2] == ["lever", "globex"] and len(line["key"]) == 3 for line in lines)
    assert {"table": "boards", "op": "del", "key": ["lever", "globex"]} in lines
    assert {"table": "index", "op": "del", "key": ["greenhouse", "acme", "2"]} in lines
    puts = [line for line in lines if line["op"] == "put"]
    assert {(l["table"], l["row"].get("id") or l["row"]["slug"]) for l in puts} == {("index", "z"), ("boards", "acme")}
    # the full files of day 2 no longer carry the removed board at all
    assert all(row["slug"] != "globex" for row in read_lines(second / file_name("boards", "2026-10-02")))


def test_delta_refuses_a_corrupt_base(home: Path, tmp_path: Path) -> None:
    first = tmp_path / "day1"
    export_snapshot(home, first, as_of=DAY1)
    victim = first / file_name("index", "2026-10-01")
    victim.write_bytes(victim.read_bytes()[:-5])
    with pytest.raises(SnapshotError):
        export_snapshot(home, tmp_path / "day2", base_manifest=first / "manifest.json", as_of=DAY2)
    assert not (tmp_path / "day2" / "manifest.json").exists()


def test_no_description_text_and_no_private_path_in_any_output(home: Path, tmp_path: Path) -> None:
    # A description-ish payload and a home path hidden in the synthetic cache must not reach the output.
    path = CompanyIndex.for_home(home).path("greenhouse", "acme")
    doc = json.loads(path.read_text())
    doc["postings"]["1"]["description"] = "SECRET-DESCRIPTION-TEXT about the role"
    path.write_text(json.dumps(doc))
    out = tmp_path / "out"
    export_snapshot(home, out, as_of=DAY1)
    blob = b""
    for name in sorted(p.name for p in out.iterdir()):
        raw = (out / name).read_bytes()
        blob += raw if name == "manifest.json" else b"\n".join(json.dumps(r).encode() for r in read_lines(out / name))
    assert b"SECRET-DESCRIPTION-TEXT" not in blob
    assert str(home).encode() not in blob and str(tmp_path).encode() not in blob
    for forbidden in (b'"description"', b'"body"', b"resume", b"/Users/", b".gigai"):
        assert forbidden not in blob
    assert sorted(p.name for p in out.iterdir()) == sorted([*(_digests(out)), "manifest.json"])


def test_scan_refuses_description_field_and_private_paths_and_cleans_up(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs import snapshot

    out = tmp_path / "out"
    export_snapshot(home, out, as_of=DAY1)
    name = file_name("boards", "2026-10-01")
    snapshot_file = out / name
    # direct scan of a poisoned file
    bad = tmp_path / "bad"
    bad.mkdir()
    data, _ = snapshot._compress([{"ats": "lever", "slug": "x", "description": "words"}])
    (bad / "boards.jsonl.xz").write_bytes(data)
    with pytest.raises(SnapshotError) as caught:
        scan_for_private_content(bad, ["boards.jsonl.xz"])
    assert caught.value.code == "forbidden_field"
    data, _ = snapshot._compress([{"slug": "/Users/someone/.gigai/profile.json"}])
    (bad / "p.jsonl.xz").write_bytes(data)
    with pytest.raises(SnapshotError) as private:
        scan_for_private_content(bad, ["p.jsonl.xz"])
    assert private.value.code == "private_path"
    assert snapshot_file.exists()
    # a poisoned row inside export: nothing is left behind
    monkeypatch.setattr(snapshot, "_board_row", lambda entry: {"ats": entry.ats, "slug": entry.slug, "description": "x"})
    out2 = tmp_path / "out2"
    with pytest.raises(SnapshotError):
        export_snapshot(home, out2, as_of=DAY1)
    assert list(out2.iterdir()) == []


def test_export_is_deterministic(home: Path, tmp_path: Path) -> None:
    export_snapshot(home, tmp_path / "a", as_of=DAY1)
    export_snapshot(home, tmp_path / "b", as_of=DAY1)
    assert _digests(tmp_path / "a") == _digests(tmp_path / "b")
    assert (tmp_path / "a" / "manifest.json").read_bytes() == (tmp_path / "b" / "manifest.json").read_bytes()


def test_output_inside_home_and_empty_index_are_refused(home: Path, tmp_path: Path) -> None:
    with pytest.raises(SnapshotError) as inside:
        export_snapshot(home, home / "cache" / "out", as_of=DAY1)
    assert inside.value.code == "out_inside_home"
    with pytest.raises(SnapshotError) as empty:
        export_snapshot(tmp_path / "empty-home", tmp_path / "o", as_of=DAY1)
    assert empty.value.code == "empty_index"
    assert not (tmp_path / "empty-home").exists()


def test_cli_exports_and_prints_gh_commands(home: Path, tmp_path: Path) -> None:
    out = tmp_path / "cli-out"
    result = CliRunner().invoke(scout_group, ["snapshot", "export", "--out", str(out), "--home", str(home)])
    assert result.exit_code == 0, result.output
    assert "Nothing was published" in result.output and "gh release upload" in result.output
    assert (out / "manifest.json").is_file()
    failed = CliRunner().invoke(scout_group, ["snapshot", "export", "--out", str(tmp_path / "x"), "--home", str(tmp_path / "none"), "--json"])
    assert failed.exit_code == 1 and json.loads(failed.output)["code"] == "empty_index"
