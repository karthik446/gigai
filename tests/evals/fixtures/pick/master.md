<!-- gigai-master:1  SYNTHETIC (pick eval, 0110-10-15): an invented person, invented companies. No name, no contact data. This is the LARGE master; sizes.json names the lines the medium and the small one leave out. -->

## Summary

- Staff engineer with 17 years of experience building backend platforms and, since 2021, LLM agent systems that run in production: led a 7-engineer AI platform team serving 60 internal teams. <!-- id:sum-ai -->
- Distributed-systems engineer in Go and Python on Kubernetes, PostgreSQL and Kafka, operating platforms end to end with SLOs and on-call ownership. <!-- id:sum-backend -->
- Technical leader who sets direction across teams, mentors senior engineers and turns ambiguous platform problems into shipped, measured systems. <!-- id:sum-lead -->

## Experience

### Halcyon Freight <!-- id:r-hal -->
Staff Engineer, AI Platform | Mar 2021 - Present
- Architected the agent runtime that executes 400,000 tool-calling LLM tasks per day for 60 internal teams, with typed tool contracts, retries and per-team budgets, and ran it through three years of growth from a 2-team pilot to the company's default way of shipping LLM features. <!-- id:hal-01 -->
- Led a 7-engineer AI platform team and owned its roadmap end to end, from design review to the on-call rotation, setting quarterly goals with product and finance and reporting progress to the engineering leadership group. <!-- id:hal-02 -->
- Built an evaluation harness in Python that scores every prompt change against 2,200 labelled cases before release, cutting regressions that reached production from 9 per quarter to 1 and making evaluation a required check in every team's release pipeline. <!-- id:hal-03 -->
- Designed a retrieval pipeline on PostgreSQL with pgvector serving 12 million embedded passages at a p95 of 140 ms, including chunking, hybrid ranking and a nightly re-embedding job that keeps the index within one day of the source documents. <!-- id:hal-04 -->
- Cut LLM spend 38% through prompt caching and routing easy requests to smaller models, across the OpenAI and Anthropic APIs, with a weekly report that shows every team what its prompts cost. <!-- id:hal-05 -->
- Ran model serving and the agent runtime on Kubernetes across AWS and GCP at 99.95% availability, with GPU autoscaling and an on-call rotation of 6, and led the response to the 4 major incidents of the last two years. <!-- id:hal-06 -->
- Mentored 3 senior engineers to staff-level scope through weekly design reviews and shared ownership of the platform's hardest projects; one was promoted within 14 months. <!-- id:hal-07 -->

### Quillon Health <!-- id:r-qui -->
Staff Software Engineer | Feb 2019 - Feb 2021
- Owned the scheduling platform's control plane in Go: 30 microservices on Kubernetes across AWS and GCP at 99.98% availability, serving 2,400 clinics through two regional failovers and a full cluster migration with no customer-visible downtime. <!-- id:qui-01 -->
- Designed the appointment event backbone on Kafka carrying 150 million messages a day with exactly-once handling for billing events, replacing nightly batch files between 11 services and cutting the delay before a booking is billable from hours to seconds. <!-- id:qui-02 -->
- Defined SLOs for 18 services and built burn-rate alerting on Prometheus and Grafana, reducing customer-visible incidents 45% year over year and replacing 200 noisy threshold alerts with 30 that page only when a user is affected. <!-- id:qui-03 -->
- Managed infrastructure as code with Terraform across 2 clouds and 9 regions, with policy checks that block unsafe changes before apply and a module library that 14 teams use to provision their own services. <!-- id:qui-04 -->

### Brassline Pay <!-- id:r-bra -->
Senior Software Engineer | Jun 2017 - Jan 2019
- Built the ledger service in Java on PostgreSQL that records 20 million payment events a day with double-entry invariants checked on every write, and partitioned its largest tables so month-end reporting no longer slowed the write path. <!-- id:bra-01 -->
- Designed idempotent payment APIs (REST and gRPC) used by 250 merchants, removing duplicate charges during retries and cutting integration support tickets by a third through versioned contracts and a public sandbox. <!-- id:bra-02 -->
- Led a 4-engineer team that delivered PCI DSS scope reduction by tokenizing card data at the edge, working with security, legal and two external auditors to pass the assessment on the first attempt. <!-- id:bra-03 -->

### Pellucid Search <!-- id:r-pel -->
Senior Software Engineer | Aug 2013 - May 2017
- Built the indexing pipeline in Java that ingests 40 million documents a day into Elasticsearch with a freshness target of 5 minutes. <!-- id:pel-01 -->
- Designed the query-rewrite service in Python that raised click-through 11% in A/B tests. <!-- id:pel-02 -->
- Reduced index storage 35% by changing the shard layout and field mappings. <!-- id:pel-03 -->
- Built relevance evaluation tooling: 6,000 judged queries and nightly regression reports for ranking changes. <!-- id:pel-04 -->
- Operated the search clusters on AWS with autoscaling and blue-green index swaps. <!-- id:pel-05 -->
- Mentored 2 junior engineers through their first on-call rotations. <!-- id:pel-06 -->
- Migrated the crawl scheduler from cron jobs to Airflow and Spark, cutting the nightly run from 6 hours to 70 minutes. <!-- id:pel-07 -->
- Introduced contract tests between 12 services in CI/CD, catching breaking changes before deploy. <!-- id:pel-08 -->
- Built the synonym-mining job that proposes 1,500 query synonyms a week for editors to approve. <!-- id:pel-09 -->
- Wrote the on-call runbooks and trained 10 engineers joining the rotation. <!-- id:pel-10 -->
- Ran the Kafka clusters for the search group, including upgrades and partition rebalancing with no downtime. <!-- id:pel-11 -->

### Ostrava Games <!-- id:r-ost -->
Software Engineer | Jul 2011 - Jul 2013
- Built the matchmaking service in Java handling 80,000 concurrent players with a median wait of 12 seconds. <!-- id:ost-01 -->
- Wrote the telemetry pipeline that collects 2 billion game events a month for the analytics team. <!-- id:ost-02 -->
- Added rate limiting and abuse detection to the chat service, cutting spam reports 60%. <!-- id:ost-03 -->
- Built the leaderboard service on Redis sorted sets serving 5,000 requests per second. <!-- id:ost-04 -->
- Automated load tests that replay recorded traffic before each release. <!-- id:ost-05 -->
- Built the anti-cheat rules engine in Kotlin that evaluates 300 rules per match in under 15 ms. <!-- id:ost-06 -->
- Built the REST API behind the iOS and Android game clients, serving 9,000 requests per second at peak. <!-- id:ost-07 -->
- Cut build times from 25 minutes to 7 by caching dependencies in the CI/CD pipeline. <!-- id:ost-08 -->

### Tarn Systems <!-- id:r-tar -->
Software Engineer | Jun 2009 - Jun 2011
- Developed billing and invoicing features for a telecom back-office product in Java and SQL. <!-- id:tar-01 -->
- Wrote data migration scripts that moved 3 million customer records between schema versions with no data loss. <!-- id:tar-02 -->
- Built internal reporting dashboards used by 40 support agents. <!-- id:tar-03 -->
- Fixed and tested defects across the provisioning module as part of a 6-person team. <!-- id:tar-04 -->
- Wrote the nightly reconciliation job in SQL and Bash that flags invoice mismatches for the finance team. <!-- id:tar-05 -->
- Supported 2 customer go-lives on site, including data load and cut-over. <!-- id:tar-06 -->

## Projects

### Loomhand: an agent runtime <!-- id:p-loom -->
- Built a local agent runtime in Python in which a coordinator agent plans work and dispatches it to worker agents, each with its own tools and task brief, and merges what they return into one reviewed result. <!-- id:loom-01 -->
- Designed durable task state on SQLite so a long agent run survives a restart without repeating paid model calls: every step is checkpointed and a resumed run picks up from the last completed step. <!-- id:loom-02 -->
- Added human checkpoints: an agent stops and asks before any irreversible action, and every decision is logged with its reason so a reviewer can see afterwards why each step was taken. <!-- id:loom-03 -->
- Wrote 140 scripted evaluation cases that replay recorded agent sessions against each new prompt version. <!-- id:loom-04 -->
- Integrated the OpenAI and Anthropic APIs behind one adapter with per-run token and cost accounting. <!-- id:loom-05 -->
- Published the tool as open source; used weekly by about 30 developers. <!-- id:loom-06 -->
- Added a Model Context Protocol server so any MCP client can drive the runtime's tools under one permission model. <!-- id:loom-07 -->
- Sandboxed tool execution in containers with a per-tool allow-list, so a tool cannot reach the network or files it was not given. <!-- id:loom-08 -->

### Relaywright: multi-agent workflows on a durable runtime <!-- id:p-relay -->
- Built a multi-agent workflow engine in TypeScript in which a planner agent hands steps to specialist agents and merges their results, with an approval gate before any external write and a typed contract for every hand-off. <!-- id:relay-01 -->
- Designed the durable runtime underneath: every step is checkpointed to PostgreSQL, so a workflow resumes after a crash or a deploy from its last completed step without repeating the model calls it already paid for. <!-- id:relay-02 -->
- Added retries with idempotency keys and per-step timeouts, taking failed 40-step workflows from 7% to 0.4%. <!-- id:relay-03 -->
- Built a replay debugger that re-runs any stored workflow step by step with its prompts, tool calls and token counts. <!-- id:relay-04 -->
- Wrote an injection test suite of 120 cases that checks untrusted tool output cannot steer a planner agent, run on every change to a prompt or a tool contract. <!-- id:relay-05 -->
- Added per-workflow budgets and model routing that send easy steps to smaller models, cutting cost per run 45% while holding the pass rate of the evaluation suite. <!-- id:relay-06 -->
- Documented the engine with 12 worked examples; 600 stars and 25 outside contributors in 5 months. <!-- id:relay-07 -->
- Benchmarked 1,000 concurrent workflows on one node at a p95 step latency of 300 ms. <!-- id:relay-08 -->

### evalgrid <!-- id:p-eval -->
- Open-source Python library for regression-testing LLM prompts against labelled cases, with a CLI and a GitHub Actions reporter. <!-- id:eval-01 -->
- Supports rubric-based judging with a second model and a disagreement report for human review. <!-- id:eval-02 -->
- Runs 1,000 cases in under 4 minutes with request batching and a local response cache. <!-- id:eval-03 -->
- Used by 3 teams at Halcyon Freight before the internal harness replaced it. <!-- id:eval-04 -->
- Added dataset versioning so every score names the exact cases and prompt it was measured on. <!-- id:eval-05 -->

### pgtail-queue <!-- id:p-pgq -->
- A job queue on PostgreSQL in Go using SKIP LOCKED, with at-least-once delivery, retries and a dead-letter table. <!-- id:pgq-01 -->
- Benchmarked at 9,000 jobs per second on a single node. <!-- id:pgq-02 -->
- Ships Prometheus metrics and a Grafana dashboard. <!-- id:pgq-03 -->
- Added a Helm chart and a Terraform module so a team can deploy it to Kubernetes in one step. <!-- id:pgq-04 -->

### trailmark <!-- id:p-trail -->
- A tracing viewer in TypeScript and React that renders OpenTelemetry traces of agent runs as a timeline of prompts and tool calls. <!-- id:trail-01 -->
- Loads a 50 MB trace in under 2 seconds using incremental parsing in a web worker. <!-- id:trail-02 -->
- Exports a redacted trace for bug reports, with secrets and personal data removed. <!-- id:trail-03 -->
- Added a cost view that totals tokens and spend per agent, per tool and per run. <!-- id:trail-04 -->

### hostglance <!-- id:p-host -->
- A terminal dashboard in Go for home-lab machines: CPU, disk and service health on one screen. <!-- id:host-01 -->
- Reads metrics over SSH with nothing installed on the target machines. <!-- id:host-02 -->
- Packaged for Homebrew and apt; about 200 installs. <!-- id:host-03 -->

## Skills

- Python/Go/TypeScript · Java/Kotlin · SQL/Bash <!-- id:s-lang -->
- OpenAI/Anthropic APIs · LLM agents/multi-agent systems/tool calling · RAG/pgvector/embeddings · LLM evaluation/prompt caching · Model Context Protocol (MCP) <!-- id:s-ai -->
- PostgreSQL/Redis/Kafka · gRPC/REST · Spark/Airflow/Elasticsearch <!-- id:s-data -->
- Kubernetes/Docker/Helm · Terraform/AWS/GCP · Prometheus/Grafana/OpenTelemetry <!-- id:s-infra -->

## Education

### Varnholt Technical University <!-- id:e-var -->
B.S. Computer Science | 2005 - 2009

## Other

- Speaker, "Regression-testing LLM agents before release", Northlake AI Engineering Summit 2025 (400 attendees). <!-- id:o-talk -->
- AWS Certified Solutions Architect - Professional (2022). <!-- id:o-aws -->
- HashiCorp Certified: Terraform Associate (2022). <!-- id:o-tf -->
- Volunteer mentor for 6 early-career engineers through a local coding non-profit since 2020. <!-- id:o-mentor -->
- Organizer of a 300-member local Go meetup, 2018 - 2022. <!-- id:o-meetup -->
