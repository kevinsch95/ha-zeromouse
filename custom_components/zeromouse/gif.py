"""Build an animated GIF from the JPEG photos of a ZeroMOUSE event."""

from __future__ import annotations

from collections.abc import Sequence
import io
from itertools import pairwise

from PIL import Image

# Playback limits (milliseconds). The photos carry their capture time, so the
# animation runs in real time, clamped to stay watchable.
DEFAULT_FRAME_MS = 250
MIN_FRAME_MS = 60
MAX_FRAME_MS = 1000
LAST_FRAME_HOLD_MS = 1200
MAX_WIDTH = 480


def frame_durations(times: Sequence[int | None]) -> list[int]:
    """Return per-frame display durations from capture times (ms).

    Frame i is shown until frame i+1 was captured. Missing or non-increasing
    times fall back to a default. The last frame is held a little longer so
    the loop is easy to follow.
    """
    durations: list[int] = []
    for current, following in pairwise(times):
        if current is None or following is None or following <= current:
            durations.append(DEFAULT_FRAME_MS)
        else:
            durations.append(min(max(following - current, MIN_FRAME_MS), MAX_FRAME_MS))
    if times:
        durations.append(LAST_FRAME_HOLD_MS)
    return durations


def build_gif(
    frames: Sequence[bytes],
    times: Sequence[int | None] | None = None,
    max_width: int = MAX_WIDTH,
) -> bytes:
    """Return GIF bytes for the given JPEG frames (blocking, run in executor)."""
    if not frames:
        raise ValueError("No frames")
    images: list[Image.Image] = []
    for data in frames:
        with Image.open(io.BytesIO(data)) as img:
            frame = img.convert("RGB")
        if frame.width > max_width:
            height = round(frame.height * max_width / frame.width)
            frame = frame.resize((max_width, height), Image.Resampling.LANCZOS)
        if images and frame.size != images[0].size:
            frame = frame.resize(images[0].size, Image.Resampling.LANCZOS)
        images.append(frame)

    # One shared adaptive palette from the first frame keeps colours stable
    # between frames (the camera image barely changes) and the file small.
    palette = images[0].quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    paletted = [
        img.quantize(palette=palette, dither=Image.Dither.NONE) for img in images
    ]

    durations = frame_durations(list(times) if times else [None] * len(images))
    out = io.BytesIO()
    paletted[0].save(
        out,
        format="GIF",
        save_all=True,
        append_images=paletted[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )
    return out.getvalue()
