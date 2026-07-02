// Curated sample prompts for the agent-orchestration demo UI.
// Each prompt is designed to decompose naturally into 2-5 subtasks across the
// worker roles defined in config/worker_roles.yaml:
//   research, analysis, code, vision, fact_check, writing, summarization, general

export interface SamplePrompt {
  id: string
  category: string
  title: string
  prompt: string
  recurring?: boolean
}

export const CATEGORIES: string[] = [
  "Research & Analysis",
  "Technology Comparisons",
  "Business & Strategy",
  "Writing & Content",
  "Fact-Checking & Verification",
  "Engineering & Code",
  "Recurring Reports",
]

export const SAMPLE_PROMPTS: SamplePrompt[] = [
  // ---------------------------------------------------------------
  // Research & Analysis
  // ---------------------------------------------------------------
  {
    id: "cpu-llm-inference-xeon",
    category: "Research & Analysis",
    title: "CPU-only LLM inference on Xeon",
    prompt:
      "Research the current state of CPU-only LLM inference (vLLM, llama.cpp, OpenVINO), compare their throughput and memory trade-offs, and write a one-page recommendation for a team standardizing on Intel Xeon servers.",
  },
  {
    id: "quantization-quality-impact",
    category: "Research & Analysis",
    title: "Quantization vs. model quality",
    prompt:
      "Research how INT8, INT4, and FP8 quantization affect LLM output quality, gather published benchmark numbers for Llama 3 70B under each scheme, and summarize the accuracy-versus-memory trade-off in a short brief with a comparison table.",
  },
  {
    id: "solid-state-battery-status",
    category: "Research & Analysis",
    title: "Solid-state battery progress",
    prompt:
      "Investigate the current state of solid-state battery development at Toyota, QuantumScape, and Samsung SDI, analyze their claimed energy densities and production timelines, and write an assessment of which is most likely to ship at automotive scale first.",
  },
  {
    id: "small-model-distillation",
    category: "Research & Analysis",
    title: "Small model distillation trends",
    prompt:
      "Research recent techniques for distilling large language models into sub-3B-parameter models, compare reported benchmark retention rates across at least three published methods, and produce a summary of when distillation beats training small models from scratch.",
  },
  {
    id: "grid-storage-economics",
    category: "Research & Analysis",
    title: "Grid-scale storage economics",
    prompt:
      "Research the levelized cost of storage for lithium-ion, iron-air, and pumped hydro at grid scale, build a comparison of cost per kWh-cycle and duration sweet spots, and write a two-paragraph conclusion on which technology wins for 100-hour storage.",
  },
  {
    id: "rag-vs-long-context",
    category: "Research & Analysis",
    title: "RAG vs. long-context windows",
    prompt:
      "Research the evidence for and against replacing retrieval-augmented generation with million-token context windows, analyze the cost and accuracy data from published evaluations, and summarize the strongest argument on each side in a balanced brief.",
  },
  {
    id: "arm-datacenter-adoption",
    category: "Research & Analysis",
    title: "ARM in the datacenter",
    prompt:
      "Research the adoption of ARM server CPUs (AWS Graviton, Ampere Altra, NVIDIA Grace) in cloud datacenters, compare price-performance figures against x86 equivalents, and analyze which workload classes are actually migrating.",
  },
  {
    id: "desalination-tech-review",
    category: "Research & Analysis",
    title: "Desalination technology review",
    prompt:
      "Research the energy consumption of reverse osmosis versus emerging membrane-free desalination approaches, verify the most-cited efficiency claims against published data, and write a summary of realistic cost floors for desalinated water by 2030.",
  },
  {
    id: "post-quantum-crypto-migration",
    category: "Research & Analysis",
    title: "Post-quantum crypto readiness",
    prompt:
      "Research the NIST post-quantum cryptography standards (ML-KEM, ML-DSA, SLH-DSA), analyze the performance overhead each adds compared to current elliptic-curve schemes, and write a migration-priority brief for infrastructure teams.",
  },
  {
    id: "vertical-farming-viability",
    category: "Research & Analysis",
    title: "Vertical farming unit economics",
    prompt:
      "Investigate why several major vertical farming startups failed between 2022 and 2024, analyze the unit economics of lettuce grown under LEDs versus field farming, and summarize the conditions under which vertical farms can actually be profitable.",
  },

  // ---------------------------------------------------------------
  // Technology Comparisons
  // ---------------------------------------------------------------
  {
    id: "postgres-vs-clickhouse-analytics",
    category: "Technology Comparisons",
    title: "Postgres vs. ClickHouse for analytics",
    prompt:
      "Compare PostgreSQL and ClickHouse for analytical workloads on 500GB of event data: research their query performance and operational overhead, build a comparison table across ingestion speed, query latency, and cost, and recommend one for a five-person startup.",
  },
  {
    id: "rust-vs-go-services",
    category: "Technology Comparisons",
    title: "Rust vs. Go for backend services",
    prompt:
      "Compare Rust and Go for building high-throughput network services: gather benchmark data on latency and memory usage, analyze developer productivity trade-offs, and write a decision brief with a sample code snippet in the recommended language.",
  },
  {
    id: "kafka-vs-nats-vs-redpanda",
    category: "Technology Comparisons",
    title: "Kafka vs. NATS vs. Redpanda",
    prompt:
      "Research Kafka, NATS JetStream, and Redpanda as event streaming platforms, compare them on throughput, latency, and operational complexity in a table, and recommend one for a team processing 50,000 messages per second with a three-engineer platform team.",
  },
  {
    id: "react-vs-svelte-vs-htmx",
    category: "Technology Comparisons",
    title: "React vs. Svelte vs. HTMX",
    prompt:
      "Compare React, Svelte, and HTMX for building an internal dashboard product: research bundle sizes and rendering performance, analyze hiring-pool and ecosystem trade-offs, and write a one-page recommendation for a team of four full-stack developers.",
  },
  {
    id: "k8s-vs-nomad-vs-ecs",
    category: "Technology Comparisons",
    title: "Kubernetes vs. Nomad vs. ECS",
    prompt:
      "Compare Kubernetes, HashiCorp Nomad, and AWS ECS for orchestrating around 40 containerized services, analyze total operational cost and learning curve for each, and recommend the best fit for a startup without a dedicated platform team.",
  },
  {
    id: "vector-db-shootout",
    category: "Technology Comparisons",
    title: "Vector database shootout",
    prompt:
      "Research pgvector, Qdrant, and Milvus for storing 100 million embeddings, compare recall, query latency, and infrastructure cost in a table, fact-check the vendors' headline benchmark claims, and recommend one for a RAG application.",
  },
  {
    id: "webassembly-vs-containers",
    category: "Technology Comparisons",
    title: "WASM vs. containers at the edge",
    prompt:
      "Compare WebAssembly runtimes (Wasmtime, WasmEdge) against lightweight containers (Firecracker) for edge compute, research cold-start times and memory footprints for each, and analyze which workload types genuinely benefit from WASM today.",
  },
  {
    id: "grpc-vs-rest-vs-graphql",
    category: "Technology Comparisons",
    title: "gRPC vs. REST vs. GraphQL",
    prompt:
      "Compare gRPC, REST, and GraphQL for a service mesh of 20 internal microservices, benchmark-research their serialization overhead and tooling maturity, and write a recommendation with a short code example of the winning approach.",
  },
  {
    id: "terraform-vs-pulumi-vs-cdk",
    category: "Technology Comparisons",
    title: "Terraform vs. Pulumi vs. CDK",
    prompt:
      "Compare Terraform, Pulumi, and AWS CDK as infrastructure-as-code tools, analyze state management, language ergonomics, and multi-cloud support in a table, and recommend one for a team managing 300 cloud resources across AWS and GCP.",
  },
  {
    id: "duckdb-vs-spark-medium-data",
    category: "Technology Comparisons",
    title: "DuckDB vs. Spark for medium data",
    prompt:
      "Research the argument that most Spark workloads fit on a single large machine, compare DuckDB on a 128-core server against a small Spark cluster for 1TB of parquet data, and summarize when each approach wins on cost and speed.",
  },

  // ---------------------------------------------------------------
  // Business & Strategy
  // ---------------------------------------------------------------
  {
    id: "gpu-cloud-pricing-analysis",
    category: "Business & Strategy",
    title: "GPU cloud pricing landscape",
    prompt:
      "Research current H100 GPU pricing across AWS, CoreWeave, Lambda Labs, and RunPod, analyze the cost per training-hour differences in a table, and write a procurement brief on when reserved capacity beats on-demand for a six-month training project.",
  },
  {
    id: "open-source-monetization",
    category: "Business & Strategy",
    title: "Open-source monetization models",
    prompt:
      "Analyze how GitLab, HashiCorp, and Grafana Labs monetized open-source software, compare their license changes and revenue outcomes, and write a strategy brief on which model best suits a new developer-tools company in 2026.",
  },
  {
    id: "ev-charging-market-entry",
    category: "Business & Strategy",
    title: "EV charging market entry",
    prompt:
      "Research the US EV fast-charging market including Tesla Supercharger network opening to other brands, analyze utilization rates and margin structures for charging operators, and assess whether a new entrant can compete profitably in 2026.",
  },
  {
    id: "saas-pricing-teardown",
    category: "Business & Strategy",
    title: "SaaS pricing model teardown",
    prompt:
      "Compare seat-based, usage-based, and outcome-based pricing across Datadog, Snowflake, and Intercom, analyze how each model affected net revenue retention, and recommend a pricing structure for an AI-powered analytics startup.",
  },
  {
    id: "chip-fab-subsidy-race",
    category: "Business & Strategy",
    title: "Semiconductor subsidy race",
    prompt:
      "Research the semiconductor manufacturing incentives in the US CHIPS Act, the EU Chips Act, and Japan's fab subsidies, compare committed spending and fabs actually under construction, and analyze which region is winning advanced-node capacity.",
  },
  {
    id: "streaming-consolidation",
    category: "Business & Strategy",
    title: "Streaming market consolidation",
    prompt:
      "Analyze the economics of the video streaming market: research subscriber counts and content spend for Netflix, Disney+, and Max, fact-check the most recent profitability claims from each, and write a brief on the likely consolidation endgame.",
  },
  {
    id: "developer-tools-tam",
    category: "Business & Strategy",
    title: "AI coding assistant market sizing",
    prompt:
      "Research the market size for AI coding assistants, analyze adoption and pricing data for GitHub Copilot, Cursor, and Claude Code, and produce a bottoms-up TAM estimate with the key assumptions laid out in a table.",
  },
  {
    id: "reshoring-manufacturing-math",
    category: "Business & Strategy",
    title: "Reshoring manufacturing math",
    prompt:
      "Research the actual cost delta of manufacturing consumer electronics in the US versus China and Vietnam in 2026, analyze labor, logistics, and tariff components separately, and write an executive summary on which product categories can reshore profitably.",
  },
  {
    id: "space-launch-economics",
    category: "Business & Strategy",
    title: "Launch cost economics",
    prompt:
      "Research the cost per kilogram to orbit for SpaceX Falcon 9, Rocket Lab Electron, and the projected Starship figures, verify the most widely cited numbers against primary sources, and analyze what sub-$100/kg launch would unlock economically.",
  },
  {
    id: "grocery-delivery-unit-economics",
    category: "Business & Strategy",
    title: "Grocery delivery unit economics",
    prompt:
      "Analyze why rapid grocery delivery startups like Gorillas and Getir collapsed while Instacart survived, compare their order economics and basket sizes, and summarize the three structural lessons for future on-demand delivery ventures.",
  },

  // ---------------------------------------------------------------
  // Writing & Content
  // ---------------------------------------------------------------
  {
    id: "explainer-moe-models",
    category: "Writing & Content",
    title: "Explainer: mixture-of-experts",
    prompt:
      "Research how mixture-of-experts architectures like Mixtral and DeepSeek-V3 route tokens to experts, then write an 800-word explainer for engineers new to ML that includes one concrete numerical example of the compute savings.",
  },
  {
    id: "blog-post-io-uring",
    category: "Writing & Content",
    title: "Blog post on io_uring",
    prompt:
      "Research how io_uring changed Linux async I/O, gather benchmark comparisons against epoll, and write a technical blog post with a code sample showing a minimal io_uring echo server and a diagram of the submission/completion queue flow.",
  },
  {
    id: "onboarding-doc-distributed-systems",
    category: "Writing & Content",
    title: "Distributed systems primer",
    prompt:
      "Write an onboarding primer on distributed systems fundamentals covering CAP theorem, consensus, and idempotency, research two real-world outage postmortems to use as case studies, and end with a ten-question self-check quiz.",
  },
  {
    id: "release-notes-narrative",
    category: "Writing & Content",
    title: "Narrative-style release notes",
    prompt:
      "Research how Linear, Stripe, and Vercel write product changelogs, analyze what makes their release notes engaging, and write a style guide with three before-and-after examples showing how to turn dry changelog entries into narrative release notes.",
  },
  {
    id: "conference-talk-outline",
    category: "Writing & Content",
    title: "Conference talk on CPU inference",
    prompt:
      "Draft a 25-minute conference talk outline titled 'Serving LLMs Without GPUs', research three real deployments of CPU-based inference to cite as evidence, and write the full speaker notes for the opening five minutes.",
  },
  {
    id: "whitepaper-agent-orchestration",
    category: "Writing & Content",
    title: "Whitepaper: agent orchestration",
    prompt:
      "Research published multi-agent orchestration patterns (planner-worker, debate, blackboard), compare their failure modes in a table, and write the introduction and architecture sections of a whitepaper on reliable agent swarm design.",
  },
  {
    id: "newsletter-issue-quantum",
    category: "Writing & Content",
    title: "Newsletter issue on quantum computing",
    prompt:
      "Research the three most significant quantum computing announcements of the past year, fact-check the headline qubit and error-rate claims, and write a 600-word newsletter issue that explains why they matter to a technical but non-physicist audience.",
  },
  {
    id: "docs-rewrite-error-messages",
    category: "Writing & Content",
    title: "Error message style guide",
    prompt:
      "Analyze what makes error messages helpful using examples from Rust, Elm, and TypeScript compilers, then write an error-message style guide with five rules and rewrite six examples of bad error messages to follow them.",
  },
  {
    id: "case-study-migration",
    category: "Writing & Content",
    title: "Case study: monolith migration",
    prompt:
      "Research two public engineering-blog accounts of monolith-to-services migrations that went badly, analyze the common decision mistakes, and write a 700-word cautionary case study with a checklist teams should complete before starting a migration.",
  },
  {
    id: "executive-brief-ai-regulation",
    category: "Writing & Content",
    title: "Executive brief on AI regulation",
    prompt:
      "Research the current obligations under the EU AI Act for general-purpose AI providers, verify the compliance deadlines and penalty figures, and write a one-page executive brief on what a US-based model provider must do to sell in Europe.",
  },

  // ---------------------------------------------------------------
  // Fact-Checking & Verification
  // ---------------------------------------------------------------
  {
    id: "verify-10x-inference-claims",
    category: "Fact-Checking & Verification",
    title: "Verify '10x faster inference' claims",
    prompt:
      "Collect three recent vendor claims of 10x-or-greater LLM inference speedups, research the benchmark conditions behind each claim, and deliver a verdict on each: supported, misleading, or unsupported, with the evidence summarized.",
  },
  {
    id: "check-cobalt-free-batteries",
    category: "Fact-Checking & Verification",
    title: "Fact-check cobalt-free battery claims",
    prompt:
      "Fact-check the claim that LFP batteries have eliminated cobalt from most new EVs: research current LFP market share in China, Europe, and the US, verify the chemistry breakdown numbers, and summarize what the claim gets right and wrong.",
  },
  {
    id: "verify-remote-work-productivity",
    category: "Fact-Checking & Verification",
    title: "Remote work productivity studies",
    prompt:
      "Verify the competing claims that remote work raises or lowers productivity: find the three most-cited studies on each side, analyze their methodologies and sample sizes, and deliver a verdict on what the evidence actually supports.",
  },
  {
    id: "check-ai-energy-consumption",
    category: "Fact-Checking & Verification",
    title: "AI datacenter energy claims",
    prompt:
      "Fact-check the widely repeated claim that AI datacenters will consume 10 percent of global electricity by 2030: trace the claim to its original source, compare it against IEA projections, and write a corrected version of the statistic with proper caveats.",
  },
  {
    id: "verify-language-benchmark-saturation",
    category: "Fact-Checking & Verification",
    title: "Are LLM benchmarks saturated?",
    prompt:
      "Investigate the claim that MMLU and HumanEval are saturated and no longer discriminate between frontier models, gather the recent top-10 scores on each, and verify whether score compression actually supports the saturation claim.",
  },
  {
    id: "check-microplastics-health",
    category: "Fact-Checking & Verification",
    title: "Microplastics health claims",
    prompt:
      "Fact-check three viral claims about microplastics in human blood and brains, research what the underlying peer-reviewed studies actually measured, and summarize the gap between the headlines and the evidence in plain language.",
  },
  {
    id: "verify-cyber-attack-statistics",
    category: "Fact-Checking & Verification",
    title: "Ransomware cost statistics audit",
    prompt:
      "Audit the commonly cited statistic that ransomware costs businesses over 20 billion dollars annually: trace it to its original source, check whether the methodology still holds, and produce a verdict with a better-supported replacement figure.",
  },
  {
    id: "check-carbon-capture-progress",
    category: "Fact-Checking & Verification",
    title: "Direct air capture cost claims",
    prompt:
      "Verify vendor claims that direct air capture will reach 100 dollars per ton of CO2 by 2030, research the current actual costs at Climeworks and other operating facilities, and analyze how plausible the cost-decline curve really is.",
  },
  {
    id: "verify-github-copilot-stats",
    category: "Fact-Checking & Verification",
    title: "AI coding productivity stats",
    prompt:
      "Fact-check the claim that AI assistants make developers 55 percent faster: locate the original GitHub Copilot study behind the number, analyze its task design and external validity, and deliver a verdict on how the finding generalizes to real work.",
  },
  {
    id: "check-nuclear-smr-timelines",
    category: "Fact-Checking & Verification",
    title: "SMR deployment timeline check",
    prompt:
      "Verify the announced deployment timelines for small modular reactors from NuScale, X-energy, and TerraPower, compare each against their historical schedule slips, and summarize which 2030-or-sooner claims are credible.",
  },

  // ---------------------------------------------------------------
  // Engineering & Code
  // ---------------------------------------------------------------
  {
    id: "rate-limiter-design",
    category: "Engineering & Code",
    title: "Distributed rate limiter design",
    prompt:
      "Research token-bucket versus sliding-window rate limiting for a distributed API gateway, write a working Python implementation of the better algorithm backed by Redis, and produce an architecture diagram showing how it fits in front of multiple service replicas.",
  },
  {
    id: "websocket-scaling-pattern",
    category: "Engineering & Code",
    title: "Scaling WebSockets to 100k clients",
    prompt:
      "Research patterns for scaling WebSocket servers to 100,000 concurrent connections, compare sticky-session load balancing against a pub/sub fan-out design, and write a minimal Node.js example of the recommended architecture with a diagram.",
  },
  {
    id: "idempotency-keys-implementation",
    category: "Engineering & Code",
    title: "Idempotency keys for payments",
    prompt:
      "Explain how Stripe-style idempotency keys prevent duplicate charges, write a Python implementation of an idempotency middleware with a storage-backed key check, and analyze the edge cases around key expiry and concurrent retries.",
  },
  {
    id: "cache-stampede-protection",
    category: "Engineering & Code",
    title: "Cache stampede protection",
    prompt:
      "Research cache stampede failures and the three main mitigations (locking, probabilistic early expiry, request coalescing), compare their trade-offs in a table, and implement request coalescing in Go with a short code example.",
  },
  {
    id: "sqlite-server-side",
    category: "Engineering & Code",
    title: "SQLite as a production database",
    prompt:
      "Research the case for running SQLite server-side with Litestream or LiteFS replication, analyze the write-throughput ceiling against Postgres, and write a sample deployment configuration with a diagram of the replication flow.",
  },
  {
    id: "feature-flag-system",
    category: "Engineering & Code",
    title: "Build a feature flag system",
    prompt:
      "Design a minimal feature-flag system supporting percentage rollouts and user targeting, write the core evaluation logic in TypeScript with deterministic bucketing, and diagram how flag state propagates from the control plane to application pods.",
  },
  {
    id: "zero-downtime-migrations",
    category: "Engineering & Code",
    title: "Zero-downtime schema migrations",
    prompt:
      "Research the expand-contract pattern for zero-downtime database migrations, write an example migration sequence that renames a heavily used Postgres column without locking, and summarize the three mistakes that most often cause migration outages.",
  },
  {
    id: "llm-token-streaming-backpressure",
    category: "Engineering & Code",
    title: "LLM streaming with backpressure",
    prompt:
      "Analyze how to handle backpressure when streaming LLM tokens through a chain of SSE proxies, write a Python example using asyncio that buffers and drops correctly under slow clients, and diagram the flow from model server to browser.",
  },
  {
    id: "consistent-hashing-explained",
    category: "Engineering & Code",
    title: "Consistent hashing implementation",
    prompt:
      "Explain consistent hashing with virtual nodes for distributing cache keys, implement it in Python with a test that shows rebalancing moves under 30 percent of keys when a node joins, and diagram the hash ring before and after.",
  },
  {
    id: "circuit-breaker-pattern",
    category: "Engineering & Code",
    title: "Circuit breakers for microservices",
    prompt:
      "Research the circuit breaker pattern and how Netflix and Resilience4j implement half-open state probing, write a compact TypeScript circuit breaker with configurable thresholds, and analyze when circuit breaking hurts more than it helps.",
  },

  // ---------------------------------------------------------------
  // Recurring Reports
  // ---------------------------------------------------------------
  {
    id: "daily-oss-ai-digest",
    category: "Recurring Reports",
    title: "Morning open-source AI digest",
    prompt:
      "Compile a morning digest of the top developments in open-source AI models from the last 24 hours, fact-check the three most significant claims, and summarize in five bullet points.",
    recurring: true,
  },
  {
    id: "weekly-security-advisories",
    category: "Recurring Reports",
    title: "Weekly security advisory roundup",
    prompt:
      "Compile the week's critical CVEs affecting Linux servers, Kubernetes, and popular Python packages, analyze which ones are actively exploited, and write a prioritized patch-now list with severity scores.",
    recurring: true,
  },
  {
    id: "daily-chip-industry-brief",
    category: "Recurring Reports",
    title: "Daily semiconductor brief",
    prompt:
      "Produce a daily brief on semiconductor industry news covering TSMC, Intel, NVIDIA, and ASML, verify any stock-moving claims against primary sources, and summarize the day in three headlines with one-sentence context each.",
    recurring: true,
  },
  {
    id: "weekly-llm-leaderboard-watch",
    category: "Recurring Reports",
    title: "Weekly LLM leaderboard watch",
    prompt:
      "Check the major LLM leaderboards for ranking changes this week, analyze any new entrants or significant score jumps, fact-check the most surprising result, and summarize the state of the race in a short table plus three bullets.",
    recurring: true,
  },
  {
    id: "weekly-cloud-price-tracker",
    category: "Recurring Reports",
    title: "Weekly cloud pricing tracker",
    prompt:
      "Track changes in GPU and compute instance pricing across AWS, GCP, Azure, and CoreWeave from the past week, flag any price cuts over 10 percent, and produce a comparison table of the cheapest option per instance class.",
    recurring: true,
  },
  {
    id: "daily-arxiv-systems-papers",
    category: "Recurring Reports",
    title: "Daily systems papers digest",
    prompt:
      "Scan the last 24 hours of arXiv submissions in distributed systems and ML systems, pick the three most practically relevant papers, and summarize each in two sentences with a note on who should read it.",
    recurring: true,
  },
  {
    id: "weekly-devtools-launches",
    category: "Recurring Reports",
    title: "Weekly developer tools launches",
    prompt:
      "Compile the developer tools launched or major-versioned this week across GitHub trending and Hacker News, analyze which address genuine gaps versus crowded categories, and write a five-item ranked digest with one-line takes.",
    recurring: true,
  },
  {
    id: "monthly-energy-transition-report",
    category: "Recurring Reports",
    title: "Monthly energy transition report",
    prompt:
      "Compile the month's key data on renewable capacity additions, battery storage deployments, and EV sales in the US, EU, and China, verify the headline figures against official statistics, and write a one-page trend report with a chart-ready table.",
    recurring: true,
  },
  {
    id: "weekly-open-source-releases",
    category: "Recurring Reports",
    title: "Weekly infra release notes digest",
    prompt:
      "Summarize this week's releases of Kubernetes, PostgreSQL, Redis, and Nginx including patch versions, analyze which changes matter for production operators, and produce an upgrade-urgency table with a recommendation per project.",
    recurring: true,
  },
  {
    id: "daily-market-open-tech-brief",
    category: "Recurring Reports",
    title: "Daily tech market-open brief",
    prompt:
      "Compile a pre-market brief on overnight technology sector news, fact-check the two most significant earnings or guidance claims, and summarize the likely narrative of the trading day in four bullet points.",
    recurring: true,
  },
]
