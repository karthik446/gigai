## Summary

Distributed-systems background in Go and Python on Kubernetes, PostgreSQL and Kafka, operated end to end with SLOs and on-call ownership.
Staff engineer with 17 years of experience building backend platforms and, since 2023, LLM agent systems that run in production: led a 7-engineer AI platform team serving 60 internal teams.

## Experience

### Halcyon Freight
Staff Engineer, AI Platform | Mar 2023 - Present
- Led a 7-engineer AI platform team and owned its roadmap end to end, from design review to the on-call rotation.
- Created the tracing layer for agent runs on OpenTelemetry, so any production answer can be replayed step by step with its prompts, tool calls and token counts.
- Designed a retrieval pipeline on PostgreSQL with pgvector serving 12 million embedded passages at a p95 of 140 ms.
- Mentored 3 senior engineers to staff-level scope; one was promoted within 14 months.

### Quillon Health
Staff Software Engineer | Jan 2020 - Feb 2023
- Owned the scheduling platform's control plane in Go: 30 microservices on Kubernetes across AWS and GCP at 99.98% availability.
- Designed the appointment event backbone on Kafka carrying 150 million messages a day with exactly-once handling for billing events.
- Defined SLOs for 18 services and built burn-rate alerting on Prometheus and Grafana, reducing customer-visible incidents 45% year over year.
- Managed infrastructure as code with Terraform across 2 clouds and 9 regions, with policy checks that block unsafe changes before apply.
- Led the migration of 30 microservices from hand-rolled deploys to Helm charts released through GitHub Actions, taking deploy time from 50 minutes to 8.
- Built the gRPC service template and code generator used by 14 teams to start new services with tracing, auth and health checks.
- Rebuilt the patient-search API on PostgreSQL with read replicas and Redis caching, cutting p99 latency from 900 ms to 110 ms.
- Led incident response as on-call lead for the platform group; wrote the postmortem process adopted by 6 teams.

### Brassline Pay
Senior Software Engineer | Jun 2016 - Dec 2019
- Ran the Kafka clusters for the payments group, including upgrades and partition rebalancing with no downtime.
- Designed idempotent payment APIs (REST and gRPC) used by 250 merchants, removing duplicate charges during retries.
- Built the ledger service in Java on PostgreSQL that records 20 million payment events a day with double-entry invariants checked on every write.

### Pellucid Search
Senior Software Engineer | Aug 2013 - May 2016
- Operated the search clusters on AWS with autoscaling and blue-green index swaps.

## Projects

### pgtail-queue
- A job queue on PostgreSQL in Go using SKIP LOCKED, with at-least-once delivery, retries and a dead-letter table.
- Benchmarked at 9,000 jobs per second on a single node.
- Ships Prometheus metrics and a Grafana dashboard.

## Skills

- Kubernetes/Docker/Helm · Terraform/AWS/GCP · Prometheus/Grafana/OpenTelemetry <!-- src: s-770dfa -->
- PostgreSQL/Redis/Kafka · gRPC/REST · Spark/Airflow/Elasticsearch <!-- src: s-1c2669 -->
- Python/Go/TypeScript · Java/Kotlin · SQL/Bash <!-- src: s-7f4452 -->
- OpenAI/Anthropic APIs · LLM agents/multi-agent systems/tool calling · RAG/pgvector/embeddings · LLM evaluation/prompt caching · Model Context Protocol (MCP) <!-- src: s-5481b9 -->

## Education

### Varnholt Technical University
B.S. Computer Science | 2005 - 2009
