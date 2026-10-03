"""Tests for local agent research goal tools."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.agent.skills import SkillsLoader
from src.goal import GoalStore
from src.tools.goal_tool import (
    AddGoalEvidenceTool,
    GetResearchGoalTool,
    StartResearchGoalTool,
    UpdateResearchGoalStatusTool,
)


def test_local_goal_tools_use_injected_session(tmp_path: Path) -> None:
    """Agent tools can start, inspect, and mutate the current session goal."""
    store = GoalStore(tmp_path / "goals.db")
    start = StartResearchGoalTool(default_session_id="session-1", store=store)
    get = GetResearchGoalTool(default_session_id="session-1", store=store)
    add = AddGoalEvidenceTool(default_session_id="session-1", store=store)

    created = json.loads(
        start.execute(
            objective="Evaluate NVDA momentum as a research-only thesis.",
            criteria=["Define thesis", "Check price action"],
        )
    )

    assert created["status"] == "ok"
    assert created["snapshot"]["goal"]["session_id"] == "session-1"

    fetched = json.loads(get.execute())
    assert fetched["status"] == "ok"
    assert fetched["snapshot"]["goal"]["goal_id"] == created["snapshot"]["goal"]["goal_id"]

    evidence = json.loads(
        add.execute(
            provenance_kind="manual",
            criterion_index=2,
            text="NVDA outperformed QQQ over the last 5 sessions.",
            source_provider="pytest",
            source_type="manual_note",
        )
    )

    assert evidence["status"] == "ok"
    assert evidence["snapshot"]["evidence_count"] == 1
    assert (
        evidence["evidence"]["criterion_id"]
        == created["snapshot"]["criteria"][1]["criterion_id"]
    )


def test_goal_tool_without_session_returns_validation_error(tmp_path: Path) -> None:
    """Goal tools fail cleanly when no session id was injected or supplied."""
    store = GoalStore(tmp_path / "goals.db")
    result = json.loads(GetResearchGoalTool(store=store).execute())

    assert result["status"] == "error"
    assert result["error_type"] == "validation"
    assert "session_id" in result["error"]


def test_goal_tool_rejects_live_trading_objective(tmp_path: Path) -> None:
    """Local agent tools keep the research-only boundary."""
    store = GoalStore(tmp_path / "goals.db")
    tool = StartResearchGoalTool(default_session_id="session-1", store=store)

    result = json.loads(tool.execute(objective="Buy 1 BTC now."))

    assert result["status"] == "error"
    assert result["error_type"] == "validation"
    assert "live trading" in result["error"]


def test_registry_injects_session_id_into_goal_tools() -> None:
    """SessionService can build a registry with session-scoped goal tools."""
    from src.tools import build_registry

    registry = build_registry(session_id="session-xyz")
    tool = registry.get("start_research_goal")

    assert tool is not None
    assert getattr(tool, "_default_session_id") == "session-xyz"


def test_goal_tools_emit_mutation_events(tmp_path: Path) -> None:
    """Goal tools notify the host when they mutate session-scoped goal state."""
    store = GoalStore(tmp_path / "goals.db")
    events: list[tuple[str, dict]] = []

    def emit(event_type: str, data: dict) -> None:
        events.append((event_type, data))

    start = StartResearchGoalTool(default_session_id="session-1", store=store, event_callback=emit)
    add = AddGoalEvidenceTool(default_session_id="session-1", store=store, event_callback=emit)

    created = json.loads(
        start.execute(
            objective="Evaluate NVDA momentum as a research-only thesis.",
            criteria=["Define thesis", "Check price action"],
        )
    )
    evidence = json.loads(
        add.execute(
            provenance_kind="manual",
            goal_id=created["snapshot"]["goal"]["goal_id"],
            criterion_index=1,
            text="Evidence from a local tool call.",
        )
    )

    assert created["status"] == "ok"
    assert evidence["status"] == "ok"
    assert [item[0] for item in events] == ["goal.created", "goal.evidence"]
    assert events[0][1]["goal"]["session_id"] == "session-1"
    assert events[1][1]["goal_id"] == created["snapshot"]["goal"]["goal_id"]


def test_goal_evidence_tool_binds_runtime_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Runtime run_dir artifacts become verified goal evidence automatically."""
    run_root = tmp_path / "runs"
    run_dir = run_root / "goal-tool-run"
    artifact = run_dir / "artifacts" / "metrics.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("symbol,return\nNVDA,0.12\n", encoding="utf-8")
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(run_root))
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_FILE_ROOTS", str(run_root))

    store = GoalStore(tmp_path / "goals.db")
    start = StartResearchGoalTool(default_session_id="session-1", store=store)
    add = AddGoalEvidenceTool(default_session_id="session-1", store=store)
    created = json.loads(
        start.execute(
            objective="Evaluate NVDA momentum as a research-only thesis.",
            criteria=["Check price action"],
        )
    )

    result = json.loads(
        add.execute(
            provenance_kind="manual",
            goal_id=created["snapshot"]["goal"]["goal_id"],
            criterion_index=1,
            text="Generated metrics artifact for NVDA momentum.",
            source_provider="pytest",
            source_type="market_data",
            artifact_path="artifacts/metrics.csv",
            run_dir=str(run_dir),
        )
    )

    expected_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    evidence = result["evidence"]
    assert result["status"] == "ok"
    assert evidence["run_id"] == "goal-tool-run"
    assert evidence["artifact_path"] == str(artifact.resolve())
    assert evidence["artifact_hash"] == expected_hash
    assert evidence["verification_status"] == "verified"



def test_goal_evidence_preserves_exact_tool_call_provenance(tmp_path: Path) -> None:
    """Tool-backed goal evidence keeps the exact source call id beside its own evidence id."""
    store = GoalStore(tmp_path / "goals.db")
    start = StartResearchGoalTool(default_session_id="session-1", store=store)
    add = AddGoalEvidenceTool(default_session_id="session-1", store=store)
    created = json.loads(
        start.execute(
            objective="Audit one tool-backed metric.",
            criteria=["Record the metric provenance"],
        )
    )
    call_id = "call_iDflHZFxCGQG1VfvfLoNBXCk|fc_0fa4bc82"

    result = json.loads(
        add.execute(
            provenance_kind="single_tool",
            goal_id=created["snapshot"]["goal"]["goal_id"],
            criterion_index=1,
            text="The source tool returned the audited metric.",
            tool_call_id=call_id,
            source_provider="pytest",
            source_type="market_data",
            _runtime_observed_tool_calls=[{"call_id": call_id, "tool": "market_data", "status": "ok"}],
        )
    )

    evidence = result["evidence"]
    assert result["status"] == "ok"
    assert evidence["evidence_id"].startswith("ev_")
    assert evidence["tool_call_id"] == call_id
    assert evidence["evidence_id"] != evidence["tool_call_id"]
    assert result["snapshot"]["evidence"][0]["tool_call_id"] == call_id



def test_goal_evidence_rejects_missing_single_tool_call_id_with_candidates(tmp_path: Path) -> None:
    """Single-tool evidence fails closed and returns observed runtime candidates."""
    store = GoalStore(tmp_path / "goals.db")
    start = StartResearchGoalTool(default_session_id="session-1", store=store)
    add = AddGoalEvidenceTool(default_session_id="session-1", store=store)
    start.execute(objective="Audit provenance.", criteria=["Record evidence"])

    result = json.loads(
        add.execute(
            provenance_kind="single_tool",
            criterion_index=1,
            text="Metric from one tool result.",
            _runtime_observed_tool_calls=[
                {"call_id": "call_real_1", "tool": "market_data", "status": "ok"},
                {"call_id": "call_real_2", "tool": "risk_xray", "status": "error"},
            ],
        )
    )

    assert result["status"] == "error"
    assert result["error_type"] == "provenance"
    assert [item["call_id"] for item in result["tool_call_candidates"]] == [
        "call_real_1",
        "call_real_2",
    ]



def test_goal_evidence_accepts_observed_error_call_as_single_tool(tmp_path: Path) -> None:
    """A tool error can itself be the exact source of a negative finding."""
    store = GoalStore(tmp_path / "goals.db")
    start = StartResearchGoalTool(default_session_id="session-1", store=store)
    add = AddGoalEvidenceTool(default_session_id="session-1", store=store)
    start.execute(objective="Audit unavailable risk metric.", criteria=["Record evidence"])

    result = json.loads(
        add.execute(
            provenance_kind="single_tool",
            criterion_index=1,
            text="Risk X-Ray reported insufficient history.",
            tool_call_id="call_risk_error",
            _runtime_observed_tool_calls=[
                {"call_id": "call_risk_error", "tool": "risk_xray", "status": "error"}
            ],
        )
    )

    assert result["status"] == "ok"
    assert result["evidence"]["tool_call_id"] == "call_risk_error"

def test_goal_evidence_rejects_unknown_single_tool_call_id(tmp_path: Path) -> None:
    """A model cannot bind goal evidence to an invented or stale call id."""
    store = GoalStore(tmp_path / "goals.db")
    start = StartResearchGoalTool(default_session_id="session-1", store=store)
    add = AddGoalEvidenceTool(default_session_id="session-1", store=store)
    start.execute(objective="Audit provenance.", criteria=["Record evidence"])

    result = json.loads(
        add.execute(
            provenance_kind="single_tool",
            criterion_index=1,
            text="Metric from one tool result.",
            tool_call_id="call_invented",
            _runtime_observed_tool_calls=[{"call_id": "call_real", "tool": "market_data", "status": "ok"}],
        )
    )

    assert result["status"] == "error"
    assert result["error_type"] == "provenance"
    assert result["tool_call_candidates"] == [{"call_id": "call_real", "tool": "market_data", "status": "ok"}]


def test_goal_evidence_synthesis_stays_unbound_to_single_call(tmp_path: Path) -> None:
    """Multi-source synthesis is valid without fabricating one source call id."""
    store = GoalStore(tmp_path / "goals.db")
    start = StartResearchGoalTool(default_session_id="session-1", store=store)
    add = AddGoalEvidenceTool(default_session_id="session-1", store=store)
    start.execute(objective="Compare evidence.", criteria=["Synthesize sources"])

    result = json.loads(
        add.execute(
            provenance_kind="synthesis",
            criterion_index=1,
            text="Comparison across performance and risk sources.",
            _runtime_observed_tool_calls=[
                {"call_id": "call_perf", "tool": "performance", "status": "ok"},
                {"call_id": "call_risk", "tool": "risk_xray", "status": "error"},
            ],
        )
    )

    assert result["status"] == "ok"
    assert result["evidence"]["tool_call_id"] is None


def test_goal_evidence_synthesis_rejects_single_call_binding(tmp_path: Path) -> None:
    """Synthesis cannot masquerade as evidence from one selected tool call."""
    store = GoalStore(tmp_path / "goals.db")
    start = StartResearchGoalTool(default_session_id="session-1", store=store)
    add = AddGoalEvidenceTool(default_session_id="session-1", store=store)
    start.execute(objective="Compare evidence.", criteria=["Synthesize sources"])

    result = json.loads(
        add.execute(
            provenance_kind="synthesis",
            criterion_index=1,
            text="Comparison across sources.",
            tool_call_id="call_perf",
            _runtime_observed_tool_calls=[{"call_id": "call_perf", "tool": "performance", "status": "ok"}],
        )
    )

    assert result["status"] == "error"
    assert result["error_type"] == "provenance"

def test_goal_evidence_can_remain_manual_without_tool_call_id(tmp_path: Path) -> None:
    """Manual evidence does not acquire an invented tool provenance id."""
    store = GoalStore(tmp_path / "goals.db")
    start = StartResearchGoalTool(default_session_id="session-1", store=store)
    add = AddGoalEvidenceTool(default_session_id="session-1", store=store)
    created = json.loads(
        start.execute(
            objective="Record a manual research note.",
            criteria=["Record the note"],
        )
    )

    result = json.loads(
        add.execute(
            provenance_kind="manual",
            goal_id=created["snapshot"]["goal"]["goal_id"],
            criterion_index=1,
            text="Manual reasoning note.",
            source_provider="pytest",
            source_type="manual_note",
        )
    )

    assert result["status"] == "ok"
    assert result["evidence"]["tool_call_id"] is None


def test_goal_evidence_tool_schema_distinguishes_call_and_evidence_ids() -> None:
    """The always-visible tool schema tells the model which id belongs in grounding provenance."""
    description = AddGoalEvidenceTool.description
    call_description = AddGoalEvidenceTool.parameters["properties"]["tool_call_id"]["description"]

    assert "exact tool_call_id" in description
    assert AddGoalEvidenceTool.parameters["required"] == ["text", "provenance_kind"]
    assert "evidence_id (ev_...)" in description
    assert "exact observed tool call id" in call_description
    assert "Never use an evidence_id" in call_description


def test_goal_status_tool_can_cancel_current_goal(tmp_path: Path) -> None:
    """Agent tools can move a current goal to a terminal status."""
    store = GoalStore(tmp_path / "goals.db")
    start = StartResearchGoalTool(default_session_id="session-1", store=store)
    update = UpdateResearchGoalStatusTool(default_session_id="session-1", store=store)
    created = json.loads(
        start.execute(
            objective="Evaluate NVDA momentum as a research-only thesis.",
            criteria=["Define thesis"],
        )
    )
    goal_id = created["snapshot"]["goal"]["goal_id"]

    result = json.loads(
        update.execute(
            goal_id=goal_id,
            expected_goal_id=goal_id,
            status="cancelled",
            recap="Cancelled during tool test.",
        )
    )

    assert result["status"] == "ok"
    assert result["snapshot"]["goal"]["status"] == "cancelled"
    assert store.get_current_snapshot("session-1") is None


def test_registry_injects_goal_event_callback() -> None:
    """SessionService can build goal tools that emit through the event bus."""
    from src.tools import build_registry

    events: list[tuple[str, dict]] = []
    registry = build_registry(
        session_id="session-xyz",
        event_callback=lambda event_type, data: events.append((event_type, data)),
    )
    tool = registry.get("start_research_goal")

    assert tool is not None
    assert getattr(tool, "_event_callback") is not None


def test_research_goal_skill_is_bundled() -> None:
    """The agent can load workflow guidance for self-managed goals."""
    content = SkillsLoader().get_content("research-goal")

    assert "start_research_goal" in content
    assert "add_goal_evidence" in content
    assert "exact `tool_call_id`" in content
    assert "`ev_...::field`" in content


def test_goal_tools_expose_canonical_completion_contract(tmp_path: Path) -> None:
    """All goal reads expose canonical criterion ids and criterion-local evidence ids."""
    store = GoalStore(tmp_path / "goals.db")
    start = StartResearchGoalTool(default_session_id="session-1", store=store)
    get = GetResearchGoalTool(default_session_id="session-1", store=store)
    add = AddGoalEvidenceTool(default_session_id="session-1", store=store)

    created = json.loads(
        start.execute(
            objective="Evaluate NVDA momentum as a research-only thesis.",
            criteria=["Define thesis", "Check price action"],
        )
    )
    contract = created["completion_contract"]
    assert [row["criterion_index"] for row in contract["criteria"]] == [1, 2]
    assert [row["criterion_id"] for row in contract["criteria"]] == [
        item["criterion_id"] for item in created["snapshot"]["criteria"]
    ]

    evidence = json.loads(
        add.execute(
            provenance_kind="manual",
            criterion_index=2,
            text="Concrete price-action evidence.",
        )
    )
    evidence_id = evidence["evidence"]["evidence_id"]
    row = evidence["completion_contract"]["criteria"][1]
    assert row["criterion_id"] == created["snapshot"]["criteria"][1]["criterion_id"]
    assert row["evidence_ids"] == [evidence_id]

    fetched = json.loads(get.execute())
    assert fetched["completion_contract"] == evidence["completion_contract"]


def test_completion_error_returns_repair_contract(tmp_path: Path) -> None:
    """A rejected completion points the model at canonical ids instead of more research."""
    store = GoalStore(tmp_path / "goals.db")
    start = StartResearchGoalTool(default_session_id="session-1", store=store)
    update = UpdateResearchGoalStatusTool(default_session_id="session-1", store=store)

    created = json.loads(
        start.execute(
            objective="Evaluate NVDA momentum as a research-only thesis.",
            criteria=["Define thesis", "Check price action"],
        )
    )
    result = json.loads(
        update.execute(
            status="complete",
            audit=[
                {
                    "criterion_index": 1,
                    "result": "satisfied",
                    "evidence_ids": [],
                }
            ],
        )
    )

    assert result["status"] == "error"
    assert "completion_contract" in result
    assert result["completion_contract"]["goal_id"] == created["snapshot"]["goal"]["goal_id"]
    assert len(result["completion_contract"]["criteria"]) == 2
    assert "criterion" in result["error"].lower() or "verified evidence" in result["error"].lower()


def test_completion_audit_accepts_canonical_criterion_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """criterion_index resolves to the current canonical criterion id for completion."""
    run_root = tmp_path / "runs"
    run_dir = run_root / "goal-tool-run"
    run_dir.mkdir(parents=True)
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(run_root))

    store = GoalStore(tmp_path / "goals.db")
    start = StartResearchGoalTool(default_session_id="session-1", store=store)
    add = AddGoalEvidenceTool(default_session_id="session-1", store=store)
    update = UpdateResearchGoalStatusTool(default_session_id="session-1", store=store)

    created = json.loads(
        start.execute(
            objective="Evaluate NVDA momentum as a research-only thesis.",
            criteria=["Check price action"],
        )
    )
    evidence = json.loads(
        add.execute(
            provenance_kind="manual",
            criterion_index=1,
            text="Verified evidence from the current run.",
            run_dir=str(run_dir),
        )
    )
    evidence_id = evidence["evidence"]["evidence_id"]

    completed = json.loads(
        update.execute(
            status="complete",
            audit=[
                {
                    "criterion_index": 1,
                    "result": "satisfied",
                    "evidence_ids": [evidence_id],
                }
            ],
        )
    )

    assert completed["status"] == "ok"
    assert completed["snapshot"]["goal"]["status"] == "complete"
