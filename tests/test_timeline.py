"""Timeline composition: ordering, emergency preemption, restore rules."""

T = "2026-10-06T"


def mk_scene(client, name, start, channels, kind="normal", end=None):
    payload = {"name": name, "kind": kind, "start": start, "channels": channels}
    if end is not None:
        payload["end"] = end
    r = client.post("/scenes", json=payload)
    assert r.status_code == 201, r.json()
    return r.json()


def entries(client, **params):
    r = client.get("/timeline", params=params)
    assert r.status_code == 200
    return r.json()


def test_timeline_orders_normal_scenes(client, two_lamps):
    mk_scene(client, "Noon", f"{T}12:00:00+08:00",
             [{"channel": "ch-1", "brightness": 80, "fade_seconds": 60}])
    mk_scene(client, "Morning", f"{T}09:00:00+08:00",
             [{"channel": "ch-1", "brightness": 50, "fade_seconds": 30}])
    tl = entries(client)
    assert [(e["kind"], e["brightness"]) for e in tl["entries"]] == [
        ("scene", 50), ("scene", 80),
    ]
    assert tl["entries"][0]["time"] == "2026-10-06T01:00:00Z"
    assert tl["entries"][1]["time"] == "2026-10-06T04:00:00Z"
    assert tl["preemptions"] == []


def test_emergency_preempts_and_restores(client, two_lamps):
    day = mk_scene(client, "Day", f"{T}09:00:00+08:00",
                   [{"channel": "ch-1", "brightness": 60, "fade_seconds": 30}])
    emg = mk_scene(client, "Fire drill", f"{T}10:00:00+08:00",
                   [{"channel": "ch-1", "brightness": 100, "fade_seconds": 5}],
                   kind="emergency", end=f"{T}10:15:00+08:00")
    tl = entries(client)
    assert [(e["kind"], e["brightness"], e["fade_seconds"]) for e in tl["entries"]] == [
        ("scene", 60, 30),
        ("emergency", 100, 5),
        ("restore", 60, 5),
    ]
    assert tl["entries"][2]["scene_id"] == day["id"]
    p = tl["preemptions"][0]
    assert p["emergency_scene_id"] == emg["id"]
    assert p["preempted_scene_id"] == day["id"]
    assert p["restored_scene_id"] == day["id"]
    assert p["restored_brightness"] == 60


def test_restore_falls_back_to_default_when_scene_expired(client, two_lamps):
    # Normal scene valid 09:00-09:30 only; emergency runs 10:00-10:15.
    # The preempted scene is no longer valid at emergency end -> default (10).
    mk_scene(client, "Short", f"{T}08:30:00+08:00",
             [{"channel": "ch-1", "brightness": 70, "fade_seconds": 10}],
             end=f"{T}08:50:00+08:00")
    # Emergency starts while "Short" is still valid, ends after it expired.
    mk_scene(client, "Drill", f"{T}08:45:00+08:00",
             [{"channel": "ch-1", "brightness": 100, "fade_seconds": 5}],
             kind="emergency", end=f"{T}09:00:00+08:00")
    tl = entries(client)
    kinds = [(e["kind"], e["brightness"]) for e in tl["entries"]]
    assert kinds == [("scene", 70), ("emergency", 100), ("restore", 10)]
    p = tl["preemptions"][0]
    assert p["restored_scene_id"] is None
    assert p["restored_brightness"] == 10  # ch-1 default_brightness


def test_emergency_drops_normal_commands_inside_window(client, two_lamps):
    mk_scene(client, "Base", f"{T}09:00:00+08:00",
             [{"channel": "ch-1", "brightness": 50, "fade_seconds": 10}])
    mk_scene(client, "During emergency", f"{T}10:05:00+08:00",
             [{"channel": "ch-1", "brightness": 90, "fade_seconds": 10}])
    mk_scene(client, "Drill", f"{T}10:00:00+08:00",
             [{"channel": "ch-1", "brightness": 100, "fade_seconds": 5}],
             kind="emergency", end=f"{T}10:10:00+08:00")
    tl = entries(client)
    # The 10:05 normal command is preempted; restore returns to "Base" (50),
    # never to the scene that started during the emergency.
    assert [(e["kind"], e["brightness"]) for e in tl["entries"]] == [
        ("scene", 50), ("emergency", 100), ("restore", 50),
    ]


def test_emergency_only_affects_its_own_channels(client, two_lamps):
    mk_scene(client, "Hall A", f"{T}09:00:00+08:00",
             [{"channel": "ch-1", "brightness": 60, "fade_seconds": 10}])
    mk_scene(client, "Hall B", f"{T}09:00:00+08:00",
             [{"channel": "ch-2", "brightness": 40, "fade_seconds": 10}])
    mk_scene(client, "Drill A", f"{T}10:00:00+08:00",
             [{"channel": "ch-1", "brightness": 100, "fade_seconds": 5}],
             kind="emergency", end=f"{T}10:10:00+08:00")
    tl = entries(client)
    ch2 = [e for e in tl["entries"] if e["channel"] == "ch-2"]
    assert [(e["kind"], e["brightness"]) for e in ch2] == [("scene", 40)]
    ch1 = [e for e in tl["entries"] if e["channel"] == "ch-1"]
    assert [e["kind"] for e in ch1] == ["scene", "emergency", "restore"]


def test_timeline_window_filter(client, two_lamps):
    mk_scene(client, "S1", f"{T}09:00:00+08:00",
             [{"channel": "ch-1", "brightness": 50, "fade_seconds": 10}])
    mk_scene(client, "S2", f"{T}12:00:00+08:00",
             [{"channel": "ch-1", "brightness": 80, "fade_seconds": 10}])
    tl = entries(client, **{
        "from": f"{T}10:00:00+08:00", "to": f"{T}13:00:00+08:00",
    })
    assert len(tl["entries"]) == 1
    assert tl["entries"][0]["brightness"] == 80


def test_timeline_rejects_naive_window(client, two_lamps):
    r = client.get("/timeline", params={"from": "2026-10-06T09:00:00"})
    assert r.status_code == 422
