from pathlib import Path

from experiments.v9_3 import perf_g
from scripts import stitch_a_implicit_standardized as stitcher
from scripts.analyze_scheduler_load_cross import _v5_plot_style


def test_standardized_plot_uses_formal_family_styles_and_generates_figure(tmp_path, monkeypatch):
    expected = {
        "ASAP": ("#0072B2", "o"),
        "ALAP": ("#D55E00", "s"),
        "ST": ("#009E73", "^"),
    }
    for family, (color, marker) in expected.items():
        styles = [_v5_plot_style(scheduler) for scheduler in perf_g.FORMAL_SCHEDULERS
                  if scheduler.startswith(family + "-")]
        assert {style["color"] for style in styles} == {color}
        assert {style["marker"] for style in styles} == {marker}
        assert {style["linestyle"] for style in styles} == {"-", "--", "-."}

    rows = [{
        "target_uc": "1/10",
        "target_ue": "9/10",
        "scheduler": scheduler,
        "wholepass_ratio": 0.5,
        "ci95_low": 0.4,
        "ci95_high": 0.6,
    } for scheduler in perf_g.FORMAL_SCHEDULERS]
    seen = []
    formal_style = stitcher._v5_plot_style

    def capture_style(scheduler):
        seen.append(scheduler)
        return formal_style(scheduler)

    monkeypatch.setattr(stitcher, "_v5_plot_style", capture_style)
    output = tmp_path / "standardized-uc.png"
    stitcher._plot(
        output, rows, axis="target_uc",
        fixed=[("9/10", "low"), ("3/4", "medium"), ("3/5", "high")],
        xlabel="U_C",
    )
    assert output.is_file()
    assert seen == list(perf_g.FORMAL_SCHEDULERS)
