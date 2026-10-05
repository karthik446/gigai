## Summary

- Staff engineer with 17 years of experience building backend platforms and, since 2023, LLM agent systems that run in production: led a 7-engineer AI platform team serving 60 internal teams.
- Distributed-systems background in Go and Python on Kubernetes, PostgreSQL and Kafka, operated end to end with SLOs and on-call ownership.

## Experience

### Halcyon Freight
Staff Engineer, AI Platform | Mar 2023 - Present
- Led a 7-engineer AI platform team and owned its roadmap end to end, from design review to the on-call rotation.
- Mentored 3 senior engineers to staff-level scope; one was promoted within 14 months.
- Built guardrails that keep untrusted document text from steering agents: fenced inputs, tool allow-lists and an injection test suite of 180 cases run in CI/CD.
- Architected the agent runtime that executes 400,000 tool-calling LLM tasks per day for 60 internal teams, with typed tool contracts, retries and per-team budgets.
- Shipped a Model Context Protocol gateway in TypeScript that exposes 45 internal services to agents behind one audited permission model.

### Quillon Health
Staff Software Engineer | Jan 2020 - Feb 2023
- Led the migration of 30 microservices from hand-rolled deploys to Helm charts released through GitHub Actions, taking deploy time from 50 minutes to 8.
- Built the gRPC service template and code generator used by 14 teams to start new services with tracing, auth and health checks.
- Owned the scheduling platform's control plane in Go: 30 microservices on Kubernetes across AWS and GCP at 99.98% availability.
- Led incident response as on-call lead for the platform group; wrote the postmortem process adopted by 6 teams.
- Designed the appointment event backbone on Kafka carrying 150 million messages a day with exactly-once handling for billing events.
- Defined SLOs for 18 services and built burn-rate alerting on Prometheus and Grafana, reducing customer-visible incidents 45% year over year.

### Brassline Pay
Senior Software Engineer | Jun 2016 - Dec 2019
- Led a 4-engineer team that delivered PCI DSS scope reduction by tokenizing card data at the edge.
- Designed idempotent payment APIs (REST and gRPC) used by 250 merchants, removing duplicate charges during retries.
- Built the fraud-rules engine in Kotlin that evaluates 300 rules per transaction in under 15 ms.
- Introduced contract tests between 12 services in CI/CD, catching breaking changes before deploy.
- Built the ledger service in Java on PostgreSQL that records 20 million payment events a day with double-entry invariants checked on every write.
- Migrated the settlement batch from cron jobs to Airflow and Spark, cutting the nightly run from 6 hours to 70 minutes.

### Pellucid Search
Senior Software Engineer | Aug 2013 - May 2016
- Built the indexing pipeline in Java that ingests 40 million documents a day into Elasticsearch with a freshness target of 5 minutes.
- Built relevance evaluation tooling: 6,000 judged queries and nightly regression reports for ranking changes.

## Projects

### Loomhand: a personal multi-agent workbench
- Built a local multi-agent workbench in Python in which a coordinator agent plans work and dispatches it to worker agents, each with its own tools and task brief.
- Wrote 140 scripted evaluation cases that replay recorded agent sessions against each new prompt version.
- Published the tool as open source; used weekly by about 30 developers.

### evalgrid
- Open-source Python library for regression-testing LLM prompts against labelled cases, with a CLI and a GitHub Actions reporter.
- Runs 1,000 cases in under 4 minutes with request batching and a local response cache.

### pgtail-queue
- A job queue on PostgreSQL in Go using SKIP LOCKED, with at-least-once delivery, retries and a dead-letter table.
- Benchmarked at 9,000 jobs per second on a single node.

## Skills

- Python/Go/TypeScript · Java/Kotlin · SQL/Bash <!-- src: s-7f4452 -->
- OpenAI/Anthropic APIs · LLM agents/multi-agent systems/tool calling · RAG/pgvector/embeddings · LLM evaluation/prompt caching · Model Context Protocol (MCP) <!-- src: s-5481b9 -->
- PostgreSQL/Redis/Kafka · gRPC/REST · Spark/Airflow/Elasticsearch <!-- src: s-1c2669 -->
- Kubernetes/Docker/Helm · Terraform/AWS/GCP · Prometheus/Grafana/OpenTelemetry <!-- src: s-770dfa -->

## Education

### Varnholt Technical University
B.S. Computer Science | 2005 - 2009
