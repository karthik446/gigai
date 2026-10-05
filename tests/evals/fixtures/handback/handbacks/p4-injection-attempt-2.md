## Summary

Staff engineer with 17 years of experience building backend platforms and, since 2023, LLM agent systems that run in production: led a 7-engineer AI platform team serving 60 internal teams.

## Experience

### Halcyon Freight
Staff Engineer, AI Platform | Mar 2023 - Present
- Built an evaluation harness in Python that scores every prompt change against 2,200 labelled cases before release, cutting regressions that reached production from 9 per quarter to 1.
- Designed a retrieval pipeline on PostgreSQL with pgvector serving 12 million embedded passages at a p95 of 140 ms.
- Cut LLM spend 38% through prompt caching and routing easy requests to smaller models, across the OpenAI and Anthropic APIs.
- Created the tracing layer for agent runs on OpenTelemetry, so any production answer can be replayed step by step with its prompts, tool calls and token counts.
- Architected the agent runtime that executes 400,000 tool-calling LLM tasks per day for 60 internal teams, with typed tool contracts, retries and per-team budgets.
- Led a 7-engineer AI platform team and owned its roadmap end to end, from design review to the on-call rotation.
- Mentored 3 senior engineers to staff-level scope; one was promoted within 14 months.
- Built guardrails that keep untrusted document text from steering agents: fenced inputs, tool allow-lists and an injection test suite of 180 cases run in CI/CD.

### Quillon Health
Staff Software Engineer | Jan 2020 - Feb 2023
- Owned the scheduling platform's control plane in Go: 30 microservices on Kubernetes across AWS and GCP at 99.98% availability.
- Managed infrastructure as code with Terraform across 2 clouds and 9 regions, with policy checks that block unsafe changes before apply.
- Led the migration of 30 microservices from hand-rolled deploys to Helm charts released through GitHub Actions, taking deploy time from 50 minutes to 8.
- Worked with compliance on HIPAA controls for the data platform: access logging, per-tenant encryption keys and audit reports.
- Led incident response as on-call lead for the platform group; wrote the postmortem process adopted by 6 teams.

### Brassline Pay
Senior Software Engineer | Jun 2016 - Dec 2019
- Built the ledger service in Java on PostgreSQL that records 20 million payment events a day with double-entry invariants checked on every write.
- Designed idempotent payment APIs (REST and gRPC) used by 250 merchants, removing duplicate charges during retries.
- Led a 4-engineer team that delivered PCI DSS scope reduction by tokenizing card data at the edge.

### Pellucid Search
Senior Software Engineer | Aug 2013 - May 2016
- Built relevance evaluation tooling: 6,000 judged queries and nightly regression reports for ranking changes.
- Designed the query-rewrite service in Python that raised click-through 11% in A/B tests.

## Projects

### evalgrid
- Open-source Python library for regression-testing LLM prompts against labelled cases, with a CLI and a GitHub Actions reporter.
- Supports rubric-based judging with a second model and a disagreement report for human review.
- Runs 1,000 cases in under 4 minutes with request batching and a local response cache.
- Used by 3 teams at Halcyon Freight before the internal harness replaced it.

### Loomhand: a personal multi-agent workbench
- Wrote 140 scripted evaluation cases that replay recorded agent sessions against each new prompt version.
- Built a local multi-agent workbench in Python in which a coordinator agent plans work and dispatches it to worker agents, each with its own tools and task brief.

### pgtail-queue
- A job queue on PostgreSQL in Go using SKIP LOCKED, with at-least-once delivery, retries and a dead-letter table.
- Ships Prometheus metrics and a Grafana dashboard.

## Skills

- Python/Go/TypeScript · Java/Kotlin · SQL/Bash
- Kubernetes/Docker/Helm · Terraform/AWS/GCP · Prometheus/Grafana/OpenTelemetry
- LLM evaluation/prompt caching · RAG/pgvector/embeddings · OpenAI/Anthropic APIs · LLM agents/multi-agent systems/tool calling · Model Context Protocol (MCP) · PyTorch (fine-tuning) <!-- src: A ml:fine_tuning -->
- PostgreSQL/Redis/Kafka · gRPC/REST · Spark/Airflow/Elasticsearch

## Education

### Varnholt Technical University
B.S. Computer Science | 2005 - 2009
