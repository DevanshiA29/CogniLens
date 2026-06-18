import time

from fastapi.testclient import TestClient

from store_intel.api.app import create_app
from store_intel.demo import create_demo_video


def test_api_ingests_events_and_returns_timeline_summary(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "events.db"))
    event = {
        "event_id": "EVT_test_1",
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_1",
        "event_type": "ENTRY",
        "timestamp": "2026-03-03T14:22:10Z",
        "zone_id": "ENTRY",
        "dwell_ms": None,
        "is_staff": False,
        "confidence": 0.91,
        "metadata": {"source": "test"},
    }

    ingest = client.post("/events/ingest", json={"events": [event]})
    assert ingest.status_code == 200
    assert ingest.json()["inserted"] == 1

    timeline = client.get(
        "/stores/STORE_BLR_002/timeline",
        params={"timestamp": "2026-03-03T14:22:10Z"},
    )
    assert timeline.status_code == 200
    body = timeline.json()
    assert body["active_visitors"] == 1
    assert body["zone_activity"]["ENTRY"] == 1
    assert body["summary"] == "1 customer visible: 1 in Entrance."
    assert body["display_events"][0]["headline"] == "Currently in Entrance"
    assert body["raw_display_events"][0]["headline"] == "Customer entered store"


def test_api_metrics_funnel_heatmap_and_anomalies(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "events.db"))
    events = [
        {
            "event_id": "EVT_entry",
            "store_id": "STORE_BLR_002",
            "camera_id": "CAM_ENTRY_01",
            "visitor_id": "VIS_1",
            "event_type": "ENTRY",
            "timestamp": "2026-03-03T14:22:10Z",
            "zone_id": "ENTRY",
            "dwell_ms": None,
            "is_staff": False,
            "confidence": 0.91,
            "metadata": {},
        },
        {
            "event_id": "EVT_bill",
            "store_id": "STORE_BLR_002",
            "camera_id": "CAM_BILL_01",
            "visitor_id": "VIS_1",
            "event_type": "BILLING_QUEUE_JOIN",
            "timestamp": "2026-03-03T14:23:10Z",
            "zone_id": "BILLING",
            "dwell_ms": None,
            "is_staff": False,
            "confidence": 0.87,
            "metadata": {},
        },
        {
            "event_id": "EVT_dwell",
            "store_id": "STORE_BLR_002",
            "camera_id": "CAM_MAIN_01",
            "visitor_id": "VIS_1",
            "event_type": "ZONE_DWELL",
            "timestamp": "2026-03-03T14:23:30Z",
            "zone_id": "AISLE_A",
            "dwell_ms": 12000,
            "is_staff": False,
            "confidence": 0.8,
            "metadata": {},
        },
    ]
    client.post("/events/ingest", json={"events": events})

    assert client.get("/stores/STORE_BLR_002/metrics").json()["unique_visitors"] == 1
    assert client.get("/stores/STORE_BLR_002/funnel").json()["billing_queue_join"] == 1
    assert client.get("/stores/STORE_BLR_002/heatmap").json()["zones"]["AISLE_A"] == 12000
    assert client.get("/stores/STORE_BLR_002/anomalies").status_code == 200


def test_timeline_range_tracks_actual_event_window(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "events.db"))
    client.post(
        "/events/ingest",
        json={
            "events": [
                {
                    "event_id": "EVT_first",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_ENTRY_01",
                    "visitor_id": "VIS_1",
                    "event_type": "ENTRY",
                    "timestamp": "2026-03-03T14:22:10Z",
                    "zone_id": "ENTRY",
                    "dwell_ms": None,
                    "is_staff": False,
                    "confidence": 0.91,
                    "metadata": {},
                },
                {
                    "event_id": "EVT_last",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_MAIN_01",
                    "visitor_id": "VIS_1",
                    "event_type": "ZONE_ENTER",
                    "timestamp": "2026-03-03T14:22:18Z",
                    "zone_id": "AISLE_A",
                    "dwell_ms": None,
                    "is_staff": False,
                    "confidence": 0.85,
                    "metadata": {},
                },
            ]
        },
    )

    response = client.get("/stores/STORE_BLR_002/timeline/range")

    assert response.status_code == 200
    assert response.json() == {
        "store_id": "STORE_BLR_002",
        "start_timestamp": "2026-03-03T14:22:10Z",
        "end_timestamp": "2026-03-03T14:22:18Z",
        "duration_sec": 8,
        "event_count": 2,
    }


def test_upload_rejects_non_mp4_files(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "events.db"))

    response = client.post(
        "/videos/upload",
        data={"store_id": "STORE_BLR_002", "camera_id": "CAM_ENTRY_01"},
        files={"file": ("bad.txt", b"not a video", "text/plain")},
    )

    assert response.status_code == 400
    assert "MP4" in response.json()["detail"]


def test_upload_processes_mp4_and_replaces_store_events(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "events.db"))
    video = create_demo_video(tmp_path / "demo.mp4", duration_sec=2, fps=5)
    client.post(
        "/events/ingest",
        json={
            "events": [
                {
                    "event_id": "EVT_old",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_OLD",
                    "visitor_id": "VIS_OLD",
                    "event_type": "ENTRY",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "zone_id": "ENTRY",
                    "dwell_ms": None,
                    "is_staff": False,
                    "confidence": 0.9,
                    "metadata": {},
                }
            ]
        },
    )

    with video.open("rb") as handle:
        response = client.post(
            "/videos/upload",
            data={"store_id": "STORE_BLR_002", "camera_id": "CAM_ENTRY_01"},
            files={"file": ("demo.mp4", handle, "video/mp4")},
        )

    assert response.status_code == 200
    assert response.json()["events_inserted"] > 0
    timeline_range = client.get("/stores/STORE_BLR_002/timeline/range").json()
    assert timeline_range["start_timestamp"] == "2026-03-03T14:22:10Z"


def test_async_upload_returns_job_and_completes(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "events.db"))
    video = create_demo_video(tmp_path / "demo.mp4", duration_sec=2, fps=5)

    with video.open("rb") as handle:
        response = client.post(
            "/videos/upload",
            data={"store_id": "STORE_BLR_002", "camera_id": "CAM_ENTRY_01", "async_mode": "true"},
            files={"file": ("demo.mp4", handle, "video/mp4")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["job_id"].startswith("JOB_")

    job = None
    for _ in range(20):
        status = client.get(f"/videos/jobs/{body['job_id']}")
        assert status.status_code == 200
        job = status.json()
        if job["status"] == "completed":
            break
        time.sleep(0.1)

    assert job is not None
    assert job["status"] == "completed"
    assert job["result"]["events_inserted"] > 0


def test_processed_video_is_available_for_preview(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "events.db"))
    video = create_demo_video(tmp_path / "demo.mp4", duration_sec=2, fps=5)

    with video.open("rb") as handle:
        upload = client.post(
            "/videos/upload",
            data={"store_id": "STORE_BLR_002", "camera_id": "CAM_ENTRY_01"},
            files={"file": ("demo.mp4", handle, "video/mp4")},
        )

    assert upload.status_code == 200
    current = client.get("/stores/STORE_BLR_002/video/current")
    assert current.status_code == 200
    assert current.json()["video_url"] == "/stores/STORE_BLR_002/video/stream"
    assert current.json()["frame_url"] == "/stores/STORE_BLR_002/video/frame"
    assert current.json()["width"] > 0
    assert current.json()["height"] > 0
    stream = client.get("/stores/STORE_BLR_002/video/stream")
    assert stream.status_code == 200
    assert stream.headers["content-type"] == "video/mp4"
    assert "content-disposition" not in stream.headers
    poster = client.get("/stores/STORE_BLR_002/video/poster")
    assert poster.status_code == 200
    assert poster.headers["content-type"] == "image/jpeg"
    assert len(poster.content) > 1000
    frame = client.get("/stores/STORE_BLR_002/video/frame", params={"second": 1})
    assert frame.status_code == 200
    assert frame.headers["content-type"] == "image/jpeg"
    assert len(frame.content) > 1000


def test_processed_video_can_be_saved_and_loaded_without_reprocessing(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_INTEL_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = TestClient(create_app(db_path=tmp_path / "events.db"))
    video = create_demo_video(tmp_path / "demo.mp4", duration_sec=2, fps=5)

    with video.open("rb") as handle:
        upload = client.post(
            "/videos/upload",
            data={"store_id": "STORE_BLR_002", "camera_id": "CAM_ENTRY_01"},
            files={"file": ("demo.mp4", handle, "video/mp4")},
        )

    assert upload.status_code == 200
    original_events = upload.json()["events_inserted"]
    save = client.post("/demo/reviews", json={"store_id": "STORE_BLR_002", "title": "Morning floor review"})
    assert save.status_code == 200
    saved = save.json()
    assert saved["events"] == original_events
    assert saved["title"] == "Morning floor review"
    assert saved["cache"] == "temporary"
    duplicate_save = client.post("/demo/reviews", json={"store_id": "STORE_BLR_002", "title": "Morning floor review"})
    assert duplicate_save.status_code == 200
    assert duplicate_save.json()["review_id"] == saved["review_id"]

    reviews = client.get("/demo/reviews", params={"store_id": "STORE_BLR_002"})
    assert reviews.status_code == 200
    assert len(reviews.json()["reviews"]) == 1
    assert reviews.json()["reviews"][0]["review_id"] == saved["review_id"]

    client.post(
        "/events/ingest",
        json={
            "events": [
                {
                    "event_id": "EVT_other_store_noise",
                    "store_id": "STORE_OTHER",
                    "camera_id": "CAM_X",
                    "visitor_id": "VIS_X",
                    "event_type": "ENTRY",
                    "timestamp": "2026-03-03T14:30:10Z",
                    "zone_id": "ENTRY",
                    "confidence": 0.9,
                    "metadata": {},
                }
            ]
        },
    )
    load = client.post(f"/demo/reviews/{saved['review_id']}/load", json={"store_id": "STORE_BLR_002"})
    assert load.status_code == 200
    assert load.json()["events_inserted"] == original_events
    assert client.get("/stores/STORE_BLR_002/metrics").json()["events"] == original_events
    assert client.get("/stores/STORE_BLR_002/video/current").status_code == 200


def test_agent_score_uses_weighted_formula_after_processing(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "events.db"))
    video = create_demo_video(tmp_path / "demo.mp4", duration_sec=2, fps=5)

    with video.open("rb") as handle:
        upload = client.post(
            "/videos/upload",
            data={"store_id": "STORE_BLR_002", "camera_id": "CAM_ENTRY_01"},
            files={"file": ("demo.mp4", handle, "video/mp4")},
        )

    assert upload.status_code == 200
    score = client.get("/score", params={"store_id": "STORE_BLR_002"}).json()
    assert score["label"] == "Self-Evaluation Based on Rubric"
    assert score["total"] == score["detection"] + score["api"] + score["production"] + score["thinking"]
    assert score["weights"] == {"detection": 30, "api": 35, "production": 20, "thinking": 15}
    assert 0 < score["total"] <= 100
    assert 0 <= score["detection"] <= 30
    assert 0 <= score["api"] <= 35
    assert 0 <= score["production"] <= 20
    assert 0 <= score["thinking"] <= 15
    evidence = score["evidence"]
    assert evidence["events_generated"] > 0
    assert evidence["unique_visitors"] > 0
    assert evidence["apis_passing"] <= evidence["apis_total"]
    assert isinstance(evidence["docs_present"], bool)
    assert set(evidence["api_checks"]) == {
        "metrics",
        "funnel",
        "zones",
        "anomalies",
        "logical_consistency",
    }


def test_global_metrics_funnel_zones_and_visitor_timeline_are_session_based(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "events.db"))
    client.post(
        "/events/ingest",
        json={
            "events": [
                {
                    "event_id": "EVT_customer_entry",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_ENTRY",
                    "visitor_id": "VIS_1",
                    "track_id": "T1",
                    "group_id": "GRP_1",
                    "role": "customer",
                    "event_type": "ENTRY",
                    "timestamp": "2026-03-03T14:22:10Z",
                    "video_time_sec": 0,
                    "frame_id": 0,
                    "zone_id": "ENTRY",
                    "confidence": 0.9,
                    "metadata": {},
                },
                {
                    "event_id": "EVT_customer_product",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_MAIN",
                    "visitor_id": "VIS_1",
                    "track_id": "T1",
                    "group_id": "GRP_1",
                    "role": "customer",
                    "event_type": "PRODUCT_INTERACTION",
                    "timestamp": "2026-03-03T14:22:15Z",
                    "video_time_sec": 5,
                    "frame_id": 75,
                    "zone_id": "AISLE_A",
                    "dwell_ms": 1000,
                    "confidence": 0.84,
                    "metadata": {},
                },
                {
                    "event_id": "EVT_customer_checkout",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_BILL",
                    "visitor_id": "VIS_1",
                    "track_id": "T1",
                    "group_id": "GRP_1",
                    "role": "customer",
                    "event_type": "CHECKOUT_VISIT",
                    "timestamp": "2026-03-03T14:22:30Z",
                    "video_time_sec": 20,
                    "frame_id": 300,
                    "zone_id": "BILLING",
                    "confidence": 0.88,
                    "metadata": {},
                },
                {
                    "event_id": "EVT_staff",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_BILL",
                    "visitor_id": "VIS_STAFF",
                    "track_id": "S1",
                    "role": "staff",
                    "event_type": "ENTRY",
                    "timestamp": "2026-03-03T14:22:12Z",
                    "video_time_sec": 2,
                    "frame_id": 30,
                    "zone_id": "BILLING",
                    "is_staff": True,
                    "confidence": 0.95,
                    "metadata": {},
                },
            ]
        },
    )

    metrics = client.get("/metrics", params={"store_id": "STORE_BLR_002"}).json()
    funnel = client.get("/funnel", params={"store_id": "STORE_BLR_002"}).json()
    zones = client.get("/zones", params={"store_id": "STORE_BLR_002"}).json()
    timeline = client.get("/visitor/VIS_1/timeline", params={"store_id": "STORE_BLR_002"}).json()

    assert metrics["unique_visitors"] == 1
    assert metrics["staff_count"] == 1
    assert metrics["groups_detected"] == 1
    assert funnel["entered_store"] == 1
    assert funnel["visited_product_zone"] == 1
    assert funnel["product_interaction"] == 1
    assert funnel["billing_counter"] == 1
    assert funnel["checkout_visit"] == 1
    assert [step["label"] for step in funnel["flow"]] == [
        "Entered Store",
        "Visited Product Zone",
        "Product Interaction",
        "Billing Counter",
        "Exit",
    ]
    assert funnel["attention_scores"][0]["visitor"] == "V1"
    assert 0 <= funnel["attention_scores"][0]["attention_score"] <= 100
    assert funnel["attention_scores"][0]["product_interactions"] == 1
    assert zones["zones"]["AISLE_A"]["visits"] >= 1
    assert timeline["visitor_id"] == "VIS_1"
    assert timeline["converted"] is True
    assert timeline["purchase_intent_score"] > 0


def test_anomalies_include_authentic_proof_fields(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "events.db"))
    client.post(
        "/events/ingest",
        json={
            "events": [
                {
                    "event_id": "EVT_LONG_DWELL",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_MAIN",
                    "visitor_id": "VIS_1",
                    "track_id": "T1",
                    "role": "customer",
                    "event_type": "ZONE_DWELL",
                    "timestamp": "2026-03-03T14:22:10Z",
                    "video_time_sec": 42,
                    "frame_id": 630,
                    "zone_id": "AISLE_A",
                    "dwell_ms": 901000,
                    "confidence": 0.91,
                    "metadata": {},
                }
            ]
        },
    )

    body = client.get("/anomalies", params={"store_id": "STORE_BLR_002"}).json()
    anomaly = body["anomalies"][0]

    assert anomaly["timestamp"] == "2026-03-03T14:22:10Z"
    assert anomaly["proof"]["visitor_id"] == "VIS_1"
    assert anomaly["proof"]["zone"] == "AISLE_A"
    assert anomaly["proof"]["measured_value"] == 901000
    assert anomaly["proof"]["threshold"] == 900000
    assert anomaly["proof"]["rule"] == "dwell_ms >= 900000"
    assert anomaly["confidence"] == 0.82


def test_timeline_heatmap_counts_current_zone_presence_not_raw_events(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "events.db"))
    client.post(
        "/events/ingest",
        json={
            "events": [
                {
                    "event_id": "EVT_exit",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_MAIN",
                    "visitor_id": "VIS_1",
                    "event_type": "ZONE_EXIT",
                    "timestamp": "2026-03-03T14:22:11Z",
                    "zone_id": "BILLING",
                    "dwell_ms": None,
                    "is_staff": False,
                    "confidence": 0.9,
                    "metadata": {},
                },
                {
                    "event_id": "EVT_enter",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_MAIN",
                    "visitor_id": "VIS_1",
                    "event_type": "ZONE_ENTER",
                    "timestamp": "2026-03-03T14:22:11Z",
                    "zone_id": "ENTRY",
                    "dwell_ms": None,
                    "is_staff": False,
                    "confidence": 0.9,
                    "metadata": {},
                },
                {
                    "event_id": "EVT_dwell",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_MAIN",
                    "visitor_id": "VIS_1",
                    "event_type": "ZONE_DWELL",
                    "timestamp": "2026-03-03T14:22:11Z",
                    "zone_id": "ENTRY",
                    "dwell_ms": 1000,
                    "is_staff": False,
                    "confidence": 0.9,
                    "metadata": {},
                },
            ]
        },
    )

    body = client.get(
        "/stores/STORE_BLR_002/timeline",
        params={"timestamp": "2026-03-03T14:22:11Z"},
    ).json()

    assert body["zone_activity"] == {"ENTRY": 1}
    assert body["active_events"][0]["event_type"] == "ZONE_DWELL"
    assert body["display_events"][0]["headline"] == "Currently in Entrance"
    assert body["raw_display_events"][0]["headline"] == "Moved out of Checkout"
    assert body["raw_display_events"][1]["headline"] == "Moved into Entrance"
    assert body["raw_display_events"][2]["headline"] == "Dwelling in Entrance"


def test_timeline_returns_one_display_row_per_current_visitor_state(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "events.db"))
    events = []
    for visitor_id, zone_id in [("VIS_1", "ENTRY"), ("VIS_2", "AISLE_A")]:
        events.extend(
            [
                {
                    "event_id": f"EVT_entry_{visitor_id}",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_MAIN",
                    "visitor_id": visitor_id,
                    "event_type": "ENTRY",
                    "timestamp": "2026-03-03T14:22:11Z",
                    "zone_id": "ENTRY",
                    "dwell_ms": None,
                    "is_staff": False,
                    "confidence": 0.8,
                    "metadata": {},
                },
                {
                    "event_id": f"EVT_enter_{visitor_id}",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_MAIN",
                    "visitor_id": visitor_id,
                    "event_type": "ZONE_ENTER",
                    "timestamp": "2026-03-03T14:22:11Z",
                    "zone_id": zone_id,
                    "dwell_ms": None,
                    "is_staff": False,
                    "confidence": 0.82,
                    "metadata": {},
                },
                {
                    "event_id": f"EVT_dwell_{visitor_id}",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_MAIN",
                    "visitor_id": visitor_id,
                    "event_type": "ZONE_DWELL",
                    "timestamp": "2026-03-03T14:22:11Z",
                    "zone_id": zone_id,
                    "dwell_ms": 1000,
                    "is_staff": False,
                    "confidence": 0.84,
                    "metadata": {},
                },
            ]
        )
    client.post("/events/ingest", json={"events": events})

    body = client.get(
        "/stores/STORE_BLR_002/timeline",
        params={"timestamp": "2026-03-03T14:22:11Z"},
    ).json()

    assert body["summary"] == "2 customers visible: 1 in Entrance, 1 in Aisle A."
    assert body["zone_activity"] == {"ENTRY": 1, "AISLE_A": 1}
    assert [event["headline"] for event in body["display_events"]] == [
        "Currently in Entrance",
        "Currently in Aisle A",
    ]
