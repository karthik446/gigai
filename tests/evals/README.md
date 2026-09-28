# Evals

## Jev budget (uat-bug-021 decision c)

`jev_budget.daily_budget_usd` (default $0.50/day) applies to every ranking
pass that goes through `jev_rank.rank_postings_report` -- acquire, `POST
/rank`, quick assess, and an eval's own `--with-jev` pass alike; once the
day's spend reaches the budget, a pass stops scoring and reports
`daily_budget_reached`, same as any other caller. The evals keep their own
per-run cap (`run_assess_eval.py --jev-cost-cap-usd`, default $1.00) on top
of that, but the daily budget is checked first and is shared with whatever
else on the machine ranked Jev that day. To run an eval without the $0.50
default cutting a `--with-jev` pass short, set
`GIGAI_JEV_DAILY_BUDGET_USD` to the eval's own cap (e.g.
`GIGAI_JEV_DAILY_BUDGET_USD=1.00`) before running it.
