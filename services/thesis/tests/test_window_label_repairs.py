"""services/thesis/evals/window_label_repairs.py classifies repair rounds."""
from services.thesis.evals import window_label_repairs as W


def test_window_only_rounds_are_counted_as_avoided():
    assert W.classify("scenario 'Mild fade' mentions 24, outside its own implied price range [92.13, 107.49]; "
                      "bull[0].text mentions 24, which is not grounded in analytics or intel; "
                      "what_changes_my_mind[1] mentions 8, which is not grounded in analytics or intel") == "window_only"


def test_any_other_error_keeps_the_repair():
    assert W.classify("bull[0].text mentions 24, which is not grounded in analytics or intel; "
                      "bull[1].text mentions 21, which is not grounded in analytics or intel") == "other"
    assert W.classify("call pays 1.4R to its first target (114.0), under the 1.5R minimum") == "other"


def test_summarize_counts_theses_and_repairs():
    lines = ["[thesis] oil: draft failed validation, repairing: x mentions 48, which is not grounded in analytics",
             "[thesis] oil: 2 call(s), mode=full, input=1 output=1",
             "[thesis] oil: 1 call(s), mode=full, input=1 output=1"]
    assert W.summarize(lines) == {"theses": 2, "repairs": 1, "window_only": 1, "other": 0}
