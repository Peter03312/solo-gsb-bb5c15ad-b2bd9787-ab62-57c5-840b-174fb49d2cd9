"""API-level tests: lamps, scene validation, conflict reporting."""

T = "2026-10-06T"


def scene_payload(**overrides):
    payload = {
        "name": "Morning",
        "kind": "normal",
        "start": f"{T}09:00:00+08:00",
        "channels": [{"channel": "ch-1", "brightness": 60, "fade_seconds": 30}],
    }
    payload.update(overrides)
    return payload


# -- lamps -------------------------------------------------------------------

def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_and_get_lamp(client):
    r = client.post("/lamps", json={"name": "Spot", "channel": "ch-1"})
    assert r.status_code == 201
    lamp = r.json()
    assert lamp["min_brightness"] == 0 and lamp["max_brightness"] == 100
    assert client.get(f"/lamps/{lamp['id']}").json()["channel"] == "ch-1"
    assert len(client.get("/lamps").json()) == 1


def test_duplicate_channel_rejected(client):
    client.post("/lamps", json={"name": "A", "channel": "ch-1"})
    r = client.post("/lamps", json={"name": "B", "channel": "ch-1"})
    assert r.status_code == 409


def test_delete_lamp_in_use_rejected(client, two_lamps):
    assert client.post("/scenes", json=scene_payload()).status_code == 201
    lamp_id = client.get("/lamps").json()[0]["id"]
    assert client.delete(f"/lamps/{lamp_id}").status_code == 409


# -- scene validation ----------------------------------------------------------

def test_create_scene_ok(client, two_lamps):
    r = client.post("/scenes", json=scene_payload())
    assert r.status_code == 201
    assert r.json()["start"] == "2026-10-06T01:00:00Z"  # normalized to UTC
    assert len(client.get("/scenes").json()) == 1


def test_unknown_channel_rejected_and_not_saved(client, two_lamps):
    r = client.post("/scenes", json=scene_payload(
        channels=[{"channel": "ch-99", "brightness": 50, "fade_seconds": 10}],
    ))
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "scene_rejected"
    c = body["conflicts"][0]
    assert c["channel"] == "ch-99" and c["reason"] == "lamp_not_found"
    assert client.get("/scenes").json() == []  # nothing persisted


def test_brightness_out_of_range(client, two_lamps):
    r = client.post("/scenes", json=scene_payload(
        channels=[{"channel": "ch-2", "brightness": 90, "fade_seconds": 10}],
    ))
    assert r.status_code == 409
    c = r.json()["conflicts"][0]
    assert c["channel"] == "ch-2"
    assert c["reason"] == "brightness_out_of_range"
    assert "[20, 80]" in c["detail"]
    assert client.get("/scenes").json() == []


def test_fade_overlap_conflict_reports_interval(client, two_lamps):
    assert client.post("/scenes", json=scene_payload(
        name="S1", start=f"{T}09:00:00+08:00",
        channels=[{"channel": "ch-1", "brightness": 60, "fade_seconds": 300}],
    )).status_code == 201
    r = client.post("/scenes", json=scene_payload(
        name="S2", start=f"{T}09:02:00+08:00",
        channels=[{"channel": "ch-1", "brightness": 30, "fade_seconds": 60}],
    ))
    assert r.status_code == 409
    c = r.json()["conflicts"][0]
    assert c["reason"] == "fade_interval_overlap"
    assert c["channel"] == "ch-1"
    # intersection of [09:00, 09:05] and [09:02, 09:03]
    assert c["interval"]["start"] == "2026-10-06T01:02:00Z"
    assert c["interval"]["end"] == "2026-10-06T01:03:00Z"
    assert len(client.get("/scenes").json()) == 1  # only S1 saved


def test_back_to_back_fades_allowed(client, two_lamps):
    assert client.post("/scenes", json=scene_payload(
        name="S1", start=f"{T}09:00:00+08:00",
        channels=[{"channel": "ch-1", "brightness": 60, "fade_seconds": 300}],
    )).status_code == 201
    r = client.post("/scenes", json=scene_payload(
        name="S2", start=f"{T}09:05:00+08:00",
        channels=[{"channel": "ch-1", "brightness": 30, "fade_seconds": 60}],
    ))
    assert r.status_code == 201


def test_different_channels_may_overlap(client, two_lamps):
    assert client.post("/scenes", json=scene_payload(
        name="S1", channels=[
            {"channel": "ch-1", "brightness": 60, "fade_seconds": 300},
        ],
    )).status_code == 201
    r = client.post("/scenes", json=scene_payload(
        name="S2", channels=[
            {"channel": "ch-2", "brightness": 40, "fade_seconds": 300},
        ],
    ))
    assert r.status_code == 201


def test_emergency_requires_end(client, two_lamps):
    r = client.post("/scenes", json=scene_payload(kind="emergency"))
    assert r.status_code == 422


def test_end_must_be_after_start(client, two_lamps):
    r = client.post("/scenes", json=scene_payload(
        start=f"{T}10:00:00+08:00", end=f"{T}09:00:00+08:00",
    ))
    assert r.status_code == 422


def test_naive_datetime_rejected(client, two_lamps):
    r = client.post("/scenes", json=scene_payload(start="2026-10-06T09:00:00"))
    assert r.status_code == 422


def test_duplicate_channel_in_scene_rejected(client, two_lamps):
    r = client.post("/scenes", json=scene_payload(channels=[
        {"channel": "ch-1", "brightness": 10, "fade_seconds": 5},
        {"channel": "ch-1", "brightness": 20, "fade_seconds": 5},
    ]))
    assert r.status_code == 422


def test_emergency_window_overlap_rejected(client, two_lamps):
    base = dict(
        kind="emergency",
        channels=[{"channel": "ch-1", "brightness": 100, "fade_seconds": 5}],
    )
    assert client.post("/scenes", json=scene_payload(
        name="E1", start=f"{T}12:00:00+08:00", end=f"{T}12:10:00+08:00", **base,
    )).status_code == 201
    r = client.post("/scenes", json=scene_payload(
        name="E2", start=f"{T}12:05:00+08:00", end=f"{T}12:15:00+08:00", **base,
    ))
    assert r.status_code == 409
    c = r.json()["conflicts"][0]
    assert c["reason"] == "emergency_window_overlap"
    assert c["interval"]["start"] == "2026-10-06T04:05:00Z"
    assert c["interval"]["end"] == "2026-10-06T04:10:00Z"


def test_normal_fade_may_overlap_emergency_fade(client, two_lamps):
    """Normal-vs-emergency interplay is preemption, not a conflict."""
    assert client.post("/scenes", json=scene_payload(
        name="S1", start=f"{T}12:02:00+08:00",
        channels=[{"channel": "ch-1", "brightness": 50, "fade_seconds": 600}],
    )).status_code == 201
    r = client.post("/scenes", json=scene_payload(
        name="E1", kind="emergency",
        start=f"{T}12:00:00+08:00", end=f"{T}12:10:00+08:00",
        channels=[{"channel": "ch-1", "brightness": 100, "fade_seconds": 10}],
    ))
    assert r.status_code == 201


def test_delete_scene(client, two_lamps):
    scene_id = client.post("/scenes", json=scene_payload()).json()["id"]
    assert client.delete(f"/scenes/{scene_id}").status_code == 204
    assert client.get("/scenes").json() == []
    assert client.delete("/scenes/nope").status_code == 404
