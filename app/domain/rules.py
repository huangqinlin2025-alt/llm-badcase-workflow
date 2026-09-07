"""Pure L1/L2/L3 Badcase classification rules."""
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping


RULE_SET_VERSION = "layered-v1"
L1_SCREEN_LINE = 6.0
L2_QUALIFICATION_LINE = 6.0
L2_DELTA_LINE = -1.5
L3_PRIORITY_DELTA = -0.5
L3_EXCELLENCE_LINE = 6.8
L3_OUTLIER_GAP = 2.0
L3_OUTLIER_MIN_SAMPLES = 10


@dataclass(frozen=True)
class RuleDecision:
    case_id: str
    side: str
    evaluation_version: str
    scene: str
    severity: str
    evidence: Mapping[str, object]

    @property
    def candidate_key(self) -> str:
        return f"{self.case_id}::{self.side}::{self.evaluation_version}::{RULE_SET_VERSION}"

    @property
    def content_hash(self) -> str:
        return sha256(
            json.dumps(self.evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def evaluate_layered(aligned_cases: list[dict[str, Any]]) -> list[RuleDecision]:
    """Evaluate candidates with L1 > L2 > L3 precedence and no external side effects."""
    by_pair = {
        (str(item["case_id"]), str(item["evaluation_version"])): item
        for item in aligned_cases
        if str(item.get("side")) == "B"
    }
    baselines = _l3_baselines(aligned_cases)
    decisions: list[RuleDecision] = []
    for item in aligned_cases:
        if item.get("prompt_stage") == "优化前 Prompt":
            continue
        decision = _evaluate_one(item, by_pair, baselines)
        if decision is not None:
            decisions.append(decision)
    return decisions


def _evaluate_one(
    item: dict[str, Any],
    baselines: Mapping[tuple[str, str, str], list[float]],
    l3_baselines: Mapping[tuple[str, str, str], list[float]],
) -> RuleDecision | None:
    score = _primary_score(item)
    if score is None:
        return None
    dims = [str(value) for value in score.get("dims", [])]
    values = [float(value) if value is not None else None for value in score.get("values", [])]
    counterpart = baselines.get((str(item["case_id"]), str(item["evaluation_version"])))
    counterpart_score = _primary_score(counterpart) if counterpart else None
    counterpart_values = _values_by_dim(counterpart_score)
    machine_l1_available = _has_machine_l1_evidence(item)
    layers: dict[str, list[dict[str, object]]] = {"L1": _machine_l1_triggers(item), "L2": [], "L3": []}
    l1_llm_screening: list[dict[str, object]] = []
    for dimension, value in zip(dims, values, strict=False):
        if value is None:
            continue
        level = _metric_level(dimension)
        baseline_value = counterpart_values.get(dimension)
        delta = round(value - baseline_value, 2) if baseline_value is not None else None
        detail = {"dimension": dimension, "candidate_score": value, "baseline_score": baseline_value, "delta": delta}
        if level == "L1" and value < L1_SCREEN_LINE:
            screen = {**detail, "rule": "L1_LLM_FALLBACK_SCREEN"}
            if machine_l1_available:
                l1_llm_screening.append({**screen, "rule": "L1_LLM_SUPPORTING_EVIDENCE"})
            else:
                layers["L1"].append(screen)
        elif level == "L2" and (value < L2_QUALIFICATION_LINE or (delta is not None and delta <= L2_DELTA_LINE)):
            layers["L2"].append({**detail, "rule": "L2_QUALIFICATION_OR_SIGNIFICANT_DELTA"})
        elif level == "L3":
            if delta is not None and delta < 0:
                rule = "L3_RELATIVE_DEGRADATION_PRIORITY" if delta <= L3_PRIORITY_DELTA else "L3_RELATIVE_DEGRADATION"
                layers["L3"].append({**detail, "rule": rule})
            l3_group = l3_baselines.get(_l3_group_key(item, dimension), [])
            if len(l3_group) >= L3_OUTLIER_MIN_SAMPLES:
                mean = sum(l3_group) / len(l3_group)
                if mean - value >= L3_OUTLIER_GAP:
                    layers["L3"].append({**detail, "rule": "L3_OUTLIER", "baseline_mean": round(mean, 2), "baseline_n": len(l3_group)})

    l3_values = [value for dimension, value in zip(dims, values, strict=False) if value is not None and _metric_level(dimension) == "L3"]
    if l3_values and sum(l3_values) / len(l3_values) < L3_EXCELLENCE_LINE:
        layers["L3"].append({"rule": "L3_BELOW_EXCELLENCE", "l3_average": round(sum(l3_values) / len(l3_values), 2), "excellence_line": L3_EXCELLENCE_LINE})

    disagreements = _evaluation_disagreements(item)
    level = next((candidate for candidate in ("L1", "L2", "L3") if layers[candidate]), None)
    if level is None:
        return None
    review = {"L1": "FULL_FACT_REVIEW", "L2": "SAMPLE_50_PERCENT", "L3": "SAMPLE_10_TO_20_PERCENT"}[level]
    severity = {"L1": "P0", "L2": "P1", "L3": "P2"}[level]
    evidence = {
        "rule_set_version": RULE_SET_VERSION,
        "metric_level": level,
        "assessment_relation": "llm_only",
        "value_grade": "PENDING_AGGREGATION",
        "review_strategy": review,
        "triggers": layers[level],
        "all_layer_triggers": {key: value for key, value in layers.items() if value},
        "l1_trigger_source": "MACHINE_HARD_RULE" if machine_l1_available else "LLM_FALLBACK",
        "l1_llm_supporting_evidence": l1_llm_screening,
        "evaluation_disagreements": disagreements,
        "source_label": item.get("source_label"),
        "prompt_stage": item.get("prompt_stage"),
        "metric_profile": item.get("metric_profile", "generic-v1"),
        "ab_evidence": item.get("ab_evidence", {}),
        "thresholds": {
            "l1_screen": L1_SCREEN_LINE,
            "l2_qualification": L2_QUALIFICATION_LINE,
            "l2_delta": L2_DELTA_LINE,
            "l3_priority_delta": L3_PRIORITY_DELTA,
            "l3_excellence": L3_EXCELLENCE_LINE,
            "l3_outlier_gap": L3_OUTLIER_GAP,
            "l3_outlier_min_samples": L3_OUTLIER_MIN_SAMPLES,
        },
    }
    return RuleDecision(
        case_id=str(item["case_id"]),
        side=str(item["side"]),
        evaluation_version=str(item["evaluation_version"]),
        scene=str(item.get("scene", "未分类")),
        severity=severity,
        evidence=evidence,
    )


def _has_machine_l1_evidence(item: Mapping[str, Any]) -> bool:
    return any(key in item for key in ("hard_fails", "machine_hard_fails", "machine_pass"))


def _machine_l1_triggers(item: Mapping[str, Any]) -> list[dict[str, object]]:
    hard_fails = item.get("hard_fails") or item.get("machine_hard_fails") or []
    if isinstance(hard_fails, str):
        hard_fails = [hard_fails] if hard_fails.strip() else []
    triggers = [
        {"rule": "L1_MACHINE_HARD_FAIL", "detail": str(value)}
        for value in hard_fails
        if str(value).strip()
    ]
    if item.get("machine_pass") is False:
        triggers.append({"rule": "L1_MACHINE_PASS_FALSE"})
    return triggers


def _primary_score(item: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not item:
        return None
    scores = item.get("scores", {})
    return scores.get("LLM") or scores.get("人工")


def _values_by_dim(score: Mapping[str, Any] | None) -> dict[str, float]:
    if not score:
        return {}
    return {
        str(dimension): float(value)
        for dimension, value in zip(score.get("dims", []), score.get("values", []), strict=False)
        if value is not None
    }


def _metric_level(dimension: str) -> str:
    normalized = dimension.lower()
    if "格式" in dimension or "安全" in dimension or "format" in normalized or "safety" in normalized:
        return "L1"
    if any(key in dimension for key in ("输入", "理解", "采信", "指令", "遵循", "场景", "适配", "一致")):
        return "L2"
    return "L3"


def _l3_group_key(item: Mapping[str, Any], dimension: str) -> tuple[str, str, str]:
    return (
        str(item.get("scene", "未分类")),
        str(item.get("prompt_stage") or item.get("source_label") or item.get("side", "unknown")),
        dimension,
    )


def _l3_baselines(aligned_cases: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[float]]:
    groups: dict[tuple[str, str, str], list[float]] = {}
    for item in aligned_cases:
        score = _primary_score(item)
        if score is None:
            continue
        for dimension, value in zip(score.get("dims", []), score.get("values", []), strict=False):
            if value is not None and _metric_level(str(dimension)) == "L3":
                groups.setdefault(_l3_group_key(item, str(dimension)), []).append(float(value))
    return groups


def _evaluation_disagreements(item: Mapping[str, Any]) -> list[dict[str, object]]:
    scores = item.get("scores", {})
    human = scores.get("人工")
    llm = scores.get("LLM")
    machine = scores.get("机检")
    disagreements: list[dict[str, object]] = []
    if human and llm:
        gap = abs(float(human.get("avg", 0)) - float(llm.get("avg", 0)))
        if gap >= 1.5:
            disagreements.append({"type": "HUMAN_LLM_GAP", "gap": round(gap, 2)})
    if machine and llm and float(machine.get("avg", 99)) <= 6:
        l1_values = [
            float(value)
            for dimension, value in zip(llm.get("dims", []), llm.get("values", []), strict=False)
            if value is not None and _metric_level(str(dimension)) == "L1"
        ]
        if l1_values and max(l1_values) >= 8:
            disagreements.append({"type": "MACHINE_LLM_L1_CONFLICT", "machine_average": machine.get("avg"), "llm_l1_max": max(l1_values)})
    return disagreements
