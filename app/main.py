"""Museum lighting orchestrator — HTTP API."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from .models import (
    Lamp,
    LampCreate,
    Scene,
    SceneCreate,
    SceneKind,
    SceneRejected,
    Timeline,
    _require_tz_aware,
)
from .store import store
from .timeline import build_timeline
from .validation import validate_scene

app = FastAPI(
    title="Museum Lighting Orchestrator",
    version="1.0.0",
    description=(
        "Schedule timed lighting scenes for museum exhibition halls: "
        "validated scene orchestration, executable timeline composition, "
        "and emergency preemption with safe restore."
    ),
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Lamps
# ---------------------------------------------------------------------------

@app.post("/lamps", status_code=201, response_model=Lamp)
def create_lamp(payload: LampCreate) -> Lamp:
    if store.lamp_by_channel(payload.channel) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"channel '{payload.channel}' is already registered",
        )
    return store.add_lamp(uuid4().hex, payload)


@app.get("/lamps", response_model=List[Lamp])
def list_lamps() -> List[Lamp]:
    return store.list_lamps()


@app.get("/lamps/{lamp_id}", response_model=Lamp)
def get_lamp(lamp_id: str) -> Lamp:
    lamp = store.get_lamp(lamp_id)
    if lamp is None:
        raise HTTPException(status_code=404, detail="lamp not found")
    return lamp


@app.delete("/lamps/{lamp_id}", status_code=204, response_class=Response)
def delete_lamp(lamp_id: str) -> Response:
    if store.get_lamp(lamp_id) is None:
        raise HTTPException(status_code=404, detail="lamp not found")
    in_use = store.lamp_in_use(lamp_id)
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=f"lamp is referenced by scenes: {in_use}",
        )
    store.delete_lamp(lamp_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------

@app.post("/scenes", status_code=201, response_model=Scene)
def create_scene(payload: SceneCreate):
    conflicts = validate_scene(payload, store.lamps_by_channel(), store.list_scenes())
    if conflicts:
        # Invalid orchestration is rejected as a whole and never persisted.
        return JSONResponse(
            status_code=409,
            content=SceneRejected(conflicts=conflicts).model_dump(mode="json"),
        )
    scene = Scene(id=uuid4().hex, **payload.model_dump())
    return store.add_scene(scene)


@app.get("/scenes", response_model=List[Scene])
def list_scenes(kind: Optional[SceneKind] = None) -> List[Scene]:
    scenes = store.list_scenes()
    if kind is not None:
        scenes = [s for s in scenes if s.kind == kind]
    return sorted(scenes, key=lambda s: (s.start, s.id))


@app.get("/scenes/{scene_id}", response_model=Scene)
def get_scene(scene_id: str) -> Scene:
    scene = store.get_scene(scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    return scene


@app.delete("/scenes/{scene_id}", status_code=204, response_class=Response)
def delete_scene(scene_id: str) -> Response:
    if not store.delete_scene(scene_id):
        raise HTTPException(status_code=404, detail="scene not found")
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

@app.get("/timeline", response_model=Timeline)
def get_timeline(
    from_: Optional[datetime] = Query(default=None, alias="from"),
    to: Optional[datetime] = Query(default=None),
) -> Timeline:
    try:
        window_from = _require_tz_aware(from_) if from_ is not None else None
        window_to = _require_tz_aware(to) if to is not None else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if window_from is not None and window_to is not None and window_to < window_from:
        raise HTTPException(status_code=422, detail="'to' must not be before 'from'")
    return build_timeline(store.list_lamps(), store.list_scenes(), window_from, window_to)
