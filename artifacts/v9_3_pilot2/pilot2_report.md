# ASAP-BLOCK v9.3 Pilot-2 report

This is a timeout/tightness pilot, not a formal experiment or final paper statistic.

## Timeout sensitivity

- Saved 15-second TIMEOUT requests: `27`
- 15→30 seconds completed: `18`
- 30→60 seconds attempted/completed: `9` / `7`
- Still TIMEOUT at 60 seconds: `2`
- Newly certified / NO_CANDIDATE: `15` / `10`
- Non-timeout candidate/certification/outcome drift: `0` / `0` / `0`
- Temporary formal-run recommendation: `FURTHER_EVALUATION`
- Basis: 2 request(s) still timed out at 60 seconds

- Recovered-request solver wall mean/median/p95/max: `25.215706568919998` / `18.022844345` / `51.786222031` / `57.176629019` seconds
## Baseline complete/local diagnosis

- Classification: `B_ENVELOPE_STRICT_WITHOUT_CLOSURE_CHANGE`
- DEADLINE_CARRY_IN: envelope common/strict/equal/violation `42475` / `2389` / `40086` / `0`; local-only closure `0`; response strict/equal/violation `0` / `272` / `0`
- FIXED_CW_CARRY_IN: envelope common/strict/equal/violation `5909` / `1286` / `4623` / `0`; local-only closure `0`; response strict/equal/violation `0` / `229` / `0`
- RECURSIVE_CARRY_IN: envelope common/strict/equal/violation `0` / `0` / `0` / `0`; local-only closure `0`; response strict/equal/violation `0` / `281` / `0`

## Actual baseline parameter audit

- Deadline mode: `implicit`; D/T distribution: `{"1": 600}`
- Distinct exact powers: `4`; all tasks same power: `false`
- M / task_n distribution / E0 distribution: `4` / `{'10': 60}` / `{'0': 30, '1': 30}`
- U_norm distribution: `{'0.2': 20, '0.4': 20, '0.6': 20}`
- Common-access q+h=w: `501/48384` (`0.010354662698412698`)
- Strict-access E0 separation intervals: `3675`; midpoint p05/median/p95 `0.024301499592000002` / `0.092860872417` / `0.198073115991`
- Actual equality mechanism: local envelopes were pointwise smaller, but the tested supply thresholds remained on the same pass/fail side, so no local-only closure and no earlier candidate occurred. Heterogeneous power was already present and is not a missing-mode explanation.
- Next minimal adjustment: Keep n=10, M=4, the saved generator semantics, powers, and service curve fixed; replace the coarse E0 endpoints/fixed E0=1 with a one-dimensional exact-rational intermediate-E0 probe near the measured strict-access midpoint median, then bracket with the measured p05/p95 midpoint range. This is a diagnostic recommendation, not a claimed differentiating region.

## Screening cells

- S1-U0.4 (implicit, alias=None): generated `5`, completed/certified/timeout/no-candidate/N/A `25` / `25` / `0` / `0` / `0`; common/strict `150` / `0`; envelope strict/local-only closure `1053` / `0`; runtime mean/median/p95/max `6.003740660759999` / `5.464189738` / `11.453258415` / `11.459091862`; differentiating `false`
- S1-U0.6 (implicit, alias=None): generated `5`, completed/certified/timeout/no-candidate/N/A `6` / `6` / `0` / `16` / `3`; common/strict `98` / `0`; envelope strict/local-only closure `803` / `0`; runtime mean/median/p95/max `17.70725387` / `9.594383673` / `58.161428323` / `58.271307544`; differentiating `false`
- S2-U0.4 (constrained, alias=None): generated `5`, completed/certified/timeout/no-candidate/N/A `15` / `15` / `0` / `8` / `2`; common/strict `116` / `0`; envelope strict/local-only closure `850` / `0`; runtime mean/median/p95/max `3.06368965108` / `1.983109337` / `9.053630016` / `10.165397787`; differentiating `false`
- S2-U0.6 (constrained, alias=None): generated `5`, completed/certified/timeout/no-candidate/N/A `0` / `0` / `0` / `20` / `5`; common/strict `72` / `0`; envelope strict/local-only closure `653` / `0`; runtime mean/median/p95/max `9.74595309248` / `4.721863147` / `30.230046491` / `32.230993149`; differentiating `false`
- S3-U0.4 (implicit, alias=S1): generated `5`, completed/certified/timeout/no-candidate/N/A `25` / `25` / `0` / `0` / `0`; common/strict `150` / `0`; envelope strict/local-only closure `776` / `0`; runtime mean/median/p95/max `5.71014618036` / `4.339418172` / `13.947407494` / `14.143392939`; differentiating `false`
- S3-U0.6 (implicit, alias=S1): generated `5`, completed/certified/timeout/no-candidate/N/A `5` / `5` / `5` / `12` / `3`; common/strict `99` / `0`; envelope strict/local-only closure `1070` / `0`; runtime mean/median/p95/max `25.951619304159998` / `14.224205571` / `60.313621821` / `60.325179004`; differentiating `false`
- S4-U0.4 (constrained, alias=S2): generated `5`, completed/certified/timeout/no-candidate/N/A `3` / `3` / `0` / `18` / `4`; common/strict `87` / `0`; envelope strict/local-only closure `660` / `0`; runtime mean/median/p95/max `5.183332930960001` / `3.054933029` / `16.819413104` / `17.300463119`; differentiating `false`
- S4-U0.6 (constrained, alias=S2): generated `5`, completed/certified/timeout/no-candidate/N/A `0` / `0` / `0` / `20` / `5`; common/strict `71` / `0`; envelope strict/local-only closure `398` / `0`; runtime mean/median/p95/max `6.827717894799999` / `5.142112594` / `25.861705583` / `26.304651974`; differentiating `false`

- Differentiating cells: `[]`
- Selected confirmation cells: `[]`

## Confirmation

- Executed: `false`

## Dominance and problems

- LOC_D_vs_CW_D: common/tighter/equal/violation `324` / `0` / `324` / `0`
- LOC_THETA_CW_vs_CW_THETA_CW: common/tighter/equal/violation `179` / `0` / `179` / `0`
- LOC_THETA_LOC_vs_CW_THETA_CW: common/tighter/equal/violation `340` / `0` / `340` / `0`
- P0: none
- P1: 2 timeout request(s) unresolved at 60 seconds; 5 screening analysis timeout(s)
- P2: S3/S4 are separately seeded aliases because the baseline power mode is already heterogeneous; no screening cell met the strict-response differentiation criterion

## Design readiness

- Evidence is sufficient to design a formal parameter grid: `false`
- No formal large-scale experiment was executed or claimed.
