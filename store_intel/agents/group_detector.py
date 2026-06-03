from __future__ import annotations

from typing import Any


class GroupDetector:
    """Assigns group ids when people enter close together and remain nearby."""

    def assign_groups(self, detections: list[dict[str, Any]], frame_shape: tuple[int, ...], second: int) -> dict[int, str | None]:
        if len(detections) < 2:
            return {int(detection["track_id"]): None for detection in detections}
        width = frame_shape[1]
        centers = {
            int(detection["track_id"]): detection["bbox"][0] + detection["bbox"][2] / 2
            for detection in detections
        }
        sorted_ids = sorted(centers, key=centers.get)
        groups: dict[int, str | None] = {track_id: None for track_id in sorted_ids}
        group_index = 1
        current = [sorted_ids[0]]
        for track_id in sorted_ids[1:]:
            previous = current[-1]
            if abs(centers[track_id] - centers[previous]) / max(width, 1) <= 0.18:
                current.append(track_id)
            else:
                if len(current) >= 2:
                    group_id = f"GRP_{second}_{group_index}"
                    for member in current:
                        groups[member] = group_id
                    group_index += 1
                current = [track_id]
        if len(current) >= 2:
            group_id = f"GRP_{second}_{group_index}"
            for member in current:
                groups[member] = group_id
        return groups
