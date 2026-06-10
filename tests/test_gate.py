"""Mechanical gate (decompose-verify spec v6 §5).

The gate is purely structural — no demo-corpus rules. It catches cycles,
dangling/duplicate ids, and orphan subtasks (not consumed by the synthesis sink),
and in strict mode requires success_criteria presence. It must NOT reject valid
general plans (e.g. one with no research task) the way the old demo rules did.
"""
from backend.graph.swarm_graph import validate_task_graph
from backend.schemas.models import TaskGraph, TaskSpec, TaskType


def _tg(*tasks: TaskSpec) -> TaskGraph:
    return TaskGraph(query="q", reasoning="r", tasks=list(tasks))


def test_valid_plan_passes():
    g = _tg(
        TaskSpec(id="s1", type=TaskType.analysis, success_criteria=["a"]),
        TaskSpec(id="s2", type=TaskType.analysis, success_criteria=["b"]),
        TaskSpec(id="s3", type=TaskType.writing, is_synthesis=True,
                 dependencies=["s1", "s2"], success_criteria=["c"]),
    )
    assert validate_task_graph(g, strict=True).ok


def test_no_research_task_is_fine():
    # The old demo rule "at least one research task" must be gone — a pure
    # analysis/writing plan is structurally valid.
    g = _tg(
        TaskSpec(id="s1", type=TaskType.analysis, success_criteria=["a"]),
        TaskSpec(id="s2", type=TaskType.writing, is_synthesis=True,
                 dependencies=["s1"], success_criteria=["b"]),
    )
    res = validate_task_graph(g, strict=True)
    assert res.ok, res.errors


def test_orphan_subtask_rejected():
    # s2's output is never consumed by the synthesis node s3.
    g = _tg(
        TaskSpec(id="s1", type=TaskType.analysis, success_criteria=["a"]),
        TaskSpec(id="s2", type=TaskType.analysis, success_criteria=["b"]),
        TaskSpec(id="s3", type=TaskType.writing, is_synthesis=True,
                 dependencies=["s1"], success_criteria=["c"]),
    )
    res = validate_task_graph(g)
    assert not res.ok
    assert any("Orphan" in e and "s2" in e for e in res.errors)


def test_cycle_rejected():
    g = _tg(
        TaskSpec(id="s1", type=TaskType.analysis, dependencies=["s2"]),
        TaskSpec(id="s2", type=TaskType.analysis, dependencies=["s1"]),
    )
    assert not validate_task_graph(g).ok


def test_dangling_and_duplicate_ids_rejected():
    dangling = _tg(TaskSpec(id="s1", type=TaskType.writing, dependencies=["ghost"]))
    assert not validate_task_graph(dangling).ok

    dup = _tg(
        TaskSpec(id="s1", type=TaskType.analysis),
        TaskSpec(id="s1", type=TaskType.writing, is_synthesis=True,
                 dependencies=["s1"]),
    )
    assert not validate_task_graph(dup).ok


def test_strict_requires_success_criteria():
    g = _tg(
        TaskSpec(id="s1", type=TaskType.analysis),   # no success_criteria
        TaskSpec(id="s2", type=TaskType.writing, is_synthesis=True,
                 dependencies=["s1"], success_criteria=["c"]),
    )
    assert not validate_task_graph(g, strict=True).ok   # planner gate fails it
    assert validate_task_graph(g, strict=False).ok       # lenient path passes


def test_legacy_writing_sink_backward_compat():
    # No is_synthesis marker: the unique writing task is identified as the sink.
    g = _tg(
        TaskSpec(id="s1", type=TaskType.research),
        TaskSpec(id="s2", type=TaskType.research),
        TaskSpec(id="w", type=TaskType.writing, dependencies=["s1", "s2"]),
    )
    assert validate_task_graph(g, strict=False).ok
