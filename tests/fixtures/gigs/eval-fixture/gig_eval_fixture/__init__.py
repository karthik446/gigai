"""Test-only gig: its [evals] step `answer` asks the deterministic adapter one synthetic question and traces the call."""

import dataclasses
import json
from importlib import resources

import click

from gigai.adapters.deterministic import DeterministicAdapter
from gigai.adapters.factory import ModelAdapterBinding
from gigai.adapters.port import RetryPolicy
from gigai.canonical import digest_imported_bytes
from gigai.config import Endpoint, ModelTarget
from gigai.model_targets import ResolvedModelTarget
from gigai.sdk import Dataset, ItemRef, Prereg, PromptRef, RunRecord, model_call, new_id

PROMPT = "Answer the question with one JSON object {{\"answer\": <text>}}.\nQuestion: {question}"
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
TARGET = ResolvedModelTarget(
    target=ModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
    endpoint=Endpoint(name="offline", adapter="deterministic"),
)
PREREG = Prereg(
    primary_metric="exact_match",
    guard_metric="parse_ok",
    pass_rule="exact_match >= baseline + margin",
    margin=0.05,
    n_planned=6,
    registered_at="2026-01-01T00:00:00.000Z",
)


def _file(name):
    return (resources.files(__package__) / name).read_bytes()


def _items():
    return [json.loads(line) for line in _file("dataset.jsonl").decode("utf-8").splitlines() if line.strip()]


class FixtureAdapter(DeterministicAdapter):
    """The deterministic adapter, answering this gig's rendered prompts from `responses.json`."""

    def __init__(self):
        responses = json.loads(_file("responses.json"))
        self._responses = {PROMPT.format(question=item["question"]): responses[item["id"]] for item in _items()}


def postprocess(text):
    """The `answer` step's parser: `{"answer": <text>}` is ok, other JSON invalid, anything else not JSON."""

    try:
        value = json.loads(text)
    except ValueError:
        return None, "not_json"
    if isinstance(value, dict) and isinstance(value.get("answer"), str):
        return {"answer": value["answer"]}, "ok"
    return value, "invalid"


def run_record(run_id, n):
    dataset = _file("dataset.jsonl")
    return RunRecord(
        run_id=run_id,
        gig="eval-fixture",
        manifest_digest=digest_imported_bytes(_file("gig.toml")),
        dataset=Dataset(
            id="eval-fixture-questions",
            version="1",
            sha256=digest_imported_bytes(dataset),
            slice_recipe={"filter": None, "stratify": "cluster", "count": n},
            seed=11,
        ),
        prereg=PREREG,
    )


@click.group()
def eval_fixture():
    """test gig: eval-fixture"""


@eval_fixture.command("answer")
@click.option("--item", "item_ids", multiple=True, help="Dataset item to answer (repeatable). Default: the first one.")
@click.option("--run-id", default=None, help="Record the traces under this run. Default: a new run.")
@click.option("--repeat", "repeat_index", type=int, default=0, help="The repeat index to record.")
@click.pass_obj
def answer(ctx, item_ids, run_id, repeat_index):
    """Ask the deterministic adapter the `answer` step for each item and trace every call."""

    items = {item["id"]: item for item in _items()}
    chosen = [items[item_id] for item_id in item_ids] if item_ids else [_items()[0]]
    run = run_record(run_id or new_id("run"), len(chosen))
    binding = ModelAdapterBinding(TARGET, FixtureAdapter())
    prompt = PromptRef.of("answer", "1", PROMPT)
    for item in chosen:
        text = PROMPT.format(question=item["question"])
        request = dataclasses.replace(
            binding.request(role="reviewer", prompt=text),
            temperature=0.0,
            top_p=1.0,
            seed=7,
            output_mode="schema",
            schema=SCHEMA,
            retry_policy=RetryPolicy(attempts=1, backoff_seconds=0.0),
        )
        result = binding.port.invoke(request)
        parsed, status = postprocess(result.output_text)
        record = ctx.trace(
            "answer",
            text,
            result.output_text,
            run=run,
            item=ItemRef(item["id"], item["version"], item["cluster"]),
            prompt=prompt,
            model=model_call(binding, request, result),
            repeat_index=repeat_index,
            output_parsed=parsed,
            parse_status=status,
        )
        click.echo(json.dumps({"run_id": run.run_id, "trace_id": record.trace_id, "parse_status": status}))


@eval_fixture.command("replay")
@click.argument("run_id")
@click.pass_obj
def replay_run(ctx, run_id):
    """Re-score the stored `answer` traces of RUN_ID through the postprocess, with no model call."""

    for row in ctx.replay(run_id, postprocess, step_id="answer"):
        click.echo(json.dumps(dataclasses.asdict(row), sort_keys=True))


@eval_fixture.command("note")
@click.argument("text")
@click.pass_obj
def note(ctx, text):
    """Keep one note in the gig's own notes store."""

    db = ctx.store.db(ctx.name, "notes")
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS notes (text TEXT NOT NULL)")
        db.execute("INSERT INTO notes (text) VALUES (?)", (text,))
    db.close()


@eval_fixture.command("ask")
@click.argument("step")
@click.argument("text")
@click.option("--temperature", type=float, default=None, help="Ask with this temperature (a request setting).")
@click.pass_obj
def ask(ctx, step, text, temperature):
    """Ask STEP's model target TEXT through ctx.model and print its answer (BK2)."""

    result = ctx.model(step, text, **({} if temperature is None else {"temperature": temperature}))
    click.echo(json.dumps({"step": step, "output": result.output_text, "model": result.resolved_model}))
