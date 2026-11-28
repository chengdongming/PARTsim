# Solar irradiance inputs

The current ordinary and priority-energy system templates select the measured
UTRGV Solar Radiation Lab (SRL) global horizontal irradiance (GHI) trace from
Edinburg, Texas. This is a new input identity, not a reinterpretation of the
historical NASA trace. The old NASA file and frozen reproduction templates
remain unchanged.

## Current measured data

- Source: [UTRGV SRL, MIDC/National Laboratory of the Rockies](https://midcdmz.nlr.gov/apps/sitehome.pl?site=UTPASRL).
- Citation: Ramos, J.; Andreas, A. (2011). University of Texas Panamerican
  (UTPA): Solar Radiation Lab (SRL); Edinburg, Texas (Data). NREL Report
  DA-5500-56514. [DOI: 10.5439/1052555](https://doi.org/10.5439/1052555).
- Original export: `raw/srl_edinburg_20251101_20261101.csv`, all-day
  Selected 1-Min Data, Global Horizontal, 2025-11-01 through 2026-11-01
  inclusive (station CST, fixed UTC-06:00; no daylight-saving conversion).
- Runtime input: `processed/srl_edinburg_solar_minute.csv`, one nonnegative
  irradiance value in W/m² per minute, same format as the historical loader.
- Provenance, hashes, and cleaning report:
  `processed/srl_edinburg_solar_minute.metadata.json`.

The unmodified source contains 526,919 observations; the complete timeline is
527,040 minutes. The 121 missing minutes on 2026-08-22 06:00–08:00 CST are
filled with zero placeholders (not measurements). 255,498 finite negative
observations, all between -10 and 0 W/m², are clipped to zero. No values are
interpolated. Avoid missing-data windows in measured-data evaluation; any
intentional outage study must identify them as placeholders. The underlying
data provider has not certified accuracy/completeness.

Reproduce from the original timestamped export:

```sh
python scripts/prepare_srl_solar.py data/raw/srl_edinburg_20251101_20261101.csv \
  --output data/processed/srl_edinburg_solar_minute.csv \
  --metadata data/processed/srl_edinburg_solar_minute.metadata.json \
  --start-date 2025-11-01 --end-date 2026-11-01
```

## Time and energy model

In the existing legacy solar interface, `day_of_year` is a **one-based row-day
index relative to the selected CSV's first date**, not a calendar day-of-year
when that CSV starts outside January. For this trace, day 1 is 2025-11-01;
day 248 is 2026-07-06. The templates preserve the former July 6 / 06:05
phase using index 248 and `time_of_day_ms: 21900000`. Runtime reads the
existing minute-indexed piecewise-constant irradiance (no manufactured
sub-minute fluctuations). Use complete in-range windows; the native legacy
source returns zero past the end of the file, while some older Python
diagnostic readers wrap.

Irradiance is converted to energy using the unchanged area and efficiency:
`E = GHI × pv_area_m2 × pv_efficiency × duration_seconds`.
The current templates retain 1 m² and 0.18 efficiency; the simulation
projection may explicitly scale area for controlled experiments.

The common paired-trace builder and existing service-curve extractor can
derive a finite-window beta table from the same runtime trace. Such a bound
is conditional on the observed/selected finite trace; it is not a guarantee
for future weather. The six-method RTA campaigns retain their existing
hypothetical service-curve inputs. Synthetic charging models are unchanged.

For historical NASA runs, use their recorded source/config and the original
`processed/shenyang_solar_minute.csv` (hourly data expanded to minute rows).
