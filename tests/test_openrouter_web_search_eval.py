from __future__ import annotations

from io import StringIO


def test_paid_openrouter_web_eval_refuses_without_explicit_opt_in():
    from scripts import eval_openrouter_web_search as paid_eval

    called = False

    def brain_factory():
        nonlocal called
        called = True
        raise AssertionError("paid provider must not be loaded or called")

    stderr = StringIO()
    code = paid_eval.main(
        env={"OPENROUTER_API_KEY": "sk-or-test"},
        brain_factory=brain_factory,
        completion_helpers=(str, lambda _: 1, lambda _: ["https://example.com"]),
        stderr=stderr,
    )

    assert code == 2
    assert called is False
    assert paid_eval.OPT_IN_ENV in stderr.getvalue()
