# G23 Fixture Corpus

- Status: Accepted
- Implementation commit: `67d9d9c`, `f2fa8c0`, `f74e6bd`, `2ccb490`, `0345d73`
- Test module: `tests/test_g23_portability.py`

The fixture corpus contains 13 focused tests covering the accepted amendment
and the runtime boundary:

| Area | Evidence |
|---|---|
| Sealed pointer | Valid portable pointer, legacy pointer (`reported_non_portable`), live substitution refusal, and unpublished-pointer recovery refusal |
| Manifest semantics | Valid binding, valid-but-different digest refusal, unsafe path refusal, and cross-Gig binding refusal |
| Lineage | Single-hop resolution, ordered three-hop resolution, cycle refusal, missing-parent refusal, and cross-Gig refusal |
| Authority/effects | Pointer and journal remain byte-identical after read verification; portability code has no network imports, commit operation, or installed-tool path access |
| Transport | Fresh second-home replay copies only the manifest and pinned source, then invokes G17's installer locally |

No Run-consumption fixture is included. `resolved_tools` remains Run authority,
as required by the amendment, and installation is not execution.
