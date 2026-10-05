"""Compose an executable timeline from the scheduled scenes.

Per channel:
  * Normal scenes contribute a fade command at their start time.
  * An emergency scene preempts its channels for [start, end): normal
    commands inside the window are dropped, an emergency command is issued
    at the window start, and a restore command at the window end.
  * The restore target is the scene that was in effect at the moment of
    preemption, but only if that scene is still valid at the emergency's
    end (open-ended, or end > emergency end). Otherwise the channel
    returns to the lamp's default brightness.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .models import (
    ConflictInterval,
    Lamp,
    Preemption,
    Scene,
    SceneKind,
    Timeline,
    TimelineEntry,
)


@dataclass
class _Command:
    time: datetime
    brightness: int
    fade_seconds: int
    kind: str  # scene | emergency | restore
    scene_id: Optional[str]
    scene_name: Optional[str]


def build_timeline(
    lamps: List[Lamp],
    scenes: List[Scene],
    window_from: Optional[datetime] = None,
    window_to: Optional[datetime] = None,
) -> Timeline:
    lamps_by_channel: Dict[str, Lamp] = {l.channel: l for l in lamps}
    scene_by_id: Dict[str, Scene] = {s.id: s for s in scenes}
    normals = sorted(
        (s for s in scenes if s.kind == SceneKind.normal),
        key=lambda s: (s.start, s.id),
    )
    emergencies = sorted(
        (s for s in scenes if s.kind == SceneKind.emergency),
        key=lambda s: (s.start, s.id),
    )
    channels = sorted({ch.channel for s in scenes for ch in s.channels})

    entries: List[TimelineEntry] = []
    preemptions: List[Preemption] = []

    for channel in channels:
        lamp = lamps_by_channel.get(channel)
        default = lamp.default_brightness if lamp else 0

        # Base schedule from normal scenes.
        base: List[_Command] = []
        for s in normals:
            ch = s.channel_map.get(channel)
            if ch is not None:
                base.append(
                    _Command(s.start, ch.brightness, ch.fade_seconds, "scene", s.id, s.name)
                )
        base.sort(key=lambda c: c.time)

        commands: List[_Command] = list(base)
        for e in emergencies:
            ech = e.channel_map.get(channel)
            if ech is None:
                continue  # emergency does not touch this channel

            # Scene in effect at the moment of preemption (from the base
            # schedule, i.e. ignoring other emergencies).
            active: Optional[_Command] = None
            for c in base:
                if c.time <= e.start:
                    active = c
                else:
                    break

            # Preempt: drop normal commands inside the emergency window.
            commands = [c for c in commands if not (e.start <= c.time < e.end)]
            commands.append(
                _Command(e.start, ech.brightness, ech.fade_seconds, "emergency", e.id, e.name)
            )

            # Restore only to the pre-preemption scene, and only if it is
            # still valid when the emergency ends; otherwise fall back to
            # the lamp default.
            restore_scene: Optional[Scene] = None
            restore_brightness = default
            if active is not None:
                src = scene_by_id[active.scene_id]
                if src.end is None or src.end > e.end:
                    restore_scene = src
                    restore_brightness = active.brightness
            commands.append(
                _Command(
                    e.end,
                    restore_brightness,
                    ech.fade_seconds,
                    "restore",
                    restore_scene.id if restore_scene else None,
                    restore_scene.name if restore_scene else None,
                )
            )
            preemptions.append(
                Preemption(
                    emergency_scene_id=e.id,
                    emergency_scene_name=e.name,
                    channel=channel,
                    window=ConflictInterval(start=e.start, end=e.end),
                    preempted_scene_id=active.scene_id if active else None,
                    restored_scene_id=restore_scene.id if restore_scene else None,
                    restored_brightness=restore_brightness,
                )
            )

        for c in commands:
            entries.append(
                TimelineEntry(
                    time=c.time,
                    channel=channel,
                    brightness=c.brightness,
                    fade_seconds=c.fade_seconds,
                    kind=c.kind,
                    scene_id=c.scene_id,
                    scene_name=c.scene_name,
                )
            )

    entries.sort(key=lambda e: (e.time, e.channel))
    preemptions.sort(key=lambda p: (p.window.start, p.channel))

    if window_from is not None or window_to is not None:
        entries = [
            e
            for e in entries
            if (window_from is None or e.time >= window_from)
            and (window_to is None or e.time <= window_to)
        ]
        preemptions = [
            p
            for p in preemptions
            if (window_from is None or p.window.end >= window_from)
            and (window_to is None or p.window.start <= window_to)
        ]

    return Timeline(
        generated_at=datetime.now(timezone.utc),
        entries=entries,
        preemptions=preemptions,
    )
