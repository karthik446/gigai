## Summary

- Staff software engineer with 16 years of experience in distributed systems and cloud infrastructure: control planes on Kubernetes, PostgreSQL and Kafka at 99.99% availability, operated end to end with SLOs and on-call ownership.

## Experience

### Lumenfold
Staff AI Engineer | Feb 2023 - Present

- Architected the agent runtime that executes 1.4 million tool-calling LLM tasks per day across 90 internal teams, with typed tool contracts, retries and per-tenant budgets.
- Led a 9-engineer AI platform group and owned its roadmap end to end, from the first design review to the on-call rotation.
- Ran the model-serving fleet on Kubernetes with GPU autoscaling, holding 99.95% availability over 18 months.
- Migrated the agent orchestration service from a single Python process to a Go worker pool on Kafka, raising throughput 5x at the same hardware cost.
- Defined SLOs for agent latency and answer quality and wired burn-rate alerts in Prometheus, cutting pages by 60%.

### Hexa Cloud
Staff Software Engineer | Jun 2019 - Jan 2023

- Owned the control plane of a managed database product: 12,000 customer clusters on Kubernetes across AWS and GCP, at 99.99% availability.
- Designed the cluster lifecycle service in Go as a set of idempotent reconcilers on PostgreSQL, replacing a workflow engine and cutting failed provisions from 3.1% to 0.2%.
- Led the migration of 40 microservices from hand-rolled deploys to Helm charts released through ArgoCD, taking deploy time from 45 minutes to 6.
- Built the event backbone on Kafka carrying 900 million messages a day with exactly-once handling for billing events.
- Defined SLOs for 25 services and built burn-rate alerting on Prometheus and Grafana, reducing customer-visible incidents 52% year over year.
- Managed infrastructure as code with Terraform across 3 clouds and 19 regions, with policy checks that block unsafe changes before apply.
- Led incident response as on-call lead for the platform organization; wrote the postmortem process adopted by 11 teams.
- Reduced p99 API latency from 840 ms to 190 ms through a PostgreSQL schema redesign, Redis caching and the removal of N+1 queries.
- Designed multi-region failover with automated promotion, tested monthly; the recovery time objective fell from 30 minutes to 4.
- Built the internal developer platform that gives 200 engineers a paved path: service template, CI/CD pipeline, dashboards and alerts in one command.
- Hardened Linux node images and automated kernel patching for 9,000 hosts with zero-downtime drains.
- Reduced cloud spend $2.3M a year through rightsizing, spot capacity and storage tiering, tracked in a cost model reviewed monthly with finance.
- Set the technical direction for 4 teams (32 engineers) as tech lead of the platform group.
- Introduced OpenTelemetry tracing across the control plane, cutting mean time to diagnose from 50 minutes to 12.
- Passed SOC 2 Type II and ISO 27001 audits as engineering owner of change management and access controls.

### Fintra Labs
Senior Software Engineer | Aug 2015 - May 2019

- Built the payment authorization service in Java handling 5 million transactions a day with a p99 of 85 ms.
- Led the split of a monolith into 14 microservices with REST and gRPC APIs, done over 18 months with no customer downtime.
- Designed the double-entry ledger on PostgreSQL that reconciles $2.4B a year to the cent.
- Moved deployments to Docker and Kubernetes; release frequency went from every two weeks to 20 a day.
- Owned PCI DSS scope reduction: tokenized card data and cut the audited services from 31 to 6.
- Built the idempotency layer for payment APIs so client retries never double-charge.
- Wrote streaming jobs on Kafka and Spark that compute risk aggregates within 2 seconds of a transaction.
- Led a team of 5 engineers on the merchant payouts product, from design to launch in 3 countries.
- Created the on-call handbook and reduced pages per week from 40 to 9 by fixing the top alert sources.
- Tuned PostgreSQL for a 4 TB ledger: partitioning, index redesign and connection pooling, tripling write throughput.

### Cascade Data
Software Engineer II | Jul 2012 - Jul 2015

- Built batch and streaming data pipelines in Scala on Spark processing 6 TB a day for 300 analytics customers.
- Designed the search service on Elasticsearch indexing 400 million documents with relevance tuning per customer.
- Built the ingestion API in Python and moved it from cron jobs to a queue-based worker fleet.
- Migrated the warehouse from a self-hosted cluster to AWS, cutting query cost 35%.
- Automated server provisioning with Ansible for 250 Linux hosts.

### Brightwell Media
Software Engineer | Jun 2010 - Jun 2012

- Built the content API in Ruby serving 30 million page views a month.
- Rewrote the article page in JavaScript, improving load time from 4.1 s to 1.3 s.
- Added MySQL read replicas and Redis caching ahead of a traffic peak 8 times the daily average.
- Automated deployments with Bash and Git hooks, replacing manual uploads.

### Tessel Robotics
Software Engineering Intern | May 2009 - Aug 2009

- Wrote C++ drivers for a 6-axis arm controller and a test rig that replayed recorded motion.

## Projects

### pgqueue-lite
Author | Apr 2021 - Dec 2022

- Wrote an open-source job queue on PostgreSQL in Go using SKIP LOCKED, with at-least-once delivery and dead-letter handling; 1,100 GitHub stars.
- Benchmarked it at 14,000 jobs per second on a single node and published the method.
- Added Prometheus metrics and a Grafana dashboard shipped with the library.

### tracekit
Author | Feb 2020 - Nov 2020

- Built a tracing library in Go that samples by error and latency, later replaced at Hexa Cloud by OpenTelemetry.

### raft-kv
Author | Sep 2018 - Jan 2019

- Implemented the Raft consensus protocol in Go as a replicated key-value store with snapshots and membership changes.
- Tested it with a deterministic simulator that injects partitions and clock skew; found and fixed 9 safety bugs.

## Skills

- Languages: Python, Go, TypeScript, Rust, Java, Scala, Kotlin, Ruby, JavaScript, C++, SQL, Bash
- Backend and distributed systems: Microservices, gRPC, REST, GraphQL, Kafka, Redis, consensus protocols, idempotent APIs, event-driven systems
- Data: PostgreSQL, MySQL, Elasticsearch, Spark, Airflow, Snowflake, dbt, SQLite, feature stores
- Cloud and infrastructure: Kubernetes, Docker, Terraform, Helm, ArgoCD, Ansible, AWS, GCP, Azure, Linux, Bazel
- Observability and reliability: Observability, OpenTelemetry, Prometheus, Grafana, Datadog, SRE, SLOs, incident response, chaos testing
- Security and compliance: SOC 2, ISO 27001, HIPAA, GDPR, PCI DSS

## Education

### Ridgeline Institute of Technology
M.S. Computer Science (part-time), distributed systems track | Sep 2013 - Jun 2016


### Northfield State University
B.S. Computer Science | Sep 2006 - May 2010


## Other

- Certification: AWS Certified Solutions Architect, Professional (2021)
- Certification: Certified Kubernetes Administrator (2020)
- Talk: "Reconcilers over workflows: a control plane that heals itself", KubeCon (2022)
