import pytest

from store_intel.orchestration import (
    CONSTRAINTS_BLOCK,
    MAX_ITERATIONS,
    SYSTEM_PROMPTS,
    AgentRunState,
    OrchestrationLimitError,
    fallback_summary,
    telemetry_step,
)
from store_intel.pipeline import StoreIntelligencePipeline


def test_max_iterations_is_hard_capped_at_four():
    state = AgentRunState(max_iterations=99)

    for _ in range(MAX_ITERATIONS):
      state.next_iteration("folder")

    with pytest.raises(OrchestrationLimitError):
        state.next_iteration("folder")


def test_duplicate_tool_call_with_identical_inputs_is_blocked():
    state = AgentRunState()
    telemetry_step(
        state,
        step=1,
        total=1,
        agent="InputAgent",
        tool="inspect_video",
        inputs={"video_path": "demo.mp4"},
        run=lambda: {"ok": True},
    )

    with pytest.raises(OrchestrationLimitError):
        telemetry_step(
            state,
            step=1,
            total=1,
            agent="InputAgent",
            tool="inspect_video",
            inputs={"video_path": "demo.mp4"},
            run=lambda: {"ok": True},
        )


def test_system_prompts_include_required_constraints():
    assert "Do not guess missing information." in CONSTRAINTS_BLOCK
    assert "Do not call the same tool with identical inputs more than once." in CONSTRAINTS_BLOCK
    assert "Use compact JSON state between nodes." in CONSTRAINTS_BLOCK
    for prompt in SYSTEM_PROMPTS.values():
        assert "CONSTRAINTS" in prompt
        assert "OUTPUT_SCHEMA" in prompt


def test_fallback_summary_is_json_compatible():
    state = AgentRunState()
    state.next_iteration("folder")
    summary = fallback_summary(state, "max_iterations=4 exceeded")

    assert summary["status"] == "fallback"
    assert summary["max_iterations"] == 4
    assert summary["iterations_used"] == 1


def test_pipeline_caps_long_video_metadata(monkeypatch):
    monkeypatch.setenv("STORE_INTEL_MAX_ANALYSIS_SECONDS", "4")
    monkeypatch.setenv("STORE_INTEL_CHUNK_SECONDS", "3")
    metadata = {
        "video_id": "VID_TEST",
        "duration_sec": 12,
        "chunks": [f"{second}-{second + 1}s" for second in range(12)],
    }

    capped = StoreIntelligencePipeline._cap_metadata_duration(metadata)

    assert capped["duration_sec"] == 4
    assert capped["analysis_duration_sec"] == 4
    assert capped["original_duration_sec"] == 12
    assert capped["analysis_capped"] is True
    assert capped["analysis_chunks"] == [
        {"start_sec": 0, "end_sec": 3, "duration_sec": 3},
        {"start_sec": 3, "end_sec": 4, "duration_sec": 1},
    ]
    assert capped["chunks"] == ["0-3s", "3-4s"]


def test_pipeline_fragments_full_video_without_total_cap(monkeypatch):
    monkeypatch.setenv("STORE_INTEL_MAX_ANALYSIS_SECONDS", "0")
    monkeypatch.setenv("STORE_INTEL_CHUNK_SECONDS", "300")
    metadata = {
        "video_id": "VID_LONG",
        "duration_sec": 725,
        "chunks": [],
    }

    prepared = StoreIntelligencePipeline._prepare_analysis_metadata(metadata)

    assert prepared["duration_sec"] == 725
    assert prepared["analysis_duration_sec"] == 725
    assert "analysis_capped" not in prepared
    assert prepared["analysis_chunks"] == [
        {"start_sec": 0, "end_sec": 300, "duration_sec": 300},
        {"start_sec": 300, "end_sec": 600, "duration_sec": 300},
        {"start_sec": 600, "end_sec": 725, "duration_sec": 125},
    ]
