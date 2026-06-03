from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import cv2
import numpy as np


def create_demo_video(path: str | Path, duration_sec: int = 8, fps: int = 10) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = 960, 540
    raw_output = output.with_suffix(".avi")
    writer = _open_mjpeg_writer(raw_output, fps, (width, height))
    total_frames = duration_sec * fps
    for frame_index in range(total_frames):
        t = frame_index / max(total_frames - 1, 1)
        frame = np.full((height, width, 3), (34, 38, 42), dtype=np.uint8)
        cv2.rectangle(frame, (0, 0), (335, height), (45, 65, 62), -1)
        cv2.rectangle(frame, (335, 0), (690, height), (58, 55, 72), -1)
        cv2.rectangle(frame, (690, 0), (width, height), (73, 54, 54), -1)
        cv2.putText(frame, "ENTRY", (36, 52), cv2.FONT_HERSHEY_SIMPLEX, 1, (210, 235, 225), 2)
        cv2.putText(frame, "AISLE_A", (380, 52), cv2.FONT_HERSHEY_SIMPLEX, 1, (225, 220, 240), 2)
        cv2.putText(frame, "BILLING", (730, 52), cv2.FONT_HERSHEY_SIMPLEX, 1, (245, 220, 215), 2)

        customer_x = int(80 + t * 760)
        cv2.ellipse(frame, (customer_x, 210), (34, 72), 0, 0, 360, (85, 185, 255), -1)
        cv2.circle(frame, (customer_x, 118), 26, (95, 210, 255), -1)

        if frame_index > fps * 2:
            second_x = int(40 + min(max(t - 0.25, 0), 0.55) * 520)
            cv2.ellipse(frame, (second_x, 390), (30, 64), 0, 0, 360, (90, 220, 140), -1)
            cv2.circle(frame, (second_x, 310), 23, (110, 240, 160), -1)

        cv2.rectangle(frame, (835, 165), (890, 335), (220, 105, 110), -1)
        cv2.circle(frame, (862, 125), 24, (235, 125, 125), -1)
        writer.write(frame)
    writer.release()
    if not _transcode_to_browser_mp4(raw_output, output):
        logging.warning("demo_video.transcode_failed_using_opencv_fallback", extra={"output": str(output)})
        writer = _open_browser_video_writer(output, fps, (width, height))
        capture = cv2.VideoCapture(str(raw_output))
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            writer.write(frame)
        capture.release()
        writer.release()
    raw_output.unlink(missing_ok=True)
    return output


def _open_mjpeg_writer(output: Path, fps: int, size: tuple[int, int]) -> cv2.VideoWriter:
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    if not writer.isOpened():
        raise ValueError(f"Unable to create raw demo video: {output}")
    return writer


def _transcode_to_browser_mp4(source: Path, output: Path) -> bool:
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True)
        return output.exists() and output.stat().st_size > 0
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _open_browser_video_writer(output: Path, fps: int, size: tuple[int, int]) -> cv2.VideoWriter:
    for codec in ("avc1", "H264", "mp4v"):
        writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*codec), fps, size)
        if writer.isOpened():
            return writer
        writer.release()
    raise ValueError(f"Unable to create demo video: {output}")
