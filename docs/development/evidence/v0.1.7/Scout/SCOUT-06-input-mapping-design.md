# SCOUT-06 exact role-input mapping design

Date: 2026-09-10. This is a read-only design handoff for the SCOUT-06
research bridge. It neither changes a schema nor approves, allocates, starts,
or records a Run.

## Finding

`research.py` deliberately requires a selected input with this closed
projection:

```json
{
  "record_id": "record_<uuidv4>",
  "revision_id": "revision_<uuidv4>",
  "kind": "role"
}
```

That is not representable by the currently admitted external inputs. The
native content and private-record revision schemas have
`profile_preferences`, `experience_qa`, `imported_reference`,
`supplied_source`, and `selected_conversation`; `scout_inputs._NATIVE_KINDS`
and the external Plan schema admit only the first two native kinds. Direct
G45 references/run-inputs have `ref_`/`input_` identities, not the required
`record_`/`revision_` identity. A G45 posting is therefore neither a role
request nor a valid substitute for the renderer's role projection.

Reusing `profile_preferences`, `supplied_source`, or a posting would silently
change the evidence family. In particular, a source label or free-text
conversation does not establish a typed requested role. That would violate
the role-required output contract and leave the Plan unable to authenticate
the exact title given to the renderer.

## Recommended smallest additive path

Add one C1 native content kind, `role_request`, rather than a new generic
external input family or a synthetic G45 record. It is a user-request input,
not a research output and not a job posting. It uses the existing immutable
native record/revision, `jsl_blob`, operation-receipt, project/Gig scope, and
replay mechanisms.

The new closed native content payload is:

```json
{
  "schema_version": "1.0",
  "kind": "role_request",
  "scope": {
    "mode": "saved_default",
    "task_context_id": null,
    "base": null
  },
  "payload": {
    "role_title": "Forward Deployed Engineer",
    "role_context": "B2B implementation work in Denver",
    "provenance": {
      "kind": "user_reported",
      "source_refs": []
    }
  }
}
```

`role_title` is a required non-empty bounded string; `role_context` is either
`null` or a bounded string; `provenance` is closed and uses the existing
provenance shape. The native revision must have `origin: "user_reported"` and
the explicit local operator actor for the supported *user asked to research a
role* path. An agent-supplied candidate title is not silently promoted: it
must first be explicitly confirmed and recorded as a new user-reported native
revision. The existing `saved_default` scope is sufficient because selection
is still by explicit `record_id` plus `revision_id`; it does not choose a
default by itself. A future task-only override may use the existing
`run_override` discipline only with an already committed exact base revision.

This requires synchronized, strict enum/conditional additions in
`native-record-content.schema.json` and `private-record-revision.schema.json`,
plus the existing native writer validation. It is not a blob-shaped escape
hatch.

## Exact Plan-to-renderer bridge

The public Plan input continues to use the existing `scout_record` selector:

```json
{
  "family": "scout_record",
  "record_id": "record_<uuidv4>",
  "revision_id": "revision_<uuidv4>",
  "scope": {"mode": "saved_default", "task_context_id": null}
}
```

`scout_inputs.resolve_external_input` admits `role_request` as a native kind
and seals the normal full resolved envelope, including `native_kind`, full
scope, `content.family: "jsl_blob"`, exact blob artifact reference, and its
SHA-256. The external Plan's existing `scout_native_record` variant must add
only `role_request` to its strict native-kind enum. Since the external
resources are versioned strict contracts, this is a deliberate new reader
resource/version dispatch: old readers continue to read old plans and refuse
new role-request plans rather than accepting an unknown nested shape.

At `external start`, normal `revalidate_external_input` re-resolves this full
envelope from the committed snapshot under the writer lock. It must compare
the exact record/revision, native kind, scope, blob ref and content digest;
no latest-record lookup, working-copy byte, cross-Gig record, changed
revision, or archived replacement is selected. A previously sealed revision
keeps its historical identity when a later role-request revision is saved,
consistent with the existing sealed-input rule.

The SCOUT-06 domain bridge then performs this narrow, non-generic projection
from the already sealed Plan input:

```python
@dataclass(frozen=True)
class ResolvedRoleRequest:
    record_id: str
    revision_id: str
    role_title: str
    role_context: str | None
    sealed_input: dict[str, object]  # full authenticated Plan envelope

def resolve_research_role_request(
    *, sealed_input: Mapping[str, object], committed_snapshot: JournalSnapshot
) -> ResolvedRoleRequest: ...

def research_selected_input(value: ResolvedRoleRequest) -> dict[str, str]:
    return {
        "record_id": value.record_id,
        "revision_id": value.revision_id,
        "kind": "role",
    }
```

The resolver re-reads the committed blob named by the sealed envelope and
validates the typed `role_request` sidecar, revision identity, project/Gig,
user-reported origin, and operator actor before extracting any text. It passes
only `research_selected_input(...)` to the fixed pure `research.py` validator;
the Plan and Run retain the full original sealed envelope. The bridge also
requires `research["role_title"] == ResolvedRoleRequest.role_title` byte-for-
byte before accepting a rendered packet. It supplies `role_context` to the
external actor as inert input data, but does not claim it is reflected in an
output unless the packet explicitly says so. This preserves title provenance
without teaching the pure renderer to read a workpad or silently normalizing
"FDE" into a different title.

There is intentionally no mapping from a direct `g45_reference` or
`g45_run_input` to the renderer's record/revision projection. A future
`scout_record` wrapper over a committed G45 posting may be projected as
`kind: "posting"` only after its own domain discriminator proves it is a
posting; it still cannot satisfy the required role input. Existing native
`profile_preferences` and `experience_qa` can remain optional projections as
`preference` and `experience` respectively, but neither may become `role`.

## Required implementation tests

1. Record a user-reported `role_request` through the existing native operation
   service, explicitly select its record/revision in a research Plan, and
   prove the renderer receives exactly the `role` projection while the Plan
   retains the full blob ref/digest.
2. Reject at Plan allocation or the bridge (before Run allocation as
   applicable) a direct G45 input, a G45 posting, profile preferences,
   experience answers, supplied sources, and selected conversation material as
   the required role. Do not relabel any of them.
3. Reject foreign-Gig, uncommitted, malformed, swapped record/revision,
   wrong native kind/origin/actor, changed blob digest, scope mismatch, and
   changed `research.role_title`; assert no Plan/Run/receipt is published.
4. Change the requested role in a new native revision. A new explicit Plan
   can select it, while the old Plan/Run continues to resolve the original
   record/revision and cannot be mutated into the new title.
5. Prove optional preference/experience projections retain their original
   exact native references, and that a posting may appear only under a future
   authenticated posting mapping—not as the role request.

## Deliberate limits and handoff

This bridge authenticates that a user-recorded title was selected and that the
packet repeats it exactly. It does not establish that external research claims
are true, run network research, execute Gig-owned code, approve a Graph Set,
or make the candidate research source shipped. The pending SCOUT-06
persistence/domain-output work must still add the approved composite
`research` contract, fixed validator/source binding, versioned external
resources, checkpoint/submit persistence, and source-inventory successor.

The implementation owner should make the native-kind/schema additions and
research bridge together, with coordinator integration of strict schema
registration and hashes. No existing native/G45 family should be widened or
relabelled to obtain this path.
