from datetime import datetime, timezone

from store_intel.agents.event_generator import EventGeneratorAgent
from store_intel.agents.memory_store import MemoryEventStoreAgent


def test_event_store_deduplicates_events_and_updates_sessions(tmp_path):
    db_path = tmp_path / "events.db"
    store = MemoryEventStoreAgent(db_path)
    generator = EventGeneratorAgent()
    ts = datetime(2026, 3, 3, 14, 22, 10, tzinfo=timezone.utc)

    event = generator.from_observation(
        {
            "store_id": "STORE_BLR_002",
            "camera_id": "CAM_ENTRY_01",
            "timestamp": ts.isoformat().replace("+00:00", "Z"),
            "visitor_id": "VIS_c8a2f1",
            "action": "entered_store",
            "zone": "ENTRY",
            "is_staff": False,
            "confidence": 0.91,
            "metadata": {"track_id": 7},
        }
    )

    inserted_once = store.ingest_events([event])
    inserted_twice = store.ingest_events([event])

    assert inserted_once == 1
    assert inserted_twice == 0
    assert store.count("events") == 1
    session = store.get_session("STORE_BLR_002", "VIS_c8a2f1")
    assert session["entry_time"] == event.timestamp
    assert session["exit_time"] is None


def test_event_store_tracks_zone_dwell(tmp_path):
    db_path = tmp_path / "events.db"
    store = MemoryEventStoreAgent(db_path)
    generator = EventGeneratorAgent()

    enter = generator.from_observation(
        {
            "store_id": "STORE_BLR_002",
            "camera_id": "CAM_MAIN_01",
            "timestamp": "2026-03-03T14:22:10Z",
            "visitor_id": "VIS_1",
            "action": "zone_enter",
            "zone": "AISLE_A",
            "is_staff": False,
            "confidence": 0.88,
        }
    )
    exit_event = generator.from_observation(
        {
            "store_id": "STORE_BLR_002",
            "camera_id": "CAM_MAIN_01",
            "timestamp": "2026-03-03T14:22:15Z",
            "visitor_id": "VIS_1",
            "action": "zone_exit",
            "zone": "AISLE_A",
            "is_staff": False,
            "confidence": 0.86,
        }
    )

    store.ingest_events([enter, exit_event])

    dwell = store.zone_dwell("STORE_BLR_002")
    assert dwell["AISLE_A"]["total_dwell_ms"] == 5000
    assert dwell["AISLE_A"]["visits"] == 1


def test_reentry_does_not_create_new_unique_visitor(tmp_path):
    store = MemoryEventStoreAgent(tmp_path / "events.db")
    generator = EventGeneratorAgent()
    events = [
        generator.from_observation(
            {
                "store_id": "STORE_BLR_002",
                "camera_id": "CAM_ENTRY_01",
                "timestamp": "2026-03-03T14:22:10Z",
                "visitor_id": "VIS_1",
                "track_id": "T1",
                "action": "entered_store",
                "zone": "ENTRY",
                "confidence": 0.9,
            }
        ),
        generator.from_observation(
            {
                "store_id": "STORE_BLR_002",
                "camera_id": "CAM_ENTRY_01",
                "timestamp": "2026-03-03T14:22:20Z",
                "visitor_id": "VIS_1",
                "track_id": "T1",
                "action": "exited_store",
                "zone": "EXIT",
                "confidence": 0.9,
            }
        ),
        generator.from_observation(
            {
                "store_id": "STORE_BLR_002",
                "camera_id": "CAM_ENTRY_01",
                "timestamp": "2026-03-03T14:22:40Z",
                "visitor_id": "VIS_1",
                "track_id": "T1",
                "action": "entered_store",
                "zone": "ENTRY",
                "confidence": 0.88,
            }
        ),
    ]

    store.ingest_events(events)

    session = store.get_session("STORE_BLR_002", "VIS_1")
    assert session["reentry_count"] == 1
    assert store.rows("SELECT COUNT(*) AS n FROM sessions WHERE store_id = ?", ("STORE_BLR_002",))[0]["n"] == 1


def test_staff_group_and_excessive_dwell_are_recorded(tmp_path):
    store = MemoryEventStoreAgent(tmp_path / "events.db")
    generator = EventGeneratorAgent()
    events = [
        generator.from_observation(
            {
                "store_id": "STORE_BLR_002",
                "camera_id": "CAM_BILL_01",
                "timestamp": "2026-03-03T14:22:10Z",
                "visitor_id": "VIS_STAFF",
                "track_id": "S1",
                "group_id": "GRP_staff",
                "action": "zone_dwell",
                "zone": "BILLING",
                "role": "staff",
                "is_staff": True,
                "dwell_ms": 1000,
                "confidence": 0.95,
            }
        ),
        generator.from_observation(
            {
                "store_id": "STORE_BLR_002",
                "camera_id": "CAM_MAIN",
                "timestamp": "2026-03-03T14:22:12Z",
                "visitor_id": "VIS_1",
                "track_id": "T1",
                "group_id": "GRP_1",
                "action": "zone_dwell",
                "zone": "AISLE_A",
                "dwell_ms": 901000,
                "confidence": 0.9,
            }
        ),
    ]

    store.ingest_events(events)

    staff = store.get_session("STORE_BLR_002", "VIS_STAFF")
    customer = store.get_session("STORE_BLR_002", "VIS_1")
    anomalies = store.rows("SELECT * FROM anomalies WHERE store_id = ?", ("STORE_BLR_002",))
    assert staff["is_staff"] == 1
    assert staff["group_id"] == "GRP_staff"
    assert customer["group_id"] == "GRP_1"
    assert any(row["anomaly_type"] == "EXCESSIVE_DWELL" for row in anomalies)


def test_people_roles_are_classified_only_as_customer_or_staff(tmp_path):
    store = MemoryEventStoreAgent(tmp_path / "events.db")
    generator = EventGeneratorAgent()
    events = [
        generator.from_observation(
            {
                "store_id": "STORE_BLR_002",
                "camera_id": "CAM_ENTRY",
                "timestamp": "2026-03-03T14:22:10Z",
                "visitor_id": "VIS_CUSTOMER",
                "track_id": "T1",
                "action": "entered_store",
                "zone": "ENTRY",
                "confidence": 0.8,
            }
        ),
        generator.from_observation(
            {
                "store_id": "STORE_BLR_002",
                "camera_id": "CAM_BILL",
                "timestamp": "2026-03-03T14:22:11Z",
                "visitor_id": "VIS_STAFF",
                "track_id": "S1",
                "action": "entered_store",
                "zone": "BILLING",
                "role": "staff",
                "is_staff": True,
                "confidence": 0.9,
            }
        ),
    ]

    store.ingest_events(events)

    roles = {row["role"] for row in store.rows("SELECT role FROM events")}
    assert roles == {"customer", "staff"}
