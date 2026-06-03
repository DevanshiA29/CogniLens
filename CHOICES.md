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

## Why Heuristics

The challenge rewards correct, explainable retail intelligence more than incomplete complex models. Heuristics make behavior auditable:

- long/repeated restricted-zone presence -> staff
- same visitor returning shortly after exit -> reentry
- visitors close in frame at the same time -> group
- high dwell/repeated entry-exit/crowding -> anomaly

## Known Limitations

- Cross-camera identity is approximate unless a stronger appearance model is enabled.
- Mirror/reflection suppression is geometric and may miss unusual reflective layouts.
- Staff uniform color is represented as metadata-ready logic but not a trained classifier.
- Product interactions are inferred from non-entry/non-billing zone dwell in the fallback analyzer.

## Edge Cases Handled

- Empty or unreadable video returns a clean API error.
- Non-MP4 upload is rejected.
- Re-entry does not create a new unique visitor.
- Staff is excluded from customer metrics and funnel counts.
- Groups preserve individual visitor ids.
- Anomalies include excessive dwell, repeated entry-exit, unusual movement, and crowding.
- Anomalies include proof fields: timestamp, visitor id when available, zone, measured value, threshold, rule, video time, and frame id when available.
