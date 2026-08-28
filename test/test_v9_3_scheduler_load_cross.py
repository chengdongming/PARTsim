import csv
import json
from concurrent.futures import Future
from fractions import Fraction
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.v9_3 import perf_g
from experiments.v9_3 import scheduler_load_cross as experiment
from experiments.v9_3 import taskset_store
from experiments.v9_3.simulation_engine import should_retain_failure_trace
from experiments.v9_3.simulation_engine import (
    _render_taskset_yaml, derive_fixed_priority_ranks,
    normalize_scheduler_priority_policy,
)
from experiments.v9_3.simulation_result import SimulationStatus
from experiments.v9_3.performance_outcome import evaluate_outcome
from scripts.analyze_scheduler_load_cross import (
    analyze, dmr_cluster_bootstrap_ci, summarize_dmr, wilson_ci,
    _parse_dmr_ymin, _plot_style, decimal_axis_labels, plot_composite_dmr,
    plot_composite_scan, plot_dmr_scan, plot_scan,
)
import scripts.run_scheduler_load_cross as scheduler_runner


def _available_outcome(adjudicable_jobs=1, deadline_miss_jobs=0, wholepass=True):
    return {
        "outcome_status": "AVAILABLE",
        "adjudicable_jobs": adjudicable_jobs,
        "deadline_miss_jobs": deadline_miss_jobs,
        "wholepass": wholepass,
        "taskset_pass": wholepass,
    }


def _harvest_model_fields():
    return dict(experiment.HARVEST_MODEL_IDENTITY)


def test_exact_ue_eta_mapping_and_deduplicated_cells():
    assert experiment.eta_for_ue(Fraction(2, 5)) == Fraction(5, 2)
    assert experiment.eta_for_ue(Fraction(3, 10)) == Fraction(10, 3)
    assert experiment.parse_cells("1/2:2/5,0.5:0.4,1/2:1/5") == (
        (Fraction(1, 2), Fraction(2, 5)), (Fraction(1, 2), Fraction(1, 5)),
    )
    assert len(experiment.DEFAULT_CELLS) == 51
    assert len(set(experiment.DEFAULT_CELLS)) == 51


def test_decimal_axis_labels_preserve_exact_scan_ticks():
    profile = experiment.normalize_scan_profile()
    assert profile["axis_ticks"] == [
        "0", "1/10", "1/5", "3/10", "2/5", "1/2", "3/5",
        "7/10", "4/5", "9/10", "1",
    ]
    assert decimal_axis_labels(profile["axis_ticks"]) == [
        "0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6",
        "0.7", "0.8", "0.9", "1",
    ]
    custom = experiment.normalize_scan_profile(
        axis_display_min="0", axis_display_max="1", axis_tick_step="1/5",
    )
    assert custom["axis_ticks"] == ["0", "1/5", "2/5", "3/5", "4/5", "1"]
    assert decimal_axis_labels(custom["axis_ticks"]) == [
        "0", "0.2", "0.4", "0.6", "0.8", "1",
    ]


def test_all_scan_plot_paths_use_decimal_axis_labels(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt
    monkeypatch.setattr(plt, "close", lambda _figure: None)

    expected_labels = [
        "0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6",
        "0.7", "0.8", "0.9", "1",
    ]
    expected_ticks = [index / 10 for index in range(11)]

    class CaptureAxis:
        def __init__(self):
            self.xticks = None
            self.xticklabels = None
            self.errorbar_x = []

        def errorbar(self, x, *_args, **_kwargs):
            self.errorbar_x.extend(x)
            return SimpleNamespace(lines=[SimpleNamespace()])

        def set_xlabel(self, _label):
            pass

        def set_ylabel(self, _label):
            pass

        def set_title(self, _title):
            pass

        def set_xlim(self, *_args):
            pass

        def set_xticks(self, ticks):
            self.xticks = ticks

        def set_xticklabels(self, labels):
            self.xticklabels = labels

        def set_ylim(self, *_args):
            pass

        def grid(self, **_kwargs):
            pass

        def legend(self, **_kwargs):
            pass

    class CaptureFigure:
        def savefig(self, _path):
            pass

        def suptitle(self, _title):
            pass

        def tight_layout(self):
            pass

        def legend(self, *_args, **_kwargs):
            pass

        def subplots_adjust(self, **_kwargs):
            pass

    ticks = experiment.normalize_scan_profile()["axis_ticks"]
    scan_rows = [{
        "target_uc": "1/10", "scheduler": "ASAP-BLOCK",
        "wholepass_ratio": 0.5, "ci95_low": 0.4, "ci95_high": 0.6,
    }]
    dmr_rows = [{
        "target_uc": "1/10", "scheduler": "ASAP-BLOCK", "dmr": 0.5,
        "dmr_ci95_low": 0.4, "dmr_ci95_high": 0.6,
    }]
    slice_config = {
        "x_key": "target_uc", "fixed_key": "target_ue",
        "fixed_value": "7/10", "label": "low", "x_values": ["1/10"],
    }
    composite_rows = [{
        "scheduler": "ASAP-BLOCK", "target_uc": "1/10",
        "wholepass_ratio": 0.5, "ci95_low": 0.4, "ci95_high": 0.6,
        "dmr": 0.5, "dmr_ci95_low": 0.4, "dmr_ci95_high": 0.6,
    }]

    def capture_axes():
        axes = [CaptureAxis() for _ in range(3)]
        monkeypatch.setattr(plt, "subplots", lambda *args, **kwargs: (CaptureFigure(), axes))
        return axes

    axes = capture_axes()
    plot_scan(
        scan_rows, tmp_path, "scan.png", "target_uc", ["ASAP-BLOCK"], "U_C", "scan",
        axis_min="0", axis_max="1", axis_ticks=ticks,
    )
    assert [axis.xticklabels for axis in axes] == [expected_labels] * 3
    assert [axis.xticks for axis in axes] == [expected_ticks] * 3
    assert all(0 not in axis.errorbar_x for axis in axes)

    axes = capture_axes()
    plot_dmr_scan(
        dmr_rows, tmp_path, "dmr.png", "target_uc", ["ASAP-BLOCK"], "U_C", "dmr",
        axis_min="0", axis_max="1", axis_ticks=ticks,
    )
    assert [axis.xticklabels for axis in axes] == [expected_labels] * 3
    assert [axis.xticks for axis in axes] == [expected_ticks] * 3
    assert all(0 not in axis.errorbar_x for axis in axes)

    axes = [[CaptureAxis() for _ in range(3)]]
    monkeypatch.setattr(plt, "subplots", lambda *args, **kwargs: (CaptureFigure(), axes))
    plot_composite_scan(
        [(slice_config, composite_rows)], tmp_path, "composite.png", "target_uc",
        ["ASAP-BLOCK"], "U_C", "composite", axis_min="0", axis_max="1", axis_ticks=ticks,
    )
    assert [axis.xticklabels for axis in axes[0]] == [expected_labels] * 3
    assert [axis.xticks for axis in axes[0]] == [expected_ticks] * 3
    assert all(0 not in axis.errorbar_x for axis in axes[0])

    axes = [[CaptureAxis() for _ in range(3)]]
    monkeypatch.setattr(plt, "subplots", lambda *args, **kwargs: (CaptureFigure(), axes))
    plot_composite_dmr(
        [(slice_config, composite_rows)], tmp_path, "composite-dmr.png", "target_uc",
        ["ASAP-BLOCK"], "U_C", "composite dmr", 0.0,
        axis_min="0", axis_max="1", axis_ticks=ticks,
    )
    assert [axis.xticklabels for axis in axes[0]] == [expected_labels] * 3
    assert [axis.xticks for axis in axes[0]] == [expected_ticks] * 3
    assert all(0 not in axis.errorbar_x for axis in axes[0])


def test_frozen_main_figure_has_exactly_16_cells_and_two_slices():
    assert len(experiment.FORMAL_CELLS) == 16
    assert len(set(experiment.FORMAL_CELLS)) == 16
    assert (Fraction(3, 10), Fraction(7, 10)) in experiment.FORMAL_CELLS
    assert experiment.FORMAL_CELLS.count((Fraction(3, 10), Fraction(7, 10))) == 1
    slices = experiment.resolve_figure_slices(
        experiment.FORMAL_CELLS,
        fixed_ue=Fraction(7, 10), fixed_uc=Fraction(3, 10),
    )
    assert slices["uc_scan"]["fixed_value"] == "7/10"
    assert slices["ue_scan"]["fixed_value"] == "3/10"
    experiment.validate_frozen_main_figure(
        experiment.FORMAL_CELLS, experiment.ALL_SCHEDULERS, horizon_ms=60000,
    )


def test_default_and_explicit_nine_scheduler_lists():
    assert experiment.parse_schedulers(None) == experiment.DEFAULT_SCHEDULERS
    assert experiment.parse_schedulers(",".join(experiment.ALL_SCHEDULERS)) == experiment.ALL_SCHEDULERS


def _priority_projection_payload():
    return [
        {"task_id": "A", "priority_rank": 0, "C": 3, "D": 9, "T": 10,
         "P": "5/2", "workload": "hash", "arrival_offset": 0},
        {"task_id": "B", "priority_rank": 1, "C": 4, "D": 5, "T": 20,
         "P": "3", "workload": "bzip2", "arrival_offset": 0},
    ]


def test_priority_policy_normalization_and_fail_closed():
    assert normalize_scheduler_priority_policy(" rm ") == "RM"
    assert normalize_scheduler_priority_policy("dm") == "DM"
    for value in ("EDF", "", "garbage", None):
        with pytest.raises(Exception, match="priority_policy"):
            normalize_scheduler_priority_policy(value)


def test_rm_projection_is_byte_for_byte_backward_compatible():
    payload = _priority_projection_payload()
    legacy = _render_taskset_yaml(payload)
    explicit = _render_taskset_yaml(payload, priority_policy="RM")
    assert legacy == explicit
    assert "fixed_priority_rank" not in explicit


def test_dm_projection_uses_relative_deadline_and_does_not_mutate_payload():
    payload = _priority_projection_payload()
    original = [dict(row) for row in payload]
    assert derive_fixed_priority_ranks(payload, "DM") == {"A": 1, "B": 0}
    rendered = _render_taskset_yaml(payload, priority_policy="DM")
    assert "fixed_priority_rank=1" in rendered
    assert "fixed_priority_rank=0" in rendered
    assert payload == original
    assert [(row["task_id"], row["C"], row["D"], row["T"], row["P"], row["workload"])
            for row in payload] == [
                ("A", 3, 9, 10, "5/2", "hash"),
                ("B", 4, 5, 20, "3", "bzip2"),
            ]


def test_dm_priority_tie_breaks_are_deterministic_and_d_equals_t_matches_rm():
    payload = [
        {"task_id": "A", "priority_rank": 0, "C": 1, "D": 5, "T": 10},
        {"task_id": "B", "priority_rank": 1, "C": 1, "D": 5, "T": 20},
        {"task_id": "C", "priority_rank": 2, "C": 1, "D": 5, "T": 20},
    ]
    assert derive_fixed_priority_ranks(payload, "DM") == {"A": 0, "B": 1, "C": 2}
    implicit = [
        {"task_id": "A", "priority_rank": 0, "C": 1, "D": 10, "T": 10},
        {"task_id": "B", "priority_rank": 1, "C": 1, "D": 20, "T": 20},
    ]
    assert derive_fixed_priority_ranks(implicit, "DM") == {"A": 0, "B": 1}


def test_rm_and_dm_request_identity_is_paired_but_distinct():
    class Taskset:
        taskset_id = "same-taskset"
        semantic_hash = "same-hash"
        target_utilization = Fraction(2)
        actual_utilization = Fraction(2)
        processors = 4
        taskset_index = 0
        seed = 9

    rm = experiment.request_rows(
        [Taskset()], ((Fraction(1, 2), Fraction(1, 5)),),
        ("ASAP-BLOCK",), 2000, priority_policy="RM",
    )[0]
    dm = experiment.request_rows(
        [Taskset()], ((Fraction(1, 2), Fraction(1, 5)),),
        ("ASAP-BLOCK",), 2000, priority_policy="DM",
    )[0]
    assert rm["taskset_id"] == dm["taskset_id"]
    assert rm["taskset_hash"] == dm["taskset_hash"]
    assert rm["priority_policy"] == "RM"
    assert dm["priority_policy"] == "DM"
    assert rm["request_id"] != dm["request_id"]


def test_nine_scheduler_mapping_is_complete_and_unique():
    assert tuple(perf_g.FORMAL_SCHEDULERS) == experiment.ALL_SCHEDULERS
    assert len(perf_g.FORMAL_SCHEDULERS) == 9
    assert len(perf_g.SCHEDULER_CLI) == 9
    assert len(set(perf_g.SCHEDULER_CLI.values())) == 9
    assert all(
        perf_g.SCHEDULER_CLI[name].startswith("gpfp_")
        for name in perf_g.FORMAL_SCHEDULERS
    )


def test_system_templates_are_scoped_to_their_experiment_paths():
    ordinary = experiment._config(
        1, utilizations=[Fraction(1, 10)], count=1, processors=4, tasks=10,
        period_min=perf_g.PERIOD_MIN_MS, period_max=perf_g.PERIOD_MAX_MS,
        min_task_util=perf_g.MIN_TASK_UTILIZATION,
        max_task_util=perf_g.MAX_TASK_UTILIZATION,
        tolerance=perf_g.UTILIZATION_TOLERANCE,
    )
    perf_g_default = perf_g._task_generation_config(
        "FORMAL", [Fraction(1, 10)], 1,
    )
    assert ordinary["energy"]["service_curve"]["system_template"] == (
        experiment.ORDINARY_SYSTEM_TEMPLATE
    )
    assert perf_g_default["energy"]["service_curve"]["system_template"] == (
        perf_g.BASE_SYSTEM_TEMPLATE
    )
    assert perf_g.BASE_SYSTEM_TEMPLATE != experiment.ORDINARY_SYSTEM_TEMPLATE


def _synthetic_service_config(template_name):
    config = experiment._config(
        1, utilizations=[Fraction(1, 10)], count=1, processors=4, tasks=2,
        period_min=1, period_max=2, min_task_util=Fraction(1, 100),
        max_task_util=Fraction(1, 10), tolerance=Fraction(1, 100),
    )
    config["energy"]["service_curve"]["system_template"] = template_name
    config["energy"]["service_curve"]["horizon"] = 4
    return config


def _synthetic_template(source, *, solar_file, pv_area, pv_efficiency):
    rendered = source.replace(
        'solar_data_file: "data/processed/shenyang_solar_minute.csv"',
        f'solar_data_file: "{solar_file}"',
    )
    rendered = rendered.replace("pv_area_m2: 1.0", f"pv_area_m2: {pv_area}")
    rendered = rendered.replace(
        "pv_efficiency: 0.18", f"pv_efficiency: {pv_efficiency}"
    )
    return rendered


def test_synthetic_service_does_not_require_solar_csv(tmp_path, monkeypatch):
    source = (
        taskset_store.PROJECT_ROOT / experiment.ORDINARY_SYSTEM_TEMPLATE
    ).read_text(encoding="utf-8")
    template = tmp_path / "synthetic.yml"
    template.write_text(
        _synthetic_template(
            source, solar_file="missing.csv", pv_area="1", pv_efficiency="0.18",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(taskset_store, "PROJECT_ROOT", tmp_path)

    service = taskset_store.prepare_service_curve(
        _synthetic_service_config(template.name), tmp_path / "service"
    )
    material = json.loads(service.raw_spec)

    assert material["use_real_solar_data"] is False
    assert material["harvest_model"] == experiment.HARVEST_MODEL
    assert material["base_harvesting_rate"]
    assert "solar_data_sha256" not in material
    assert "effective_pv_area_m2" not in material
    assert "source_template_sha256" not in material


def test_synthetic_service_identity_ignores_solar_and_pv_fields(
    tmp_path, monkeypatch,
):
    source = (
        taskset_store.PROJECT_ROOT / experiment.ORDINARY_SYSTEM_TEMPLATE
    ).read_text(encoding="utf-8")
    template = tmp_path / "synthetic.yml"
    monkeypatch.setattr(taskset_store, "PROJECT_ROOT", tmp_path)
    config = _synthetic_service_config(template.name)

    template.write_text(
        _synthetic_template(
            source, solar_file="missing-a.csv", pv_area="1", pv_efficiency="0.18",
        ),
        encoding="utf-8",
    )
    first = taskset_store.prepare_service_curve(config, tmp_path / "first")

    template.write_text(
        _synthetic_template(
            source, solar_file="missing-b.csv", pv_area="7.5", pv_efficiency="0.91",
        ),
        encoding="utf-8",
    )
    second = taskset_store.prepare_service_curve(config, tmp_path / "second")

    assert first.values == second.values
    assert first.raw_spec == second.raw_spec
    assert first.identity == second.identity


def test_strict_wholepass_and_technical_outcome_contract():
    on_time = evaluate_outcome(
        [{"task_id": "0", "release": 0, "absolute_deadline": 9, "completion": 9}],
        ["0"], horizon=10, minimum_adjudicable_jobs=1, strict_wholepass=True,
    )
    assert on_time["wholepass"] is True
    miss = evaluate_outcome(
        [{"task_id": "0", "release": 0, "absolute_deadline": 9, "completion": 10}],
        ["0"], horizon=20, minimum_adjudicable_jobs=1, strict_wholepass=True,
    )
    assert miss["wholepass"] is False
    unfinished = evaluate_outcome(
        [{"task_id": "0", "release": 0, "absolute_deadline": 9, "completion": None}],
        ["0"], horizon=20, minimum_adjudicable_jobs=1, strict_wholepass=True,
    )
    assert unfinished["wholepass"] is False
    technical = evaluate_outcome(
        [], ["0"], horizon=20, minimum_adjudicable_jobs=1,
        simulation_completed=False, technical_error="timeout", strict_wholepass=True,
    )
    assert technical["wholepass"] is None
    assert technical["technical_failure"] is True


def test_wilson_ci_handles_zero_one_and_middle():
    low = wilson_ci(0, 100)
    high = wilson_ci(100, 100)
    middle = wilson_ci(5, 10)
    assert 0 <= low[0] <= low[1] <= 1
    assert 0 <= high[0] <= high[1] <= 1
    assert 0 <= middle[0] <= middle[1] <= 1
    assert low[0] == 0.0
    assert low[0] <= 0 <= low[1]
    assert high[0] <= 1 <= high[1]
    assert high[1] == 1.0
    assert middle[0] < 0.5 < middle[1]

    for k in range(101):
        p = k / 100
        interval = wilson_ci(k, 100)
        assert 0 <= interval[0] <= p <= interval[1] <= 1


def test_plot_scan_accepts_zero_wholepass_ratio(tmp_path):
    rows = [{
        "target_uc": "1/10",
        "target_ue": "7/10",
        "scheduler": "ASAP-BLOCK",
        "wholepass_ratio": 0.0,
        "ci95_low": wilson_ci(0, 100)[0],
        "ci95_high": wilson_ci(0, 100)[1],
    }]

    plot_scan(
        rows,
        tmp_path,
        "zero-wholepass.png",
        "target_uc",
        ["ASAP-BLOCK"],
        "U_C",
        "test",
    )

    assert (tmp_path / "zero-wholepass.png").is_file()


def test_plot_styles_are_consistent_across_panels_and_emphasize_asap(
    tmp_path, monkeypatch,
):
    import matplotlib.pyplot as plt

    class CaptureAxis:
        def __init__(self):
            self.errorbars = []
            self.ylabel = None
            self.ylims = []

        def errorbar(self, *args, **kwargs):
            self.errorbars.append(kwargs)

        def set_xlabel(self, _label):
            pass

        def set_ylabel(self, label):
            self.ylabel = label

        def set_title(self, _title):
            pass

        def set_ylim(self, *args):
            self.ylims.append(args)

        def grid(self, **_kwargs):
            pass

        def legend(self, **_kwargs):
            pass

    class CaptureFigure:
        def savefig(self, _path):
            pass

        def suptitle(self, _title):
            pass

        def tight_layout(self):
            pass

    axes = [CaptureAxis() for _ in range(3)]
    monkeypatch.setattr(plt, "subplots", lambda *args, **kwargs: (CaptureFigure(), axes))
    monkeypatch.setattr(plt, "close", lambda _figure: None)
    rows = []
    for panel_schedulers in experiment.PANEL_GROUPS.values():
        for scheduler in panel_schedulers:
            rows.append({
                "target_uc": "1/10", "scheduler": scheduler,
                "wholepass_ratio": 0.5, "ci95_low": 0.4, "ci95_high": 0.6,
            })

    plot_scan(rows, tmp_path, "styles.png", "target_uc",
              list(experiment.ALL_SCHEDULERS), "U_C", "RM — Whole-taskset pass ratio")

    assert all(len(axis.errorbars) == 3 for axis in axes)
    per_panel = [
        {
            call["label"].split("-", 1)[0]: {
                key: call[key]
                for key in ("color", "linestyle", "linewidth", "markersize", "zorder")
            }
            for call in axis.errorbars
        }
        for axis in axes
    ]
    assert per_panel[0] == per_panel[1] == per_panel[2]
    assert per_panel[0]["ASAP"]["color"] == "tab:blue"
    assert per_panel[0]["ASAP"]["linestyle"] == "-"
    assert per_panel[0]["ASAP"]["linewidth"] > per_panel[0]["ALAP"]["linewidth"]
    assert per_panel[0]["ASAP"]["markersize"] > per_panel[0]["ALAP"]["markersize"]
    assert per_panel[0]["ASAP"]["zorder"] > per_panel[0]["ALAP"]["zorder"]
    assert _plot_style("ALAP")["linestyle"] == "--"
    assert _plot_style("ST")["linestyle"] == ":"

    dmr_rows = [{
        "target_uc": "1/10", "scheduler": scheduler, "dmr": 0.5,
        "dmr_ci95_low": 0.4, "dmr_ci95_high": 0.6,
    } for scheduler in experiment.ALL_SCHEDULERS]
    plot_dmr_scan(
        dmr_rows, tmp_path, "styles-dmr.png", "target_uc",
        list(experiment.ALL_SCHEDULERS), "U_C",
        "DM — Job-level deadline-meeting ratio (DMR)",
    )
    assert axes[0].ylabel == "Deadline-meeting ratio (DMR)"
    assert all(axis.ylims[-1] == (0.0, 1.0) for axis in axes)


def test_dmr_ymin_is_validated_and_never_clips_ci(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt

    class CaptureAxis:
        def __init__(self):
            self.ylims = []

        def errorbar(self, *args, **kwargs):
            pass

        def set_xlabel(self, _label):
            pass

        def set_ylabel(self, _label):
            pass

        def set_title(self, _title):
            pass

        def set_ylim(self, *args):
            self.ylims.append(args)

        def grid(self, **_kwargs):
            pass

        def legend(self, **_kwargs):
            pass

    class CaptureFigure:
        def savefig(self, _path):
            pass

        def suptitle(self, _title):
            pass

        def tight_layout(self):
            pass

    axes = [CaptureAxis() for _ in range(3)]
    monkeypatch.setattr(plt, "subplots", lambda *args, **kwargs: (CaptureFigure(), axes))
    monkeypatch.setattr(plt, "close", lambda _figure: None)
    rows = [{
        "target_uc": "1/10", "scheduler": "ASAP-BLOCK", "dmr": 0.95,
        "dmr_ci95_low": 0.90, "dmr_ci95_high": 0.99,
    }]

    plot_dmr_scan(
        rows, tmp_path, "zoomed.png", "target_uc", ["ASAP-BLOCK"],
        "U_C", "RM — DMR (zoomed y-axis)", 0.89,
    )
    assert all(axis.ylims == [(0.89, 1.0)] for axis in axes)

    with pytest.raises(ValueError, match="minimum observed.*0.9"):
        plot_dmr_scan(
            rows, tmp_path, "clipped.png", "target_uc", ["ASAP-BLOCK"],
            "U_C", "DMR", 0.91,
        )
    for invalid in (-0.01, 1.0, float("nan")):
        with pytest.raises(ValueError, match=r"\[0, 1\)"):
            plot_dmr_scan(
                rows, tmp_path, "invalid.png", "target_uc", ["ASAP-BLOCK"],
                "U_C", "DMR", invalid,
            )


def test_dmr_ymin_cli_values_are_finite_and_in_half_open_unit_interval():
    assert _parse_dmr_ymin("0") == 0.0
    assert _parse_dmr_ymin("0.89") == 0.89
    for invalid in ("-0.01", "1", "nan", "inf"):
        with pytest.raises(Exception, match=r"\[0, 1\)"):
            _parse_dmr_ymin(invalid)


@pytest.mark.parametrize("adjudicable,misses,expected", [
    (100, 0, 1.0), (100, 1, 0.99), (100, 100, 0.0),
])
def test_dmr_uses_job_weighted_taskset_counts(adjudicable, misses, expected):
    row = {
        "outcome": _available_outcome(adjudicable, misses, misses == 0),
        "wholepass": misses == 0,
    }
    summary = summarize_dmr(
        [row], target_uc="1/10", target_ue="7/10",
        scheduler="ASAP-BLOCK", campaign_seed=710213,
    )
    assert summary["total_on_time_jobs"] == adjudicable - misses
    assert summary["dmr"] == expected
    assert summary["dmr_ci95_low"] is None
    assert summary["dmr_ci95_high"] is None


def test_dmr_is_job_weighted_not_mean_of_taskset_ratios():
    rows = [
        {"outcome": _available_outcome(100, 0), "wholepass": True},
        {"outcome": _available_outcome(10, 5, False), "wholepass": False},
    ]
    summary = summarize_dmr(
        rows, target_uc="3/10", target_ue="7/10",
        scheduler="ASAP-BLOCK", campaign_seed=710213,
    )
    assert summary["total_adjudicable_jobs"] == 110
    assert summary["total_deadline_miss_jobs"] == 5
    assert summary["total_on_time_jobs"] == 105
    assert summary["dmr"] == 105 / 110
    assert summary["dmr"] != 0.75


@pytest.mark.parametrize("rows", [
    [{"outcome": _available_outcome(100, 1), "wholepass": True}],
    [{"outcome": _available_outcome(0, 0), "wholepass": True}],
    [{"outcome": _available_outcome(10, 11, False), "wholepass": False}],
    [{"outcome": {"outcome_status": "TECHNICAL_FAILURE"}, "wholepass": None}],
])
def test_dmr_cross_checks_fail_closed(rows):
    with pytest.raises(ValueError):
        summarize_dmr(
            rows, target_uc="1/10", target_ue="7/10",
            scheduler="ASAP-BLOCK", campaign_seed=710213,
        )


def test_dmr_cluster_bootstrap_is_reproducible_and_bounded():
    counts = [(100, 0), (10, 5), (30, 3)]
    first = dmr_cluster_bootstrap_ci(counts, seed=1234, replicates=200)
    second = dmr_cluster_bootstrap_ci(counts, seed=1234, replicates=200)
    assert first == second
    assert 0 <= first[0] <= first[1] <= 1


def test_dmr_cluster_bootstrap_is_unavailable_for_one_taskset():
    assert dmr_cluster_bootstrap_ci([(100, 1)], seed=1234) == (None, None)


def test_requests_pair_two_energy_cells_and_five_schedulers():
    class Taskset:
        taskset_id = "t"
        semantic_hash = "h"
        target_utilization = Fraction(2)
        actual_utilization = Fraction(2)
        processors = 4
        taskset_index = 0
        seed = 9
    rows = experiment.request_rows(
        [Taskset()], ((Fraction(1, 2), Fraction(1, 5)), (Fraction(1, 2), Fraction(2, 5))),
        experiment.DEFAULT_SCHEDULERS, 2000,
    )
    assert len(rows) == 10
    assert len({row["request_id"] for row in rows}) == 10
    assert len({row["taskset_id"] for row in rows}) == 1
    assert {row["target_ue"] for row in rows} == {"1/5", "2/5"}


def test_frozen_grid_plans_nine_paired_requests_per_cell_taskset():
    class Taskset:
        processors = 4
        actual_utilization = Fraction(1)
        seed = 123

        def __init__(self, uc, index):
            self.target_utilization = uc * self.processors
            self.taskset_index = index
            self.taskset_id = f"taskset-{uc}-{index}"
            self.semantic_hash = f"hash-{uc}-{index}"

    tasksets = [Taskset(uc, 0) for uc in experiment.FORMAL_UC_SCAN]
    rows = experiment.request_rows(
        tasksets, experiment.FORMAL_CELLS, experiment.ALL_SCHEDULERS, 60000,
    )
    assert len(rows) == 16 * 9
    groups = {}
    for row in rows:
        groups.setdefault((row["target_uc"], row["target_ue"], row["generation_index"]), []).append(row)
    assert len(groups) == 16
    assert all(len(group) == 9 for group in groups.values())
    assert all({row["scheduler"] for row in group} == set(experiment.ALL_SCHEDULERS)
               for group in groups.values())
    for uc in experiment.FORMAL_UC_SCAN:
        ids = {
            row["taskset_id"] for row in rows
            if Fraction(row["target_uc"]) == uc
        }
        assert len(ids) == 1


def test_service_only_energy_material_preserves_canonical_power():
    class Taskset:
        processors = 4
        task_count = 2
        task_payload = ({"C": 1, "T": 10, "P": "2"}, {"C": 1, "T": 10, "P": "4"})
    material = experiment.energy_material(Taskset(), Fraction(2, 5), (Fraction(1),) * 10, kappa=Fraction(10), normalization_horizon=10)
    assert material["eta"] == "5/2"
    assert material["target_supply_mean_j_per_tick"] == "3/2"
    assert material["solar_scale"] == "3/2"
    assert material["energy_control"] == "SERVICE_ONLY_SCALING"
    assert {
        key: material[key] for key in experiment.HARVEST_MODEL_IDENTITY
    } == experiment.HARVEST_MODEL_IDENTITY


def test_analyzer_writes_both_figure_csvs(tmp_path):
    config = {"cells": [["1/2", "2/5"]], "samples_per_cell": 1,
              "schedulers": ["ASAP-BLOCK"], "processors": 4,
              "util_tolerance_total": "1/100", "use_real_solar_data": False,
              **experiment.HARVEST_MODEL_IDENTITY}
    taskset = {"taskset_id": "t", "taskset_hash": "h", "canonical_task_power": True,
               "target_uc": "1/2", "actual_uc": "1/2"}
    request = {"request_id": "r", "taskset_id": "t", "taskset_hash": "h",
               "target_uc": "1/2", "target_ue": "2/5", "generation_index": 0,
               "scheduler": "ASAP-BLOCK", **_harvest_model_fields()}
    energy = {"target_ue": "2/5", "eta": "5/2", "P_dem_j_per_tick": "3/5",
              "target_supply_mean_j_per_tick": "3/2", "raw_reference_mean_j_per_tick": "1",
              "solar_scale": "3/2",
              "runtime_configured_average_supply_j_per_tick": "3/2",
              "actual_ue": "2/5", "actual_ue_abs_error": "0",
              "actual_ue_rel_error": "0", "actual_ue_minus_target_ue": "0",
              **_harvest_model_fields()}
    result = {**request, "energy": energy, "outcome": _available_outcome(),
              "schedulable": True, "deadline_miss": False,
              "simulation_status": "SIM_PASS_OBSERVED", "technical_error": None,
              "wholepass": True, "taskset_pass": True}
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "tasksets.jsonl").write_text(json.dumps(taskset) + "\n", encoding="utf-8")
    (tmp_path / "requests.jsonl").write_text(json.dumps(request) + "\n", encoding="utf-8")
    (tmp_path / "results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")
    assert analyze(tmp_path)["complete"]
    assert (tmp_path / "figure_scheduler_uc.csv").is_file()
    assert (tmp_path / "figure_scheduler_ue.csv").is_file()
    assert (tmp_path / "figure_scheduler_uc.png").is_file()
    assert (tmp_path / "figure_scheduler_ue.png").is_file()
    assert (tmp_path / "summary_dmr.csv").is_file()
    assert (tmp_path / "figure_scheduler_uc_dmr.csv").is_file()
    assert (tmp_path / "figure_scheduler_ue_dmr.csv").is_file()
    assert (tmp_path / "figure_scheduler_uc_dmr.png").is_file()
    assert (tmp_path / "figure_scheduler_ue_dmr.png").is_file()


def test_analyzer_rejects_empty_campaign_explicitly(tmp_path):
    config = {
        "cells": [["1/2", "2/5"]], "samples_per_cell": 1,
        "schedulers": ["ASAP-BLOCK"], "processors": 4,
        "util_tolerance_total": "1/100", "use_real_solar_data": False,
        **experiment.HARVEST_MODEL_IDENTITY,
    }
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    for name in ("tasksets.jsonl", "requests.jsonl", "results.jsonl"):
        (tmp_path / name).write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="requests are empty"):
        analyze(tmp_path)


def test_analyzer_keeps_csv_and_png_scans_on_the_same_fixed_axis(tmp_path, monkeypatch):
    config = {"cells": [
        ["1/10", "2/5"], ["1/5", "2/5"],
        ["1/2", "1/5"], ["1/2", "3/10"],
        ["1/2", "2/5"], ["1/2", "1/2"],
    ], "samples_per_cell": 1, "schedulers": ["ASAP-BLOCK"],
       "processors": 4, "util_tolerance_total": "1/100",
       "use_real_solar_data": False, **experiment.HARVEST_MODEL_IDENTITY}
    tasksets = [
        {"taskset_id": "t-1", "taskset_hash": "h-1",
         "canonical_task_power": True, "target_uc": "1/10", "actual_uc": "1/10"},
        {"taskset_id": "t-2", "taskset_hash": "h-2",
         "canonical_task_power": True, "target_uc": "1/5", "actual_uc": "1/5"},
        {"taskset_id": "t-5", "taskset_hash": "h-5",
         "canonical_task_power": True, "target_uc": "1/2", "actual_uc": "1/2"},
    ]
    cells = [("t-1", "1/10", "2/5"), ("t-2", "1/5", "2/5"),
             ("t-5", "1/2", "1/5"), ("t-5", "1/2", "3/10"),
             ("t-5", "1/2", "2/5"), ("t-5", "1/2", "1/2")]
    requests = []
    results = []
    for index, (taskset_id, target_uc, target_ue) in enumerate(cells):
        request = {
            "request_id": f"r-{index}", "taskset_id": taskset_id,
            "taskset_hash": next(row["taskset_hash"] for row in tasksets if row["taskset_id"] == taskset_id),
            "target_uc": target_uc, "target_ue": target_ue,
            "generation_index": 0, "scheduler": "ASAP-BLOCK",
            **_harvest_model_fields(),
        }
        eta = str(1 / Fraction(target_ue))
        target_supply = str(1 / Fraction(target_ue))
        energy = {
            "target_ue": target_ue, "eta": eta,
            "P_dem_j_per_tick": "1", "E_burst_j": "10",
            "battery_capacity_j": "100", "initial_energy_j": "50",
            "target_supply_mean_j_per_tick": target_supply,
            "raw_reference_mean_j_per_tick": "1", "solar_scale": target_supply,
            "runtime_configured_average_supply_j_per_tick": target_supply,
            "actual_ue": target_ue, "actual_ue_abs_error": "0",
            "actual_ue_rel_error": "0", "actual_ue_minus_target_ue": "0",
            "harvest_trace_id": "fixture-trace",
            **_harvest_model_fields(),
        }
        requests.append(request)
        results.append({
            **request, "energy": energy, "schedulable": True,
            "deadline_miss": False, "simulation_status": "SIM_PASS_OBSERVED",
            "technical_error": None, "outcome": _available_outcome(),
            "wholepass": True, "taskset_pass": True,
        })
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "tasksets.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in tasksets), encoding="utf-8"
    )
    (tmp_path / "requests.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in requests), encoding="utf-8"
    )
    (tmp_path / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in results), encoding="utf-8"
    )
    plotted = {}
    monkeypatch.setattr(
        "scripts.analyze_scheduler_load_cross.plot_scan",
        lambda rows, output, filename, xkey, schedulers, xlabel, title:
            plotted.setdefault(filename, list(rows)),
    )
    monkeypatch.setattr(
        "scripts.analyze_scheduler_load_cross.plot_dmr_scan",
        lambda rows, output, filename, xkey, schedulers, xlabel, title, ymin=0.0:
            plotted.setdefault("dmr:" + filename, list(rows)),
    )
    assert analyze(tmp_path)["complete"]

    uc_rows = list(csv.DictReader((tmp_path / "figure_scheduler_uc.csv").open()))
    ue_rows = list(csv.DictReader((tmp_path / "figure_scheduler_ue.csv").open()))
    assert {row["target_ue"] for row in uc_rows} == {"2/5"}
    assert {row["target_uc"] for row in ue_rows} == {"1/2"}
    assert [row["target_uc"] for row in uc_rows] == ["1/10", "1/5", "1/2"]
    assert [row["target_ue"] for row in ue_rows] == ["1/5", "3/10", "2/5", "1/2"]
    assert [row["target_uc"] for row in plotted["figure_scheduler_uc.png"]] == [
        "1/10", "1/5", "1/2",
    ]
    assert [row["target_ue"] for row in plotted["figure_scheduler_ue.png"]] == [
        "1/5", "3/10", "2/5", "1/2",
    ]
    assert len(uc_rows) == 3
    assert len(ue_rows) == 4
    dmr_uc_rows = list(csv.DictReader((tmp_path / "figure_scheduler_uc_dmr.csv").open()))
    dmr_ue_rows = list(csv.DictReader((tmp_path / "figure_scheduler_ue_dmr.csv").open()))
    assert [row["target_uc"] for row in dmr_uc_rows] == ["1/10", "1/5", "1/2"]
    assert [row["target_ue"] for row in dmr_ue_rows] == ["1/5", "3/10", "2/5", "1/2"]
    assert [row["target_uc"] for row in plotted["dmr:figure_scheduler_uc_dmr.png"]] == [
        "1/10", "1/5", "1/2",
    ]
    assert [row["target_ue"] for row in plotted["dmr:figure_scheduler_ue_dmr.png"]] == [
        "1/5", "3/10", "2/5", "1/2",
    ]


def _write_configured_analyzer_fixture(tmp_path, priority_policy="RM"):
    config = {
        "cells": [["1/10", "3/7"], ["1/5", "3/7"],
                  ["2/5", "3/7"], ["2/5", "1/2"], ["2/5", "4/5"]],
        "samples_per_cell": 1, "schedulers": ["ASAP-BLOCK"],
        "priority_policy": priority_policy,
        "processors": 4, "util_tolerance_total": "1/100",
        "use_real_solar_data": False, **experiment.HARVEST_MODEL_IDENTITY,
        "figure_slices": {
            "uc_scan": {
                "x_key": "target_uc", "fixed_key": "target_ue",
                "fixed_value": "3/7",
            },
            "ue_scan": {
                "x_key": "target_ue", "fixed_key": "target_uc",
                "fixed_value": "2/5",
            },
        },
    }
    tasksets = [
        {"taskset_id": "t-1", "taskset_hash": "h-1",
         "canonical_task_power": True, "target_uc": "1/10", "actual_uc": "1/10"},
        {"taskset_id": "t-2", "taskset_hash": "h-2",
         "canonical_task_power": True, "target_uc": "1/5", "actual_uc": "1/5"},
        {"taskset_id": "t-4", "taskset_hash": "h-4", "canonical_task_power": True,
         "target_uc": "2/5", "actual_uc": "2/5"},
    ]
    cells = [("t-1", "1/10", "3/7"), ("t-2", "1/5", "3/7"),
             ("t-4", "2/5", "3/7"), ("t-4", "2/5", "1/2"),
             ("t-4", "2/5", "4/5")]
    requests = []
    results = []
    for index, (taskset_id, target_uc, target_ue) in enumerate(cells):
        taskset_hash = next(row["taskset_hash"] for row in tasksets if row["taskset_id"] == taskset_id)
        request = {
            "request_id": f"configured-r-{index}", "taskset_id": taskset_id,
            "taskset_hash": taskset_hash, "target_uc": target_uc,
            "target_ue": target_ue, "generation_index": 0,
            "scheduler": "ASAP-BLOCK", "priority_policy": priority_policy,
            **_harvest_model_fields(),
        }
        results.append({
            **request,
            "energy": {
                "target_ue": target_ue, "eta": str(1 / Fraction(target_ue)),
                "P_dem_j_per_tick": "1", "E_burst_j": "10",
                "battery_capacity_j": "100", "initial_energy_j": "50",
                "target_supply_mean_j_per_tick": str(1 / Fraction(target_ue)),
                "raw_reference_mean_j_per_tick": "1",
                "solar_scale": str(1 / Fraction(target_ue)),
                "runtime_configured_average_supply_j_per_tick": str(1 / Fraction(target_ue)),
                "actual_ue": target_ue, "actual_ue_abs_error": "0",
                "actual_ue_rel_error": "0", "actual_ue_minus_target_ue": "0",
                "harvest_trace_id": "fixture-trace",
                **_harvest_model_fields(),
            },
            "outcome": _available_outcome(),
            "schedulable": True, "deadline_miss": False,
            "simulation_status": "SIM_PASS_OBSERVED", "technical_error": None,
            "wholepass": True, "taskset_pass": True,
        })
        requests.append(request)
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    for name, rows in (("tasksets.jsonl", tasksets), ("requests.jsonl", requests),
                       ("results.jsonl", results)):
        (tmp_path / name).write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )


@pytest.mark.parametrize("priority_policy", ["RM", "DM"])
def test_analyzer_uses_configured_custom_slices_and_dynamic_titles(
    tmp_path, monkeypatch, priority_policy,
):
    _write_configured_analyzer_fixture(tmp_path, priority_policy)
    plotted = {}
    monkeypatch.setattr(
        "scripts.analyze_scheduler_load_cross.plot_scan",
        lambda rows, output, filename, xkey, schedulers, xlabel, title:
            plotted.setdefault(filename, (list(rows), title)),
    )
    monkeypatch.setattr(
        "scripts.analyze_scheduler_load_cross.plot_dmr_scan",
        lambda rows, output, filename, xkey, schedulers, xlabel, title, ymin=0.0:
            plotted.setdefault("dmr:" + filename, (list(rows), title, ymin)),
    )
    assert analyze(tmp_path, uc_dmr_ymin=0.11, ue_dmr_ymin=0.22)["complete"]
    uc_rows = list(csv.DictReader((tmp_path / "figure_scheduler_uc.csv").open()))
    ue_rows = list(csv.DictReader((tmp_path / "figure_scheduler_ue.csv").open()))
    assert {row["target_ue"] for row in uc_rows} == {"3/7"}
    assert {row["target_uc"] for row in ue_rows} == {"2/5"}
    assert [row["target_uc"] for row in uc_rows] == ["1/10", "1/5", "2/5"]
    assert [row["target_ue"] for row in ue_rows] == ["3/7", "1/2", "4/5"]
    assert "U_E=3/7" in plotted["figure_scheduler_uc.png"][1]
    assert "U_C=2/5" in plotted["figure_scheduler_ue.png"][1]
    assert priority_policy in plotted["figure_scheduler_uc.png"][1]
    assert priority_policy in plotted["figure_scheduler_ue.png"][1]
    assert "Whole-taskset pass ratio" in plotted["figure_scheduler_uc.png"][1]
    assert "Whole-taskset pass ratio" in plotted["figure_scheduler_ue.png"][1]
    assert "Schedulability ratio" not in plotted["figure_scheduler_uc.png"][1]
    assert "Schedulability ratio" not in plotted["figure_scheduler_ue.png"][1]
    assert "Job-level deadline-meeting ratio (DMR)" in plotted[
        "dmr:figure_scheduler_uc_dmr.png"
    ][1]
    assert "Job-level deadline-meeting ratio (DMR)" in plotted[
        "dmr:figure_scheduler_ue_dmr.png"
    ][1]
    assert priority_policy in plotted["dmr:figure_scheduler_uc_dmr.png"][1]
    assert priority_policy in plotted["dmr:figure_scheduler_ue_dmr.png"][1]
    assert plotted["dmr:figure_scheduler_uc_dmr.png"][2] == 0.11
    assert plotted["dmr:figure_scheduler_ue_dmr.png"][2] == 0.22
    assert "zoomed y-axis" in plotted["dmr:figure_scheduler_uc_dmr.png"][1]
    assert "zoomed y-axis" in plotted["dmr:figure_scheduler_ue_dmr.png"][1]
    assert [row["target_uc"] for row in plotted["figure_scheduler_uc.png"][0]] == [
        "1/10", "1/5", "2/5",
    ]
    assert [row["target_ue"] for row in plotted["figure_scheduler_ue.png"][0]] == [
        "3/7", "1/2", "4/5",
    ]


def test_analyzer_rejects_configured_slice_absent_from_cells(tmp_path):
    _write_configured_analyzer_fixture(tmp_path)
    config = json.loads((tmp_path / "run_config.json").read_text())
    config["figure_slices"]["uc_scan"]["fixed_value"] = "7/10"
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(SystemExit, match="absent from cells"):
        analyze(tmp_path)


class _FakeTaskset:
    taskset_id = "taskset-0"
    semantic_hash = "hash-0"
    target_utilization = Fraction(2, 5)
    actual_utilization = Fraction(2, 5)
    processors = 4
    task_count = 1
    taskset_index = 0
    seed = 710213
    task_payload = ({"task_id": "task-0", "C": 1, "D": 2, "T": 4, "P": "1"},)

    def generated_row(self):
        return {
            "taskset_id": self.taskset_id,
            "taskset_hash": self.semantic_hash,
            "target_utilization": "2/5",
            "actual_utilization": "2/5",
        }


def _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation):
    taskset = _FakeTaskset()
    service = SimpleNamespace(system_path=tmp_path / "system.yml")
    service.system_path.write_text("system", encoding="utf-8")
    request = {
        "request_id": "scheduler-load-cross-test-request",
        "taskset_id": taskset.taskset_id,
        "taskset_hash": taskset.semantic_hash,
        "target_uc": "1/10", "actual_uc": "1/10",
        "target_ue": "2/5", "eta": "5/2",
        "generation_index": 0, "seed": taskset.seed,
        "scheduler": "ASAP-BLOCK", "scheduler_cli": "gpfp_asap_block",
        "horizon_ms": 20,
        **_harvest_model_fields(),
    }
    monkeypatch.setattr(
        scheduler_runner.experiment, "materialize_tasksets",
        lambda *args, **kwargs: ([taskset], service),
    )
    monkeypatch.setattr(
        scheduler_runner.experiment, "request_rows",
        lambda *args, **kwargs: [dict(request)],
    )
    monkeypatch.setattr(
        scheduler_runner.experiment, "construct_paired_harvest_trace",
        lambda *args, **kwargs: (Fraction(1),) * 10,
    )
    monkeypatch.setattr(
        scheduler_runner.experiment, "energy_material",
        lambda *args, **kwargs: {
            "target_ue": "2/5", "eta": "5/2",
            "initial_energy_j": "1", "battery_capacity_j": "2",
            "solar_scale": "1", "P_dem_j_per_tick": "1",
            "raw_reference_mean_j_per_tick": "1",
            "runtime_configured_average_supply_j_per_tick": "1",
            "actual_ue": "2/5", "actual_ue_abs_error": "0",
            "actual_ue_rel_error": "0", **_harvest_model_fields(),
        },
    )
    monkeypatch.setattr(
        scheduler_runner, "evaluate_outcome",
        lambda *args, **kwargs: {
            "outcome_status": "AVAILABLE", "taskset_pass": True,
        },
    )
    monkeypatch.setattr(scheduler_runner, "run_paired_simulation", run_simulation)

    class _InlineExecutor:
        def __init__(self, *args, initializer=None, initargs=(), **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, function, job):
            future = Future()
            try:
                future.set_result(function(job))
            except Exception as exc:
                future.set_exception(exc)
            return future

    monkeypatch.setattr(scheduler_runner, "ProcessPoolExecutor", _InlineExecutor)


def _scheduler_runner_args(
    output, resume=False, keep_traces=False, parse_concurrency=1,
    cells="0.1:0.4", fixed_ue=None, fixed_uc=None,
):
    args = [
        "--output", str(output), "--seed", "710213", "--workers", "1",
        "--samples-per-cell", "1", "--cells", cells,
        "--schedulers", "ASAP-BLOCK", "--simulation-horizon", "20",
        "--timeout-seconds", "5", "--simulator", str(output / "rtsim"),
        "--parse-concurrency", str(parse_concurrency),
        *( ["--keep-traces"] if keep_traces else [] ),
        *( ["--resume"] if resume else [] ),
    ]
    if fixed_ue is not None:
        args.extend(["--uc-figure-fixed-ue", fixed_ue])
    if fixed_uc is not None:
        args.extend(["--ue-figure-fixed-uc", fixed_uc])
    return args


def test_normal_horizon_pass_never_is_a_failure_trace():
    result = SimpleNamespace(
        status=SimulationStatus.PASS_OBSERVED,
        simulation_completed=True,
        completion_reason="reached_horizon",
        release_e0_valid=False,
    )
    assert not should_retain_failure_trace(
        result, {"trace_on_failure": True}
    )


def test_failure_trace_retention_remains_opt_in_for_diagnostics():
    result = SimpleNamespace(
        status=SimulationStatus.DEADLINE_MISS,
        simulation_completed=True,
        completion_reason="reached_horizon",
        release_e0_valid=True,
    )
    assert not should_retain_failure_trace(
        result, {"trace_on_failure": False}
    )
    assert should_retain_failure_trace(
        result, {"trace_on_failure": True}
    )


def test_scheduler_runner_disables_trace_retention_by_default(tmp_path, monkeypatch):
    configs = []

    def run_simulation(**kwargs):
        configs.append(kwargs["simulation_config"])
        result = SimpleNamespace(
            status=SimulationStatus.PASS_OBSERVED, reason="observed",
            jobs=(), metrics={}, simulation_completed=True,
        )
        return SimpleNamespace(
            result=result, runtime_seconds=0.1, stdout_tail="",
            stderr_tail="", retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    assert scheduler_runner.main(_scheduler_runner_args(tmp_path / "default")) == 0
    assert configs[0]["trace_on_failure"] is False
    assert configs[0]["retain_trace"] is False

    configs.clear()
    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    assert scheduler_runner.main(
        _scheduler_runner_args(tmp_path / "debug", keep_traces=True)
    ) == 0
    assert configs[0]["trace_on_failure"] is True
    assert configs[0]["retain_trace"] is True


def test_runner_uses_v3_runtime_ue_report_name(tmp_path, monkeypatch):
    def run_simulation(**kwargs):
        result = SimpleNamespace(
            status=SimulationStatus.PASS_OBSERVED, reason="observed",
            jobs=(), metrics={}, simulation_completed=True,
        )
        return SimpleNamespace(
            result=result, runtime_seconds=0.1, stdout_tail="",
            stderr_tail="", retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    output = tmp_path / "v3-runtime-ue"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 0
    config = json.loads((output / "run_config.json").read_text())
    report = json.loads((output / "invariant_report.json").read_text())
    assert config["experiment"] == "scheduler-load-cross-v3"
    assert report["runtime_config_ue_exact"] is True
    assert "actual_" + "ue_exact" not in report


def test_parse_concurrency_is_configured_and_resume_bound(tmp_path, monkeypatch):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    output = tmp_path / "parse-concurrency"
    assert scheduler_runner.main(
        _scheduler_runner_args(output, parse_concurrency=2)
    ) == 2
    config = json.loads((output / "run_config.json").read_text())
    assert config["parse_concurrency"] == 2
    with pytest.raises(SystemExit, match="resume configuration mismatch"):
        scheduler_runner.main(
            _scheduler_runner_args(output, resume=True, parse_concurrency=4)
        )


def test_runner_persists_explicit_figure_slices_and_infers_unique_defaults(tmp_path, monkeypatch):
    def run_simulation(**kwargs):
        return SimpleNamespace(
            result=SimpleNamespace(
                status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
                metrics={}, simulation_completed=True,
            ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
            retained_trace_path=None,
        )

    cells = "0.1:3/7,0.2:3/7,0.4:2/5,0.4:1/2"
    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    explicit = tmp_path / "explicit-slices"
    assert scheduler_runner.main(_scheduler_runner_args(
        explicit, cells=cells, fixed_ue="3/7", fixed_uc="2/5",
    )) == 0
    config = json.loads((explicit / "run_config.json").read_text())
    assert config["figure_slices"] == {
        "uc_scan": {
            "x_key": "target_uc", "fixed_key": "target_ue",
            "fixed_value": "3/7",
        },
        "ue_scan": {
            "x_key": "target_ue", "fixed_key": "target_uc",
            "fixed_value": "2/5",
        },
    }

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    inferred = tmp_path / "inferred-slices"
    assert scheduler_runner.main(_scheduler_runner_args(
        inferred, cells=cells,
    )) == 0
    inferred_config = json.loads((inferred / "run_config.json").read_text())
    assert inferred_config["figure_slices"] == config["figure_slices"]


@pytest.mark.parametrize("option,value", [
    ("fixed_ue", "4/5"), ("fixed_uc", "3/5"),
])
def test_runner_rejects_figure_slice_absent_from_cells(tmp_path, monkeypatch, option, value):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    kwargs = {option: value}
    with pytest.raises(SystemExit, match="absent from cells"):
        scheduler_runner.main(_scheduler_runner_args(
            tmp_path / option, cells="0.1:3/7,0.2:3/7,0.4:2/5,0.4:1/2",
            **kwargs,
        ))


@pytest.mark.parametrize("value", ["0", "6/5", "not-a-fraction"])
def test_runner_rejects_invalid_figure_slice_fraction(tmp_path, monkeypatch, value):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    with pytest.raises(SystemExit):
        scheduler_runner.main(_scheduler_runner_args(
            tmp_path / value.replace("/", "-"), fixed_ue=value,
        ))


def test_runner_binds_figure_slices_to_resume_configuration(tmp_path, monkeypatch):
    def run_simulation(**kwargs):
        return SimpleNamespace(
            result=SimpleNamespace(
                status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
                metrics={}, simulation_completed=True,
            ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
            retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    cells = "0.1:3/7,0.2:3/7,0.3:4/7,0.4:2/5,0.4:1/2"
    output = tmp_path / "resume-slices"
    assert scheduler_runner.main(_scheduler_runner_args(
        output, cells=cells, fixed_ue="3/7", fixed_uc="2/5",
    )) == 0
    with pytest.raises(SystemExit, match="resume configuration mismatch"):
        scheduler_runner.main(_scheduler_runner_args(
            output, resume=True, cells=cells, fixed_ue="4/7", fixed_uc="2/5",
        ))


def test_runner_rejects_ambiguous_automatic_figure_slice_inference(tmp_path, monkeypatch):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    with pytest.raises(SystemExit, match="ambiguous"):
        scheduler_runner.main(_scheduler_runner_args(
            tmp_path / "ambiguous",
            cells="0.1:2/5,0.2:2/5,0.1:3/5,0.2:3/5",
        ))


def test_completion_order_persists_early_and_canonicalizes_final_results(tmp_path, monkeypatch):
    requests = [
        {
            "request_id": "request-1", "taskset_id": "taskset-0",
            "taskset_hash": "hash-0", "target_uc": "1/10",
            "actual_uc": "1/10", "target_ue": "2/5", "eta": "5/2",
            "generation_index": 0, "seed": 710213, "scheduler": "ASAP-BLOCK",
            "scheduler_cli": "gpfp_asap_block", "horizon_ms": 20,
            **_harvest_model_fields(),
        },
        {
            "request_id": "request-2", "taskset_id": "taskset-0",
            "taskset_hash": "hash-0", "target_uc": "1/10",
            "actual_uc": "1/10", "target_ue": "2/5", "eta": "5/2",
            "generation_index": 1, "seed": 710214, "scheduler": "ASAP-BLOCK",
            "scheduler_cli": "gpfp_asap_block", "horizon_ms": 20,
            **_harvest_model_fields(),
        },
    ]
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: SimpleNamespace(
        result=SimpleNamespace(
            status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
            metrics={}, simulation_completed=True,
        ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
        retained_trace_path=None,
    ))
    monkeypatch.setattr(
        scheduler_runner.experiment, "request_rows",
        lambda *args, **kwargs: [dict(row) for row in requests],
    )
    def reverse_as_completed(future_to_job):
        futures = list(reversed(list(future_to_job)))
        for future in futures:
            yield future
            assert future not in future_to_job
        assert not future_to_job

    monkeypatch.setattr(scheduler_runner, "as_completed", reverse_as_completed)
    output = tmp_path / "completion-order"
    results_path = output / "results.jsonl"
    appended_ids = []
    original_append = scheduler_runner._append_jsonl

    def record_append(path, row):
        if path == results_path:
            appended_ids.append(row["request_id"])
        return original_append(path, row)

    monkeypatch.setattr(scheduler_runner, "_append_jsonl", record_append)
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 0
    assert appended_ids == ["request-2", "request-1"]
    final_ids = [
        json.loads(line)["request_id"]
        for line in results_path.read_text().splitlines()
    ]
    assert final_ids == ["request-1", "request-2"]


def test_progress_is_periodic_and_resume_relative():
    new_run = [completed for completed in range(1, 13) if scheduler_runner._progress_due(
        completed=completed, completed_at_start=0, total=12, interval=5,
    )]
    resumed_run = [completed for completed in range(8, 20) if scheduler_runner._progress_due(
        completed=completed, completed_at_start=7, total=19, interval=5,
    )]
    assert new_run == [5, 10, 12]
    assert resumed_run == [12, 17, 19]


def test_progress_uses_outstanding_request_metric(capsys):
    scheduler_runner._print_progress(
        completed=5, total=12, outstanding_requests=7, started=0.0,
        completed_at_start=0, parse_concurrency=1,
    )
    output = capsys.readouterr().out
    assert "outstanding_requests=7" in output
    assert "active_workers" not in output


def test_persisted_metrics_drop_only_battery_trajectory_without_mutation():
    metrics = {
        "battery_trajectory": [{"time": 0, "energy_j": "1"}],
        "missed_jobs": 3,
        "energy_blocked_ticks": 7,
        "battery_minimum_j": "1/2",
        "battery_maximum_j": "3/2",
    }
    original = dict(metrics)
    persisted = scheduler_runner._persisted_metrics(metrics)
    assert "battery_trajectory" not in persisted
    assert persisted == {
        "missed_jobs": 3,
        "energy_blocked_ticks": 7,
        "battery_minimum_j": "1/2",
        "battery_maximum_j": "3/2",
    }
    assert metrics == original
    assert "battery_trajectory" in metrics
    assert persisted is not metrics


def test_completed_results_survive_later_technical_failure_and_resume(tmp_path, monkeypatch):
    requests = [
        {
            "request_id": "request-1", "taskset_id": "taskset-0",
            "taskset_hash": "hash-0", "target_uc": "1/10",
            "actual_uc": "1/10", "target_ue": "2/5", "eta": "5/2",
            "generation_index": 0, "seed": 710213, "scheduler": "ASAP-BLOCK",
            "scheduler_cli": "gpfp_asap_block", "horizon_ms": 20,
            **_harvest_model_fields(),
        },
        {
            "request_id": "request-2", "taskset_id": "taskset-0",
            "taskset_hash": "hash-0", "target_uc": "1/10",
            "actual_uc": "1/10", "target_ue": "2/5", "eta": "5/2",
            "generation_index": 1, "seed": 710214, "scheduler": "ASAP-BLOCK",
            "scheduler_cli": "gpfp_asap_block", "horizon_ms": 20,
            **_harvest_model_fields(),
        },
    ]
    fail_second = {"value": True}

    def run_simulation(**kwargs):
        if kwargs["simulation_id_value"] == "request-2" and fail_second["value"]:
            raise RuntimeError("synthetic technical failure")
        return SimpleNamespace(
            result=SimpleNamespace(
                status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
                metrics={}, simulation_completed=True,
            ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
            retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    monkeypatch.setattr(
        scheduler_runner.experiment, "request_rows",
        lambda *args, **kwargs: [dict(row) for row in requests],
    )
    monkeypatch.setattr(
        scheduler_runner, "as_completed",
        lambda future_to_job: sorted(
            future_to_job, key=lambda future: future_to_job[future]["request_id"]
        ),
    )
    output = tmp_path / "partial-resume"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 2
    results_path = output / "results.jsonl"
    assert [
        json.loads(line)["request_id"] for line in results_path.read_text().splitlines()
    ] == ["request-1"]

    fail_second["value"] = False
    assert scheduler_runner.main(_scheduler_runner_args(output, resume=True)) == 0
    assert [
        json.loads(line)["request_id"] for line in results_path.read_text().splitlines()
    ] == ["request-1", "request-2"]


def test_attempt_history_separates_technical_failure_and_retries_in_new_dir(tmp_path, monkeypatch):
    calls = []

    def run_simulation(**kwargs):
        calls.append(kwargs["run_root"])
        if len(calls) == 1:
            raise RuntimeError("trace_target_locked")
        result = SimpleNamespace(
            status=SimulationStatus.PASS_OBSERVED, reason="observed",
            jobs=(), metrics={}, simulation_completed=True,
        )
        return SimpleNamespace(
            result=result, runtime_seconds=0.1, stdout_tail="out",
            stderr_tail="", retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    output = tmp_path / "campaign"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 2
    assert not (output / "results.jsonl").exists()
    first_attempt = output / "simulations" / "scheduler-load-cross-test-request" / "attempt_0001"
    assert first_attempt.is_dir()
    history = [json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()]
    assert [(row["attempt_index"], row["simulation_status"]) for row in history] == [(1, "TECHNICAL_FAILURE")]

    stale_lock = first_attempt / "simulation_trace_work" / "trace.lock"
    stale_lock.parent.mkdir(parents=True)
    stale_lock.write_text("pid=999999\n", encoding="utf-8")
    assert scheduler_runner.main(_scheduler_runner_args(output, resume=True)) == 0
    second_attempt = output / "simulations" / "scheduler-load-cross-test-request" / "attempt_0002"
    assert second_attempt.is_dir()
    assert stale_lock.is_file()
    assert calls == [first_attempt, second_attempt]
    results = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert len(results) == 1
    assert results[0]["request_id"] == "scheduler-load-cross-test-request"
    history = [json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()]
    assert [row["attempt_index"] for row in history] == [1, 2]
    assert len({row["request_id"] for row in results}) == 1


def test_completed_request_resume_does_not_create_attempt(tmp_path, monkeypatch):
    calls = []

    def run_simulation(**kwargs):
        calls.append(kwargs)
        raise AssertionError("completed request was re-executed")

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    output = tmp_path / "campaign"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 2
    calls.clear()
    attempts = [json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()]
    terminal = {
        "request_id": attempts[0]["request_id"],
        "taskset_id": attempts[0]["taskset_id"], "taskset_hash": attempts[0]["taskset_hash"],
        "target_uc": "1/10", "actual_uc": "1/10", "target_ue": "2/5", "eta": "5/2",
        "scheduler": "ASAP-BLOCK", "simulation_status": "SIM_PASS_OBSERVED",
        "technical_error": None, **_harvest_model_fields(), "energy": {
            "eta": "5/2", "target_ue": "2/5", "actual_ue": "2/5",
            "actual_ue_abs_error": "0", "actual_ue_rel_error": "0",
            **_harvest_model_fields(),
        },
    }
    (output / "results.jsonl").write_text(json.dumps(terminal) + "\n", encoding="utf-8")
    before = sorted((output / "simulations" / terminal["request_id"]).iterdir())
    assert scheduler_runner.main(_scheduler_runner_args(output, resume=True)) == 0
    assert calls == []
    assert sorted((output / "simulations" / terminal["request_id"]).iterdir()) == before


def test_legacy_technical_row_in_active_results_fails_closed(tmp_path, monkeypatch):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    output = tmp_path / "campaign"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 2
    (output / "results.jsonl").write_text(json.dumps({
        "request_id": "scheduler-load-cross-test-request",
        "simulation_status": "SIM_INTERNAL_ERROR", "technical_error": "old failure",
        **_harvest_model_fields(),
    }) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="migration/recovery"):
        scheduler_runner.main(_scheduler_runner_args(output, resume=True))


def test_live_attempt_lock_fails_closed_without_allocating_next_attempt(tmp_path, monkeypatch):
    calls = []

    def run_simulation(**kwargs):
        calls.append(kwargs["run_root"])
        raise RuntimeError("interrupted")

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    output = tmp_path / "campaign"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 2
    calls.clear()
    request_root = output / "simulations" / "scheduler-load-cross-test-request"
    live_lock = request_root / "attempt_0001" / "simulation_trace_work" / "trace.lock"
    live_lock.parent.mkdir(parents=True)
    live_lock.write_text(f"pid={os.getpid()}\n", encoding="utf-8")
    assert scheduler_runner.main(_scheduler_runner_args(output, resume=True)) == 2
    assert calls == []
    assert not (request_root / "attempt_0002").exists()


def test_existing_attempt_directory_without_history_is_never_overwritten(tmp_path, monkeypatch):
    calls = []

    def run_simulation(**kwargs):
        calls.append(kwargs["run_root"])
        result = SimpleNamespace(
            status=SimulationStatus.DEADLINE_MISS, reason="deadline_miss",
            jobs=(), metrics={}, simulation_completed=True,
        )
        return SimpleNamespace(
            result=result, runtime_seconds=0.1, stdout_tail="",
            stderr_tail="", retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    output = tmp_path / "campaign"
    old_attempt = output / "simulations" / "scheduler-load-cross-test-request" / "attempt_0001"
    old_attempt.mkdir(parents=True)
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 0
    assert calls == [old_attempt.parent / "attempt_0002"]
    assert old_attempt.is_dir()
    assert (old_attempt.parent / "attempt_0002").is_dir()
    results = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert len(results) == 1
    assert results[0]["simulation_status"] == SimulationStatus.DEADLINE_MISS.value


def test_duplicate_active_request_ids_fail_closed(tmp_path, monkeypatch):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    output = tmp_path / "campaign"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 2
    attempt = json.loads((output / "attempts.jsonl").read_text().splitlines()[0])
    terminal = {
        "request_id": attempt["request_id"], "taskset_id": attempt["taskset_id"],
        "taskset_hash": attempt["taskset_hash"], "simulation_status": "SIM_PASS_OBSERVED",
        "technical_error": None, **_harvest_model_fields(),
    }
    (output / "results.jsonl").write_text(
        json.dumps(terminal) + "\n" + json.dumps(terminal) + "\n", encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="duplicate"):
        scheduler_runner.main(_scheduler_runner_args(output, resume=True))


def test_v4_default_scan_contract_is_51_ordered_cells():
    profile = experiment.normalize_scan_profile()
    contract = experiment.build_scan_contract(profile)
    cells = experiment.build_scan_cells(
        profile["uc_scan_values"], profile["ue_scan_values"],
        profile["uc_figure_fixed_ues"], profile["ue_figure_fixed_ucs"],
    )
    assert len(cells) == 51
    assert len(set(cells)) == 51
    assert len(profile["uc_scan_values"]) == 10
    assert len(profile["ue_scan_values"]) == 10
    assert len(profile["uc_figure_fixed_ues"]) == 3
    assert len(profile["ue_figure_fixed_ucs"]) == 3
    assert cells[:3] == (
        (Fraction(1, 10), Fraction(3, 10)),
        (Fraction(1, 5), Fraction(3, 10)),
        (Fraction(3, 10), Fraction(3, 10)),
    )
    assert (Fraction(0), Fraction(1, 10)) not in cells
    assert contract["unique_cell_count"] == 51
    assert contract["axis_ticks"] == [
        "0", "1/10", "1/5", "3/10", "2/5", "1/2", "3/5",
        "7/10", "4/5", "9/10", "1",
    ]


def test_v4_composite_plot_layout_and_csv_contract(tmp_path):
    schedulers = list(experiment.ALL_SCHEDULERS)

    def rows_for(slice_config):
        rows = []
        for x_value in slice_config["x_values"]:
            for scheduler in schedulers:
                rows.append({
                    "scheduler": scheduler, slice_config["x_key"]: x_value,
                    "wholepass_ratio": 1.0, "ci95_low": 1.0, "ci95_high": 1.0,
                    "n_wholepass": 1, "n_total": 1,
                })
        return rows

    profile = experiment.normalize_scan_profile(
        uc_scan_values="1/5,2/5,3/5,4/5,1",
        ue_scan_values="1/5,2/5,3/5,4/5,1",
        uc_figure_fixed_ues="1/5,4/5", uc_figure_labels="abundant,tight",
        ue_figure_fixed_ucs="2/5,3/5", ue_figure_labels="low_compute,high_compute",
        axis_display_min="0", axis_display_max="1", axis_tick_step="1/5",
    )
    slices = experiment.build_v4_figure_slices(profile)
    uc_rows = [(item, rows_for(item)) for item in slices["uc_scans"]]
    assert len(uc_rows) == 2
    plot_composite_scan(
        uc_rows, tmp_path, "custom-2x3.png", "target_uc", schedulers, "U_C",
        "custom", axis_min="0", axis_max="1", axis_ticks=profile["axis_ticks"],
    )
    assert (tmp_path / "custom-2x3.png").is_file()

    default = experiment.normalize_scan_profile()
    default_slices = experiment.build_v4_figure_slices(default)
    default_rows = [(item, rows_for(item)) for item in default_slices["uc_scans"]]
    plot_composite_scan(
        default_rows, tmp_path, "default-3x3.png", "target_uc", schedulers, "U_C",
        "default", axis_min="0", axis_max="1", axis_ticks=default["axis_ticks"],
    )
    assert (tmp_path / "default-3x3.png").is_file()


def test_analyzer_rejects_empty_requests_with_explainable_error(tmp_path):
    config = {
        "cells": [["1/5", "1/5"]], "samples_per_cell": 1,
        "schedulers": ["ASAP-BLOCK"], "priority_policy": "RM",
        "processors": 4, "util_tolerance_total": "1/100",
        "use_real_solar_data": False, **experiment.HARVEST_MODEL_IDENTITY,
        "figure_slices": {
            "uc_scan": {"x_key": "target_uc", "fixed_key": "target_ue", "fixed_value": "1/5"},
            "ue_scan": {"x_key": "target_ue", "fixed_key": "target_uc", "fixed_value": "1/5"},
        },
    }
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "tasksets.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "requests.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "results.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="requests are empty"):
        analyze(tmp_path)


def test_v4_custom_scan_contract_and_fraction_tokens():
    profile = experiment.normalize_scan_profile(
        uc_scan_values="1/5,2/5,3/5,4/5,1",
        ue_scan_values="1/5,2/5,3/5,4/5,1",
        uc_figure_fixed_ues="1/5,4/5",
        uc_figure_labels="abundant,tight",
        ue_figure_fixed_ucs="2/5,3/5",
        ue_figure_labels="low_compute,high_compute",
        axis_display_min="0", axis_display_max="1", axis_tick_step="1/5",
    )
    cells = experiment.build_scan_cells(
        profile["uc_scan_values"], profile["ue_scan_values"],
        profile["uc_figure_fixed_ues"], profile["ue_figure_fixed_ucs"],
    )
    assert len(cells) == 16
    slices = experiment.build_v4_figure_slices(profile)
    assert [item["label"] for item in slices["uc_scans"]] == ["abundant", "tight"]
    assert [item["label"] for item in slices["ue_scans"]] == ["low_compute", "high_compute"]
    assert [item["fixed_value"] for item in slices["ue_scans"]] == ["2/5", "3/5"]
    assert experiment.fraction_token("3/10") == "3of10"
    assert experiment.fraction_token("1") == "1"
    assert profile["axis_ticks"] == ["0", "1/5", "2/5", "3/5", "4/5", "1"]


def test_v4_default_request_count_is_dynamic_51_times_nine():
    profile = experiment.normalize_scan_profile()
    cells = experiment.build_scan_cells(
        profile["uc_scan_values"], profile["ue_scan_values"],
        profile["uc_figure_fixed_ues"], profile["ue_figure_fixed_ucs"],
    )

    class Taskset:
        processors = 4
        actual_utilization = Fraction(1)
        taskset_index = 0
        seed = 1

        def __init__(self, uc):
            self.target_utilization = uc * self.processors
            self.taskset_id = f"t-{uc}"
            self.semantic_hash = f"h-{uc}"

    tasksets = [Taskset(uc) for uc in {
        Fraction(uc) for uc, _ue in cells
    }]
    requests = experiment.request_rows(
        tasksets, cells, experiment.ALL_SCHEDULERS, 60000,
        experiment_name=experiment.V4_EXPERIMENT,
    )
    assert len(requests) == 51 * 9
    assert {row["experiment"] for row in requests} == {experiment.V4_EXPERIMENT}


@pytest.mark.parametrize("kwargs", [
    {"uc_scan_values": "1/5,1/5"},
    {"uc_scan_values": "1/5,1/10"},
    {"uc_scan_values": "0,1/5"},
    {"ue_figure_fixed_ucs": "7/20"},
    {"axis_tick_step": "2/7"},
    {"uc_figure_labels": "unsafe label,tight"},
])
def test_v4_scan_contract_rejects_invalid_grid(kwargs):
    with pytest.raises(ValueError):
        experiment.normalize_scan_profile(**kwargs)


def test_v4_runner_rejects_cells_and_structured_grid_conflict(tmp_path):
    args = scheduler_runner.make_parser().parse_args([
        "--output", str(tmp_path), "--seed", "1", "--cells", "1/5:1/5",
        "--uc-scan-values", "1/5,2/5",
    ])
    with pytest.raises(SystemExit, match="conflicts"):
        scheduler_runner._resolve_grid(args)


def test_v4_runner_grid_resolution_binds_scan_configuration(tmp_path):
    args = scheduler_runner.make_parser().parse_args([
        "--output", str(tmp_path), "--seed", "1",
        "--uc-scan-values", "1/5,2/5,3/5,4/5,1",
        "--ue-scan-values", "1/5,2/5,3/5,4/5,1",
        "--uc-figure-fixed-ues", "1/5,4/5",
        "--uc-figure-labels", "abundant,tight",
        "--ue-figure-fixed-ucs", "2/5,3/5",
        "--ue-figure-labels", "low_compute,high_compute",
        "--axis-display-min", "0", "--axis-display-max", "1",
        "--axis-tick-step", "1/5",
    ])
    cells, slices, contract, is_v4 = scheduler_runner._resolve_grid(args)
    assert is_v4 is True
    assert len(cells) == contract["unique_cell_count"] == 16
    assert contract["ordered_cells"] == [[str(uc), str(ue)] for uc, ue in cells]
    assert len(slices["uc_scans"]) == 2
