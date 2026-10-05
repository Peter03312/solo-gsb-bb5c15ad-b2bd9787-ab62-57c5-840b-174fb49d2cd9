"""In-memory repository. State is process-local; see README for limitations."""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

from .models import Lamp, LampCreate, Scene


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._lamps: Dict[str, Lamp] = {}
        self._scenes: Dict[str, Scene] = {}

    def reset(self) -> None:
        with self._lock:
            self._lamps.clear()
            self._scenes.clear()

    # -- lamps -------------------------------------------------------------
    def add_lamp(self, lamp_id: str, payload: LampCreate) -> Lamp:
        with self._lock:
            lamp = Lamp(id=lamp_id, **payload.model_dump())
            self._lamps[lamp_id] = lamp
            return lamp

    def get_lamp(self, lamp_id: str) -> Optional[Lamp]:
        return self._lamps.get(lamp_id)

    def list_lamps(self) -> List[Lamp]:
        return list(self._lamps.values())

    def delete_lamp(self, lamp_id: str) -> bool:
        with self._lock:
            return self._lamps.pop(lamp_id, None) is not None

    def lamp_by_channel(self, channel: str) -> Optional[Lamp]:
        for lamp in self._lamps.values():
            if lamp.channel == channel:
                return lamp
        return None

    def lamps_by_channel(self) -> Dict[str, Lamp]:
        return {lamp.channel: lamp for lamp in self._lamps.values()}

    def lamp_in_use(self, lamp_id: str) -> List[str]:
        lamp = self._lamps.get(lamp_id)
        if lamp is None:
            return []
        return [
            scene.id
            for scene in self._scenes.values()
            if lamp.channel in scene.channel_map
        ]

    # -- scenes ------------------------------------------------------------
    def add_scene(self, scene: Scene) -> Scene:
        with self._lock:
            self._scenes[scene.id] = scene
            return scene

    def get_scene(self, scene_id: str) -> Optional[Scene]:
        return self._scenes.get(scene_id)

    def list_scenes(self) -> List[Scene]:
        return list(self._scenes.values())

    def delete_scene(self, scene_id: str) -> bool:
        with self._lock:
            return self._scenes.pop(scene_id, None) is not None


store = Store()
