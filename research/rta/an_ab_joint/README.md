# Canonical AN/AB RTA snapshot

This directory freezes the two response-time analyses used by the current ASAP-BLOCK research line:

- AN: `AN_LEAST_JOINT_CERTIFICATE_V3`
- AB: `AB_SEQ_ALL_TASK_LEAST_JOINT_HT_V2`

The files are copied from the independently reviewed proof-chain package dated 2026-09-12. Core RTA mathematics is frozen here; new experiments should call these analyzers rather than edit them.

## Layout

- `an/`: AN v3 least joint certificate implementation and its frozen dependencies.
- `ab/`: AB H/T V2 least joint certificate implementation and its frozen dependencies.
- `proof/`: proof-chain review and method-specific proof notes.

## Model scope

The proofs assume discrete time, constrained deadlines, a shared no-overflow energy account, debit-before-harvest timing, a per-job release-bound `E(r_J) >= E0`, and an interval energy-service lower bound `beta` valid for every relevant start time.

The returned bounds are sufficient response-time upper bounds. The fixed point is the least certificate within the proposed sufficient predicate family; it is not claimed to equal the exact WCRT.
