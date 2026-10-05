## Summary

- Staff engineer with 17 years of experience building backend platforms and, since 2023, LLM agent systems that run in production: led a 7-engineer AI platform team serving 60 internal teams.
- Distributed-systems background in Go and Python on Kubernetes, PostgreSQL and Kafka, operated end to end with SLOs and on-call ownership.

## Experience

### Halcyon Freight
Staff Engineer, AI Platform | Mar 2023 - Present
- Architected the agent runtime that executes 400,000 tool-calling LLM tasks per day for 60 internal teams, with typed tool contracts, retries and per-team budgets.
- Built a multi-agent dispatch workflow in which a planner agent hands shipment exceptions to 5 specialist agents, with an approval gate before any customer-facing write.
- Ran the durable agent workflows on Temporal for about 2 years. <!-- src: A tooling:temporal -->
- Built an evaluation harness in Python that scores every prompt change against 2,200 labelled cases before release, cutting regressions that reached production from 9 per quarter to 1.
- Built guardrails that keep untrusted document text from steering agents: fenced inputs, tool allow-lists and an injection test suite of 180 cases run in CI/CD.
- Designed a retrieval pipeline on PostgreSQL with pgvector serving 12 million embedded passages at a p95 of 140 ms.
- Cut LLM spend 38% through prompt caching and routing easy requests to smaller models, across the OpenAI and Anthropic APIs.
- Shipped a Model Context Protocol gateway in TypeScript that exposes 45 internal services to agents behind one audited permission model.
- Created the tracing layer for agent runs on OpenTelemetry, so any production answer can be replayed step by step with its prompts, tool calls and token counts.
- Led a 7-engineer AI platform team and owned its roadmap end to end, from design review to the on-call rotation.
- Mentored 3 senior engineers to staff-level scope; one was promoted within 14 months.

### Quillon Health
Staff Software Engineer | Jan 2020 - Feb 2023
- Owned the scheduling platform's control plane in Go: 30 microservices on Kubernetes across AWS and GCP at 99.98% availability.
- Rebuilt the patient-search API on PostgreSQL with read replicas and Redis caching, cutting p99 latency from 900 ms to 110 ms.
- Defined SLOs for 18 services and built burn-rate alerting on Prometheus and Grafana, reducing customer-visible incidents 45% year over year.
- Led the migration of 30 microservices from hand-rolled deploys to Helm charts released through GitHub Actions, taking deploy time from 50 minutes to 8.

### Brassline Pay
Senior Software Engineer | Jun 2016 - Dec 2019
- Built the ledger service in Java on PostgreSQL that records 20 million payment events a day with double-entry invariants checked on every write.
- Designed idempotent payment APIs (REST and gRPC) used by 250 merchants, removing duplicate charges during retries.

### Pellucid Search
Senior Software Engineer | Aug 2013 - May 2016
- Built relevance evaluation tooling: 6,000 judged queries and nightly regression reports for ranking changes.

## Projects

### Loomhand: a personal multi-agent workbench
- Built a local multi-agent workbench in Python in which a coordinator agent plans work and dispatches it to worker agents, each with its own tools and task brief.
- Designed durable task state on SQLite so a long agent run survives a restart without repeating paid model calls.
- Added human checkpoints: an agent stops and asks before any irreversible action, and every decision is logged with its reason.
- Wrote 140 scripted evaluation cases that replay recorded agent sessions against each new prompt version.
- Integrated the OpenAI and Anthropic APIs behind one adapter with per-run token and cost accounting.

### evalgrid
- Open-source Python library for regression-testing LLM prompts against labelled cases, with a CLI and a GitHub Actions reporter.
- Runs 1,000 cases in under 4 minutes with request batching and a local response cache.

## Skills

- Python/Go/TypeScript · Java/Kotlin · SQL/Bash
- OpenAI/Anthropic APIs · LLM agents/multi-agent systems/tool calling · RAG/pgvector/embeddings · LLM evaluation/prompt caching · Model Context Protocol (MCP) · Temporal · Fine-tuning open models (PyTorch) <!-- src: A tooling:temporal, A ml:fine_tuning -->
- PostgreSQL/Redis/Kafka · gRPC/REST · Spark/Airflow/Elasticsearch
- Kubernetes/Docker/Helm · Terraform/AWS/GCP · Prometheus/Grafana/OpenTelemetry

## Education

### Varnholt Technical University
B.S. Computer Science | 2005 - 2009
