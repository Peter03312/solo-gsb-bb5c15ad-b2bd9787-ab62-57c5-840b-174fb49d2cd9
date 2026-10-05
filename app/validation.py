"""Business-rule validation for scene scheduling.

Rules enforced here (violations -> HTTP 409, nothing is persisted):
  1. Every referenced channel must belong to a registered lamp.
  2. Brightness must be within the lamp's [min_brightness, max_brightness].
  3. Fade intervals of the same channel must not overlap. Checked among
     scenes of the same kind (normal vs normal, emergency vs emergency);
     normal-vs-emergency interplay is resolved by preemption, not rejection.
  4. Emergency active windows on the same channel must not overlap.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from .models import (
    Conflict,
    ConflictInterval,
    Lamp,
    Scene,
    SceneCreate,
    SceneKind,
)

Interval = Tuple[datetime, datetime]


def _fade_interval(start: datetime, fade_seconds: int) -> Interval:
    return (start, start + timedelta(seconds=fade_seconds))


def _overlaps(a: Interval, b: Interval) -> bool:
    # Half-open overlap; identical start times always conflict (two commands
    # for the same channel at the same instant are ambiguous).
    return (a[0] < b[1] and b[0] < a[1]) or a[0] == b[0]


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def validate_scene(
    scene: SceneCreate,
    lamps_by_channel: Dict[str, Lamp],
    existing: List[Scene],
) -> List[Conflict]:
    conflicts: List[Conflict] = []

    # 1 + 2: lamp existence and brightness range
    for ch in scene.channels:
        lamp = lamps_by_channel.get(ch.channel)
        if lamp is None:
            conflicts.append(
                Conflict(
                    channel=ch.channel,
                    reason="lamp_not_found",
                    detail=f"no lamp registered for channel '{ch.channel}'",
                )
            )
        elif not lamp.min_brightness <= ch.brightness <= lamp.max_brightness:
            conflicts.append(
                Conflict(
                    channel=ch.channel,
                    reason="brightness_out_of_range",
                    detail=(
                        f"brightness {ch.brightness} outside allowed range "
                        f"[{lamp.min_brightness}, {lamp.max_brightness}] "
                        f"for channel '{ch.channel}'"
                    ),
                )
            )

    # 3: fade-interval overlap on the same channel (same kind only)
    for ch in scene.channels:
        new_iv = _fade_interval(scene.start, ch.fade_seconds)
        for other in existing:
            if other.kind != scene.kind:
                continue
            other_ch = other.channel_map.get(ch.channel)
            if other_ch is None:
                continue
            other_iv = _fade_interval(other.start, other_ch.fade_seconds)
            if _overlaps(new_iv, other_iv):
                lo, hi = max(new_iv[0], other_iv[0]), min(new_iv[1], other_iv[1])
                if lo >= hi:  # degenerate: zero-length fades at the same instant
                    lo, hi = new_iv
                conflicts.append(
                    Conflict(
                        channel=ch.channel,
                        reason="fade_interval_overlap",
                        interval=ConflictInterval(start=lo, end=hi),
                        detail=(
                            f"fade [{_iso(new_iv[0])}, {_iso(new_iv[1])}] overlaps "
                            f"scene '{other.name}' ({other.id}) fade "
                            f"[{_iso(other_iv[0])}, {_iso(other_iv[1])}] "
                            f"on channel '{ch.channel}'"
                        ),
                    )
                )

    # 4: emergency windows on the same channel must not overlap
    if scene.kind == SceneKind.emergency:
        new_window = (scene.start, scene.end)
        new_channels = set(scene.channel_map)
        for other in existing:
            if other.kind != SceneKind.emergency:
                continue
            shared = new_channels & set(other.channel_map)
            if not shared:
                continue
            other_window = (other.start, other.end)
            if _overlaps(new_window, other_window):
                lo = max(new_window[0], other_window[0])
                hi = min(new_window[1], other_window[1])
                for channel in sorted(shared):
                    conflicts.append(
                        Conflict(
                            channel=channel,
                            reason="emergency_window_overlap",
                            interval=ConflictInterval(start=lo, end=hi),
                            detail=(
                                f"emergency window [{_iso(new_window[0])}, "
                                f"{_iso(new_window[1])}] overlaps emergency scene "
                                f"'{other.name}' ({other.id}) window "
                                f"[{_iso(other_window[0])}, {_iso(other_window[1])}] "
                                f"on channel '{channel}'"
                            ),
                        )
                    )

    return conflicts
