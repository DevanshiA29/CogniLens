from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar


MAX_ITERATIONS = 4

CONSTRAINTS_BLOCK = """CONSTRAINTS
- Do not guess missing information.
- Do not call the same tool with identical inputs more than once.
- If a tool output returns an error or a repetitive answer, report the limitation directly to the user instead of retrying.
- Use compact JSON state between nodes. Do not pass verbose conversational history between sub-agent nodes.
- Stop after max_iterations=4 independent of model stopping behavior and return a graceful summary fallback.
"""

SYSTEM_PROMPTS: dict[str, str] = {
    "InputAgent": f"""You receive CCTV video inputs and emit metadata/chunk state.
{CONSTRAINTS_BLOCK}
OUTPUT_SCHEMA {{"video_id":"string","store_id":"string","camera_id":"string","fps":"integer","duration_sec":"integer","timestamp_offset":"ISO-8601","chunks":["start-end"]}}""",
    "FrameAnalyzerAgent": f"""You analyze frame chunks into compact visual observations.
{CONSTRAINTS_BLOCK}
OUTPUT_SCHEMA {{"observations":[{{"timestamp":"ISO-8601","visitor_id":"string","action":"string","zone":"string","is_staff":"boolean","confidence":"number","metadata":"object"}}]}}""",
    "EventGeneratorAgent": f"""You convert observations into canonical retail events.
{CONSTRAINTS_BLOCK}
OUTPUT_SCHEMA {{"events":[{{"event_id":"string","timestamp":"ISO-8601","video_time_sec":"number","frame_id":"integer","visitor_id":"string","track_id":"string|null","group_id":"string|null","role":"customer|staff|unknown","event_type":"string","zone":"string","confidence":"number","metadata":"object"}}]}}""",
    "MemoryEventStoreAgent": f"""You persist events and return storage status only.
{CONSTRAINTS_BLOCK}
OUTPUT_SCHEMA {{"received":"integer","inserted":"integer","store_id":"string"}}""",
    "IntelligenceMetricsAgent": f"""You compute session-based retail analytics from stored events.
{CONSTRAINTS_BLOCK}
OUTPUT_SCHEMA {{"metrics":"object","funnel":"object","anomalies":"array"}}""",
}


class OrchestrationLimitError(RuntimeError):
    """Raised when an agent loop or duplicate tool call violates deterministic constraints."""


T = TypeVar("T")


@dataclass
class AgentRunState:
    max_iterations: int = MAX_ITERATIONS
    iteration: int = 0
    tool_call_hashes: set[str] = field(default_factory=set)
    steps_completed: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.max_iterations = min(int(self.max_iterations or MAX_ITERATIONS), MAX_ITERATIONS)

    def next_iteration(self, label: str) -> None:
        self.iteration += 1
        if self.iteration > self.max_iterations:
            raise OrchestrationLimitError(
                f"max_iterations={self.max_iterations} exceeded while running {label}"
            )

    def register_tool_call(self, agent: str, tool: str, inputs: dict[str, Any]) -> None:
        payload = json.dumps({"agent": agent, "tool": tool, "inputs": inputs}, sort_keys=True, default=str)
        call_hash = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        if call_hash in self.tool_call_hashes:
            raise OrchestrationLimitError(f"duplicate tool call blocked: {agent}.{tool}")
        self.tool_call_hashes.add(call_hash)


def json_state(**values: Any) -> dict[str, Any]:
    """Return a compact JSON-compatible state envelope between agent nodes."""
    return json.loads(json.dumps(values, default=str))


def telemetry_step(
    state: AgentRunState,
    *,
    step: int,
    total: int,
    agent: str,
    tool: str,
    inputs: dict[str, Any],
    run: Callable[[], T],
) -> T:
    state.register_tool_call(agent, tool, inputs)
    checkpoint = f"[STEP {step}/{total}] Agent [{agent}] initiating tool [{tool}]"
    print(checkpoint, flush=True)
    logging.info("orchestration.step_start", extra={"step": step, "total": total, "agent": agent, "tool": tool})
    try:
        result = run()
    except Exception:
        print(f"[STEP {step}/{total}] Agent [{agent}] tool [{tool}] failed", flush=True)
        logging.exception("orchestration.step_error", extra={"step": step, "agent": agent, "tool": tool})
        raise
    state.steps_completed.append({"step": step, "agent": agent, "tool": tool})
    print(f"[STEP {step}/{total}] Agent [{agent}] completed tool [{tool}]", flush=True)
    logging.info("orchestration.step_done", extra={"step": step, "total": total, "agent": agent, "tool": tool})
    return result


def fallback_summary(state: AgentRunState, reason: str) -> dict[str, Any]:
    return json_state(
        status="fallback",
        reason=reason,
        max_iterations=state.max_iterations,
        iterations_used=state.iteration,
        steps_completed=state.steps_completed,
    )
