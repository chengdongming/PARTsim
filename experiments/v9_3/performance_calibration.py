"""Result-independent, Q-only calibration selector for B4."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
from statistics import median
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .performance_config import CAL_UTILIZATIONS, EXTENDED_ETAS, INITIAL_ETAS, INITIAL_KAPPAS, PRIMARY_SCHEDULERS


SELECTION_RULE_VERSION = "ASAP_BLOCK_V9_3_B4_CAL_Q_ONLY_V1"


@dataclass(frozen=True)
class CalibrationDecision:
    status: str
    extension_branch: str
    kappa_star: Optional[str]
    eta_low: Optional[str]
    eta_transition: Optional[str]
    eta_high: Optional[str]
    q_values: Tuple[Mapping[str, Any], ...]
    transition_scores: Tuple[Mapping[str, Any], ...]
    requested_extension_etas: Tuple[str, ...] = tuple()

    def document(self) -> Dict[str, Any]:
        return {"selection_rule_version": SELECTION_RULE_VERSION, **asdict(self)}


def calibration_q_values(rows: Iterable[Mapping[str, Any]]) -> Tuple[Mapping[str, Any], ...]:
    allowed = {"kappa", "eta", "u_norm", "scheduler_id", "taskset_id", "observed_pass"}
    grouped: Dict[tuple, Dict[str, Dict[str, bool]]] = {}
    for raw in rows:
        row = {key: raw[key] for key in allowed if key in raw}
        if set(row) != allowed:
            raise ValueError("CAL row lacks a Q-only field")
        scheduler = str(row["scheduler_id"])
        if scheduler not in PRIMARY_SCHEDULERS:
            raise ValueError("CAL contains a non-primary scheduler")
        key = (str(row["kappa"]), str(row["eta"]), str(row["u_norm"]))
        taskset_id = str(row["taskset_id"])
        scheduler_rows = grouped.setdefault(key, {}).setdefault(scheduler, {})
        if taskset_id in scheduler_rows:
            raise ValueError("CAL cell contains a duplicate taskset/scheduler result")
        scheduler_rows[taskset_id] = bool(row["observed_pass"])
    values = []
    for (kappa, eta, u_norm), schedulers in sorted(
        grouped.items(), key=lambda item: tuple(Fraction(value) for value in item[0])
    ):
        if tuple(schedulers) != PRIMARY_SCHEDULERS and set(schedulers) != set(PRIMARY_SCHEDULERS):
            raise ValueError("CAL cell lacks the frozen five scheduler set")
        ratios = []
        taskset_sets = []
        for scheduler in PRIMARY_SCHEDULERS:
            observations = schedulers[scheduler]
            if not observations:
                raise ValueError("CAL scheduler cell is empty")
            taskset_sets.append(set(observations))
            ratios.append(sum(observations.values()) / len(observations))
        if any(tasksets != taskset_sets[0] for tasksets in taskset_sets[1:]):
            raise ValueError("CAL cell is not completely paired across schedulers")
        values.append({
            "kappa": kappa, "eta": eta, "u_norm": u_norm,
            "Q": median(ratios), "scheduler_pass_ratios": ratios,
            "tasksets_per_scheduler": len(taskset_sets[0]),
        })
    return tuple(values)


def _select_transition(q_values: Sequence[Mapping[str, Any]]) -> tuple:
    by_cell = {}
    for row in q_values:
        by_cell.setdefault((str(row["kappa"]), str(row["eta"])), {})[str(row["u_norm"])] = float(row["Q"])
    scores = []
    for (kappa, eta), values in by_cell.items():
        if set(values) != set(CAL_UTILIZATIONS):
            continue
        n_transition = sum(0.2 <= values[u] <= 0.8 for u in CAL_UTILIZATIONS)
        score = {
            "kappa": kappa, "eta": eta, "N_T": n_transition,
            "sum_abs_Q_minus_half": sum(abs(values[u] - 0.5) for u in CAL_UTILIZATIONS),
            "abs_eta_minus_one": float(abs(Fraction(eta) - 1)),
        }
        scores.append(score)
    legal = [score for score in scores if score["N_T"] >= 2]
    if not legal:
        return None, tuple(scores)
    legal.sort(key=lambda score: (
        -score["N_T"], score["sum_abs_Q_minus_half"], score["abs_eta_minus_one"], Fraction(score["kappa"]),
    ))
    return legal[0], tuple(scores)


def select_calibration(rows: Iterable[Mapping[str, Any]], *, extension_already_used: bool = False) -> CalibrationDecision:
    q_values = calibration_q_values(rows)
    transition, scores = _select_transition(q_values)
    available_etas = {str(row["eta"]) for row in q_values}
    if transition is None:
        if not extension_already_used and available_etas == set(INITIAL_ETAS):
            return CalibrationDecision(
                "EXTENSION_REQUIRED", "B", None, None, None, None,
                q_values, scores, ("1/4", "2"),
            )
        return CalibrationDecision("STOP_NO_THREE_CONDITIONS", "B", None, None, None, None, q_values, scores)
    kappa = str(transition["kappa"])
    eta_transition = str(transition["eta"])
    midpoint = "1/2"
    lookup = {
        (str(row["kappa"]), str(row["eta"]), str(row["u_norm"])): float(row["Q"])
        for row in q_values
    }
    low_candidates = sorted(
        (Fraction(eta), eta) for eta in available_etas
        if Fraction(eta) < Fraction(eta_transition)
        and lookup.get((kappa, eta, midpoint), 1.0) <= 0.2
    )
    high_candidates = sorted(
        (Fraction(eta), eta) for eta in available_etas
        if Fraction(eta) > Fraction(eta_transition)
        and lookup.get((kappa, eta, midpoint), 0.0) >= 0.8
    )
    eta_low = low_candidates[-1][1] if low_candidates else None
    eta_high = high_candidates[0][1] if high_candidates else None
    if eta_low is not None and eta_high is not None:
        return CalibrationDecision("SELECTED", "NONE", kappa, eta_low, eta_transition, eta_high, q_values, scores)
    if not extension_already_used and available_etas == set(INITIAL_ETAS):
        requested = tuple(
            eta for eta, missing in (("1/4", eta_low is None), ("2", eta_high is None)) if missing
        )
        return CalibrationDecision(
            "EXTENSION_REQUIRED", "A", kappa, eta_low, eta_transition, eta_high,
            q_values, scores, requested,
        )
    return CalibrationDecision("STOP_NO_THREE_CONDITIONS", "A", kappa, eta_low, eta_transition, eta_high, q_values, scores)


def confirm_30s(selection: CalibrationDecision, rows_30s: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    if selection.status != "SELECTED":
        raise ValueError("30-second confirmation requires a complete provisional selection")
    q_values = calibration_q_values(rows_30s)
    lookup = {(row["kappa"], row["eta"], row["u_norm"]): row["Q"] for row in q_values}
    kappa = selection.kappa_star
    low_ok = lookup[(kappa, selection.eta_low, "1/2")] <= 0.2
    transition_count = sum(
        0.2 <= lookup[(kappa, selection.eta_transition, u_value)] <= 0.8
        for u_value in CAL_UTILIZATIONS
    )
    high_ok = lookup[(kappa, selection.eta_high, "1/2")] >= 0.8
    return {
        "confirmed": low_ok and transition_count >= 2 and high_ok,
        "low_confirmed": low_ok, "transition_N_T": transition_count,
        "high_confirmed": high_ok, "q_values": q_values,
        "fallback_full_30s_grid_required": not (low_ok and transition_count >= 2 and high_ok),
    }


def resolve_30s_confirmation(
    selection: CalibrationDecision, confirmation_rows: Iterable[Mapping[str, Any]], *,
    full_grid_rows: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Apply the single preregistered full-grid 30-second fallback."""

    confirmation_rows = list(confirmation_rows)
    confirmation = confirm_30s(selection, confirmation_rows)
    if confirmation["confirmed"]:
        return {
            "status": "CONFIRMED", "selection": selection,
            "confirmation": confirmation, "fallback_full_30s_grid_used": False,
        }
    if full_grid_rows is None:
        return {
            "status": "FULL_30S_GRID_REQUIRED", "selection": selection,
            "confirmation": confirmation, "fallback_full_30s_grid_used": False,
        }
    fallback = select_calibration(full_grid_rows, extension_already_used=True)
    if fallback.status != "SELECTED":
        return {
            "status": "STOP_NO_THREE_CONDITIONS", "selection": fallback,
            "confirmation": confirmation, "fallback_full_30s_grid_used": True,
        }
    fallback_confirmation = confirm_30s(fallback, full_grid_rows)
    return {
        "status": "CONFIRMED" if fallback_confirmation["confirmed"] else "STOP_NO_THREE_CONDITIONS",
        "selection": fallback, "confirmation": fallback_confirmation,
        "fallback_full_30s_grid_used": True,
    }
