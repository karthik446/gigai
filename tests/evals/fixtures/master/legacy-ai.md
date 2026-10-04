## Summary

- Staff engineer with 16 years of experience who builds LLM agent platforms and the evaluation, retrieval and cost controls that make them dependable in production; led a 9-engineer AI platform group serving 140 internal teams.

## Experience

### Lumenfold
Staff AI Engineer | Feb 2023 - Present

- Architected the agent runtime that executes 2.1 million tool-calling LLM tasks per day across 140 internal teams, with typed tool contracts, retries and per-tenant budgets.
- Led a 9-engineer AI platform group and owned its roadmap end to end, from the first design review to the on-call rotation.
- Built an evaluation harness in Python that scores every prompt change against 3,400 labelled cases before release, cutting regressions that reached production from 11 per quarter to 1.
- Designed a retrieval pipeline on PostgreSQL with pgvector serving 38 million embedded passages at a p95 of 120 ms, replacing a hosted vector database and saving $410K a year.
- Cut LLM inference spend 43% through prompt caching, response reuse and routing easy requests to smaller models, with a cost dashboard in Grafana per team and per feature.
- Introduced structured output validation with one retry and a fed-back error, taking malformed model responses from 6.2% to 0.3% of calls.
- Built guardrails that keep untrusted document text from steering agents: fenced inputs, tool allow-lists and an injection test suite of 260 cases run in CI/CD.
- Fine-tuned a 7B-parameter open model with PyTorch for ticket routing, reaching 94% accuracy at one eighth of the hosted model's cost.

### Hexa Cloud
Staff Software Engineer | Jun 2019 - Jan 2023

- Owned the control plane for a managed database product running 12,000 customer clusters on Kubernetes across AWS and GCP at 99.99% availability.
- Designed the cluster lifecycle service in Go as a set of idempotent reconcilers on PostgreSQL, replacing a workflow engine and cutting failed provisions from 3.1% to 0.2%.
- Built the event backbone on Kafka carrying 900 million messages a day with exactly-once handling for billing events.
- Cut p99 API latency from 840 ms to 190 ms by redesigning the PostgreSQL schema, adding Redis caching and removing N+1 queries.

### Fintra Labs
Senior Software Engineer | Aug 2015 - May 2019

- Built the payment authorization service in Java handling 5 million transactions a day with a p99 of 85 ms.
- Built fraud features and a scoring service with the data science team; the gradient-boosted model blocked $31M of fraud in its first year.

### Cascade Data
Software Engineer II | Jul 2012 - Jul 2015

- Wrote natural-language pipelines for document classification with scikit-learn, reaching 91% precision on 40 categories.

## Projects

### Taskloom: a personal multi-agent workbench
Creator and maintainer | Mar 2025 - Present

- Built an open-source workbench in Python where several LLM agents plan, execute and review work under contracts a user can read and approve.
- Designed a write-once journal on Git so every agent action is replayable and no record is ever overwritten.
- Wrote a deterministic validator that rejects any model output citing a source it was not shown; the fabrication eval holds at 0 hard failures over 26 labelled cases.

### evalbench
Author | Jan 2024 - Present

- Wrote an open-source command-line tool in Python for LLM evaluation with labelled cases, a hard call budget and before/after reports; 2,300 GitHub stars.

## Skills

- Languages: Python, Go, TypeScript, Rust, Java, Scala, Kotlin, Ruby, JavaScript, C++, SQL, Bash
- AI and machine learning: LLM, agents, retrieval-augmented generation, prompt caching, LLM evaluation, fine-tuning, PyTorch, scikit-learn, Machine Learning, Model Context Protocol, embeddings, pgvector
- Cloud and infrastructure: Kubernetes, Docker, Terraform, Helm, ArgoCD, Ansible, AWS, GCP, Azure, Linux, Bazel

## Education

### Ridgeline Institute of Technology
M.S. Computer Science (part-time), distributed systems track | Sep 2013 - Jun 2016


### Northfield State University
B.S. Computer Science | Sep 2006 - May 2010


## Other

- Talk: "Evaluating LLM agents before your users do", keynote at a 1,200-person AI engineering conference (2025)
