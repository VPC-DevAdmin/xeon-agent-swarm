"""Per-subtask retrieval args (decompose-verify spec v6 §3).

The worker builds semantic-search args from the subtask's retrieval declaration:
a focused query + top_n, skipped entirely when retrieval.needed is False.
"""
from backend.agents.worker import _doc_retrieval_args
from backend.schemas.models import RetrievalSpec, TaskSpec, TaskType


def test_focused_query_and_top_n_used():
    t = TaskSpec(id="s1", type=TaskType.research, objective="Analyze X",
                 retrieval=RetrievalSpec(needed=True, query="HTTP caching headers",
                                         top_n=7))
    args = _doc_retrieval_args(t)
    assert args == {"query": "HTTP caching headers", "max_results": 7}


def test_blank_query_falls_back_to_objective():
    t = TaskSpec(id="s1", type=TaskType.research, objective="Research Intel AMX",
                 retrieval=RetrievalSpec(needed=True, query="", top_n=5))
    args = _doc_retrieval_args(t)
    assert args == {"query": "Research Intel AMX", "max_results": 5}


def test_not_needed_returns_none():
    t = TaskSpec(id="s1", type=TaskType.analysis, objective="Reason about Y",
                 retrieval=RetrievalSpec(needed=False))
    assert _doc_retrieval_args(t) is None


def test_default_retrieval_is_backward_compatible():
    # A task with no explicit retrieval still retrieves (needed defaults True),
    # using the objective as the query — matches pre-v6 behavior.
    t = TaskSpec(id="s1", type=TaskType.research, objective="Summarize Z")
    args = _doc_retrieval_args(t)
    assert args == {"query": "Summarize Z", "max_results": 5}


def test_top_n_floored_at_one():
    t = TaskSpec(id="s1", type=TaskType.research, objective="o",
                 retrieval=RetrievalSpec(needed=True, query="q", top_n=0))
    assert _doc_retrieval_args(t)["max_results"] == 1
