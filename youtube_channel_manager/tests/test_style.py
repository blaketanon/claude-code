from ycm.models import Scene, Script
from ycm.style import PacingStyle


def _script(narrations):
    return Script(
        title="t", description="d",
        scenes=[Scene(index=i, narration=n, visual_prompt="v")
                for i, n in enumerate(narrations)],
    )


def test_short_narration_is_held_for_minimum():
    style = PacingStyle(min_scene_seconds=8, words_per_minute=110)
    # A 3-word line reads in ~1.6s but must still be held the full minimum.
    assert style.scene_duration("just three words") == 8.0


def test_long_narration_is_capped():
    style = PacingStyle(max_scene_seconds=22, words_per_minute=110)
    long_text = " ".join(["word"] * 500)
    assert style.scene_duration(long_text) == 22.0


def test_slower_wpm_yields_longer_scenes():
    text = " ".join(["word"] * 40)
    slow = PacingStyle(words_per_minute=90, min_scene_seconds=0, max_scene_seconds=99)
    fast = PacingStyle(words_per_minute=160, min_scene_seconds=0, max_scene_seconds=99)
    assert slow.scene_duration(text) > fast.scene_duration(text)


def test_first_scene_has_no_incoming_transition():
    style = PacingStyle()
    assert style.transition(0).duration_seconds == 0.0
    assert style.transition(1).kind == "crossfade"
    assert style.transition(1).duration_seconds > 0


def test_apply_is_idempotent():
    style = PacingStyle()
    script = _script(["a b c d e", "x y z", "one two three four"])
    style.apply(script)
    first = [s.duration_seconds for s in script.scenes]
    style.apply(script)
    assert [s.duration_seconds for s in script.scenes] == first


def test_total_duration_includes_transitions():
    style = PacingStyle(min_scene_seconds=10, max_scene_seconds=10,
                        transition_seconds=2)
    script = _script(["a", "b", "c"])
    style.apply(script)
    # 3 scenes * 10s - 2 overlapping crossfades * 2s = 26
    assert script.total_duration_seconds == 26.0
