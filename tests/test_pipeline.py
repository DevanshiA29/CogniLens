from store_intel.pipeline import StoreIntelligencePipeline
from store_intel.agents.query_agent import TimestampQueryAgent
from store_intel.agents.frame_analyzer import FrameAnalyzerAgent


def test_pipeline_processes_demo_video_into_events(tmp_path):
    pipeline = StoreIntelligencePipeline(db_path=tmp_path / "events.db")
    result = pipeline.run_demo(
        store_id="STORE_BLR_002",
        camera_id="CAM_ENTRY_01",
        duration_sec=3,
        fps=5,
    )

    assert result["input"]["duration_sec"] == 3
    assert result["events_inserted"] > 0
    assert result["metrics"]["unique_visitors"] >= 1

    timeline = TimestampQueryAgent(pipeline.store).at_timestamp("STORE_BLR_002", "2026-03-03T14:22:11Z")
    assert any(event["event_type"] == "ZONE_DWELL" for event in timeline["events"])


def test_analyzer_removes_probable_mirror_reflections():
    detections = [
        {"track_id": 1, "bbox": [110, 80, 80, 210], "confidence": 0.88, "model": "test"},
        {"track_id": 2, "bbox": [810, 82, 80, 208], "confidence": 0.86, "model": "test"},
        {"track_id": 3, "bbox": [420, 92, 70, 190], "confidence": 0.8, "model": "test"},
    ]

    filtered = FrameAnalyzerAgent._remove_probable_reflections(detections, (540, 1000, 3))

    assert [detection["track_id"] for detection in filtered] == [1, 3]
