"""
Orchestrator agent: decomposes a user query into a TaskGraph.

Flow:
  1. Try template match first (repeatable demo decompositions)
  2. Fall back to LLM-based decomposition if no template matches
  3. Validate the resulting graph (rules-based, no LLM needed)
  4. Retry LLM decomposition once with critique if validation fails
  5. Broadcast graph_ready event
"""
from __future__ import annotations

import logging
import os
import re

from backend.inference.client import InferenceClient
from backend.schemas.models import TaskGraph, SwarmState, EventType, SwarmEvent

logger = logging.getLogger(__name__)

ORCHESTRATOR_SYSTEM = """
You are a task decomposition specialist. Given a user query, produce a structured
task graph where every task is a precise contract that a specialist worker can
execute and a validator can verify.

AVAILABLE TASK TYPES:
  research    — extract specific facts from corpus or web sources
  analysis    — compare, rank, or synthesize findings from retrieval tasks
  code        — produce working code or a Mermaid diagram
  vision      — extract structured data from charts or diagrams in the image corpus
  fact_check  — verify specific claims against independent retrieval
  writing     — draft a structured report section with citations

MANDATORY STRUCTURE FOR EVERY TASK:
  objective:           one sentence starting with an action verb
  scope:               2-4 specific questions this task must answer
  deliverable_format:  a known format token (see below)
  success_criteria:    2-4 things that must be true of the output
  source_constraints:  {use_web: bool, use_corpus: bool, min_sources: int}

DELIVERABLE FORMAT TOKENS (use these exact strings):
  - finding_list_with_citations
  - finding_list_with_numeric_values
  - comparison_table
  - mermaid_diagram
  - code_block_python
  - extracted_chart_data
  - component_diagram_description
  - claim_verdicts
  - prose_section_with_citations
  - document_result

DECOMPOSITION RULES:
1. Include 2-3 research tasks (different facets of the query, run in parallel)
2. Include ONE analysis task depending on research tasks
3. Include ONE vision task if the query involves technical architectures or benchmarks
4. Include ONE code task if implementation or diagrams are useful
5. Include ONE fact_check task — verifies claims from research independently
6. Include ONE writing task as the final synthesis, depending on all others
7. Writing task output is always deliverable_format: document_result
8. Total tasks: 5-8

FULL WORKED EXAMPLE for query "Compare Intel Xeon and NVIDIA H100 for LLM inference":

{
  "query": "Compare Intel Xeon and NVIDIA H100 for LLM inference",
  "reasoning": "Split into parallel research for each vendor, vision for benchmark charts, analysis for synthesis, code for decision tree, fact_check for claim verification, writing for final report.",
  "tasks": [
    {
      "id": "t1",
      "type": "research",
      "objective": "Extract AMX specifications and CPU inference benchmarks for Intel Xeon from corpus",
      "scope": [
        "What is the peak INT8 TOPS of AMX on Xeon 6?",
        "What batch sizes maximize AMX throughput?",
        "What quantization formats does AMX support natively?"
      ],
      "deliverable_format": "finding_list_with_numeric_values",
      "success_criteria": [
        "At least 3 findings with specific numeric values",
        "Every finding cites a corpus source"
      ],
      "source_constraints": {"use_web": false, "use_corpus": true, "min_sources": 3},
      "dependencies": [],
      "description": "Extract AMX specifications and CPU inference benchmarks for Intel Xeon from corpus"
    },
    {
      "id": "t2",
      "type": "research",
      "objective": "Extract H100 inference throughput and memory bandwidth from web sources",
      "scope": [
        "What is H100 measured tokens/sec for Llama-3-70B inference?",
        "What is the cost per million tokens for H100 inference?",
        "What memory bandwidth constraints affect H100 inference performance?"
      ],
      "deliverable_format": "finding_list_with_numeric_values",
      "success_criteria": [
        "At least 3 findings with specific numeric values",
        "At least 2 findings cite web sources"
      ],
      "source_constraints": {"use_web": true, "use_corpus": false, "min_sources": 3},
      "dependencies": [],
      "description": "Extract H100 inference throughput and memory bandwidth from web sources"
    },
    {
      "id": "t3",
      "type": "vision",
      "objective": "Extract throughput and latency data points from benchmark charts comparing CPU and GPU inference",
      "scope": [
        "What numeric data points appear on available benchmark charts?",
        "What are the units and axis labels?"
      ],
      "deliverable_format": "extracted_chart_data",
      "success_criteria": [
        "If chart found: at least 4 data points extracted with values",
        "If no chart: fallback cleanly with image_found=false"
      ],
      "source_constraints": {"use_web": false, "use_corpus": true, "min_sources": 1},
      "expected_image_types": ["benchmark_chart"],
      "fallback_behavior": "retrieval_only",
      "dependencies": [],
      "description": "Extract throughput and latency data from benchmark charts"
    },
    {
      "id": "t4",
      "type": "analysis",
      "objective": "Build a comparison table of Xeon vs H100 across cost, throughput, and power dimensions",
      "scope": [
        "Cost per million tokens: Xeon vs H100",
        "Tokens/sec per watt: Xeon vs H100",
        "Capital expense per inference node"
      ],
      "deliverable_format": "comparison_table",
      "success_criteria": [
        "Table has at least 3 metric rows and 2 vendor columns",
        "Every cell has a specific value, not 'TBD'"
      ],
      "source_constraints": {"use_web": false, "use_corpus": false, "min_sources": 0},
      "dependencies": ["t1", "t2", "t3"],
      "description": "Compare Xeon vs H100 cost, throughput, and power tradeoffs"
    },
    {
      "id": "t5",
      "type": "code",
      "objective": "Produce a Mermaid decision tree for selecting Xeon vs H100 based on workload characteristics",
      "scope": [
        "What are the key decision factors?",
        "What threshold values guide each decision?"
      ],
      "deliverable_format": "mermaid_diagram",
      "success_criteria": [
        "Diagram has at least 5 decision nodes",
        "Every leaf node recommends a specific option"
      ],
      "source_constraints": {"use_web": false, "use_corpus": false, "min_sources": 0},
      "dependencies": ["t4"],
      "description": "Decision tree for Xeon vs H100 workload selection"
    },
    {
      "id": "t6",
      "type": "fact_check",
      "objective": "Verify numeric claims from research tasks against independent corpus lookup",
      "scope": [
        "Are Xeon benchmark numbers consistent with multiple corpus sources?",
        "Are H100 web claims supported by cited sources?"
      ],
      "deliverable_format": "claim_verdicts",
      "success_criteria": [
        "At least one verdict per numeric claim from t1 and t2",
        "Every verdict cites evidence or notes absence"
      ],
      "source_constraints": {"use_web": false, "use_corpus": true, "min_sources": 1},
      "dependencies": ["t1", "t2"],
      "description": "Verify performance claims from research tasks"
    },
    {
      "id": "t7",
      "type": "writing",
      "objective": "Draft the full intelligence report integrating all research, analysis, and code",
      "scope": [
        "Executive summary with clear recommendation",
        "Per-vendor analysis sections",
        "Comparison table section",
        "Decision framework section"
      ],
      "deliverable_format": "document_result",
      "success_criteria": [
        "All sections present with minimum word counts",
        "Citations inline for every numeric claim"
      ],
      "source_constraints": {"use_web": false, "use_corpus": false, "min_sources": 0},
      "dependencies": ["t1", "t2", "t3", "t4", "t5", "t6"],
      "description": "Draft full intelligence report on CPU vs GPU LLM inference"
    }
  ]
}

Respond with ONLY a JSON object matching this shape. No prose before or after.
""".strip()


def _make_client() -> InferenceClient:
    return InferenceClient(
        base_url=os.getenv("TEXT_ENGINE_ENDPOINT", os.getenv("ORCHESTRATOR_ENDPOINT", "http://localhost:8080/v1")),
        model=os.getenv("TEXT_ENGINE_MODEL", os.getenv("ORCHESTRATOR_MODEL", "mistralai/Mistral-7B-Instruct-v0.3")),
        hardware="cpu",
        use_semaphore=False,  # Orchestrator runs solo before workers; no contention
    )


async def orchestrate(state: SwarmState) -> SwarmState:
    """LangGraph node: decompose query into TaskGraph and update state."""
    client = _make_client()
    messages = [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM},
        {"role": "user", "content": state.query},
    ]
    task_graph: TaskGraph = await client.complete_structured(
        messages=messages,
        response_model=TaskGraph,
        max_tokens=2048,
    )
    state.task_graph = task_graph
    return state


async def orchestrate_with_events(
    query: str,
    run_id: str,
    broadcast,
    critique: str | None = None,
) -> TaskGraph:
    """
    Stand-alone helper used by main.py: decomposes query and broadcasts graph_ready.
    Accepts an optional critique string for retry-with-hint scenarios.
    Returns the TaskGraph.
    """
    client = _make_client()
    user_content = query
    if critique:
        user_content += f"\n\n[RETRY] Previous decomposition had validation errors:\n{critique}\nFix these issues in your new decomposition."

    messages = [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    task_graph: TaskGraph = await client.complete_structured(
        messages=messages,
        response_model=TaskGraph,
        max_tokens=2048,
    )
    await broadcast(
        run_id,
        SwarmEvent(
            event=EventType.graph_ready,
            run_id=run_id,
            payload=task_graph.model_dump(),
        ),
    )
    return task_graph
