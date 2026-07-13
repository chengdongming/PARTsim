# ASAP-BLOCK v9.3 five-variant pilot report

This is a pipeline pilot, not a formal paper experiment or final statistical conclusion.

## Outcome

- Mode: `full`
- Pipeline passed: `true`
- Generated tasksets: `60/60`
- Analysis terminal rows: `300/300`
- Certified tasksets (analysis rows): `106`
- Certified rows with E0=1: `106`
- Missing / duplicate / schema failures: `0` / `0` / `0`

## Runtime and failures

- Runtime mean / median / p95 / max: `3.276497` / `0.419349` / `15.044062` / `15.262919` seconds
- Timeout / numeric / internal: `27` / `0` / `0`
- Generation / crash / illegal service curve: `0` / `0` / `0`

## Dominance

- DEADLINE_CARRY_IN: common `266`, tighter `0`, equal `266`, violations `0`, max improvement `0`, mean improvement `0.0`
- FIXED_CW_CARRY_IN: common `228`, tighter `0`, equal `228`, violations `0`, max improvement `0`, mean improvement `0.0`
- RECURSIVE_CARRY_IN: common `281`, tighter `0`, equal `281`, violations `0`, max improvement `0`, mean improvement `0.0`

## Certification ratio by cell and variant

- U_norm=0.2, E0=0, CW_D: 0/10 = 0.000
- U_norm=0.2, E0=0, CW_THETA_CW: 0/10 = 0.000
- U_norm=0.2, E0=0, LOC_D: 0/10 = 0.000
- U_norm=0.2, E0=0, LOC_THETA_CW: 0/10 = 0.000
- U_norm=0.2, E0=0, LOC_THETA_LOC: 0/10 = 0.000
- U_norm=0.2, E0=1, CW_D: 10/10 = 1.000
- U_norm=0.2, E0=1, CW_THETA_CW: 10/10 = 1.000
- U_norm=0.2, E0=1, LOC_D: 10/10 = 1.000
- U_norm=0.2, E0=1, LOC_THETA_CW: 10/10 = 1.000
- U_norm=0.2, E0=1, LOC_THETA_LOC: 10/10 = 1.000
- U_norm=0.4, E0=0, CW_D: 0/10 = 0.000
- U_norm=0.4, E0=0, CW_THETA_CW: 0/10 = 0.000
- U_norm=0.4, E0=0, LOC_D: 0/10 = 0.000
- U_norm=0.4, E0=0, LOC_THETA_CW: 0/10 = 0.000
- U_norm=0.4, E0=0, LOC_THETA_LOC: 0/10 = 0.000
- U_norm=0.4, E0=1, CW_D: 8/10 = 0.800
- U_norm=0.4, E0=1, CW_THETA_CW: 9/10 = 0.900
- U_norm=0.4, E0=1, LOC_D: 8/10 = 0.800
- U_norm=0.4, E0=1, LOC_THETA_CW: 9/10 = 0.900
- U_norm=0.4, E0=1, LOC_THETA_LOC: 10/10 = 1.000
- U_norm=0.6, E0=0, CW_D: 0/10 = 0.000
- U_norm=0.6, E0=0, CW_THETA_CW: 0/10 = 0.000
- U_norm=0.6, E0=0, LOC_D: 0/10 = 0.000
- U_norm=0.6, E0=0, LOC_THETA_CW: 0/10 = 0.000
- U_norm=0.6, E0=0, LOC_THETA_LOC: 0/10 = 0.000
- U_norm=0.6, E0=1, CW_D: 1/10 = 0.100
- U_norm=0.6, E0=1, CW_THETA_CW: 4/10 = 0.400
- U_norm=0.6, E0=1, LOC_D: 1/10 = 0.100
- U_norm=0.6, E0=1, LOC_THETA_CW: 3/10 = 0.300
- U_norm=0.6, E0=1, LOC_THETA_LOC: 3/10 = 0.300

## Initial E0 comparison

- E0=0: certified `0/150` (0.000), mean runtime `0.193922` seconds
- E0=1: certified `106/150` (0.707), mean runtime `6.359072` seconds

## Problems

- P0: none
- P1: analysis timeouts: 27
- P2: none

## Smoke reproducibility

- Same-seed semantic and non-runtime outcomes match: `true`

## Next step

The formal pilot parameter expansion may be designed only if this main pipeline is marked passed; these rows are not final paper statistics.
