import pytest

from restic_tui import ui
from restic_tui.cli import SKIP, Jump, run_steps


def make(script):
    """Pasos que registran su ejecución y siguen un guion de respuestas por paso."""
    log, answers = [], {k: list(v) for k, v in script.items()}

    def step(name):
        def fn():
            log.append(name)
            a = answers[name].pop(0) if answers[name] else None
            if a == "back":
                raise ui.Back
            if isinstance(a, Jump):
                raise a
            return a
        return (name, fn)
    return log, step


def test_back_returns_to_previous_step():
    log, step = make({"a": [], "b": ["back"], "c": []})
    run_steps([step("a"), step("b"), step("c")])
    assert log == ["a", "b", "a", "b", "c"]


def test_back_skips_steps_that_asked_nothing():
    log, step = make({"a": [], "b": [SKIP, SKIP], "c": ["back"]})
    run_steps([step("a"), step("b"), step("c")])
    assert log == ["a", "b", "c", "b", "a", "b", "c"]


def test_back_on_first_step_propagates():
    _, step = make({"a": ["back"]})
    with pytest.raises(ui.Back):
        run_steps([step("a")])


def test_jump_from_review():
    log, step = make({"a": [], "b": [], "review": [Jump("a")]})
    run_steps([step("a"), step("b"), step("review")], start=2)
    assert log == ["review", "a", "b", "review"]


def test_back_right_after_jump_returns_to_review():
    log, step = make({"a": ["back"], "b": [], "review": [Jump("a"), None]})
    run_steps([step("a"), step("b"), step("review")], start=2)
    assert log == ["review", "a", "review"]


def test_jump_until_returns_to_review_without_next_section():
    log, step = make({"a": [], "b": [SKIP], "c": [], "review": [Jump("a", until="c"), None]})
    run_steps([step("a"), step("b"), step("c"), step("review")], start=3)
    assert log == ["review", "a", "b", "review"]


def test_no_color_without_tty_or_with_no_color(capsys, monkeypatch):
    ui.ok("hecho")  # capsys: stdout no es TTY
    assert "\x1b[" not in capsys.readouterr().out
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    assert ui.accent("x") == "x"
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert ui.accent("x") == "\x1b[36mx\x1b[0m"
