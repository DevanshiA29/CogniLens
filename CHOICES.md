# Implementation Choices

## Assumptions

- Existing detections provide temporary `track_id`; the system maps those to stable `visitor_id` values per camera/track.
- If deep appearance embeddings are unavailable, re-entry is detected by same stable visitor id returning near the entry within a configurable practical window.
- Staff classification can be explainable rather than model-heavy.

## Trade-Offs

- The system uses SQLite by default to stay runnable locally and in Docker without extra services.
- YOLO remains optional via `STORE_INTEL_USE_YOLO=1`; OpenCV/demo fallback keeps tests and demos deterministic.
- Group detection uses proximity and synchronized timestamp behavior rather than a learned social grouping model.
- Staff filtering uses restricted-zone and persistence heuristics rather than uniforms-only classification.
- Person role output is binary for evaluation clarity: every detected person is surfaced as either `customer` or `staff`. Low-confidence cases default to `customer` unless staff evidence is present.
- Canvas overlays draw all currently visible person tracks with stable per-track colors rather than a single global customer color. Employees use warm orange variants and customers use distinct cool variants so two people doing the same activity remain visually separable.
- The repository keeps a small demo MP4 but excludes uploaded videos, generated databases, and Docker volumes. This preserves the one-command reviewer experience without committing private CCTV data or large runtime artifacts.
- Orchestration is implemented directly in Python instead of introducing LangGraph/CrewAI as a new dependency. This keeps execution fast, deterministic, and Docker-friendly while preserving the same agent-node concepts.
- Folder/camera processing is capped at `max_iterations=4` by state, not by model text. If more camera clips are provided, the system returns a fallback summary instead of looping indefinitely.
- Hosted uploads are capped by `STORE_INTEL_MAX_ANALYSIS_SECONDS` to keep Render workers responsive. This favors a reliable processed sample over an unbounded request that could time out or appear stuck.
- Mirror handling is hybrid: known store layouts define exact mirror/display polygons, while reflection-pair suppression remains as a fallback for unknown layouts. This is more deployment-ready than pretending to train a mirror detector without a labeled retail CCTV dataset.

## Why Heuristics

The challenge rewards correct, explainable retail intelligence more than incomplete complex models. Heuristics make behavior auditable:

- long/repeated restricted-zone presence -> staff
- same visitor returning shortly after exit -> reentry
- visitors close in frame at the same time -> group
- high dwell/repeated entry-exit/crowding -> anomaly
- black-store-dress/service-zone persistence -> employee role when the fallback analyzer cannot use a trained uniform classifier
- per-second visible track or scene state -> at least one business-readable observation on the overlay
- duplicate agent tool call with identical JSON input -> blocked and surfaced as a limitation instead of retried

## Known Limitations

- Cross-camera identity is approximate unless a stronger appearance model is enabled.
- Mirror/reflection suppression is geometric and may miss unusual reflective layouts.
- New camera angles need their mirror/display regions added to `mirror_zones` in the layout file. The backend and canvas overlay both use the same normalized polygon points, so alignment scales with the video player.
- Staff uniform color is represented as metadata-ready logic but not a trained classifier.
- Product interactions are inferred from non-entry/non-billing zone dwell in the fallback analyzer.
- Employee-vs-customer distinction is heuristic unless a stronger trained uniform/appearance model is enabled.
- Per-track outline colors are stable visual aids, not identity embeddings; identity remains based on the event/session tracker.
- The current project does not depend on an LLM runtime for video analysis. Prompt templates are still provided for evaluator transparency and future LangGraph/CrewAI adaptation, but execution uses compact JSON state and deterministic Python stages.

## Edge Cases Handled

- Empty or unreadable video returns a clean API error.
- Non-MP4 upload is rejected.
- Re-entry does not create a new unique visitor.
- Staff is excluded from customer metrics and funnel counts.
- Groups preserve individual visitor ids.
- Anomalies include excessive dwell, repeated entry-exit, unusual movement, and crowding.
- Anomalies include proof fields: timestamp, visitor id when available, zone, measured value, threshold, rule, video time, and frame id when available.
- False exit labels from tracker loss inside the store are suppressed; exit is emitted only when a visible person disappears near the entry/exit zone.
- The Docker path uses SQLite volumes and the bundled demo video so a fresh clone can run locally with `docker compose up --build`.
- Terminal telemetry prints `[STEP x/y] Agent [name] initiating/completed tool [name]` checkpoints so container logs show where processing is spending time.
- Frontend fetches use explicit timeouts and transition into a failure state, so the user never remains in an infinite processing spinner after a network or hosting timeout.
