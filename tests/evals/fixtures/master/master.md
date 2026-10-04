<!-- gigai-master:1  SYNTHETIC (spike fixture): an invented person, invented companies. No name, no contact data. -->

## Summary

- Staff engineer with 16 years of experience who builds LLM agent platforms and the evaluation, retrieval and cost controls that make them dependable in production; led a 9-engineer AI platform group serving 140 internal teams. <!-- id:sum-ai tags:ai,llm,agents,evals -->
- Staff software engineer with 16 years of experience in distributed systems and cloud infrastructure: control planes on Kubernetes, PostgreSQL and Kafka at 99.99% availability, operated end to end with SLOs and on-call ownership. <!-- id:sum-backend tags:backend,distributed,infrastructure -->
- Staff engineer across ML platform and data infrastructure: feature pipelines on Spark and Airflow, model serving on Kubernetes and the compliance work (SOC 2, HIPAA) that lets regulated teams ship. <!-- id:sum-mlplat tags:ml-platform,data,compliance -->
- Technical leader who sets direction across multiple teams, mentors senior and staff engineers and turns ambiguous platform problems into shipped, measured systems. <!-- id:sum-lead tags:leadership -->

## Experience

### Lumenfold <!-- id:r-lum -->
Staff AI Engineer | Feb 2023 - Present
- Architected the agent runtime that executes 2.1 million tool-calling LLM tasks per day across 140 internal teams, with typed tool contracts, retries and per-tenant budgets. <!-- id:b-lum-01 tags:agents,llm -->
- Led a 9-engineer AI platform group and owned its roadmap end to end, from the first design review to the on-call rotation. <!-- id:b-lum-02 tags:leadership -->
- Built an evaluation harness in Python that scores every prompt change against 3,400 labelled cases before release, cutting regressions that reached production from 11 per quarter to 1. <!-- id:b-lum-03 tags:evals,llm -->
- Designed a retrieval pipeline on PostgreSQL with pgvector serving 38 million embedded passages at a p95 of 120 ms, replacing a hosted vector database and saving $410K a year. <!-- id:b-lum-04 tags:rag,retrieval -->
- Cut LLM inference spend 43% through prompt caching, response reuse and routing easy requests to smaller models, with a cost dashboard in Grafana per team and per feature. <!-- id:b-lum-05 tags:llm,cost -->
- Introduced structured output validation with one retry and a fed-back error, taking malformed model responses from 6.2% to 0.3% of calls. <!-- id:b-lum-06 tags:llm,reliability -->
- Built guardrails that keep untrusted document text from steering agents: fenced inputs, tool allow-lists and an injection test suite of 260 cases run in CI/CD. <!-- id:b-lum-07 tags:security,agents -->
- Shipped a Model Context Protocol gateway in TypeScript that exposes 85 internal services to agents behind one audited permission model. <!-- id:b-lum-08 tags:agents,mcp -->
- Created the tracing layer for agent runs on OpenTelemetry, so any production answer can be replayed step by step with its prompts, tool calls and token counts. <!-- id:b-lum-09 tags:observability,agents -->
- Fine-tuned a 7B-parameter open model with PyTorch for ticket routing, reaching 94% accuracy at one eighth of the hosted model's cost. <!-- id:b-lum-10 tags:fine-tuning,ml -->
- Ran the model-serving fleet on Kubernetes with GPU autoscaling, holding 99.95% availability over 18 months. <!-- id:b-lum-11 tags:serving,infrastructure -->
- Wrote the company's LLM evaluation guidelines and taught them to 300 engineers in 12 workshops. <!-- id:b-lum-12 tags:evals,teaching -->
- Partnered with legal and security on SOC 2 controls for AI features: data retention, redaction of personal data before any model call, and audit logs. <!-- id:b-lum-13 tags:compliance,privacy -->
- Designed a human-review queue for low-confidence agent actions that reviewers clear in a median of 4 minutes, keeping automation at 88% without unreviewed risky writes. <!-- id:b-lum-14 tags:agents,product -->
- Built a batch pipeline on Spark and Airflow that re-embeds 38 million passages weekly and checks retrieval quality before swapping the index. <!-- id:b-lum-15 tags:data,rag -->
- Mentored 4 senior engineers to staff-level scope; two were promoted within 18 months. <!-- id:b-lum-16 tags:mentoring,leadership -->
- Migrated the agent orchestration service from a single Python process to a Go worker pool on Kafka, raising throughput 5x at the same hardware cost. <!-- id:b-lum-17 tags:backend,distributed -->
- Defined SLOs for agent latency and answer quality and wired burn-rate alerts in Prometheus, cutting pages by 60%. <!-- id:b-lum-18 tags:sre,observability -->
- Reviewed and approved the architecture of 30 AI features across product teams as chair of the AI design council. <!-- id:b-lum-19 tags:leadership,architecture -->
- Built a synthetic-data generator that produces evaluation cases from production traces with personal data removed, growing the test set from 400 to 3,400 cases. <!-- id:b-lum-20 tags:evals,privacy -->
- Evaluated 14 foundation models each quarter on the company's own tasks and published the routing table product teams build against. <!-- id:b-lum-21 tags:evals,llm -->
- Wrote the Rust tokenizer service that counts and truncates prompts for every request in under 1 ms. <!-- id:b-lum-22 tags:rust,performance -->
- Built the prompt registry that versions every production prompt with its evaluation results and owner, used by 140 teams for 1,900 prompts. <!-- id:b-lum-23 tags:llm,platform -->
- Designed long-running agent workflows with durable checkpoints so a 40-minute research task survives a deploy or a node loss without repeating paid model calls. <!-- id:b-lum-24 tags:agents,reliability,distributed -->
- Added a semantic cache on Redis that answers 22% of repeated questions without a model call. <!-- id:b-lum-25 tags:llm,cost,performance -->
- Led the build-versus-buy review for a vector database and wrote the decision record that kept retrieval on PostgreSQL. <!-- id:b-lum-26 tags:architecture,rag -->
- Built the feature store interface that lets ranking models and agents read the same 600 features online and offline. <!-- id:b-lum-27 tags:ml-platform,data -->
- Set up red-team exercises with the security team twice a year; 31 findings closed, none reopened. <!-- id:b-lum-28 tags:security -->
- Hired 7 engineers for the AI platform group and designed its interview loop around a take-home evaluation exercise. <!-- id:b-lum-29 tags:hiring,leadership -->
### Kestrel Bio <!-- id:r-kes -->
Technical Advisor (part-time) | Jan 2021 - Present
- Advise a 25-person clinical data startup on its machine learning platform: model serving on Kubernetes, feature pipelines on Airflow and Spark, and evaluation before release. <!-- id:b-kes-01 tags:ml-platform,advising -->
- Designed the HIPAA-compliant data architecture: de-identification at ingestion, access logging and per-study encryption keys; passed 2 customer security reviews. <!-- id:b-kes-02 tags:compliance,privacy,data -->
- Reviewed the retrieval design for a clinical-notes assistant and its evaluation plan, with clinicians labelling 500 cases. <!-- id:b-kes-03 tags:rag,evals -->
- Coached the first 3 engineering leads and helped hire the head of data. <!-- id:b-kes-04 tags:mentoring,hiring -->
- Set up the model registry and the approval workflow that ties every deployed model to its validation report. <!-- id:b-kes-05 tags:ml-platform,compliance -->
- Wrote the incident and on-call guide adopted by a team with no prior production rotation. <!-- id:b-kes-06 tags:sre -->

### Hexa Cloud <!-- id:r-hex -->
Staff Software Engineer | Jun 2019 - Jan 2023
- Owned the control plane for a managed database product running 12,000 customer clusters on Kubernetes across AWS and GCP at 99.99% availability. <!-- id:b-hex-01 tags:backend,distributed,infrastructure -->
- Designed the cluster lifecycle service in Go as a set of idempotent reconcilers on PostgreSQL, replacing a workflow engine and cutting failed provisions from 3.1% to 0.2%. <!-- id:b-hex-02 tags:backend,distributed -->
- Led the migration of 40 microservices from hand-rolled deploys to Helm charts released through ArgoCD, taking deploy time from 45 minutes to 6. <!-- id:b-hex-03 tags:infrastructure,delivery -->
- Built the event backbone on Kafka carrying 900 million messages a day with exactly-once handling for billing events. <!-- id:b-hex-04 tags:distributed,data -->
- Defined SLOs for 25 services and built burn-rate alerting on Prometheus and Grafana, reducing customer-visible incidents 52% year over year. <!-- id:b-hex-05 tags:sre,observability -->
- Managed infrastructure as code with Terraform across 3 clouds and 19 regions, with policy checks that block unsafe changes before apply. <!-- id:b-hex-06 tags:infrastructure -->
- Led incident response as on-call lead for the platform organization; wrote the postmortem process adopted by 11 teams. <!-- id:b-hex-07 tags:sre,leadership -->
- Cut p99 API latency from 840 ms to 190 ms by redesigning the PostgreSQL schema, adding Redis caching and removing N+1 queries. <!-- id:b-hex-08 tags:backend,performance -->
- Designed multi-region failover with automated promotion, tested monthly; the recovery time objective fell from 30 minutes to 4. <!-- id:b-hex-09 tags:distributed,reliability -->
- Built the internal developer platform that gives 200 engineers a paved path: service template, CI/CD pipeline, dashboards and alerts in one command. <!-- id:b-hex-10 tags:platform,developer-experience -->
- Wrote the gRPC API guidelines and a Bazel-based code generator used by 60 services. <!-- id:b-hex-11 tags:backend,api -->
- Hardened Linux node images and automated kernel patching for 9,000 hosts with zero-downtime drains. <!-- id:b-hex-12 tags:linux,infrastructure -->
- Reduced cloud spend $2.3M a year through rightsizing, spot capacity and storage tiering, tracked in a cost model reviewed monthly with finance. <!-- id:b-hex-13 tags:cost,infrastructure -->
- Set the technical direction for 4 teams (32 engineers) as tech lead of the platform group. <!-- id:b-hex-14 tags:leadership -->
- Introduced OpenTelemetry tracing across the control plane, cutting mean time to diagnose from 50 minutes to 12. <!-- id:b-hex-15 tags:observability -->
- Passed SOC 2 Type II and ISO 27001 audits as engineering owner of change management and access controls. <!-- id:b-hex-16 tags:compliance -->
- Built a chaos-testing service that injects node, network and disk failures into staging clusters every night. <!-- id:b-hex-17 tags:reliability,testing -->
- Designed the usage metering pipeline (Kafka, Spark, Snowflake) that bills $180M of annual revenue with a 0.01% dispute rate. <!-- id:b-hex-18 tags:data,billing -->
- Mentored 6 engineers; 3 were promoted to senior and 1 to staff. <!-- id:b-hex-19 tags:mentoring -->
- Wrote the backup and restore service: point-in-time recovery for 12,000 clusters, verified by automated restore drills. <!-- id:b-hex-20 tags:reliability,data -->
- Replaced a Python scheduler with a Rust one for placement decisions, fitting 18% more clusters on the same fleet. <!-- id:b-hex-21 tags:rust,performance -->
- Ran the quarterly architecture review and kept a public decision log of 70 records. <!-- id:b-hex-22 tags:architecture,leadership -->
- Built the quota and rate-limiting service on Redis that protects the control plane API at 40,000 requests per second. <!-- id:b-hex-23 tags:backend,reliability -->
- Designed zero-downtime schema migrations for PostgreSQL and the tooling that runs 300 of them a year without an incident. <!-- id:b-hex-24 tags:data,delivery -->
- Introduced progressive delivery: canary analysis in ArgoCD with automatic rollback on SLO burn. <!-- id:b-hex-25 tags:delivery,sre -->
- Wrote the Kubernetes operator that manages database clusters as custom resources, with upgrade orchestration across 19 regions. <!-- id:b-hex-26 tags:infrastructure,distributed -->
- Led the Datadog-to-Prometheus migration for 25 services, cutting monitoring cost 58% while keeping every alert. <!-- id:b-hex-27 tags:observability,cost -->
- Built an eBPF-based network latency probe for Linux hosts that pinpointed a cross-zone packet loss issue affecting 4% of clusters. <!-- id:b-hex-28 tags:linux,observability -->
- Partnered with product on the pricing redesign; modeled the capacity plan for a 3x customer growth year. <!-- id:b-hex-29 tags:product,planning -->

### Fintra Labs <!-- id:r-fin -->
Senior Software Engineer | Aug 2015 - May 2019
- Built the payment authorization service in Java handling 5 million transactions a day with a p99 of 85 ms. <!-- id:b-fin-01 tags:backend,payments -->
- Led the split of a monolith into 14 microservices with REST and gRPC APIs, done over 18 months with no customer downtime. <!-- id:b-fin-02 tags:backend,architecture -->
- Designed the double-entry ledger on PostgreSQL that reconciles $2.4B a year to the cent. <!-- id:b-fin-03 tags:backend,data,payments -->
- Built fraud features and a scoring service with the data science team; the gradient-boosted model blocked $31M of fraud in its first year. <!-- id:b-fin-04 tags:ml,payments -->
- Introduced feature versioning and offline/online parity tests for machine learning features, removing a class of training-serving skew bugs. <!-- id:b-fin-05 tags:ml-platform,data -->
- Moved deployments to Docker and Kubernetes; release frequency went from every two weeks to 20 a day. <!-- id:b-fin-06 tags:infrastructure,delivery -->
- Owned PCI DSS scope reduction: tokenized card data and cut the audited services from 31 to 6. <!-- id:b-fin-07 tags:compliance,security -->
- Built the idempotency layer for payment APIs so client retries never double-charge. <!-- id:b-fin-08 tags:backend,reliability -->
- Wrote streaming jobs on Kafka and Spark that compute risk aggregates within 2 seconds of a transaction. <!-- id:b-fin-09 tags:data,distributed -->
- Led a team of 5 engineers on the merchant payouts product, from design to launch in 3 countries. <!-- id:b-fin-10 tags:leadership,product -->
- Created the on-call handbook and reduced pages per week from 40 to 9 by fixing the top alert sources. <!-- id:b-fin-11 tags:sre -->
- Built a model-serving gateway in Python for the fraud models with shadow traffic and automatic rollback. <!-- id:b-fin-12 tags:serving,ml-platform -->
- Tuned PostgreSQL for a 4 TB ledger: partitioning, index redesign and connection pooling, tripling write throughput. <!-- id:b-fin-13 tags:data,performance -->
- Built the GDPR data-subject request pipeline that finds and erases a customer's data across 22 stores in under 24 hours. <!-- id:b-fin-14 tags:compliance,privacy -->
- Interviewed 150 candidates and redesigned the backend interview loop. <!-- id:b-fin-15 tags:hiring -->
- Added contract tests between services, catching breaking API changes in CI/CD before release. <!-- id:b-fin-16 tags:testing,delivery -->
- Wrote the Airflow pipelines that deliver daily settlement files to 9 banking partners. <!-- id:b-fin-17 tags:data -->
- Built the React admin console used by 80 support agents to investigate payments. <!-- id:b-fin-18 tags:frontend -->
- Designed the GraphQL gateway that fronts 14 services for the merchant dashboard. <!-- id:b-fin-19 tags:backend,api -->
- Built the dispute and chargeback workflow service; handling time fell from 9 days to 3. <!-- id:b-fin-20 tags:backend,product -->
- Added MongoDB change streams to feed the search index of 80 million payment records. <!-- id:b-fin-21 tags:data,search -->
- Wrote load tests that replay production traffic at 5 times peak before every holiday season. <!-- id:b-fin-22 tags:testing,performance -->
- Built the feature pipeline that serves 120 real-time fraud features from Redis in under 10 ms. <!-- id:b-fin-23 tags:ml-platform,data -->
- Rotated as incident commander for payment outages; restored service inside the 15-minute objective in 19 of 20 incidents. <!-- id:b-fin-24 tags:sre -->

### Cascade Data <!-- id:r-cas -->
Software Engineer II | Jul 2012 - Jul 2015
- Built batch and streaming data pipelines in Scala on Spark processing 6 TB a day for 300 analytics customers. <!-- id:b-cas-01 tags:data -->
- Designed the search service on Elasticsearch indexing 400 million documents with relevance tuning per customer. <!-- id:b-cas-02 tags:search,retrieval -->
- Wrote natural-language pipelines for document classification with scikit-learn, reaching 91% precision on 40 categories. <!-- id:b-cas-03 tags:ml,nlp -->
- Built the ingestion API in Python and moved it from cron jobs to a queue-based worker fleet. <!-- id:b-cas-04 tags:backend -->
- Migrated the warehouse from a self-hosted cluster to AWS, cutting query cost 35%. <!-- id:b-cas-05 tags:data,infrastructure -->
- Created the data quality checks that run on every load and page the owning team on drift. <!-- id:b-cas-06 tags:data,reliability -->
- Automated server provisioning with Ansible for 250 Linux hosts. <!-- id:b-cas-07 tags:infrastructure,linux -->
- Built customer-facing dashboards and a SQL query builder used daily by 2,000 analysts. <!-- id:b-cas-08 tags:product,frontend -->
- Added a HIPAA-compliant processing tier for 3 healthcare customers: encryption, access logs and a signed business associate agreement path. <!-- id:b-cas-09 tags:compliance -->
- Mentored 2 interns who both returned as full-time engineers. <!-- id:b-cas-10 tags:mentoring -->
- Reduced nightly pipeline run time from 7 hours to 2 by rewriting the join strategy. <!-- id:b-cas-11 tags:data,performance -->
- Wrote the team's first runbooks and rotated on-call for the ingestion tier. <!-- id:b-cas-12 tags:sre -->
- Built the Airflow deployment and its 180 scheduled jobs, with retries and lineage for every table. <!-- id:b-cas-13 tags:data -->
- Wrote the customer data export service with per-tenant encryption keys. <!-- id:b-cas-14 tags:security,backend -->
- Introduced code review and continuous integration to a team that had neither. <!-- id:b-cas-15 tags:delivery -->
- Trained topic models over 400 million documents to power a "related documents" feature. <!-- id:b-cas-16 tags:ml,nlp -->

### Brightwell Media <!-- id:r-bri -->
Software Engineer | Jun 2010 - Jun 2012
- Built the content API in Ruby serving 30 million page views a month. <!-- id:b-bri-01 tags:backend -->
- Rewrote the article page in JavaScript, improving load time from 4.1 s to 1.3 s. <!-- id:b-bri-02 tags:frontend,performance -->
- Added MySQL read replicas and Redis caching ahead of a traffic peak 8 times the daily average. <!-- id:b-bri-03 tags:backend,data -->
- Built the A/B testing framework used for 120 experiments on headlines and layout. <!-- id:b-bri-04 tags:product,experimentation -->
- Automated deployments with Bash and Git hooks, replacing manual uploads. <!-- id:b-bri-05 tags:delivery -->
- Wrote the recommendation widget that raised pages per visit 14%. <!-- id:b-bri-06 tags:ml,product -->
- Maintained the PHP publishing system during its replacement. <!-- id:b-bri-07 tags:backend -->
- Ran the weekly engineering demo for the newsroom. <!-- id:b-bri-08 tags:communication -->
- Built the image resizing service in Node.js that replaced a nightly batch job. <!-- id:b-bri-09 tags:backend -->
- Set up the first monitoring and alerting for the site. <!-- id:b-bri-10 tags:sre -->

### Tessel Robotics <!-- id:r-tes -->
Software Engineering Intern | May 2009 - Aug 2009
- Wrote C++ drivers for a 6-axis arm controller and a test rig that replayed recorded motion. <!-- id:b-tes-01 tags:systems -->
- Built a Python tool that plots sensor logs for the hardware team. <!-- id:b-tes-02 tags:tools -->
- Fixed 23 firmware bugs found by the replay rig. <!-- id:b-tes-03 tags:systems -->

## Projects

### Taskloom: a personal multi-agent workbench <!-- id:p-task -->
Creator and maintainer | Mar 2025 - Present
- Built an open-source workbench in Python where several LLM agents plan, execute and review work under contracts a user can read and approve. <!-- id:b-task-01 tags:agents,llm,open-source -->
- Designed a write-once journal on Git so every agent action is replayable and no record is ever overwritten. <!-- id:b-task-02 tags:distributed,reliability -->
- Wrote a deterministic validator that rejects any model output citing a source it was not shown; the fabrication eval holds at 0 hard failures over 26 labelled cases. <!-- id:b-task-03 tags:evals,reliability -->
- Built a job-search agent on top of it that indexes 290,000 postings locally in SQLite and ranks them against a profile. <!-- id:b-task-04 tags:search,retrieval -->
- Shipped 19 releases with a release gate that times every read path on a real-sized home and loads the UI in a real browser. <!-- id:b-task-05 tags:delivery,testing -->
- Wrote the React interface and its REST API, with browser tests in CI/CD. <!-- id:b-task-06 tags:frontend,backend -->
- Built the local PDF renderer with Typst that fits a document to a page budget by measuring the real layout, not estimating it. <!-- id:b-task-07 tags:tools -->
- Designed a privacy boundary that strips names and contact details before any model call and proves it with static tests. <!-- id:b-task-08 tags:privacy,security -->
- Cut a 5 to 40 second page load to 0.1 seconds on a 290,000-row store by preparing data once and sharing it between requests. <!-- id:b-task-09 tags:performance,backend -->

### evalbench <!-- id:p-eval -->
Author | Jan 2024 - Present
- Wrote an open-source command-line tool in Python for LLM evaluation with labelled cases, a hard call budget and before/after reports; 2,300 GitHub stars. <!-- id:b-eval-01 tags:evals,llm,open-source -->
- Added a batched judge with a strict verdict schema so one model call scores every claim of a document. <!-- id:b-eval-02 tags:evals -->
- Built adapters for 6 model providers behind one interface, including local models. <!-- id:b-eval-03 tags:llm -->
- Documented an evaluation method now used by 3 university courses. <!-- id:b-eval-04 tags:teaching -->
- Added cost and latency reports per case so a prompt change shows its price before it ships. <!-- id:b-eval-05 tags:cost,evals -->

### kube-cost-exporter <!-- id:p-kce -->
Author | May 2022 - Mar 2023
- Wrote a Kubernetes cost exporter in Go that attributes cloud spend to namespaces and exposes it as Prometheus metrics. <!-- id:b-kce-01 tags:cost,infrastructure,open-source -->
- Packaged it as a Helm chart with Grafana dashboards; installed on 400 clusters by outside users. <!-- id:b-kce-02 tags:infrastructure,observability -->
- Added Terraform modules for AWS and GCP billing exports. <!-- id:b-kce-03 tags:infrastructure -->

### pgqueue-lite <!-- id:p-pgq -->
Author | Apr 2021 - Dec 2022
- Wrote an open-source job queue on PostgreSQL in Go using SKIP LOCKED, with at-least-once delivery and dead-letter handling; 1,100 GitHub stars. <!-- id:b-pgq-01 tags:backend,open-source -->
- Benchmarked it at 14,000 jobs per second on a single node and published the method. <!-- id:b-pgq-02 tags:performance -->
- Added Prometheus metrics and a Grafana dashboard shipped with the library. <!-- id:b-pgq-03 tags:observability -->
- Maintained it for 20 months with 45 outside contributors. <!-- id:b-pgq-04 tags:open-source,leadership -->

### tracekit <!-- id:p-trace -->
Author | Feb 2020 - Nov 2020
- Built a tracing library in Go that samples by error and latency, later replaced at Hexa Cloud by OpenTelemetry. <!-- id:b-trace-01 tags:observability -->
- Wrote the trace viewer in TypeScript and React. <!-- id:b-trace-02 tags:frontend -->
- Cut trace storage 70% with tail-based sampling. <!-- id:b-trace-03 tags:performance -->

### raft-kv <!-- id:p-raft -->
Author | Sep 2018 - Jan 2019
- Implemented the Raft consensus protocol in Go as a replicated key-value store with snapshots and membership changes. <!-- id:b-raft-01 tags:distributed -->
- Tested it with a deterministic simulator that injects partitions and clock skew; found and fixed 9 safety bugs. <!-- id:b-raft-02 tags:testing,distributed -->
- Wrote a 5-part tutorial read by 60,000 people. <!-- id:b-raft-03 tags:teaching -->

### ledger-sim <!-- id:p-ledg -->
Author | Mar 2017 - Aug 2017
- Wrote a property-based test generator in Kotlin that found 4 reconciliation bugs in the Fintra ledger before launch. <!-- id:b-ledg-01 tags:testing,payments -->
- Open-sourced the generator and presented it at a regional conference. <!-- id:b-ledg-02 tags:open-source -->

## Skills

- Languages: Python, Go, TypeScript, Rust, Java, Scala, Kotlin, Ruby, JavaScript, C++, SQL, Bash <!-- id:s-lang -->
- AI and machine learning: LLM, agents, retrieval-augmented generation, prompt caching, LLM evaluation, fine-tuning, PyTorch, scikit-learn, Machine Learning, Model Context Protocol, embeddings, pgvector <!-- id:s-ai -->
- Backend and distributed systems: Microservices, gRPC, REST, GraphQL, Kafka, Redis, consensus protocols, idempotent APIs, event-driven systems <!-- id:s-backend -->
- Data: PostgreSQL, MySQL, Elasticsearch, Spark, Airflow, Snowflake, dbt, SQLite, feature stores <!-- id:s-data -->
- Cloud and infrastructure: Kubernetes, Docker, Terraform, Helm, ArgoCD, Ansible, AWS, GCP, Azure, Linux, Bazel <!-- id:s-infra -->
- Observability and reliability: Observability, OpenTelemetry, Prometheus, Grafana, Datadog, SRE, SLOs, incident response, chaos testing <!-- id:s-obs -->
- Delivery: CI/CD, Git, contract testing, property-based testing, release gates <!-- id:s-delivery -->
- Security and compliance: SOC 2, ISO 27001, HIPAA, GDPR, PCI DSS <!-- id:s-sec -->
- Frontend: React, Node.js <!-- id:s-front -->
- Leadership: technical direction across teams, architecture reviews, mentoring, hiring, Agile <!-- id:s-lead -->

## Education

### Ridgeline Institute of Technology <!-- id:e-ms -->
M.S. Computer Science (part-time), distributed systems track | Sep 2013 - Jun 2016
- Thesis: tail-latency reduction in replicated storage; published at a peer-reviewed workshop. <!-- id:b-ms-01 tags:distributed,research -->

### Northfield State University <!-- id:e-bs -->
B.S. Computer Science | Sep 2006 - May 2010
- Graduated with honors; teaching assistant for operating systems for 3 semesters. <!-- id:b-bs-01 tags:teaching -->

## Other

- Certification: AWS Certified Solutions Architect, Professional (2021) <!-- id:o-cert-aws tags:certification,aws -->
- Certification: Certified Kubernetes Administrator (2020) <!-- id:o-cert-cka tags:certification,kubernetes -->
- Certification: Google Cloud Professional Machine Learning Engineer (2024) <!-- id:o-cert-gcp tags:certification,ml -->
- Certification: HashiCorp Certified Terraform Associate (2019) <!-- id:o-cert-tf tags:certification,terraform -->
- Talk: "Evaluating LLM agents before your users do", keynote at a 1,200-person AI engineering conference (2025) <!-- id:o-talk-evals tags:talk,evals,llm -->
- Talk: "Reconcilers over workflows: a control plane that heals itself", KubeCon (2022) <!-- id:o-talk-recon tags:talk,kubernetes,distributed -->
- Talk: "A ledger you can prove", regional payments conference (2018) <!-- id:o-talk-ledger tags:talk,payments -->
- Patent: co-inventor on a patent for tail-based trace sampling (granted 2022) <!-- id:o-patent tags:patent,observability -->
- Publication: "Tail latency in replicated storage", peer-reviewed workshop paper (2016) <!-- id:o-paper tags:publication,distributed -->
- Volunteer: taught an introductory programming course to 40 adult learners over 2 years <!-- id:o-vol tags:volunteering,teaching -->
- Languages spoken: English, Spanish (professional) <!-- id:o-lang tags:languages -->
- Award: engineering excellence award at Hexa Cloud for the multi-region failover program (2022) <!-- id:o-award tags:award -->
- Open source: maintainer of 3 libraries with 3,400 GitHub stars in total <!-- id:o-oss tags:open-source -->
- Writing: engineering blog with 40 posts on distributed systems and LLM evaluation, 25,000 monthly readers <!-- id:o-blog tags:writing,teaching -->
