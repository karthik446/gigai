# Evals

The assess eval harness (`run_assess_eval.py`) and its fixtures. A ranking pass is the local model (`model_rank`); there is no other ranker to budget.

The rules A/B mode (`run_assess_rules_eval.py`, 0110-038) is a small bounded run over `fixtures/rules_cases.json` (at most 30 synthetic postings, at most 60 model calls): location (rule 4), sponsorship (rule 5) and the candidate work mode, each case on the shipped prompt and on the pre-change one. Live only with `GIGAI_ASSESS_EVAL_LIVE=1`; `--fake-model` runs it offline.
