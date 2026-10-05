<!-- gigai-master:1  SYNTHETIC (the agent-tailoring spike fixture): an invented person, invented companies. No name, no contact data. -->

## Summary

- Staff engineer with 17 years of experience building backend platforms and, since 2023, LLM agent systems that run in production: led a 7-engineer AI platform team serving 60 internal teams.
- Distributed-systems background in Go and Python on Kubernetes, PostgreSQL and Kafka, operated end to end with SLOs and on-call ownership.

## Experience

### Halcyon Freight
Staff Engineer, AI Platform | Mar 2023 - Present
- Architected the agent runtime that executes 400,000 tool-calling LLM tasks per day for 60 internal teams, with typed tool contracts, retries and per-team budgets.
- Led a 7-engineer AI platform team and owned its roadmap end to end, from design review to the on-call rotation.
- Built an evaluation harness in Python that scores every prompt change against 2,200 labelled cases before release, cutting regressions that reached production from 9 per quarter to 1.
- Designed a retrieval pipeline on PostgreSQL with pgvector serving 12 million embedded passages at a p95 of 140 ms.
- Built a multi-agent dispatch workflow in which a planner agent hands shipment exceptions to 5 specialist agents, with an approval gate before any customer-facing write.
- Cut LLM spend 38% through prompt caching and routing easy requests to smaller models, across the OpenAI and Anthropic APIs.
- Shipped a Model Context Protocol gateway in TypeScript that exposes 45 internal services to agents behind one audited permission model.
- Built guardrails that keep untrusted document text from steering agents: fenced inputs, tool allow-lists and an injection test suite of 180 cases run in CI/CD.
- Created the tracing layer for agent runs on OpenTelemetry, so any production answer can be replayed step by step with its prompts, tool calls and token counts.
- Mentored 3 senior engineers to staff-level scope; one was promoted within 14 months.

### Quillon Health
Staff Software Engineer | Jan 2020 - Feb 2023
- Owned the scheduling platform's control plane in Go: 30 microservices on Kubernetes across AWS and GCP at 99.98% availability.
- Designed the appointment event backbone on Kafka carrying 150 million messages a day with exactly-once handling for billing events.
- Led the migration of 30 microservices from hand-rolled deploys to Helm charts released through GitHub Actions, taking deploy time from 50 minutes to 8.
- Defined SLOs for 18 services and built burn-rate alerting on Prometheus and Grafana, reducing customer-visible incidents 45% year over year.
- Managed infrastructure as code with Terraform across 2 clouds and 9 regions, with policy checks that block unsafe changes before apply.
- Rebuilt the patient-search API on PostgreSQL with read replicas and Redis caching, cutting p99 latency from 900 ms to 110 ms.
- Led incident response as on-call lead for the platform group; wrote the postmortem process adopted by 6 teams.
- Worked with compliance on HIPAA controls for the data platform: access logging, per-tenant encryption keys and audit reports.
- Built the gRPC service template and code generator used by 14 teams to start new services with tracing, auth and health checks.

### Brassline Pay
Senior Software Engineer | Jun 2016 - Dec 2019
- Built the ledger service in Java on PostgreSQL that records 20 million payment events a day with double-entry invariants checked on every write.
- Designed idempotent payment APIs (REST and gRPC) used by 250 merchants, removing duplicate charges during retries.
- Migrated the settlement batch from cron jobs to Airflow and Spark, cutting the nightly run from 6 hours to 70 minutes.
- Led a 4-engineer team that delivered PCI DSS scope reduction by tokenizing card data at the edge.
- Introduced contract tests between 12 services in CI/CD, catching breaking changes before deploy.
- Built the fraud-rules engine in Kotlin that evaluates 300 rules per transaction in under 15 ms.
- Ran the Kafka clusters for the payments group, including upgrades and partition rebalancing with no downtime.
- Wrote the on-call runbooks and trained 10 engineers joining the rotation.

### Pellucid Search
Senior Software Engineer | Aug 2013 - May 2016
- Built the indexing pipeline in Java that ingests 40 million documents a day into Elasticsearch with a freshness target of 5 minutes.
- Designed the query-rewrite service in Python that raised click-through 11% in A/B tests.
- Reduced index storage 35% by changing the shard layout and field mappings.
- Built relevance evaluation tooling: 6,000 judged queries and nightly regression reports for ranking changes.
- Operated the search clusters on AWS with autoscaling and blue-green index swaps.
- Mentored 2 junior engineers through their first on-call rotations.

### Ostrava Games
Software Engineer | Jul 2011 - Jul 2013
- Built the matchmaking service in Java handling 80,000 concurrent players with a median wait of 12 seconds.
- Wrote the telemetry pipeline that collects 2 billion game events a month for the analytics team.
- Added rate limiting and abuse detection to the chat service, cutting spam reports 60%.
- Built the leaderboard service on Redis sorted sets serving 5,000 requests per second.
- Automated load tests that replay recorded traffic before each release.

### Tarn Systems
Software Engineer | Jun 2009 - Jun 2011
- Developed billing and invoicing features for a telecom back-office product in Java and SQL.
- Wrote data migration scripts that moved 3 million customer records between schema versions with no data loss.
- Built internal reporting dashboards used by 40 support agents.
- Fixed and tested defects across the provisioning module as part of a 6-person team.

## Projects

### Loomhand: a personal multi-agent workbench
- Built a local multi-agent workbench in Python in which a coordinator agent plans work and dispatches it to worker agents, each with its own tools and task brief.
- Designed durable task state on SQLite so a long agent run survives a restart without repeating paid model calls.
- Added human checkpoints: an agent stops and asks before any irreversible action, and every decision is logged with its reason.
- Wrote 140 scripted evaluation cases that replay recorded agent sessions against each new prompt version.
- Integrated the OpenAI and Anthropic APIs behind one adapter with per-run token and cost accounting.
- Published the tool as open source; used weekly by about 30 developers.

### evalgrid
- Open-source Python library for regression-testing LLM prompts against labelled cases, with a CLI and a GitHub Actions reporter.
- Supports rubric-based judging with a second model and a disagreement report for human review.
- Runs 1,000 cases in under 4 minutes with request batching and a local response cache.
- Used by 3 teams at Halcyon Freight before the internal harness replaced it.

### pgtail-queue
- A job queue on PostgreSQL in Go using SKIP LOCKED, with at-least-once delivery, retries and a dead-letter table.
- Benchmarked at 9,000 jobs per second on a single node.
- Ships Prometheus metrics and a Grafana dashboard.

### trailmark
- A tracing viewer in TypeScript and React that renders OpenTelemetry traces of agent runs as a timeline of prompts and tool calls.
- Loads a 50 MB trace in under 2 seconds using incremental parsing in a web worker.
- Exports a redacted trace for bug reports, with secrets and personal data removed.

## Skills

- Python/Go/TypeScript · Java/Kotlin · SQL/Bash
- OpenAI/Anthropic APIs · LLM agents/multi-agent systems/tool calling · RAG/pgvector/embeddings · LLM evaluation/prompt caching · Model Context Protocol (MCP)
- PostgreSQL/Redis/Kafka · gRPC/REST · Spark/Airflow/Elasticsearch
- Kubernetes/Docker/Helm · Terraform/AWS/GCP · Prometheus/Grafana/OpenTelemetry

## Education

### Varnholt Technical University
B.S. Computer Science | 2005 - 2009
