# Scout

Your job-search workspace. Research, candidate evidence, documents and progress
stay with your Gig, so another agent session can pick up where you left off.

## Choose the work you need

- [Research a role](goalgraphs/research-role.md): responsibilities, variations,
  compensation context and reusable sources. A resume is not required.
- [Find jobs](goalgraphs/find-jobs.md): compare opportunities with your selected
  preferences. Research is useful but is not a mandatory user step.
- [Tailor an application](goalgraphs/tailor-application.md): start directly from
  a posting and candidate evidence; request a resume, cover letter, or both.
- [Record progress](goalgraphs/record-application.md): record your instruction,
  including an application made outside Scout.
- [Prepare for an interview](goalgraphs/prepare-interview.md): reuse research,
  evidence and practice feedback, with or without an application record.

These are selectable goals, not a compulsory funnel. Missing information should
lead to a focused question, not an invented answer or a silent state change.

## Ownership and history

Your agent does the semantic work. GigAI validates and records supplied results;
it does not silently browse, apply, contact employers or call another provider.
An external agent's claimed checks remain agent-reported unless independently
verified. Its usage is unobserved or reported, never invented accounting.

Select the exact preferences, experience, posting and research revisions for
each Run. A later edit does not change an old draft's inputs. Task-specific
overrides do not silently replace saved defaults. Resume finalization and
application submission are separate facts.

The private journal owns durable history. The single `state.sqlite` provides a
rebuildable view. Keep imported content under canonical `references/` or
`run-inputs/`; keep native content and readable documents under `records/` and
`docs/`. Do not copy private data into a shareable source package.

## Customize your copy

`goalgraphs/` contains readable instructions. `ui/template.html` and `ui/style.css`
are editable UI source; `reports/scout/` is generated output and must not overwrite
those sources. A report can link back to validated documents and Run artifacts.
Supporting local tools must use validated GigAI persistence, not direct database
edits. Definition or tool changes go through the normal version/approval path.

This bundled authoring source is inert: reading, inspecting, installing or
updating it grants no approval and executes no code. Provisioning and release
readiness are separate from the availability of these source files.
