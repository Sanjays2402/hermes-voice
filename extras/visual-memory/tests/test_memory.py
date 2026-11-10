"""The acceptance scenarios, on synthetic scenes with a scripted model (letters match the handoff's tests)."""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest
from vismem import annotate, queries
from vismem.config import Config
from vismem.memory import Memory
from vismem.store import Store
from vismem.vision_local import Thresholds, Visibility, assess, difference
from vismem.vision_model import VisionError, parse

from .scenes import BLACK_BOX, CARDBOARD, COVERED, DESK, FakeVision, Obj, dark, draw, shot


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def env(tmp_path: Path):
    cfg = Config(data_dir=str(tmp_path), min_auto_interval=60, mirror_for_user=False, auto_analyze=True, fresh_for=0)
    store = Store(cfg.data_path)
    fake = FakeVision()
    clock = Clock()
    mem = Memory(cfg, store, fake, clock=clock, background=False)
    mem.set_enabled(True)
    return cfg, store, fake, clock, mem


def run(mem: Memory, clock: Clock, s, seconds: int) -> None:
    for _ in range(seconds):
        mem.step(s)
        clock.t += 1


def baseline(mem: Memory, fake: FakeVision, clock: Clock, objs: list[Obj]) -> None:
    fake.scene = list(objs)
    run(mem, clock, shot(draw(objs)), 5)
    assert len(fake.calls) == 1, "the first steady scene is analysed once"


def kinds(store: Store, mem: Memory) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in store.changes(mem.baseline_id or 0):
        out.setdefault(c["kind"], []).append((c["detail"] or {}).get("label", ""))
    return out


def test_quality_states() -> None:
    t = Thresholds()
    assert assess(draw(DESK), t).visibility is Visibility.CLEAR
    assert assess(dark(), t).visibility is Visibility.UNREADABLE
    blurred = cv2.GaussianBlur(draw(DESK), (0, 0), 12)
    assert assess(blurred, t).visibility in (Visibility.UNCERTAIN, Visibility.UNREADABLE)


def test_light_and_nudge_are_not_changes() -> None:
    t = Thresholds()
    a = assess(draw(DESK), t).small
    assert difference(a, assess(draw(DESK, light=0.7, rng_seed=5), t).small).fraction < 0.004
    assert difference(a, assess(draw(DESK, shift=(6, 3), rng_seed=6), t).small).fraction < 0.004
    gone = [o for o in DESK if o.label != "keys"]
    d = difference(a, assess(draw(gone), t).small)
    assert d.fraction > 0.004 and d.regions


def test_a_records_objects_with_positions(env) -> None:
    _cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    obs = store.observations(mem.baseline_id)
    assert {o["label"] for o in obs} == {o.label for o in DESK}
    red = next(o for o in obs if o["label"] == "red screwdriver")
    assert red["position"] == "on the left" and red["attributes"] == ["red handle"]
    assert store.image_path(store.scene(mem.baseline_id))


def test_j_unchanged_scene_makes_no_more_calls(env) -> None:
    _cfg, _store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    for n in range(600):  # ten minutes, slightly different sensor noise every second
        mem.step(shot(draw(DESK, rng_seed=n)))
        clock.t += 1
    assert len(fake.calls) == 1


def test_movement_never_settles_no_calls(env) -> None:
    _cfg, _store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    hand = Obj("hand", "x", (), (300, 300, 600, 700), (120, 150, 200))
    for n in range(60):
        moved = hand.at((300 + n % 5 * 60, 300, 600 + n % 5 * 60, 700))
        mem.step(shot(draw([*DESK, moved], rng_seed=n)))
        clock.t += 1
    assert len(fake.calls) == 1


def test_b_cover_and_uncover_without_changes(env) -> None:
    _cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    clock.t += 120
    run(mem, clock, COVERED, 5)
    assert mem.live.visibility is Visibility.UNREADABLE
    run(mem, clock, shot(dark()), 3)  # a hand over the lens: dark, not a 423
    run(mem, clock, shot(draw(DESK, rng_seed=9)), 5)
    assert len(fake.calls) == 1, "nothing changed: no model call"
    assert not store.items(("missing", "possibly_obstructed"))
    r = queries.what_changed(mem, fresh=False)
    assert r["say"].count("Nothing changed") == 1 and "after the camera's view was interrupted" in r["say"]


def test_cde_demo_missing_moved_added(env) -> None:
    _cfg, store, fake, clock, mem = env
    mem.set_enabled(True, mode="demo")
    baseline(mem, fake, clock, DESK)
    clock.t += 30
    run(mem, clock, COVERED, 4)
    after = [o for o in DESK if o.label not in ("keys", "blue tape")]
    after = [o.at((380, 420, 460, 590)) if o.label == "coffee mug" else o for o in after] + [BLACK_BOX]
    fake.scene = after
    run(mem, clock, shot(draw(after)), 5)
    assert len(fake.calls) == 2
    k = kinds(store, mem)
    assert sorted(k["missing"]) == ["blue tape", "keys"]
    assert k["moved"] == ["coffee mug"] and k["added"] == ["small black box"]
    say = queries.what_changed(mem, fresh=False)["say"]
    assert "no longer visible" in say and "doesn't prove" in say
    assert "moved from on the left to in the middle" in say and "small black box" in say
    assert "Still there:" in say and "red screwdriver" in say
    evidence = annotate.comparison(store, mem.baseline_id, mirror=False)
    assert evidence and cv2.imdecode(__import__("numpy").frombuffer(evidence, "uint8"), 1).shape[1] == 1280


def test_f_box_in_front_is_possibly_obstructed(env) -> None:
    _cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    clock.t += 120
    now = [*DESK, CARDBOARD]
    fake.scene, fake.blind, fake.missing_says = now, {"red screwdriver"}, "gone"  # the model even says "gone"
    run(mem, clock, shot(draw(now)), 5)
    assert kinds(store, mem)["possibly_obstructed"] == ["red screwdriver"]
    say = queries.find(mem, "red screwdriver")["say"]
    assert "I last saw the red screwdriver on the left" in say and "may be obstructed" in say
    assert "don't see it there anymore" not in say
    # The box goes away: it is back where it was, visible again.
    clock.t += 120
    fake.scene, fake.blind = list(DESK), set()
    run(mem, clock, shot(draw(DESK)), 5)
    assert store.items(("visible",)) and not store.items(("possibly_obstructed",))


def test_object_removed_from_visible_spot_is_missing(env) -> None:
    _cfg, _store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    clock.t += 120
    now = [o for o in DESK if o.label != "red screwdriver"]
    fake.scene = now
    run(mem, clock, shot(draw(now)), 5)
    say = queries.find(mem, "where's my red screwdriver")["say"]
    assert "was on the left" in say and "don't see it there anymore" in say


def test_missed_by_model_but_spot_unchanged_is_not_missing(env) -> None:
    _cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    clock.t += 120
    now = [*DESK, BLACK_BOX]
    fake.scene, fake.blind = now, {"yellow pencil"}  # the model overlooks the pencil
    run(mem, clock, shot(draw(now)), 5)
    pencil = next(i for i in store.items() if i["label"] == "yellow pencil")
    assert pencil["status"] == "unconfirmed"
    assert "very likely still there" in queries.find(mem, "pencil")["say"]


def test_model_forgets_link_still_moved(env) -> None:
    _cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    clock.t += 120
    now = [o.at((380, 420, 460, 590)) if o.label == "coffee mug" else o for o in DESK]
    fake.scene, fake.link = now, False
    run(mem, clock, shot(draw(now)), 5)
    k = kinds(store, mem)
    assert k.get("moved") == ["coffee mug"] and "added" not in k and "missing" not in k


def test_g_ambiguous_screwdrivers(env) -> None:
    _cfg, _store, fake, clock, mem = env
    three = [
        Obj("red screwdriver", "screwdriver", ("red handle",), (60, 300, 200, 360), (40, 40, 220)),
        Obj("black screwdriver", "screwdriver", ("black handle",), (300, 300, 440, 360), (30, 30, 30)),
        Obj("screwdriver", "screwdriver", (), (600, 600, 740, 660), (120, 120, 120)),
        Obj("blue pliers", "pliers", ("blue handles",), (450, 450, 560, 560), (200, 90, 30)),
    ]
    baseline(mem, fake, clock, three)
    r = queries.find(mem, "red screwdriver")
    assert r["items"][0]["label"] == "red screwdriver"
    green = queries.find(mem, "green screwdriver")["say"]
    assert "haven't seen one I could tell was green" in green and "couldn't make out" in green
    assert "black screwdriver" in green


def test_h_unreadable_suspends_updates(env) -> None:
    _cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    before = {i["id"]: i["status"] for i in store.items()}
    run(mem, clock, shot(dark()), 60)
    blurred = cv2.GaussianBlur(draw(DESK[:3]), (0, 0), 14)
    run(mem, clock, shot(blurred), 10)
    assert len(fake.calls) == 1
    assert {i["id"]: i["status"] for i in store.items()} == before
    assert "obstructed" in queries.status(mem)["say"] or mem.live.visibility is not Visibility.CLEAR


def test_i_memory_survives_restart(env, tmp_path: Path) -> None:
    cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    sid = mem.baseline_id
    store.close()
    store2 = Store(cfg.data_path)
    mem2 = Memory(cfg, store2, FakeVision(), clock=clock, background=False)
    assert mem2.baseline_id == sid and mem2.baseline_small is not None
    assert "red screwdriver is on the left" in queries.find(mem2, "red screwdriver")["say"]
    item = next(i for i in store2.items() if i["label"] == "keys")
    assert annotate.item_evidence(store2, item, mirror=True)
    assert not mem2.enabled or cfg.resume_after_restart is False


def test_limits_defer_then_analyse(env) -> None:
    _cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    now = [*DESK, BLACK_BOX]
    fake.scene = now
    run(mem, clock, shot(draw(now)), 5)  # 5 s after the first analysis: must wait for the 60 s gap
    assert len(fake.calls) == 1 and mem.pending is not None and "waiting" in mem.live.deferred
    run(mem, clock, shot(draw(now, rng_seed=2)), 60)
    assert len(fake.calls) == 2 and mem.pending is None
    assert kinds(store, mem)["added"] == ["small black box"]
    events = store.q("SELECT * FROM events WHERE kind='deferred'")
    assert len(events) == 1


def test_hourly_cap(env) -> None:
    cfg, _store, fake, clock, mem = env
    cfg.auto_per_hour = 2
    baseline(mem, fake, clock, DESK)
    for n, extra in enumerate([BLACK_BOX, CARDBOARD.at((300, 600, 500, 700))]):
        clock.t += 61
        fake.scene = [*DESK, extra]
        run(mem, clock, shot(draw(fake.scene, rng_seed=n)), 5)
    assert len(fake.calls) == 2 and "hourly limit" in mem.live.deferred


def test_asked_analysis_and_what_changed(env) -> None:
    _cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    now = [*DESK, BLACK_BOX]
    fake.scene = now
    run(mem, clock, shot(draw(now)), 4)  # deferred by the 60 s gap
    r = queries.what_changed(mem)
    assert len(fake.calls) == 2 and "small black box" in r["say"]
    assert store.usage(0)[-1]["trigger"] == "asked"


def test_paused_takes_no_pictures_and_says_so(env) -> None:
    _cfg, _store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    mem.set_enabled(False)
    assert "off" in queries.status(mem)["say"]
    assert "Visual memory is off" in queries.find(mem, "keys")["say"]


def test_history_and_before_obstruction(env) -> None:
    _cfg, _store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    clock.t += 300
    run(mem, clock, COVERED, 3)
    r = queries.scene(mem, "before_obstruction")
    assert "red screwdriver" in r["say"]
    h = queries.history(mem, "black box", since="1h")
    assert "didn't see a black box" in h["say"]
    h = queries.history(mem, "keys", since="1h")
    assert "keys" in h["say"]


def test_forget_everything(env) -> None:
    cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    store.forget()
    assert not store.items() and not list((cfg.data_path / "snapshots").rglob("*.jpg"))


def test_parse_model_answer() -> None:
    a = parse(
        {
            "summary": "desk",
            "objects": [
                "red screwdriver|screwdriver|red handle, metal tip|60 300 200 360|f|o4",
                "mug|mug||250 250 330 420|p|",
                "bad line",
                "x|y|z|5 5 1 1|f|",
            ],
            "missing": ["o7|hidden", "9|gone", "nonsense"],
        }
    )
    assert [d.label for d in a.objects] == ["red screwdriver", "mug"]
    assert a.objects[0].same == 4 and a.objects[0].attributes == ["red handle", "metal tip"]
    assert a.objects[1].same is None and a.objects[1].vis == "p"
    assert a.missing == {7: "hidden", 9: "gone"}


def hand_over_lens(seed: int = 0):  # -> a numpy image
    """Skin right against the lens: bright (auto-exposure), some shading, no focus."""
    import numpy as np

    rng = np.random.default_rng(seed)
    img = np.zeros((720, 1280, 3), np.float32)
    img[:] = (120, 150, 200)
    shade = cv2.GaussianBlur(rng.normal(0, 1, (720, 1280)).astype(np.float32), (0, 0), 60) * 5000
    img += shade[..., None]
    skin = cv2.GaussianBlur(rng.normal(0, 1, (720, 1280)).astype(np.float32), (0, 0), 2.5) * 40
    img += skin[..., None]  # skin texture, out of focus: the Show measured 20-55 under a hand
    return np.clip(img + rng.normal(0, 2, img.shape), 0, 255).astype(np.uint8)


def test_b_hand_over_lens_is_obstruction_not_change(env) -> None:
    _cfg, _store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    run(mem, clock, shot(draw(DESK, rng_seed=3)), 5)  # learn this view's usual detail
    for n in range(6):
        mem.step(shot(hand_over_lens(n)))
        clock.t += 1
        assert mem.live.visibility is Visibility.UNREADABLE, f"hand frame {n}: {mem.live.why}"
    run(mem, clock, shot(draw(DESK, rng_seed=11)), 5)
    assert len(fake.calls) == 1
    assert "Nothing changed" in queries.what_changed(mem, fresh=False)["say"]


def test_a_plain_view_that_stays_becomes_normal(env) -> None:
    cfg, _store, fake, clock, mem = env
    cfg.dull_accept_samples = 10
    baseline(mem, fake, clock, DESK)
    run(mem, clock, shot(draw(DESK, rng_seed=3)), 5)
    run(mem, clock, shot(hand_over_lens(1)), 15)  # the same dull view for longer than dull_accept_samples
    assert mem.live.visibility is not Visibility.UNREADABLE


def test_renamed_by_the_model_at_the_same_spot_is_the_same_thing(env) -> None:
    _cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    clock.t += 120
    renamed = [
        Obj("metal key ring", "key ring", ("silver",), o.box, o.colour) if o.label == "keys" else o
        for o in DESK
    ] + [BLACK_BOX]
    fake.scene, fake.link = renamed, False
    run(mem, clock, shot(draw(renamed)), 5)
    k = kinds(store, mem)
    assert k.get("added") == ["small black box"] and "missing" not in k


def test_a_quiet_relook_keeps_the_real_comparison(env) -> None:
    _cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    clock.t += 120
    now = [o for o in DESK if o.label != "keys"]
    fake.scene = now
    run(mem, clock, shot(draw(now)), 5)
    real = int(store.get("last_comparison"))
    clock.t += 120
    hand = Obj("someone's arm", "x", (), (850, 0, 1000, 200), (25, 25, 30))  # movement in the background
    fake.scene = now  # the model sees the same objects
    run(mem, clock, shot(draw([*now, hand])), 5)
    assert len(fake.calls) == 3 and int(store.get("last_comparison")) == real
    assert "keys" in queries.what_changed(mem, fresh=False)["say"]


def test_thin_thing_put_in_an_empty_spot_does_not_hide_it(env) -> None:
    _cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    clock.t += 120
    # the screwdriver is taken away and the pencil laid across the middle of its old spot
    now = [
        o.at((50, 315, 210, 345)) if o.label == "yellow pencil" else o
        for o in DESK
        if o.label != "red screwdriver"
    ]
    fake.scene, fake.missing_says = now, "gone"
    run(mem, clock, shot(draw(now)), 5)
    k = kinds(store, mem)
    assert k.get("missing") == ["red screwdriver"] and "possibly_obstructed" not in k


def test_find_takes_a_fresh_look_when_automatic_looks_are_capped(env) -> None:
    _cfg, _store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    now = [o for o in DESK if o.label != "keys"]
    fake.scene = now
    run(mem, clock, shot(draw(now)), 5)  # changed, but the 60 s gap holds the automatic look back
    assert len(fake.calls) == 1
    say = queries.find(mem, "keys", fresh=True)["say"]
    assert len(fake.calls) == 2 and "don't see it there anymore" in say
    queries.find(mem, "keys", fresh=True)  # nothing new: no further call
    assert len(fake.calls) == 2


def test_c_after_f_hidden_then_taken_away_is_missing(env) -> None:
    """Tonight's live sequence: a box in front of the screwdriver, then box and screwdriver both gone."""
    _cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    clock.t += 120
    fake.scene, fake.blind = [*DESK, CARDBOARD], {"red screwdriver"}
    run(mem, clock, shot(draw([*DESK, CARDBOARD])), 5)
    assert kinds(store, mem)["possibly_obstructed"] == ["red screwdriver"]
    clock.t += 120
    now = [o for o in DESK if o.label != "red screwdriver"]
    fake.scene, fake.blind, fake.missing_says = now, set(), "hidden"  # the model even guesses "hidden"
    run(mem, clock, shot(draw(now)), 5)
    k = kinds(store, mem)
    assert "red screwdriver" in k.get("missing", []), k  # compared with where it was last seen, not the box
    assert "red screwdriver" not in k.get("possibly_obstructed", []), k


def test_renamed_with_same_colour_nearby_is_the_same_thing(env) -> None:
    _cfg, store, fake, clock, mem = env
    baseline(mem, fake, clock, DESK)
    clock.t += 120
    now = [
        Obj("orange bowl", "bowl", ("orange",), (690, 470, 800, 590), o.colour)
        if o.label == "orange ball"
        else o
        for o in DESK
    ]
    fake.scene, fake.link = now, False
    run(mem, clock, shot(draw(now)), 5)
    k = kinds(store, mem)
    assert "added" not in k and "missing" not in k, k


def test_a_plainer_view_that_stays_stops_being_uncertain(env) -> None:
    """Live: after things were rearranged the view had less texture and stayed "uncertain" for good."""
    cfg, _store, fake, clock, mem = env
    cfg.dull_accept_samples = 10
    baseline(mem, fake, clock, DESK)
    run(mem, clock, shot(draw(DESK, rng_seed=3)), 5)
    plain = cv2.GaussianBlur(draw(DESK[:2], rng_seed=4), (0, 0), 1.6)  # fewer things, softer: less detail
    run(mem, clock, shot(plain), 3)
    assert mem.live.visibility is not Visibility.CLEAR
    run(mem, clock, shot(plain), 15)
    assert mem.live.visibility is Visibility.CLEAR


def test_brief_line_to_say(env) -> None:
    _cfg, _store, fake, clock, mem = env
    mem.set_enabled(True, mode="demo")
    baseline(mem, fake, clock, DESK)
    clock.t += 30
    run(mem, clock, COVERED, 4)
    after = [o for o in DESK if o.label not in ("keys", "blue tape")]
    after = [o.at((380, 420, 460, 590)) if o.label == "coffee mug" else o for o in after] + [BLACK_BOX]
    fake.scene = after
    run(mem, clock, shot(draw(after)), 5)
    r = queries.what_changed(mem, fresh=False)
    assert r["brief"] == "The keys and the blue tape are gone. New: a small black box."
    assert len(r["say"]) > len(r["brief"])  # the details stay for the screen


def test_covering_after_an_unseen_change_looks_at_the_before(env) -> None:
    """Live: the desk was rearranged while automatic looks were capped, then covered; "before" was 5 min old."""
    cfg, store, fake, clock, mem = env
    mem.set_enabled(True, mode="demo")
    cfg.auto_per_day = 1  # the first look used the day's automatic budget
    baseline(mem, fake, clock, DESK)
    clock.t += 300
    rearranged = [*DESK, BLACK_BOX]
    fake.scene = rearranged
    run(mem, clock, shot(draw(rearranged)), 5)  # steady and changed, but no automatic look is allowed
    assert len(fake.calls) == 1
    run(mem, clock, COVERED, 3)  # covered: the view just before is analysed now
    assert len(fake.calls) == 2 and store.scene(mem.baseline_id)["trigger"] == "before_obstruction"
    gone = [o for o in rearranged if o.label != "keys"]
    fake.scene = gone
    run(mem, clock, shot(draw(gone)), 5)
    r = queries.what_changed(mem, fresh=False)
    assert r["brief"] == "The keys are gone.", r["brief"]  # compared with just before the cover


def test_a_question_uses_the_quick_model(tmp_path) -> None:
    cfg = Config(data_dir=str(tmp_path), mirror_for_user=False, auto_analyze=True, fresh_for=0)
    store, slow, quick, clock = Store(cfg.data_path), FakeVision(), FakeVision(), Clock()
    mem = Memory(cfg, store, slow, clock=clock, background=False, quick_vision=quick)
    mem.set_enabled(True)
    baseline(mem, slow, clock, DESK)
    now = [o for o in DESK if o.label != "keys"]
    slow.scene = quick.scene = now
    run(mem, clock, shot(draw(now)), 4)  # held back by the 60 s gap
    queries.what_changed(mem)
    assert len(slow.calls) == 1 and len(quick.calls) == 1


def test_a_steady_plainer_view_after_uncovering_is_looked_at_at_once(env) -> None:
    """Live: something was taken away, the desk got plainer, and the after-cover look waited a minute."""
    cfg, _store, fake, clock, mem = env
    mem.set_enabled(True, mode="demo")
    baseline(mem, fake, clock, DESK)
    run(mem, clock, shot(draw(DESK, rng_seed=3)), 5)  # learn this view's usual detail
    clock.t += 30
    run(mem, clock, COVERED, 4)
    plain = DESK[:3]
    fake.scene = plain
    soft = cv2.GaussianBlur(draw(plain, rng_seed=4), (0, 0), 0.6)
    from vismem.vision_local import Thresholds as T, assess as A

    ref = A(draw(DESK, rng_seed=3), T()).sharpness
    ratio = A(soft, T()).sharpness / ref
    assert cfg.obstructed_detail < ratio < cfg.uncertain_detail, ratio  # the "somewhat plainer" band
    run(mem, clock, shot(soft), 5)
    assert len(fake.calls) == 2, "the after-cover look ran as soon as the view was steady"


# Looks only when asked (the default)


@pytest.fixture
def ondemand(tmp_path: Path):
    cfg = Config(data_dir=str(tmp_path), mirror_for_user=False, fresh_for=0)
    assert cfg.auto_analyze is False
    store, fake, clock = Store(cfg.data_path), FakeVision(), Clock()
    mem = Memory(cfg, store, fake, clock=clock, background=False)
    mem.set_enabled(True)
    return cfg, store, fake, clock, mem


def test_demo_whats_in_front_then_what_did_i_take(ondemand) -> None:
    """Three things on the desk; "what's in front of you?"; cover, take one, uncover; "what did I take?"."""
    _cfg, store, fake, clock, mem = ondemand
    baseline(mem, fake, clock, DESK)  # the one look when it is turned on
    r = queries.scene(mem, "now", fresh=True)
    assert len(fake.calls) == 1, "nothing changed since that look: no new one"
    assert r["brief"] == "I see 9 things, including a red screwdriver, a coffee mug, keys and a blue tape."
    run(mem, clock, COVERED, 4)
    two = [o for o in DESK if o.label != "coffee mug"]
    fake.scene = two
    run(mem, clock, shot(draw(two)), 20)
    assert len(fake.calls) == 2, "uncovering starts the comparison at once"
    r = queries.what_changed(mem, fresh=True)
    assert len(fake.calls) == 2, "the question uses it"
    assert r["brief"] == "The coffee mug is gone.", r["brief"]
    assert annotate.comparison(store, r["scene"], False)


def test_no_change_needs_no_look(ondemand) -> None:
    _cfg, _store, fake, clock, mem = ondemand
    baseline(mem, fake, clock, DESK)
    run(mem, clock, COVERED, 4)
    run(mem, clock, shot(draw(DESK)), 6)
    r = queries.what_changed(mem, fresh=True)
    assert len(fake.calls) == 1 and r["brief"] == "Nothing changed."
    clock.t += 120
    r = queries.what_changed(mem, fresh=True)
    assert len(fake.calls) == 1 and r["brief"] == "Nothing changed."


def test_changes_are_saved_not_looked_at(ondemand) -> None:
    _cfg, store, fake, clock, mem = ondemand
    baseline(mem, fake, clock, DESK)
    for extra in (BLACK_BOX, CARDBOARD):
        scene_now = [*DESK, extra]
        fake.scene = scene_now
        run(mem, clock, shot(draw(scene_now)), 6)
        run(mem, clock, shot(draw(scene_now)), 6)  # the same view again is not saved twice
    assert len(fake.calls) == 1
    assert store.one("SELECT COUNT(*) AS n FROM scenes WHERE kind='local'")["n"] == 2


def test_turning_on_again_looks_once_more(ondemand) -> None:
    _cfg, _store, fake, clock, mem = ondemand
    baseline(mem, fake, clock, DESK)
    mem.set_enabled(False)
    mem.set_enabled(True)
    run(mem, clock, shot(draw(DESK)), 5)
    assert len(fake.calls) == 2


def test_hourly_limit_on_asked_looks(ondemand) -> None:
    cfg, _store, fake, clock, mem = ondemand
    cfg.manual_per_hour = 2
    baseline(mem, fake, clock, DESK)
    fake.scene = [*DESK, BLACK_BOX]
    run(mem, clock, shot(draw(fake.scene)), 5)
    assert mem.analyze_now(wait=0)["state"] == "done"
    fake.scene = DESK
    run(mem, clock, shot(draw(DESK)), 5)
    assert mem.analyze_now(wait=0)["state"] == "limited"
    cfg.manual_per_hour = 0  # unlimited
    assert mem.analyze_now(wait=0)["state"] == "done"


def test_spoken_lists() -> None:
    assert queries._list(["red screwdriver", "coffee mug", "keys"]) == "a red screwdriver, a coffee mug and keys"
    assert queries._list(["orange ball"]) == "an orange ball"


def test_without_look_after_uncover_the_question_looks(ondemand) -> None:
    cfg, store, fake, clock, mem = ondemand
    cfg.look_after_uncover = False
    baseline(mem, fake, clock, DESK)
    run(mem, clock, COVERED, 4)
    rest = [o for o in DESK if o.label != "keys"]
    fake.scene = rest
    run(mem, clock, shot(draw(rest)), 20)
    assert len(fake.calls) == 1, "no look until asked"
    saved = store.one("SELECT * FROM scenes WHERE kind='local' ORDER BY ts DESC LIMIT 1")
    assert saved and saved["after_obstruction"] == 1 and store.image_path(saved)
    r = queries.what_changed(mem, fresh=True)
    assert len(fake.calls) == 2 and r["brief"] == "The keys are gone.", r["brief"]


def test_watching_the_desk_ignores_the_rest(ondemand) -> None:
    """The first look finds the desk (here: the left half); things off it are not tracked, and changes
    there (someone moving behind the desk) are not changes."""
    _cfg, store, fake, clock, mem = ondemand
    fake.workspace = [0, 0, 500, 1000]
    baseline(mem, fake, clock, DESK)
    assert mem.workspace is not None
    names = {i["label"] for i in store.items()}
    assert "red screwdriver" in names and "green notebook" not in names  # notebook is on the right
    off_desk = [*DESK, BLACK_BOX]  # the black box appears on the right
    fake.scene = off_desk
    run(mem, clock, shot(draw(off_desk)), 6)
    assert store.one("SELECT COUNT(*) AS n FROM scenes WHERE kind='local'")["n"] == 0
    r = queries.what_changed(mem, fresh=True)
    assert len(fake.calls) == 1 and r["brief"] == "Nothing changed."
    on_desk = [o for o in off_desk if o.label != "keys"]  # keys are on the left
    fake.scene = on_desk
    run(mem, clock, shot(draw(on_desk)), 6)
    r = queries.what_changed(mem, fresh=True)
    assert len(fake.calls) == 2 and r["brief"] == "The keys are gone.", r["brief"]


def test_watching_everything(ondemand) -> None:
    _cfg, store, fake, clock, mem = ondemand
    fake.workspace = [0, 0, 500, 1000]
    mem.set_enabled(True, area="all")
    baseline(mem, fake, clock, DESK)
    assert mem.workspace is None and "green notebook" in {i["label"] for i in store.items()}


def test_first_look_doesnt_wait_for_someone_to_hold_still(ondemand) -> None:
    _cfg, store, fake, clock, mem = ondemand
    fake.scene, fake.workspace = list(DESK), [0, 0, 500, 1000]
    for i in range(8):  # someone moving in view: never steady
        moving = [*DESK, BLACK_BOX.at((800 - 40 * i, 300, 900 - 40 * i, 420))]
        mem.step(shot(draw(moving)))
        clock.t += 1
    assert len(fake.calls) == 1 and mem.workspace is not None
    assert annotate.watching(store, mem.baseline_id, mem.workspace, False)


def test_a_question_can_be_the_first_look(ondemand) -> None:
    cfg, _store, fake, clock, mem = ondemand
    cfg.first_look_within = 999
    fake.scene, fake.workspace = list(DESK), [0, 0, 500, 1000]
    mem.step(shot(draw(DESK)))
    queries.scene(mem, "now", fresh=True)
    assert len(fake.calls) == 1 and mem.workspace is not None


def test_a_question_right_after_a_look_uses_it(ondemand) -> None:
    cfg, _store, fake, clock, mem = ondemand
    cfg.fresh_for = 15
    baseline(mem, fake, clock, DESK)
    fake.scene = [*DESK, BLACK_BOX]
    run(mem, clock, shot(draw(fake.scene)), 3)
    assert mem.analyze_now(wait=0)["state"] == "same" and len(fake.calls) == 1
    clock.t += 20
    run(mem, clock, shot(draw(fake.scene)), 1)
    assert mem.analyze_now(wait=0)["state"] == "done" and len(fake.calls) == 2


def test_turning_on_starts_a_fresh_picture_of_the_desk(ondemand) -> None:
    _cfg, store, fake, clock, mem = ondemand
    baseline(mem, fake, clock, DESK)
    mem.set_enabled(False)
    mem.set_enabled(True)
    three = DESK[:3]
    fake.scene = three
    run(mem, clock, shot(draw(three)), 5)
    assert len(fake.calls) == 2 and fake.calls[-1] == (False, []), "not compared with the last session"
    now = {i["label"] for i in store.items(("visible",))}
    assert now == {o.label for o in three}
    r = queries.scene(mem, "now")
    assert "green notebook" not in r["say"]
    assert "before visual memory was last turned on" in queries.find(mem, "green notebook")["say"]


def test_without_model_comparison_the_model_sees_only_the_current_picture(tmp_path) -> None:
    cfg = Config(data_dir=str(tmp_path), mirror_for_user=False, fresh_for=0, model_compares="false")
    store, fake, clock = Store(cfg.data_path), FakeVision(), Clock()
    mem = Memory(cfg, store, fake, clock=clock, background=False)
    mem.set_enabled(True)
    fake.link = False
    baseline(mem, fake, clock, DESK)
    run(mem, clock, COVERED, 4)
    rest = [o for o in DESK if o.label != "coffee mug"]
    fake.scene = rest
    run(mem, clock, shot(draw(rest)), 8)
    r = queries.what_changed(mem, fresh=True)
    assert all(c == (False, []) for c in fake.calls)
    assert r["brief"] == "The coffee mug is gone.", r["brief"]


class FailingOnce(FakeVision):
    """The model times out on its first look (it was still loading), then answers."""

    def __init__(self, failures: int = 1) -> None:
        super().__init__()
        self.failures = failures

    def analyze(self, current, previous, previous_objects):
        if self.failures:
            self.failures -= 1
            self.calls.append((previous is not None, previous_objects))
            raise VisionError("the model took longer than 30 s")
        return super().analyze(current, previous, previous_objects)


def test_a_failed_first_look_is_tried_again(tmp_path) -> None:
    cfg = Config(data_dir=str(tmp_path), mirror_for_user=False, fresh_for=0)
    store, fake, clock = Store(cfg.data_path), FailingOnce(), Clock()
    mem = Memory(cfg, store, fake, clock=clock, background=False)
    mem.set_enabled(True)
    fake.scene = list(DESK)
    run(mem, clock, shot(draw(DESK)), 12)
    assert len(fake.calls) == 2, "tried again a few seconds later"
    assert mem.baseline_id is not None and not mem.first_pending
    run(mem, clock, COVERED, 4)
    two = [o for o in DESK if o.label != "coffee mug"]
    fake.scene = two
    run(mem, clock, shot(draw(two)), 20)
    assert queries.what_changed(mem, fresh=True)["brief"] == "The coffee mug is gone."


def test_without_a_first_look_nothing_is_called_new(tmp_path) -> None:
    """The first look failed and the things were moved before it was tried again: the question's look is
    the first one, so it can't say what changed, and must not call everything "new"."""
    cfg = Config(data_dir=str(tmp_path), mirror_for_user=False, fresh_for=0)
    store, fake, clock = Store(cfg.data_path), FailingOnce(), Clock()
    mem = Memory(cfg, store, fake, clock=clock, background=False)
    mem.set_enabled(True)
    two = DESK[:2]
    fake.scene = two
    run(mem, clock, shot(draw(two)), 5)  # the first look fails
    assert mem.first_pending
    r = queries.what_changed(mem, fresh=True)
    assert r["brief"].startswith("I didn't get a look before, so I can't tell what changed. I see"), r["brief"]
    assert not store.changes(mem.baseline_id) or all(c["kind"] != "added" for c in store.changes(mem.baseline_id))


def test_one_thing_per_thing_and_spoken_names() -> None:
    a = parse(
        {
            "objects": [
                "green-capped-marker|marker||342 860 520 998|f|",
                "snow globe|snow globe||527 417 685 715|f|",
                "van-shaped snow globe base|base||487 690 739 940|f|",
                "red marker|marker||100 100 200 200|f|",
                "blue marker|marker||150 150 250 250|f|",
            ]
        }
    )
    labels = sorted(d.label for d in a.objects)
    assert labels == ["blue marker", "green capped marker", "red marker", "snow globe"]
    globe = next(d for d in a.objects if d.label == "snow globe")
    assert globe.box == [487, 417, 739, 940]


def test_spoken_comparison_skips_nudges() -> None:
    nudged = {"kind": "moved", "detail": {"label": "keys", "from": "on the left", "to": "on the left"}}
    gone = {"kind": "missing", "detail": {"label": "green-capped-marker"}}
    assert queries.brief([gone, nudged]) == "The green capped marker is gone."
    assert queries.brief([nudged]) == "I don't see any clear changes."


def test_local_model_looks_when_the_desk_settles(tmp_path) -> None:
    cfg = Config(data_dir=str(tmp_path), mirror_for_user=False, fresh_for=0, vision="local", local_model="m")
    assert cfg.settled_looks and not Config().settled_looks
    store, fake, clock = Store(cfg.data_path), FakeVision(), Clock()
    mem = Memory(cfg, store, fake, clock=clock, background=False)
    mem.set_enabled(True)
    fake.scene = list(DESK)
    run(mem, clock, shot(draw(DESK)), 6)
    assert len(fake.calls) == 1
    two = [o for o in DESK if o.label != "coffee mug"]
    fake.scene = two
    run(mem, clock, shot(draw(two)), 12)  # taken away in view (no cover): looked at once it settles
    assert len(fake.calls) == 2
    r = queries.scene(mem, "now", fresh=True)
    assert len(fake.calls) == 2, "answered from that look"
    assert "coffee mug" not in r["brief"]
    assert queries.what_changed(mem, fresh=True)["brief"] == "The coffee mug is gone."
    assert len(fake.calls) == 2
    with_box = [*two, BLACK_BOX]
    fake.scene = with_box
    run(mem, clock, shot(draw(with_box)), 4)  # settles again within 10 s of the last look: waits
    assert len(fake.calls) == 2
    run(mem, clock, shot(draw(with_box)), 8)
    assert len(fake.calls) == 3


def test_taken_away_while_something_moves_onto_its_spot(tmp_path) -> None:
    """Live: mouse, marker, snow globe; the snow globe taken and the mouse moved over the bottom of its old
    spot. A local model just lists marker and mouse: the snow globe is gone, not "hidden by the mouse"."""
    cfg = Config(data_dir=str(tmp_path), mirror_for_user=False, fresh_for=0, vision="local", local_model="m")
    store, fake, clock = Store(cfg.data_path), FakeVision(), Clock()
    mem = Memory(cfg, store, fake, clock=clock, background=False)
    mem.set_enabled(True)
    globe = Obj("snow globe", "snow globe", ("teal",), (486, 493, 651, 990), (200, 160, 40))
    mouse = Obj("black computer mouse", "mouse", ("black",), (238, 784, 429, 995), (25, 25, 25))
    marker = Obj("green marker", "marker", ("green",), (750, 842, 803, 995), (60, 170, 60))
    desk = [globe, mouse, marker]
    fake.scene = desk
    run(mem, clock, shot(draw(desk)), 6)
    run(mem, clock, COVERED, 4)
    after = [mouse.at((496, 775, 712, 990)), marker.at((302, 924, 518, 1000))]
    fake.scene = after
    run(mem, clock, shot(draw(after)), 20)
    r = queries.what_changed(mem, fresh=True)
    assert r["brief"].startswith("The snow globe is gone."), r["brief"]


def test_replay_marker_taken_globe_moved_onto_its_spot(tmp_path) -> None:
    """Live (11:05 PM): a lamp at the back counted as on the desk; a "pendant" seen once, missed next, was
    later called gone; and the marker, taken while the snow globe moved onto its spot, was "maybe hidden"."""
    cfg = Config(data_dir=str(tmp_path), mirror_for_user=False, fresh_for=0, vision="local", local_model="m")
    store, fake, clock = Store(cfg.data_path), FakeVision(), Clock()
    mem = Memory(cfg, store, fake, clock=clock, background=False)
    mem.set_enabled(True)
    lamp = Obj("black desk lamp", "lamp", ("black",), (0, 46, 200, 713), (20, 20, 20))
    mouse = Obj("black computer mouse", "mouse", ("black",), (249, 738, 410, 915), (25, 25, 25))
    marker = Obj("green marker", "marker", ("green",), (436, 811, 597, 957), (60, 170, 60))
    globe = Obj("snow globe", "snow globe", ("teal",), (607, 472, 803, 926), (200, 160, 40))
    pendant = Obj("hanging pendant", "pendant", ("silver",), (880, 700, 900, 760), (200, 200, 200))
    fake.workspace = [0, 615, 1000, 1000]
    first = [lamp, mouse, marker, globe, pendant]
    fake.scene = first
    run(mem, clock, shot(draw(first)), 6)
    assert "black desk lamp" not in {i["label"] for i in store.items(("visible",))}, "off the desk"
    second = [lamp, mouse.at((249, 760, 410, 935)), marker, globe]  # the pendant is missed this time
    fake.scene = [lamp, second[1], marker, globe]
    run(mem, clock, shot(draw(second + [pendant])), 14)
    assert len(fake.calls) == 2
    run(mem, clock, COVERED, 4)
    third = [lamp, mouse.at((627, 750, 828, 955)), globe.at((386, 471, 594, 928)), pendant]
    fake.scene = [o for o in third if o is not pendant] 
    run(mem, clock, shot(draw(third)), 20)
    r = queries.what_changed(mem, fresh=True)
    assert r["brief"] == "The green marker is gone.", r["brief"]
