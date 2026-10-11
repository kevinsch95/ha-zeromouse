"""Build an animated GIF from the JPEG photos of a ZeroMOUSE event."""

from __future__ import annotations

from collections.abc import Sequence
import io
from itertools import pairwise

from PIL import Image

# Playback timing (milliseconds). The photos carry their capture time; the gaps
# are clamped to stay watchable, then everything is slowed down by
# PLAYBACK_SLOWDOWN so the cat's movement is easier to follow.
PLAYBACK_SLOWDOWN = 1.25
DEFAULT_FRAME_MS = 250
MIN_FRAME_MS = 60
MAX_FRAME_MS = 1000
LAST_FRAME_HOLD_MS = 1200
MAX_WIDTH = 480


def frame_durations(times: Sequence[int | None]) -> list[int]:
    """Return per-frame display durations from capture times (ms).

    Frame i is shown until frame i+1 was captured. Missing or non-increasing
    times fall back to a default. The last frame is held a little longer so
    the loop is easy to follow. All durations are scaled by PLAYBACK_SLOWDOWN.
    """
    durations: list[int] = []
    for current, following in pairwise(times):
        if current is None or following is None or following <= current:
            durations.append(DEFAULT_FRAME_MS)
        else:
            durations.append(min(max(following - current, MIN_FRAME_MS), MAX_FRAME_MS))
    if times:
        durations.append(LAST_FRAME_HOLD_MS)
    # GIF stores delays in 10 ms steps; round here instead of letting the
    # encoder truncate.
    return [int(ms * PLAYBACK_SLOWDOWN / 10 + 0.5) * 10 for ms in durations]


def _shared_palette(images: Sequence[Image.Image]) -> Image.Image:
    """Return a 256-colour palette image representative of all frames."""
    sample_w = 160
    tiles = [
        img.resize((sample_w, max(1, round(img.height * sample_w / img.width))))
        for img in images
    ]
    sheet = Image.new("RGB", (sample_w, sum(t.height for t in tiles)))
    y = 0
    for tile in tiles:
        sheet.paste(tile, (0, y))
        y += tile.height
    return sheet.quantize(colors=256, method=Image.Quantize.MEDIANCUT)


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

    # One shared adaptive palette sampled from every frame keeps colours
    # stable between frames and the file small, while frames that look very
    # different (e.g. the IR light switching on) still get their own colours.
    palette = _shared_palette(images)
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
