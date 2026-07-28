import csv
import hashlib
import io
import json
import sys
from pathlib import Path

import matplotlib.image as mpimg
import pytest


TEST_DIR = Path(__file__).resolve().parent
B4_DIR = TEST_DIR.parent
sys.path.insert(0, str(B4_DIR))
sys.path.insert(0, str(TEST_DIR))

import statistics_common as statistics
import statistics_plotting as plotting
from test_statistics_pipeline import write_synthetic_analysis


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    root = write_synthetic_analysis(tmp_path_factory.mktemp("i5d-render") / "analysis")
    first, manifest, audit = statistics.build_outputs(root, "validation", True)
    second, second_manifest, second_audit = statistics.build_outputs(root, "validation", True)
    return first, second, manifest, audit, second_manifest, second_audit


def test_validation_generates_all_five_pdf_png_pairs(rendered):
    outputs = rendered[0]
    for index in range(1, 6):
        pdf = next(name for name in outputs if name.startswith(f"figure{index}_") and name.endswith(".pdf"))
        png = pdf[:-4] + ".png"
        assert outputs[pdf].startswith(b"%PDF-")
        assert outputs[pdf].rstrip().endswith(b"%%EOF")
        assert outputs[png].startswith(b"\x89PNG\r\n\x1a\n")
        assert len(outputs[pdf]) > 1000
        assert len(outputs[png]) > 1000


def test_all_pngs_are_parseable(rendered, tmp_path):
    outputs = rendered[0]
    for name, material in outputs.items():
        if not name.endswith(".png"):
            continue
        path = tmp_path / name
        path.write_bytes(material)
        image = mpimg.imread(str(path))
        assert image.ndim == 3
        assert image.shape[0] > 100 and image.shape[1] > 100


def test_figure1_has_identical_fixed_x_ranges(rendered):
    spec = rendered[3]["figure_structure"]
    assert spec["figure1_x_ranges"] == [[0.0, 1.0], [0.0, 1.0]]


def test_figure1_renders_frozen_algorithm_tick_labels(rendered):
    rows = [json.loads(line) for line in rendered[0]["algorithm_summary.jsonl"].splitlines()]
    figure = plotting._figure1(rows)
    try:
        assert [tick.get_text() for tick in figure.axes[0].get_yticklabels()] == list(statistics.ALGORITHMS)
    finally:
        plotting.plt.close(figure)


def test_figures_2_and_3_share_one_symmetric_scale(rendered):
    lower, upper = rendered[3]["figure_structure"]["figure23_shared_symmetric_scale"]
    assert lower == -upper
    assert 0.05 <= upper <= 1.0
    assert round(upper / 0.05) == pytest.approx(upper / 0.05)


def test_heatmaps_render_actual_lambda_and_utilization_ticks(rendered):
    rows = [json.loads(line) for line in rendered[0]["cell_summary.jsonl"].splitlines()]
    figure = plotting._heatmap_figure(rows, statistics.COMPARATORS[:2], 1.0, ("A", "B"))
    try:
        assert [tick.get_text() for tick in figure.axes[0].get_xticklabels()] == ["0.7", "1"]
        assert [tick.get_text() for tick in figure.axes[0].get_yticklabels()] == ["0.2", "0.4"]
    finally:
        plotting.plt.close(figure)


def test_figure4_records_zero_reference_line(rendered):
    assert rendered[3]["figure_structure"]["figure4_zero_reference"] is True


def test_figure5_has_rank_boundary_four_tradeoffs_and_six_mechanisms(rendered):
    spec = rendered[3]["figure_structure"]
    assert spec["figure5_rank_boundary"] == 4.5
    assert spec["figure5_tradeoff_comparator_count"] == 4
    assert spec["figure5_mechanism_facet_count"] == 6
    assert spec["figure5_shared_tradeoff_limit"][0] == -spec["figure5_shared_tradeoff_limit"][1]


def test_validation_watermark_is_required_and_audited(rendered):
    outputs, _, manifest, audit, _, _ = rendered
    assert manifest["paper_results_authorized"] is False
    assert manifest["validation_watermark"] is True
    assert audit["checks"]["validation_watermark"] is True
    assert audit["figure_structure"]["validation_watermark"] is True
    for name, material in outputs.items():
        if name.endswith(".png"):
            assert b"source-data-sha256=" in material


def test_pdf_metadata_has_no_creation_or_modification_date(rendered):
    for name, material in rendered[0].items():
        if name.endswith(".pdf"):
            assert b"CreationDate" not in material
            assert b"ModDate" not in material
            assert b"source-data-sha256=" in material


def test_all_rendered_outputs_are_byte_identical_on_repeat(rendered):
    first, second, manifest, audit, second_manifest, second_audit = rendered
    assert first == second
    assert manifest == second_manifest
    assert audit == second_audit


def test_figure_source_hashes_bind_authoritative_statistics(rendered):
    outputs, _, _, audit, _, _ = rendered
    bindings = audit["figure_source_data_sha256"]
    for name, source_sha in bindings.items():
        assert name in outputs
        assert source_sha.encode("ascii") in outputs[name]
    assert bindings["figure1_algorithm_pass_rates.pdf"] == hashlib.sha256(outputs["algorithm_summary.jsonl"]).hexdigest()
    assert bindings["figure4_confirmatory_effects.png"] == hashlib.sha256(outputs["confirmatory_effects.jsonl"]).hexdigest()


def test_hash_dag_has_no_self_or_cycle(rendered):
    outputs, _, manifest, audit, _, _ = rendered
    audit_hashes = audit["output_file_sha256"]
    assert "statistics_audit.json" not in audit_hashes
    assert "statistics_manifest.json" not in audit_hashes
    generated = {item["name"]: item["sha256"] for item in manifest["generated_outputs"]}
    assert "statistics_manifest.json" not in generated
    assert generated["statistics_audit.json"] == hashlib.sha256(outputs["statistics_audit.json"]).hexdigest()
    for name, expected in audit_hashes.items():
        assert hashlib.sha256(outputs[name]).hexdigest() == expected


def test_tables_have_frozen_headers_and_tex_fragments_only(rendered):
    outputs = rendered[0]
    table1 = list(csv.reader(io.StringIO(outputs["table1_confirmatory_effects.csv"].decode())))
    table2 = list(csv.reader(io.StringIO(outputs["table2_algorithm_summary.csv"].decode())))
    assert table1[0] == [
        "comparator", "HP risk difference (pp)", "HP 95% CI", "raw p",
        "Holm-adjusted p", "reject at 0.05", "WholePass risk difference (pp)",
        "WholePass 95% CI", "ASAP-BLOCK-only", "comparator-only", "both", "neither",
    ]
    assert [row[0] for row in table1[1:]] == list(statistics.COMPARATORS)
    assert [row[0] for row in table2[1:]] == list(statistics.ALGORITHMS)
    for name in ("table1_confirmatory_effects.tex", "table2_algorithm_summary.tex"):
        text = outputs[name].decode()
        assert text.startswith("\\begin{tabular}")
        assert text.endswith("\\end{tabular}\n")
        assert "\\begin{document}" not in text
        assert "/tmp/" not in text


def test_json_and_csv_outputs_have_no_nonfinite_tokens(rendered):
    for name, material in rendered[0].items():
        if name.endswith((".jsonl", ".json", ".csv")):
            lowered = material.lower()
            assert b"nan" not in lowered
            assert b"infinity" not in lowered


def test_undefined_mechanism_ratio_is_null_in_json_and_empty_in_csv(rendered):
    outputs = rendered[0]
    rows = [json.loads(line) for line in outputs["mechanism_summary.jsonl"].splitlines()]
    target = next(row for row in rows if row["mechanism"] == "BypassRate" and row["algorithm"] == "ASAP-BLOCK")
    assert target["macro_mean"] is None
    csv_rows = list(csv.DictReader(io.StringIO(outputs["mechanism_summary.csv"].decode())))
    target_csv = next(row for row in csv_rows if row["mechanism"] == "BypassRate" and row["algorithm"] == "ASAP-BLOCK")
    assert target_csv["macro_mean"] == ""
    assert target_csv["exposure_pooled_rate"] == ""


def test_atomic_publication_writes_only_applicable_outputs(rendered, tmp_path):
    outputs = rendered[0]
    target = tmp_path / "published"
    statistics.publish_outputs(target, outputs)
    assert {path.name for path in target.iterdir()} == set(outputs)
    for name, material in outputs.items():
        assert (target / name).read_bytes() == material
    assert not list(tmp_path.glob(".b4pe-i5d-stage-*"))
