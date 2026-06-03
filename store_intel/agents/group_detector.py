from __future__ import annotations

from typing import Any


class GroupDetector:
    """Assigns group ids when people enter close together and remain nearby."""

    def __init__(self) -> None:
        self._known_groups: dict[frozenset[int], str] = {}
        self._next_group_index = 1

    def assign_groups(self, detections: list[dict[str, Any]], frame_shape: tuple[int, ...], second: int) -> dict[int, str | None]:
        if len(detections) < 2:
            return {int(detection["track_id"]): None for detection in detections}
        height, width = frame_shape[:2]
        centers = {
            int(detection["track_id"]): (
                detection["bbox"][0] + detection["bbox"][2] / 2,
                detection["bbox"][1] + detection["bbox"][3] * 0.82,
            )
            for detection in detections
        }
        sorted_ids = sorted(centers, key=lambda track_id: centers[track_id][0])
        groups: dict[int, str | None] = {track_id: None for track_id in sorted_ids}
        current = [sorted_ids[0]]
        for track_id in sorted_ids[1:]:
            previous = current[-1]
            x_gap = abs(centers[track_id][0] - centers[previous][0]) / max(width, 1)
            y_gap = abs(centers[track_id][1] - centers[previous][1]) / max(height, 1)
            if x_gap <= 0.14 and y_gap <= 0.2:
                current.append(track_id)
            else:
                self._assign_group(current, groups)
                current = [track_id]
        self._assign_group(current, groups)
        return groups

    def _assign_group(self, members: list[int], groups: dict[int, str | None]) -> None:
        if len(members) < 2:
            return
        group_key = frozenset(members)
        group_id = self._known_groups.get(group_key)
        if group_id is None:
            group_id = f"GRP_{self._next_group_index}"
            self._known_groups[group_key] = group_id
            self._next_group_index += 1
        for member in members:
            groups[member] = group_id
