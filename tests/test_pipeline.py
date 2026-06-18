import numpy as np

from store_intel.agents.group_detector import GroupDetector
from store_intel.agents.input_agent import InputAgent
from store_intel.agents.query_agent import TimestampQueryAgent
from store_intel.agents.frame_analyzer import FrameAnalyzerAgent
from store_intel.agents.staff_classifier import StaffClassifier
from store_intel.pipeline import StoreIntelligencePipeline


def test_pipeline_processes_demo_video_into_events(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_INTEL_FORCE_SYNTHETIC_DEMO", "1")
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


def test_analyzer_removes_configured_mirror_zone_detections():
    layout = InputAgent.default_layout()
    frame_shape = (540, 960, 3)
    detections = [
        {"track_id": 1, "bbox": [132, 220, 64, 185], "confidence": 0.88, "model": "test"},
        {"track_id": 2, "bbox": [330, 245, 90, 230], "confidence": 0.86, "model": "test"},
    ]

    filtered = FrameAnalyzerAgent._remove_mirror_zone_detections(detections, frame_shape, layout, second=12)

    assert [detection["track_id"] for detection in filtered] == [2]
    assert "ENTRY_MIRROR" in layout["mirror_zones"]


def test_analyzer_keeps_partial_non_mirror_overlap_detections():
    layout = InputAgent.default_layout()
    frame_shape = (540, 960, 3)
    detections = [
        {"track_id": 1, "bbox": [200, 245, 90, 230], "confidence": 0.88, "model": "test"},
    ]

    filtered = FrameAnalyzerAgent._remove_mirror_zone_detections(detections, frame_shape, layout, second=12)

    assert [detection["track_id"] for detection in filtered] == [1]


def test_analyzer_removes_right_side_camera_angle_mirror_detections():
    layout = InputAgent.default_layout()
    frame_shape = (1080, 1920, 3)
    detections = [
        {"track_id": 1, "bbox": [1380, 300, 220, 620], "confidence": 0.9, "model": "test"},
        {"track_id": 2, "bbox": [900, 260, 170, 610], "confidence": 0.88, "model": "test"},
    ]

    filtered = FrameAnalyzerAgent._remove_mirror_zone_detections(detections, frame_shape, layout, second=48)

    assert [detection["track_id"] for detection in filtered] == [2]
    assert "RIGHT_PROMO_MIRROR" in layout["mirror_zones"]


def test_mirror_only_detections_do_not_create_headcount_events(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_INTEL_MAX_ANALYSIS_SECONDS", "1")
    pipeline = StoreIntelligencePipeline(db_path=tmp_path / "events.db")
    video = tmp_path / "mirror_only.mp4"
    from store_intel.demo import create_demo_video

    create_demo_video(video, duration_sec=1, fps=5)
    mirror_detection = [{"track_id": 1, "bbox": [132, 220, 64, 185], "confidence": 0.88, "model": "test"}]
    monkeypatch.setattr(pipeline.analyzer, "_detect_people", lambda frame: mirror_detection)
    monkeypatch.setattr(pipeline.analyzer, "_fallback_motion_people", lambda frame, second: [])
    monkeypatch.setattr(pipeline.analyzer, "_add_service_zone_people", lambda detections, frame, layout: detections)

    result = pipeline.process_video(video, "STORE_BLR_002", "CAM_MIRROR", replace_store=True)

    assert result["events_inserted"] == 0
    assert result["metrics"]["unique_visitors"] == 0


def test_analyzer_rejects_flat_poster_like_people_boxes():
    frame = np.zeros((540, 960, 3), dtype=np.uint8)
    detections = [
        {"track_id": 1, "bbox": [120, 70, 120, 140], "confidence": 0.9, "model": "test"},
        {"track_id": 2, "bbox": [320, 160, 85, 300], "confidence": 0.86, "model": "test"},
    ]

    filtered = FrameAnalyzerAgent._filter_person_like_detections(detections, frame)

    assert [detection["track_id"] for detection in filtered] == [2]


def test_analyzer_rejects_upper_wall_tv_ad_person_boxes():
    frame = np.zeros((540, 960, 3), dtype=np.uint8)
    cv2_like_noise = np.indices((140, 160)).sum(axis=0).astype(np.uint8) * 3
    frame[40:180, 620:780, 0] = cv2_like_noise
    frame[40:180, 620:780, 1] = 255 - cv2_like_noise
    frame[40:180, 620:780, 2] = cv2_like_noise // 2
    detections = [
        {"track_id": 1, "bbox": [620, 40, 160, 140], "confidence": 0.92, "model": "test"},
        {"track_id": 2, "bbox": [240, 170, 90, 305], "confidence": 0.84, "model": "test"},
    ]

    filtered = FrameAnalyzerAgent._filter_person_like_detections(detections, frame)

    assert [detection["track_id"] for detection in filtered] == [2]


def test_group_detector_keeps_relevant_group_id_stable():
    detector = GroupDetector()
    first = [
        {"track_id": 1, "bbox": [120, 210, 80, 260]},
        {"track_id": 2, "bbox": [215, 214, 80, 258]},
        {"track_id": 3, "bbox": [760, 90, 80, 260]},
    ]
    second = [
        {"track_id": 1, "bbox": [130, 210, 80, 260]},
        {"track_id": 2, "bbox": [225, 214, 80, 258]},
        {"track_id": 3, "bbox": [770, 90, 80, 260]},
    ]

    first_groups = detector.assign_groups(first, (540, 960, 3), second=1)
    second_groups = detector.assign_groups(second, (540, 960, 3), second=2)

    assert first_groups[1] == first_groups[2]
    assert second_groups[1] == first_groups[1]
    assert first_groups[3] is None


def test_excel_layout_maps_brigade_road_entry_assets_and_staff_zones(tmp_path):
    layout_path = tmp_path / "Brigade Road - Store layout.xlsx"
    layout_path.write_bytes(b"placeholder workbook")

    layout = InputAgent().load_store_layout(layout_path)

    assert layout["layout_name"] == "Brigade Road"
    assert "ENTRY" in layout["entry_zones"]
    assert "EXIT" in layout["exit_zones"]
    assert {"BILLING", "PMU"}.issubset(set(layout["staff_service_zones"]))
    assert {"WALL_PRODUCTS", "PRODUCT_AISLE", "CENTER_DISPLAY"}.issubset(set(layout["product_zones"]))


def test_brigade_layout_prioritizes_cash_counter_over_product_aisle():
    layout = InputAgent.default_layout()
    # x=0.9, y=0.7 overlaps the lower product aisle visually, but the floor
    # plan marks this right side as PMU/cash service.
    zone = FrameAnalyzerAgent._zone_for_bbox([835, 320, 80, 140], (540, 960, 3), layout)

    assert zone in {"BILLING", "PMU"}


def test_staff_classifier_keeps_service_area_tracks_as_employees():
    role, confidence = StaffClassifier().classify(
        "PMU",
        {"bbox": [850, 280, 80, 220]},
        track_seen_seconds=2,
        restricted_zones={"BILLING", "PMU"},
        first_zone="PMU",
        movement_ratio=0.02,
    )

    assert role == "staff"
    assert confidence >= 0.86


def test_service_zone_fallback_adds_employee_candidate_only_in_staff_area():
    frame = np.full((540, 960, 3), 35, dtype=np.uint8)
    cv2 = __import__("cv2")
    cv2.rectangle(frame, (865, 315), (905, 455), (220, 105, 110), -1)
    cv2.circle(frame, (885, 285), 22, (235, 125, 125), -1)
    layout = InputAgent.default_layout()

    detections = FrameAnalyzerAgent._add_service_zone_people([], frame, layout)

    assert detections
    assert detections[0]["track_id"] >= 900
    assert "service_zone_fallback" in detections[0]["model"]


def test_tracker_absence_only_counts_as_exit_near_doorway():
    layout = InputAgent.default_layout()

    assert FrameAnalyzerAgent._should_emit_exit("ENTRY", layout)
    assert FrameAnalyzerAgent._should_emit_exit("EXIT", layout)
    assert not FrameAnalyzerAgent._should_emit_exit("BILLING", layout)
    assert not FrameAnalyzerAgent._should_emit_exit("CENTER_DISPLAY", layout)
    assert not FrameAnalyzerAgent._should_emit_exit("PRODUCT_AISLE", layout)
