from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from store_intel.schemas import EventType, StoreEvent, parse_timestamp


class MemoryEventStoreAgent:
    """SQLite-backed event memory for events, sessions, tracks, dwell, POS, anomalies."""

    def __init__(self, db_path: str | Path = "data/store_intel.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                  event_id TEXT PRIMARY KEY,
                  store_id TEXT NOT NULL,
                  camera_id TEXT NOT NULL,
                  visitor_id TEXT NOT NULL,
                  video_time_sec REAL,
                  frame_id INTEGER,
                  track_id TEXT,
                  group_id TEXT,
                  role TEXT NOT NULL DEFAULT 'unknown',
                  event_type TEXT NOT NULL,
                  timestamp TEXT NOT NULL,
                  zone_id TEXT,
                  dwell_ms INTEGER,
                  is_staff INTEGER NOT NULL,
                  confidence REAL NOT NULL,
                  metadata TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_store_time ON events(store_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_events_store_type ON events(store_id, event_type);

                CREATE TABLE IF NOT EXISTS sessions (
                  session_id TEXT PRIMARY KEY,
                  store_id TEXT NOT NULL,
                  visitor_id TEXT NOT NULL,
                  first_seen TEXT,
                  last_seen TEXT,
                  entry_time TEXT,
                  exit_time TEXT,
                  zones_visited TEXT NOT NULL DEFAULT '[]',
                  dwell_time_by_zone TEXT NOT NULL DEFAULT '{}',
                  interactions TEXT NOT NULL DEFAULT '[]',
                  reentry_count INTEGER NOT NULL DEFAULT 0,
                  is_staff INTEGER NOT NULL DEFAULT 0,
                  group_id TEXT,
                  metadata TEXT NOT NULL DEFAULT '{}',
                  UNIQUE(store_id, visitor_id)
                );

                CREATE TABLE IF NOT EXISTS visitor_tracks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  store_id TEXT NOT NULL,
                  camera_id TEXT NOT NULL,
                  visitor_id TEXT NOT NULL,
                  track_id TEXT,
                  group_id TEXT,
                  role TEXT NOT NULL DEFAULT 'unknown',
                  timestamp TEXT NOT NULL,
                  zone_id TEXT,
                  action TEXT NOT NULL,
                  bbox TEXT,
                  confidence REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS zone_dwell (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  store_id TEXT NOT NULL,
                  visitor_id TEXT NOT NULL,
                  zone_id TEXT NOT NULL,
                  enter_time TEXT NOT NULL,
                  exit_time TEXT,
                  dwell_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS pos_transactions (
                  transaction_id TEXT PRIMARY KEY,
                  store_id TEXT NOT NULL,
                  timestamp TEXT NOT NULL,
                  amount REAL NOT NULL,
                  metadata TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS anomalies (
                  anomaly_id TEXT PRIMARY KEY,
                  store_id TEXT NOT NULL,
                  timestamp TEXT NOT NULL,
                  anomaly_type TEXT NOT NULL,
                  severity TEXT NOT NULL,
                  message TEXT NOT NULL,
                  metadata TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS processed_videos (
                  store_id TEXT PRIMARY KEY,
                  video_path TEXT NOT NULL,
                  camera_id TEXT NOT NULL,
                  duration_sec INTEGER NOT NULL,
                  fps INTEGER NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS saved_reviews (
                  review_id TEXT PRIMARY KEY,
                  store_id TEXT NOT NULL,
                  title TEXT NOT NULL,
                  video_path TEXT NOT NULL,
                  camera_id TEXT NOT NULL,
                  duration_sec INTEGER NOT NULL,
                  fps INTEGER NOT NULL,
                  updated_at TEXT NOT NULL,
                  saved_at TEXT NOT NULL,
                  events_json TEXT NOT NULL
                );
                """
            )
            self._ensure_columns(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            "events": {
                "video_time_sec": "REAL",
                "frame_id": "INTEGER",
                "track_id": "TEXT",
                "group_id": "TEXT",
                "role": "TEXT NOT NULL DEFAULT 'unknown'",
            },
            "sessions": {
                "first_seen": "TEXT",
                "last_seen": "TEXT",
                "zones_visited": "TEXT NOT NULL DEFAULT '[]'",
                "dwell_time_by_zone": "TEXT NOT NULL DEFAULT '{}'",
                "interactions": "TEXT NOT NULL DEFAULT '[]'",
                "reentry_count": "INTEGER NOT NULL DEFAULT 0",
                "group_id": "TEXT",
            },
            "visitor_tracks": {
                "track_id": "TEXT",
                "group_id": "TEXT",
                "role": "TEXT NOT NULL DEFAULT 'unknown'",
            },
        }
        for table, table_columns in columns.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, definition in table_columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def ingest_events(self, events: list[StoreEvent]) -> int:
        inserted = 0
        with self.connect() as conn:
            for event in events:
                result = conn.execute(
                    """
                    INSERT OR IGNORE INTO events
                    (event_id, store_id, camera_id, visitor_id, video_time_sec, frame_id,
                     track_id, group_id, role, event_type, timestamp,
                     zone_id, dwell_ms, is_staff, confidence, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.store_id,
                        event.camera_id,
                        event.visitor_id,
                        event.video_time_sec,
                        event.frame_id,
                        event.track_id,
                        event.group_id,
                        event.role.value,
                        event.event_type.value,
                        event.timestamp,
                        event.zone_id or event.zone,
                        event.dwell_ms,
                        int(event.is_staff or event.role.value == "staff"),
                        event.confidence,
                        json.dumps(event.metadata),
                    ),
                )
                if result.rowcount:
                    inserted += 1
                    self._update_derived_tables(conn, event)
            self._detect_anomalies(conn, events)
        logging.info("event_store.ingested", extra={"received": len(events), "inserted": inserted})
        return inserted

    def _update_derived_tables(self, conn: sqlite3.Connection, event: StoreEvent) -> None:
        session_id = f"{event.store_id}:{event.visitor_id}"
        zone_id = event.zone_id or event.zone
        is_staff = event.is_staff or event.role.value == "staff"
        existing = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO sessions(session_id, store_id, visitor_id, first_seen, last_seen, is_staff, group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, event.store_id, event.visitor_id, event.timestamp, event.timestamp, int(is_staff), event.group_id),
        )
        reentry_increment = 0
        if event.event_type == EventType.ENTRY and existing and existing["exit_time"]:
            seconds_since_exit = (parse_timestamp(event.timestamp) - parse_timestamp(existing["exit_time"])).total_seconds()
            if 0 <= seconds_since_exit <= 600:
                reentry_increment = 1
        if event.event_type in {EventType.ENTRY, EventType.REENTRY}:
            conn.execute(
                """
                UPDATE sessions
                SET entry_time = COALESCE(entry_time, ?),
                    exit_time = CASE WHEN ? THEN NULL ELSE exit_time END,
                    reentry_count = reentry_count + ?,
                    is_staff = ?,
                    group_id = COALESCE(?, group_id)
                WHERE session_id = ?
                """,
                (event.timestamp, reentry_increment, reentry_increment, int(is_staff), event.group_id, session_id),
            )
        if event.event_type == EventType.EXIT:
            conn.execute("UPDATE sessions SET exit_time = ? WHERE session_id = ?", (event.timestamp, session_id))
        self._update_session_state(conn, session_id, event, zone_id, is_staff)

        conn.execute(
            """
            INSERT INTO visitor_tracks(store_id, camera_id, visitor_id, track_id, group_id, role, timestamp, zone_id, action, bbox, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.store_id,
                event.camera_id,
                event.visitor_id,
                event.track_id,
                event.group_id,
                event.role.value,
                event.timestamp,
                zone_id,
                event.event_type.value,
                json.dumps(event.metadata.get("bbox")),
                event.confidence,
            ),
        )

        if event.event_type == EventType.ZONE_ENTER and zone_id:
            conn.execute(
                """
                INSERT INTO zone_dwell(store_id, visitor_id, zone_id, enter_time)
                VALUES (?, ?, ?, ?)
                """,
                (event.store_id, event.visitor_id, zone_id, event.timestamp),
            )
        if event.event_type == EventType.ZONE_EXIT and zone_id:
            row = conn.execute(
                """
                SELECT id, enter_time FROM zone_dwell
                WHERE store_id = ? AND visitor_id = ? AND zone_id = ? AND exit_time IS NULL
                ORDER BY enter_time DESC LIMIT 1
                """,
                (event.store_id, event.visitor_id, zone_id),
            ).fetchone()
            if row:
                dwell_ms = int((parse_timestamp(event.timestamp) - parse_timestamp(row["enter_time"])).total_seconds() * 1000)
                conn.execute(
                    "UPDATE zone_dwell SET exit_time = ?, dwell_ms = ? WHERE id = ?",
                    (event.timestamp, dwell_ms, row["id"]),
                )
        if event.event_type in {EventType.ZONE_DWELL, EventType.PRODUCT_INTERACTION} and zone_id and event.dwell_ms is not None:
            conn.execute(
                """
                INSERT INTO zone_dwell(store_id, visitor_id, zone_id, enter_time, exit_time, dwell_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event.store_id, event.visitor_id, zone_id, event.timestamp, event.timestamp, event.dwell_ms),
            )

    def _update_session_state(self, conn: sqlite3.Connection, session_id: str, event: StoreEvent, zone_id: str | None, is_staff: bool) -> None:
        row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        zones = json.loads(row["zones_visited"] or "[]")
        dwell = json.loads(row["dwell_time_by_zone"] or "{}")
        interactions = json.loads(row["interactions"] or "[]")
        if zone_id and zone_id not in zones:
            zones.append(zone_id)
        if zone_id and event.dwell_ms:
            dwell[zone_id] = int(dwell.get(zone_id, 0)) + int(event.dwell_ms)
        if event.event_type in {EventType.PRODUCT_INTERACTION, EventType.CHECKOUT_VISIT}:
            interactions.append({"type": event.event_type.value, "zone": zone_id, "timestamp": event.timestamp})
        has_product_engagement = any(item.get("type") == EventType.PRODUCT_INTERACTION.value for item in interactions)
        session_is_staff = is_staff and not has_product_engagement
        conn.execute(
            """
            UPDATE sessions
            SET first_seen = COALESCE(first_seen, ?),
                last_seen = ?,
                zones_visited = ?,
                dwell_time_by_zone = ?,
                interactions = ?,
                is_staff = ?,
                group_id = COALESCE(?, group_id)
            WHERE session_id = ?
            """,
            (
                event.timestamp,
                event.timestamp,
                json.dumps(zones),
                json.dumps(dwell),
                json.dumps(interactions),
                int(session_is_staff),
                event.group_id,
                session_id,
            ),
        )

    def _detect_anomalies(self, conn: sqlite3.Connection, events: list[StoreEvent]) -> None:
        by_store_time: dict[tuple[str, str], int] = {}
        for event in events:
            if event.event_type == EventType.BILLING_QUEUE_JOIN:
                key = (event.store_id, event.timestamp)
                by_store_time[key] = by_store_time.get(key, 0) + 1
            if event.event_type in {EventType.ZONE_DWELL, EventType.PRODUCT_INTERACTION} and event.dwell_ms and event.dwell_ms >= 900000:
                anomaly_id = f"ANOM_DWELL_{event.store_id}_{event.visitor_id}_{event.timestamp}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO anomalies(anomaly_id, store_id, timestamp, anomaly_type, severity, message, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        anomaly_id,
                        event.store_id,
                        event.timestamp,
                        "EXCESSIVE_DWELL",
                        "warning",
                        f"{event.visitor_id} stayed unusually long in {event.zone_id or event.zone}.",
                        json.dumps({
                            "visitor_id": event.visitor_id,
                            "zone": event.zone_id or event.zone,
                            "measured_value": event.dwell_ms,
                            "threshold": 900000,
                            "unit": "milliseconds",
                            "rule": "dwell_ms >= 900000",
                            "video_time_sec": event.video_time_sec,
                            "frame_id": event.frame_id,
                            "confidence": 0.82,
                        }),
                    ),
                )
        for (store_id, timestamp), queue_depth in by_store_time.items():
            if queue_depth >= 5:
                anomaly_id = f"ANOM_QUEUE_{store_id}_{timestamp}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO anomalies(anomaly_id, store_id, timestamp, anomaly_type, severity, message, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        anomaly_id,
                        store_id,
                        timestamp,
                        "QUEUE_SPIKE",
                        "warning",
                        f"Billing queue depth reached {queue_depth}.",
                        json.dumps({
                            "zone": "BILLING",
                            "measured_value": queue_depth,
                            "threshold": 5,
                            "unit": "people",
                            "rule": "queue_depth >= 5",
                            "confidence": 0.8,
                        }),
                    ),
                )
        self._detect_session_anomalies(conn)

    def _detect_session_anomalies(self, conn: sqlite3.Connection) -> None:
        sessions = conn.execute("SELECT * FROM sessions").fetchall()
        for session in sessions:
            if int(session["reentry_count"] or 0) >= 2:
                anomaly_id = f"ANOM_REENTRY_{session['store_id']}_{session['visitor_id']}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO anomalies(anomaly_id, store_id, timestamp, anomaly_type, severity, message, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        anomaly_id,
                        session["store_id"],
                        session["last_seen"] or session["first_seen"],
                        "REPEATED_ENTRY_EXIT",
                        "warning",
                        f"{session['visitor_id']} repeatedly entered and exited.",
                        json.dumps({
                            "visitor_id": session["visitor_id"],
                            "measured_value": session["reentry_count"],
                            "threshold": 2,
                            "unit": "reentries",
                            "rule": "reentry_count >= 2",
                            "confidence": 0.76,
                        }),
                    ),
                )
            zones = json.loads(session["zones_visited"] or "[]")
            if len(zones) >= 4:
                anomaly_id = f"ANOM_MOVEMENT_{session['store_id']}_{session['visitor_id']}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO anomalies(anomaly_id, store_id, timestamp, anomaly_type, severity, message, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        anomaly_id,
                        session["store_id"],
                        session["last_seen"] or session["first_seen"],
                        "UNUSUAL_MOVEMENT",
                        "info",
                        f"{session['visitor_id']} visited many zones in a short session.",
                        json.dumps({
                            "visitor_id": session["visitor_id"],
                            "zones": zones,
                            "measured_value": len(zones),
                            "threshold": 4,
                            "unit": "zones",
                            "rule": "distinct_zones_visited >= 4",
                            "confidence": 0.62,
                        }),
                    ),
                )
        crowd_rows = conn.execute(
            """
            SELECT store_id, timestamp, zone_id, COUNT(DISTINCT visitor_id) AS people
            FROM visitor_tracks
            WHERE zone_id IS NOT NULL
            GROUP BY store_id, timestamp, zone_id
            HAVING people >= 5
            """
        ).fetchall()
        for row in crowd_rows:
            anomaly_id = f"ANOM_CROWD_{row['store_id']}_{row['zone_id']}_{row['timestamp']}"
            conn.execute(
                """
                INSERT OR IGNORE INTO anomalies(anomaly_id, store_id, timestamp, anomaly_type, severity, message, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    anomaly_id,
                    row["store_id"],
                    row["timestamp"],
                    "CROWDING",
                    "warning",
                    f"{row['people']} people detected in {row['zone_id']}.",
                    json.dumps({
                        "zone": row["zone_id"],
                        "measured_value": row["people"],
                        "threshold": 5,
                        "unit": "people",
                        "rule": "people_in_zone_at_timestamp >= 5",
                        "confidence": 0.8,
                    }),
                ),
            )

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def count(self, table: str) -> int:
        with self.connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

    def get_session(self, store_id: str, visitor_id: str) -> dict[str, Any] | None:
        rows = self.rows("SELECT * FROM sessions WHERE store_id = ? AND visitor_id = ?", (store_id, visitor_id))
        return rows[0] if rows else None

    def zone_dwell(self, store_id: str) -> dict[str, dict[str, int]]:
        rows = self.rows(
            """
            SELECT zone_id, COALESCE(SUM(dwell_ms), 0) AS total_dwell_ms, COUNT(*) AS visits
            FROM zone_dwell
            WHERE store_id = ? AND dwell_ms IS NOT NULL
            GROUP BY zone_id
            """,
            (store_id,),
        )
        return {row["zone_id"]: {"total_dwell_ms": int(row["total_dwell_ms"]), "visits": int(row["visits"])} for row in rows}

    def clear_store(self, store_id: str) -> None:
        with self.connect() as conn:
            for table in (
                "events",
                "sessions",
                "visitor_tracks",
                "zone_dwell",
                "pos_transactions",
                "anomalies",
            ):
                conn.execute(f"DELETE FROM {table} WHERE store_id = ?", (store_id,))

    def set_current_video(self, store_id: str, video_path: str, camera_id: str, duration_sec: int, fps: int, updated_at: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO processed_videos(store_id, video_path, camera_id, duration_sec, fps, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id) DO UPDATE SET
                  video_path = excluded.video_path,
                  camera_id = excluded.camera_id,
                  duration_sec = excluded.duration_sec,
                  fps = excluded.fps,
                  updated_at = excluded.updated_at
                """,
                (store_id, video_path, camera_id, duration_sec, fps, updated_at),
            )

    def current_video(self, store_id: str) -> dict[str, Any] | None:
        rows = self.rows("SELECT * FROM processed_videos WHERE store_id = ?", (store_id,))
        return rows[0] if rows else None

    def save_review(
        self,
        store_id: str,
        title: str,
        video_path: str,
        camera_id: str,
        duration_sec: int,
        fps: int,
        updated_at: str,
    ) -> dict[str, Any]:
        events = self.rows("SELECT * FROM events WHERE store_id = ? ORDER BY timestamp, event_id", (store_id,))
        if not events:
            raise ValueError("No analyzed events are available to save.")
        saved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        review_id = f"REV_{store_id}_{saved_at}".replace(":", "").replace("-", "").replace(".", "")
        normalized_events = [self._event_row_to_payload(event) for event in events]
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO saved_reviews(review_id, store_id, title, video_path, camera_id, duration_sec, fps, updated_at, saved_at, events_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(review_id) DO UPDATE SET
                  title = excluded.title,
                  video_path = excluded.video_path,
                  camera_id = excluded.camera_id,
                  duration_sec = excluded.duration_sec,
                  fps = excluded.fps,
                  updated_at = excluded.updated_at,
                  saved_at = excluded.saved_at,
                  events_json = excluded.events_json
                """,
                (
                    review_id,
                    store_id,
                    title,
                    video_path,
                    camera_id,
                    duration_sec,
                    fps,
                    updated_at,
                    saved_at,
                    json.dumps(normalized_events),
                ),
            )
        return {
            "review_id": review_id,
            "store_id": store_id,
            "title": title,
            "video_path": video_path,
            "camera_id": camera_id,
            "duration_sec": duration_sec,
            "fps": fps,
            "updated_at": updated_at,
            "saved_at": saved_at,
            "events": len(normalized_events),
        }

    def saved_reviews(self, store_id: str) -> list[dict[str, Any]]:
        rows = self.rows(
            """
            SELECT review_id, store_id, title, video_path, camera_id, duration_sec, fps, updated_at, saved_at, events_json
            FROM saved_reviews
            WHERE store_id = ?
            ORDER BY saved_at DESC
            """,
            (store_id,),
        )
        for row in rows:
            row["events"] = len(json.loads(row.pop("events_json") or "[]"))
        return rows

    def load_review(self, store_id: str, review_id: str) -> dict[str, Any]:
        rows = self.rows("SELECT * FROM saved_reviews WHERE store_id = ? AND review_id = ?", (store_id, review_id))
        if not rows:
            raise ValueError("Saved CCTV review was not found.")
        review = rows[0]
        events = [StoreEvent(**event) for event in json.loads(review["events_json"] or "[]")]
        self.clear_store(store_id)
        inserted = self.ingest_events(events)
        self.set_current_video(
            store_id=store_id,
            video_path=review["video_path"],
            camera_id=review["camera_id"],
            duration_sec=int(review["duration_sec"]),
            fps=int(review["fps"]),
            updated_at=review["updated_at"],
        )
        return {
            "review_id": review_id,
            "store_id": store_id,
            "title": review["title"],
            "events_inserted": inserted,
            "duration_sec": int(review["duration_sec"]),
            "fps": int(review["fps"]),
        }

    @staticmethod
    def _event_row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
        metadata = row.get("metadata") or "{}"
        if isinstance(metadata, str):
            metadata = json.loads(metadata or "{}")
        return {
            "event_id": row["event_id"],
            "store_id": row["store_id"],
            "camera_id": row["camera_id"],
            "visitor_id": row["visitor_id"],
            "video_time_sec": row.get("video_time_sec"),
            "frame_id": row.get("frame_id"),
            "track_id": row.get("track_id"),
            "group_id": row.get("group_id"),
            "role": row.get("role") or "customer",
            "event_type": row["event_type"],
            "timestamp": row["timestamp"],
            "zone_id": row.get("zone_id"),
            "dwell_ms": row.get("dwell_ms"),
            "is_staff": bool(row.get("is_staff")),
            "confidence": float(row["confidence"]),
            "metadata": metadata,
        }
