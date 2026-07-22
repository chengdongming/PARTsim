import argparse

import pytest

from global_task_generator import configure_arrival_offset_arguments
from experiments.v9_3.performance_taskset_store import generation_command, generation_seed
from v9_3_b4_helpers import config


def test_no_arrival_offset_switch_is_explicit_and_mutually_exclusive(tmp_path):
    parser = configure_arrival_offset_arguments(argparse.ArgumentParser())
    assert parser.parse_args([]).arrival_offset is True
    assert parser.parse_args(["--arrival-offset"]).arrival_offset is True
    assert parser.parse_args(["--no-arrival-offset"]).arrival_offset is False
    with pytest.raises(SystemExit):
        parser.parse_args(["--arrival-offset", "--no-arrival-offset"])
    command = generation_command(config(), utilization=__import__("fractions").Fraction("1/10"), seed=1, output=tmp_path / "x.yml")
    assert "--no-arrival-offset" in command


def test_generation_seed_is_dimension_stable():
    assert generation_seed(982201, 2, 200, 17) == 982618
    assert generation_seed(982201, 2, 200, 17, 1) == 1982618
