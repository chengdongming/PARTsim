# ASAP-BLOCK v9.3 Pilot-3 adaptive exact-E0 report

This is an adaptive diagnostic pilot. It is not a completed formal experiment or a paper-result claim.

## Exact E0 selection and predicted coverage

- `0` (CONTROL): intervals=0, tasksets=0, tasks=0, variants=0, U0.4/U0.6 intervals=0/0
- `1360181973072000001/16000000000000000000` (DATA): intervals=211, tasksets=13, tasks=23, variants=4, U0.4/U0.6 intervals=124/86
- `178487996829/2000000000000` (DATA): intervals=247, tasksets=13, tasks=24, variants=4, U0.4/U0.6 intervals=146/98
- `21473099401200000281/200000000000000000000` (DATA): intervals=348, tasksets=13, tasks=23, variants=4, U0.4/U0.6 intervals=200/148
- `43409021832000000987/400000000000000000000` (DATA): intervals=307, tasksets=14, tasks=21, variants=4, U0.4/U0.6 intervals=167/140
- `225897744831/2000000000000` (DATA): intervals=313, tasksets=14, tasks=21, variants=4, U0.4/U0.6 intervals=175/138
- `1` (CONTROL): intervals=0, tasksets=0, tasks=0, variants=0, U0.4/U0.6 intervals=0/0

## Completed screening

- Complete taskset-E0 instances: 30
- Complete production analyses: 150
- The original 70/350 full grid was paused after 20/100 and replaced by the user-directed adaptive gate.
- Five remaining E0 values each stopped after two U=0.4 tasksets because no local-only closure appeared.

## Cell outcomes

- U0.4-E0=0: A_NO_ENVELOPE_STRICTNESS; scope=FULL_FIVE; tasksets=5; strict-envelope=0; closure=0; strict-response=0; gain=0
- U0.4-E0=1: B_ENVELOPE_STRICT_NO_LOCAL_ONLY_CLOSURE; scope=ADAPTIVE_GATE_TWO; tasksets=2; strict-envelope=818; closure=0; strict-response=0; gain=0
- U0.4-E0=1360181973072000001/16000000000000000000: B_ENVELOPE_STRICT_NO_LOCAL_ONLY_CLOSURE; scope=FULL_FIVE; tasksets=5; strict-envelope=408; closure=0; strict-response=0; gain=0
- U0.4-E0=178487996829/2000000000000: B_ENVELOPE_STRICT_NO_LOCAL_ONLY_CLOSURE; scope=ADAPTIVE_GATE_TWO; tasksets=2; strict-envelope=159; closure=0; strict-response=0; gain=0
- U0.4-E0=21473099401200000281/200000000000000000000: B_ENVELOPE_STRICT_NO_LOCAL_ONLY_CLOSURE; scope=ADAPTIVE_GATE_TWO; tasksets=2; strict-envelope=159; closure=0; strict-response=0; gain=0
- U0.4-E0=225897744831/2000000000000: B_ENVELOPE_STRICT_NO_LOCAL_ONLY_CLOSURE; scope=ADAPTIVE_GATE_TWO; tasksets=2; strict-envelope=439; closure=0; strict-response=0; gain=0
- U0.4-E0=43409021832000000987/400000000000000000000: B_ENVELOPE_STRICT_NO_LOCAL_ONLY_CLOSURE; scope=ADAPTIVE_GATE_TWO; tasksets=2; strict-envelope=159; closure=0; strict-response=0; gain=0
- U0.6-E0=0: A_NO_ENVELOPE_STRICTNESS; scope=FULL_FIVE; tasksets=5; strict-envelope=0; closure=0; strict-response=0; gain=0
- U0.6-E0=1: NOT_RUN_ADAPTIVE_GATE; scope=NOT_RUN_ADAPTIVE_GATE; tasksets=0; strict-envelope=0; closure=0; strict-response=0; gain=0
- U0.6-E0=1360181973072000001/16000000000000000000: A_NO_ENVELOPE_STRICTNESS; scope=FULL_FIVE; tasksets=5; strict-envelope=0; closure=0; strict-response=0; gain=0
- U0.6-E0=178487996829/2000000000000: NOT_RUN_ADAPTIVE_GATE; scope=NOT_RUN_ADAPTIVE_GATE; tasksets=0; strict-envelope=0; closure=0; strict-response=0; gain=0
- U0.6-E0=21473099401200000281/200000000000000000000: NOT_RUN_ADAPTIVE_GATE; scope=NOT_RUN_ADAPTIVE_GATE; tasksets=0; strict-envelope=0; closure=0; strict-response=0; gain=0
- U0.6-E0=225897744831/2000000000000: NOT_RUN_ADAPTIVE_GATE; scope=NOT_RUN_ADAPTIVE_GATE; tasksets=0; strict-envelope=0; closure=0; strict-response=0; gain=0
- U0.6-E0=43409021832000000987/400000000000000000000: NOT_RUN_ADAPTIVE_GATE; scope=NOT_RUN_ADAPTIVE_GATE; tasksets=0; strict-envelope=0; closure=0; strict-response=0; gain=0

## Tightness and timeout

- Strict envelope accesses: 2142 / 13942
- Predicted energy-separation hits on new tasksets: 0
- Local-only closures: 0
- Strict response tasks: 0
- Certification gains: 0
- 60s timeouts / 90s retries / timeout at 90s: 50 / 50 / 49

## Dominance

- Deadline relation: 0 violations
- Fixed-CW relation: 0 violations
- Recursive relation: 0 violations
- Pointwise envelope violations: 0

## Decision

No local-only closure, strict response, or certification gain was observed. Parameter identification therefore failed without implying that local refinement is ineffective.
The strict envelope points did not cross the energy condition on the newly generated paired tasksets, so no earliest closure/candidate position changed.
The next pilot may change exactly one structural dimension: constrained-deadline D/T distribution.
There is not yet evidence to freeze a strict-response region or design the CORE-1/CORE-2 formal experiment grid.
