"""Overlay user-selected link icons on story videos with bundled FFmpeg."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import cv2
from imageio_ffmpeg import get_ffmpeg_exe
from PIL import Image


def render_video_with_icons(
    video_path: Path, overlays: list[dict], resample_filter: int
) -> Path | None:
    """Return a temporary MP4 with icons burned in, or None if no icons.

    The caller must delete the returned file after the upload. Icons are
    composited using the original video dimensions; audio is preserved.
    """
    items = [item for item in overlays if item.get("icon_path")]
    if not items:
        return None

    capture = cv2.VideoCapture(str(video_path))
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        capture.release()
    if width <= 0 or height <= 0:
        raise ValueError("動画の解像度を読み取れませんでした")

    with tempfile.TemporaryDirectory(prefix="story-icons-") as temp_dir:
        inputs = ["-i", str(video_path)]
        filters = []
        previous = "[0:v]"

        for index, item in enumerate(items, start=1):
            x, y, w, h = map(float, item["geom"])
            icon_width = max(1, round(w * width))
            icon_height = max(1, round(h * height))
            offset_x = round(x * width - icon_width / 2)
            offset_y = round(y * height - icon_height / 2)
            icon_file = Path(temp_dir) / f"icon-{index}.png"

            with Image.open(item["icon_path"]) as source:
                icon = source.convert("RGBA")
                icon.resize(
                    (icon_width, icon_height), resample_filter
                ).save(icon_file, format="PNG")

            inputs.extend(["-loop", "1", "-i", str(icon_file)])
            output = f"[v{index}]"
            filters.append(
                f"{previous}[{index}:v]"
                f"overlay={offset_x}:{offset_y}:shortest=1:format=auto{output}"
            )
            previous = output

        # NamedTemporaryFile avoids path races. Close it before FFmpeg opens
        # the path, especially on Windows.
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp:
            result = Path(temp.name)

        command = [
            get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            *inputs,
            "-filter_complex", ";".join(filters),
            "-map", previous, "-map", "0:a?",
            "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart",
            str(result),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            result.unlink(missing_ok=True)
            details = getattr(exc, "stderr", "") or str(exc)
            raise RuntimeError(f"動画の合成に失敗しました: {details}") from exc
        return result
