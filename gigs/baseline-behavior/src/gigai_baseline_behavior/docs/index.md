# baseline-behavior

Capture what an HTTP service answers today, keep each capture as an immutable snapshot, and diff two snapshots
to see what changed. A snapshot is the baseline you compare a later version of the service against.

## Commands

- `init --target URL` — probe `URL` with one `GET` and save it as the target in the gig's config file
  `<home>/gigs/baseline-behavior/config.toml`. Nothing is saved when the target does not answer.
- `capture --snapshot NAME [--requests FILE]` — send each request to the target and store every answer as
  snapshot `NAME`. `FILE` is JSONL, one request per line:
  `{"method": "GET", "path": "/users", "body": null}`. `method` defaults to `GET`; `path` is appended to the
  target; `body` is a string sent as is, or any other JSON value sent as JSON. Without `--requests` the capture is
  one `GET /`. A snapshot never changes: capturing an existing name is refused, and nothing is stored unless every
  request got an answer (an error status such as 404 or 500 is an answer; a redirect is recorded, not followed).
- `diff A B` — compare snapshot `A` with snapshot `B` request by request. A request is `added` (only in `B`),
  `removed` (only in `A`) or `changed` (its status or body differs). Exit 0 when nothing differs, 1 when something
  does, 2 when a snapshot does not exist.
- `report --snapshot NAME [--json]` — the snapshot's target, capture time, request count and responses per status.
- `generate-tests --snapshot NAME --dry-run` — print the prompt model step `draft-tests` would get to draft
  characterization tests from the snapshot. It makes no model call; without `--dry-run` it exits 2, because the
  model step ships in a later release.

## Data

Snapshots live in the store `baseline`, `<home>/gigs/baseline-behavior/baseline.sqlite`. Each response row keeps
the request (method, path, body), the status, the body and `content_sha256`, the sha256 of `<status>\n` followed by
the body; `diff` compares those hashes. Response headers are not kept, so a changing `Date` header is not a
difference. `gigai baseline-behavior export` (core) copies the store with the rest of the gig folder.

## Config

`config.toml` in the gig folder, validated whenever a verb reads it (the keys and limits are those of the package's
`config.schema.json`; a target with a query or fragment is refused too). `init` rewrites the file, so it also repairs
a hand-broken one: it keeps a valid `timeout_seconds` and drops what it cannot use, saying what it dropped.

| key | default | meaning |
| --- | --- | --- |
| `target` | none (set by `init`) | base URL of the service under test |
| `timeout_seconds` | `10` | how long `init` and `capture` wait for one response |

The gig talks only to the target you name: no proxy from the environment, no redirects followed.
