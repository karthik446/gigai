"""The work behind the verbs: the gig's config file, HTTP capture, the ``baseline`` store, diff, report and the
``draft-tests`` prompt. Standard library only; ``cli.py`` imports this module inside each command.

Store ``baseline`` (``<home>/gigs/baseline-behavior/baseline.sqlite``) keeps every snapshot. Its SQL stays in the
DoltLite-compatible subset (a primary key on every table, no rowid writes) so the store can later be declared
``versioned`` without a rewrite. A snapshot's rows are written once, in one transaction, and never changed.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import sqlite3
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gigai.sdk import GigContext

STORE = "baseline"
CONFIG_FILE = "config.toml"
STEP = "draft-tests"
PROMPT = "prompts/draft-tests.md"
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
PROMPT_BODY_LIMIT = 4000
_SNAPSHOT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_REQUEST_FIELDS = frozenset({"method", "path", "body"})


class BaselineError(Exception):
    """A refusal the CLI prints as one line; ``code`` is the exit status (1 failed, 2 bad input)."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


# --- config ---------------------------------------------------------------------------------------------------


def config_path(ctx: GigContext) -> Path:
    """The gig's config file ``<home>/gigs/baseline-behavior/config.toml`` (its keys: ``config.schema.json``)."""

    return ctx.folder / CONFIG_FILE


def read_config(ctx: GigContext) -> dict[str, Any]:
    """``[config.defaults]`` from ``gig.toml`` with ``config.toml`` over it, validated here (the schema's keys and
    limits, and no query or fragment in ``target``); ``BaselineError`` when it is invalid."""

    config = dict(ctx.manifest.get("config", {}).get("defaults", {}))
    path = config_path(ctx)
    if path.is_file():
        try:
            config.update(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise BaselineError(f"cannot read {path}: {exc}") from exc
    unknown = sorted(set(config) - {"target", "timeout_seconds"})
    if unknown:
        raise BaselineError(f"{path}: unknown key(s) {', '.join(unknown)}")
    if "target" in config:
        try:
            config["target"] = check_target(config["target"])
        except BaselineError as exc:
            raise BaselineError(f"{path}: {exc}") from exc
    timeout = config.get("timeout_seconds")
    if not _timeout_ok(timeout):
        raise BaselineError(f"{path}: timeout_seconds must be a number above 0 and at most 300, not {timeout!r}")
    return config


def init_config(ctx: GigContext) -> tuple[dict[str, Any], list[str]]:
    """What ``init`` starts from: ``[config.defaults]`` plus the usable keys of ``config.toml``, and what it drops.

    ``init`` is how a hand-broken ``config.toml`` gets repaired, so unlike ``read_config`` this never refuses: an
    unreadable file, an unknown key or an invalid ``timeout_seconds`` is dropped (``target`` is replaced anyway)."""

    config = dict(ctx.manifest.get("config", {}).get("defaults", {}))
    path = config_path(ctx)
    if not path.is_file():
        return config, []
    try:
        existing = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return config, [f"the unreadable {path.name}"]
    dropped = sorted(set(existing) - {"target", "timeout_seconds"})
    if "timeout_seconds" in existing:
        if _timeout_ok(existing["timeout_seconds"]):
            config["timeout_seconds"] = existing["timeout_seconds"]
        else:
            dropped.append("timeout_seconds")
    return config, dropped


def _timeout_ok(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float) and 0 < value <= 300


def write_config(ctx: GigContext, values: dict[str, Any]) -> Path:
    """Replace ``config.toml`` with ``values`` (strings and numbers only); mode 0600, atomic."""

    path = config_path(ctx)
    text = "# Written by `gigai baseline-behavior init`; schema: config.schema.json in the gig package.\n"
    text += "".join(f"{key} = {_toml_value(value)}\n" for key, value in sorted(values.items()))
    temporary = path.with_name(f".{CONFIG_FILE}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def _toml_value(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise TypeError(f"config value {value!r} is not a string or a number")
    # A JSON string is a TOML basic string: TOML accepts every escape json.dumps writes (and targets hold no DEL).
    return json.dumps(value) if isinstance(value, str) else repr(value)


def check_target(url: Any) -> str:
    """``url`` without its trailing ``/`` when it is an http(s) URL with a host and no query or fragment."""

    if not isinstance(url, str) or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise BaselineError(f"target must be an http(s) URL, not {url!r}", 2)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment:
        raise BaselineError(f"target must be an http(s) URL with a host and no query or fragment, not {url!r}", 2)
    return url.rstrip("/")


# --- HTTP -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    """One request of a capture: ``path`` is appended to the target; ``json_body`` sends ``body`` as JSON."""

    method: str
    path: str
    body: str = ""
    json_body: bool = False

    @property
    def key(self) -> str:
        """What ``diff`` matches two snapshots on: ``METHOD path``, plus a body hash when there is a body."""

        if not self.body:
            return f"{self.method} {self.path}"
        return f"{self.method} {self.path} body:{hashlib.sha256(self.body.encode()).hexdigest()[:12]}"


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes

    @property
    def content_sha256(self) -> str:
        return content_sha256(self.status, self.body)


def content_sha256(status: int, body: bytes) -> str:
    """The hash ``diff`` compares: sha256 of the status line ``b"<status>\\n"`` followed by the response body."""

    return hashlib.sha256(f"{status}\n".encode() + body).hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is the answer to record, never a request to another URL."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def fetch(target: str, request: Request, timeout: float) -> Response:
    """Send ``request`` to ``target`` (no proxy, no redirect) and return its status and body, error statuses too."""

    headers = {"Content-Type": "application/json"} if request.json_body else {}
    data = request.body.encode() if request.body else None
    outgoing = urllib.request.Request(target + request.path, data=data, headers=headers, method=request.method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(outgoing, timeout=timeout) as answer:
            return Response(answer.status, answer.read())
    except urllib.error.HTTPError as exc:
        with exc:
            return Response(exc.code, exc.read())
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        raise BaselineError(f"{request.method} {target}{request.path}: no answer ({reason})") from exc


def read_requests(file: str | Path | None) -> list[Request]:
    """The requests of a capture: one JSON object per line (``method``, ``path``, ``body``); ``GET /`` without a file."""

    if file is None:
        return [Request("GET", "/")]
    path = Path(file)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise BaselineError(f"cannot read {path}: {exc}", 2) from exc
    requests: list[Request] = []
    seen: dict[str, int] = {}
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        where = f"{path}:{number}"
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise BaselineError(f"{where}: not JSON ({exc})", 2) from exc
        request = _request(row, where)
        if request.key in seen:
            raise BaselineError(f"{where}: the same request as line {seen[request.key]} ({request.key})", 2)
        seen[request.key] = number
        requests.append(request)
    if not requests:
        raise BaselineError(f"{path}: no requests", 2)
    return requests


def _request(row: Any, where: str) -> Request:
    if not isinstance(row, dict):
        raise BaselineError(f"{where}: a request is a JSON object with method, path and body", 2)
    unknown = sorted(set(row) - _REQUEST_FIELDS)
    if unknown:
        raise BaselineError(f"{where}: unknown field(s) {', '.join(unknown)}; a request has method, path, body", 2)
    method = row.get("method", "GET")
    if not isinstance(method, str) or method.upper() not in METHODS:
        raise BaselineError(f"{where}: method must be one of {', '.join(METHODS)}, not {method!r}", 2)
    path = row.get("path")
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or "#" in path
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in path)
    ):
        raise BaselineError(f"{where}: path must start with one '/' and hold no space or '#', not {path!r}", 2)
    body = row.get("body")
    if body is None:
        return Request(method.upper(), path)
    if isinstance(body, str):
        return Request(method.upper(), path, body)
    return Request(method.upper(), path, json.dumps(body, sort_keys=True, separators=(",", ":")), json_body=True)


# --- store ----------------------------------------------------------------------------------------------------


def _v1(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE snapshots (name TEXT NOT NULL PRIMARY KEY, target TEXT NOT NULL, "
        "captured_at TEXT NOT NULL, requests INTEGER NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE responses (snapshot TEXT NOT NULL, request_key TEXT NOT NULL, method TEXT NOT NULL, "
        "path TEXT NOT NULL, request_body TEXT NOT NULL, status INTEGER NOT NULL, response_body BLOB NOT NULL, "
        "content_sha256 TEXT NOT NULL, captured_at TEXT NOT NULL, PRIMARY KEY (snapshot, request_key))"
    )


MIGRATIONS = (_v1,)


def open_store(ctx: GigContext, *, create: bool) -> sqlite3.Connection | None:
    """The ``baseline`` store at its current version; ``None`` when it does not exist and ``create`` is false."""

    if not create and not ctx.store.path(ctx.name, STORE).is_file():
        return None
    ctx.store.db(ctx.name, STORE).close()
    ctx.store.migrate(STORE, MIGRATIONS)
    connection = ctx.store.db(ctx.name, STORE)
    connection.isolation_level = None
    return connection


def check_snapshot_name(name: str) -> str:
    if not _SNAPSHOT_NAME.fullmatch(name):
        raise BaselineError(f"snapshot name {name!r}: use 1-64 letters, digits, '.', '_' or '-', starting with one", 2)
    return name


def capture(ctx: GigContext, snapshot: str, requests: Iterable[Request]) -> dict[str, Any]:
    """Send every request to the configured target, then store all answers as snapshot ``snapshot`` at once."""

    check_snapshot_name(snapshot)
    config = read_config(ctx)
    target = config.get("target")
    if target is None:
        raise BaselineError("no target yet; run `gigai baseline-behavior init --target URL` first", 2)
    connection = open_store(ctx, create=False)
    if connection is not None:
        try:
            _refuse_existing(connection, snapshot)
        finally:
            connection.close()
    answers = [(request, fetch(target, request, config["timeout_seconds"])) for request in requests]
    captured_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    connection = open_store(ctx, create=True)
    assert connection is not None
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            _refuse_existing(connection, snapshot)
            connection.execute(
                "INSERT INTO snapshots (name, target, captured_at, requests) VALUES (?, ?, ?, ?)",
                (snapshot, target, captured_at, len(answers)),
            )
            connection.executemany(
                "INSERT INTO responses (snapshot, request_key, method, path, request_body, status, response_body, "
                "content_sha256, captured_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (snapshot, request.key, request.method, request.path, request.body, answer.status, answer.body,
                     answer.content_sha256, captured_at)
                    for request, answer in answers
                ],
            )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
    return {
        "snapshot": snapshot,
        "target": target,
        "captured_at": captured_at,
        "responses": [
            {"request": request.key, "status": answer.status, "content_sha256": answer.content_sha256}
            for request, answer in answers
        ],
    }


def _refuse_existing(connection: sqlite3.Connection, snapshot: str) -> None:
    if connection.execute("SELECT 1 FROM snapshots WHERE name = ?", (snapshot,)).fetchone():
        raise BaselineError(f"snapshot {snapshot!r} exists and snapshots never change; capture under a new name")


def _snapshot(connection: sqlite3.Connection | None, name: str) -> tuple[str, str, int]:
    row = None if connection is None else connection.execute(
        "SELECT target, captured_at, requests FROM snapshots WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        raise BaselineError(f"no snapshot {name!r}; `gigai baseline-behavior capture --snapshot {name}` makes one", 2)
    return row


def diff(ctx: GigContext, left: str, right: str) -> dict[str, Any]:
    """Snapshot ``left`` against ``right``, request by request (status and content hash)."""

    connection = open_store(ctx, create=False)
    try:
        _snapshot(connection, left)
        _snapshot(connection, right)
        assert connection is not None
        rows = connection.execute(
            "SELECT a.request_key, a.status, a.content_sha256, b.status, b.content_sha256 "
            "FROM responses AS a LEFT JOIN responses AS b ON b.snapshot = ? AND b.request_key = a.request_key "
            "WHERE a.snapshot = ? "
            "UNION ALL "
            "SELECT b.request_key, NULL, NULL, b.status, b.content_sha256 FROM responses AS b "
            "WHERE b.snapshot = ? AND NOT EXISTS "
            "(SELECT 1 FROM responses AS a WHERE a.snapshot = ? AND a.request_key = b.request_key) "
            "ORDER BY 1",
            (right, left, right, left),
        ).fetchall()
    finally:
        if connection is not None:
            connection.close()
    changes: list[dict[str, Any]] = []
    same = 0
    for key, left_status, left_hash, right_status, right_hash in rows:
        if left_hash is None:
            change = "added"
        elif right_hash is None:
            change = "removed"
        elif left_hash != right_hash:
            change = "changed"
        else:
            same += 1
            continue
        changes.append(
            {"request": key, "change": change, "status": [left_status, right_status], "content_sha256": [left_hash, right_hash]}
        )
    return {"a": left, "b": right, "same": same, "changes": changes}


def report(ctx: GigContext, snapshot: str) -> dict[str, Any]:
    """Snapshot ``snapshot``'s target, capture time, request count and responses per status."""

    connection = open_store(ctx, create=False)
    try:
        target, captured_at, count = _snapshot(connection, snapshot)
        assert connection is not None
        statuses = connection.execute(
            "SELECT status, COUNT(*) FROM responses WHERE snapshot = ? GROUP BY status ORDER BY status", (snapshot,)
        ).fetchall()
    finally:
        if connection is not None:
            connection.close()
    return {
        "snapshot": snapshot,
        "target": target,
        "captured_at": captured_at,
        "requests": count,
        "statuses": {str(status): number for status, number in statuses},
    }


def draft_tests_prompt(ctx: GigContext, snapshot: str) -> str:
    """The exact ``input`` ``generate-tests`` would pass to ``ctx.model("draft-tests", input)``."""

    connection = open_store(ctx, create=False)
    try:
        target, captured_at, _ = _snapshot(connection, snapshot)
        assert connection is not None
        rows = connection.execute(
            "SELECT request_key, method, path, request_body, status, response_body FROM responses "
            "WHERE snapshot = ? ORDER BY request_key",
            (snapshot,),
        ).fetchall()
    finally:
        if connection is not None:
            connection.close()
    captures = "\n\n".join(_capture_text(*row) for row in rows)
    template = resources.files(__package__).joinpath(PROMPT).read_text(encoding="utf-8")
    replacements = {"{{snapshot}}": snapshot, "{{target}}": target, "{{captured_at}}": captured_at}
    for token, value in replacements.items():
        template = template.replace(token, value)
    return template.replace("{{captures}}", captures)


def _capture_text(key: str, method: str, path: str, request_body: str, status: int, body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    if len(text) > PROMPT_BODY_LIMIT:
        text = text[:PROMPT_BODY_LIMIT] + f"\n[... {len(text) - PROMPT_BODY_LIMIT} more characters not shown]"
    lines = [f"### {key}", f"request: {method} {path}"]
    if request_body:
        lines.append(f"request body: {request_body}")
    lines += [f"status: {status}", "response body:", "```", text, "```"]
    return "\n".join(lines)
