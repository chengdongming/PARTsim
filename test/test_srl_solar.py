"""SRL minute-axis preprocessing and existing native/Python replay contract."""
from datetime import date, datetime, timedelta
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.prepare_srl_solar import HEADER, prepare
from experiments.common.paths import resolve_solar_data_path
from experiments.common.energy_manager import EnergyConfig
from experiments.common.solar_data_loader import SolarDataLoader
from experiments.common.srl_window import validate_srl_observed_window
from experiments.pipeline import exact_energy
from experiments.pipeline.simulation_engine import (
    construct_paired_harvest_trace, materialize_simulation_inputs,
    SimulationConfigurationError, construct_shared_solar_input,
)
from experiments.pipeline.system_model import load_system_config

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/srl_edinburg_20251101_20261101.csv"
CSV = ROOT / "data/processed/srl_edinburg_solar_minute.csv"
META = CSV.with_suffix(".metadata.json")
TEMPLATE = ROOT / "configs/templates/system_config_unified_template.yml"
DAY = date(2025, 11, 1)


class SRLTests(unittest.TestCase):
    def test_reproduce_complete_dataset_and_time_axis(self):
        metadata = json.loads(META.read_text())
        self.assertEqual(metadata["observed_minutes"], 526919)
        self.assertEqual(metadata["output_minutes"], 527040)
        self.assertEqual(metadata["missing_ranges"], [{
            "first": "2026-08-22T06:00", "last": "2026-08-22T08:00",
            "minutes": 121,
        }])
        self.assertEqual(metadata["negative_observations_clipped_to_zero"], 255498)
        self.assertEqual(hashlib.sha256(RAW.read_bytes()).hexdigest(),
                         metadata["source_sha256"])
        self.assertEqual(hashlib.sha256(CSV.read_bytes()).hexdigest(),
                         metadata["processed_sha256"])
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / CSV.name
            report = prepare(RAW, output, Path(td) / META.name, DAY, date(2026, 11, 1))
            self.assertEqual(report, metadata)
            self.assertEqual(output.read_bytes(), CSV.read_bytes())
        loader = SolarDataLoader(str(CSV))
        self.assertEqual(len(loader.irradiance_data), 527040)
        self.assertGreaterEqual(float(loader.irradiance_data.min()), 0)
        self.assertEqual(float(loader.irradiance_data.max()), 1271.28)
        def at(when):
            d = datetime.fromisoformat(when)
            offset = (d.date()-DAY).days*1440+d.hour*60+d.minute
            return loader.get_irradiance_at_time(offset*60000)
        self.assertEqual(at("2026-07-06T06:05"), 33.5494)
        self.assertEqual(at("2026-07-06T06:06"), 35.8807)
        self.assertEqual(at("2026-08-22T06:00"), 0)
        self.assertEqual(at("2026-08-22T08:00"), 0)

    def test_preprocessor_fails_on_invalid_raw_data(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "test.csv"
            output = Path(td) / "out.csv"
            meta = Path(td) / "meta.json"
            header = ",".join(HEADER)+"\n"
            for rows in (
                ["11/1/2025,00:00,-999", "11/1/2025,23:59,1"],
                ["11/1/2025,00:00,nan", "11/1/2025,23:59,1"],
                ["11/1/2025,00:00,1", "11/1/2025,00:00,2", "11/1/2025,23:59,1"],
                ["11/1/2025,00:01,1", "11/1/2025,23:59,1"],
                ["11/1/2025,00:00,1", "11/1/2025,23:58,1"],
            ):
                source.write_text(header+"\n".join(rows)+"\n")
                with self.assertRaises(ValueError):
                    prepare(source, output, meta, DAY, DAY)
                self.assertFalse(output.exists())
                self.assertFalse(meta.exists())

    def test_default_path_date_and_beta(self):
        system = load_system_config(str(TEMPLATE))
        self.assertEqual(resolve_solar_data_path(system.solar_data_file, TEMPLATE), CSV)
        self.assertEqual(DAY + timedelta(days=system.day_of_year-1), date(2026, 7, 6))
        self.assertEqual(system.time_of_day_ms, 21900000)
        trace = construct_paired_harvest_trace(TEMPLATE, 60002)
        self.assertTrue(all(v == Fraction.from_float(33.5494*1.0*0.18*0.001)
                            for v in trace[:59999]))
        self.assertTrue(all(v == Fraction.from_float(35.8807*1.0*0.18*0.001)
                            for v in trace[59999:]))
        short = trace[59996:]
        beta = exact_energy.service_curve_lower_bound(short, 4)
        self.assertEqual(beta[0], 0)
        self.assertEqual(beta[1], min(short))
        self.assertTrue(all(a <= b for a,b in zip(beta, beta[1:])))

    def test_active_templates_resolve_data_outside_repository_cwd(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as td:
            try:
                os.chdir(td)
                for template in (TEMPLATE, TEMPLATE.with_name("priority_energy_system_template.yml")):
                    config = EnergyConfig()
                    self.assertTrue(config.load_from_file(str(template)), config.last_load_error)
                    self.assertEqual(Path(config.solar_data_file), CSV)
                    self.assertEqual(Path(config.harvest_source["solar_data_file"]), CSV)
                    self.assertEqual(config.harvest_source["start_offset_ms"], 356045*60000)
            finally:
                os.chdir(original_cwd)

    def test_measured_window_rejects_gap_and_end_boundary(self):
        gap = datetime(2026,8,22,6)
        offset = int((gap-datetime(2025,11,1)).total_seconds()*1000)
        validate_srl_observed_window(CSV,offset-60000,59999)
        with self.assertRaisesRegex(ValueError,"missing-data"):
            validate_srl_observed_window(CSV,offset-60000,60000)
        with self.assertRaisesRegex(ValueError,"missing-data"):
            validate_srl_observed_window(CSV,offset,1)
        after = offset+121*60000
        validate_srl_observed_window(CSV,after,1)
        final = (527040-1)*60000
        validate_srl_observed_window(CSV,final,59999)
        with self.assertRaisesRegex(ValueError,"exceeds"):
            validate_srl_observed_window(CSV,final,60000)
        with self.assertRaisesRegex(ValueError,"identity mismatch"):
            validate_srl_observed_window(CSV,after,1,payload=b"wrong bytes")
        # Exercise both production Python entry points rather than only the
        # small guard in isolation, including the immutable snapshot path.
        import yaml
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            system_path=directory/"system.yml"
            system=yaml.safe_load(TEMPLATE.read_text())
            energy=system["energy_management"]
            energy["solar_data_file"]=str(CSV)
            energy["day_of_year"]=(gap.date()-DAY).days+1
            energy["time_of_day_ms"]=6*3600000
            system_path.write_text(yaml.safe_dump(system))
            with self.assertRaisesRegex(SimulationConfigurationError,"missing-data"):
                construct_paired_harvest_trace(system_path,1)
            support=yaml.safe_load((ROOT/"test/fixtures/solar_energy_support.yaml").read_text())
            support["service_curve"]["system_template"]=str(system_path)
            support_path=directory/"support.yml"
            support_path.write_text(yaml.safe_dump(support))
            with self.assertRaisesRegex(SimulationConfigurationError,"missing-data"):
                construct_shared_solar_input(system_path,support_path,horizon=1,source_root=ROOT)

    def test_native_srl_replay_at_minute_boundary(self):
        binary = Path(os.getenv("PARTSIM_TEST_BINARY", ROOT/"build/rtsim/rtsim"))
        if not binary.is_file():
            self.skipTest("rebuild PARTsim or set PARTSIM_TEST_BINARY")
        # Cut off task releases after one slot; observe the minute boundary
        # without a long series of recurring jobs. The ledger must match.
        payload = (dict(task_id="0", priority_rank=0,C=1,D=1,T=2,
                        P="93/250000",workload="hash",arrival_offset=0),)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for scheduler in ("gpfp_asap_block", "gpfp_asap_nonblock"):
                run_root = root/scheduler
                system, tasks = materialize_simulation_inputs(
                    TEMPLATE,run_root,payload,processors=1,initial_battery=Fraction(0),
                    battery_capacity=Fraction(1000),scheduler_id=scheduler,
                    service_curve=dict(solar_scale="1",use_real_solar_data=True),
                    release_horizon=1)
                projected = load_system_config(str(system))
                self.assertEqual(Path(projected.solar_data_file), CSV)
                horizon = 60002
                reference = construct_paired_harvest_trace(system,horizon)
                run = subprocess.run([str(binary.resolve()),str(system),str(tasks),
                    str(horizon),"-t",str(run_root/"trace.json"),"--energy-timing","same-slot",
                    "--semantic-traces","--taskset-semantic-hash","a"*64,
                    "--run-id",scheduler],cwd=ROOT,capture_output=True,text=True,timeout=60)
                self.assertEqual(run.returncode,0,run.stderr[-2000:])
                trace=json.loads((run_root/"trace.json").read_text())
                ledger=trace["same_slot_energy_summary"]
                self.assertEqual(ledger["observed_energy_intervals"],horizon)
                self.assertEqual(ledger["clipped_energy_j"],0)
                self.assertAlmostEqual(ledger["offered_energy_j"],float(sum(reference)),places=6)
                self.assertAlmostEqual(ledger["offered_energy_j"],
                                       ledger["credited_energy_j"],places=8)


if __name__ == "__main__":
    unittest.main()
