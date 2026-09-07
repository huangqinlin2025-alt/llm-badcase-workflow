import unittest

from app.domain.rules import RULE_SET_VERSION, evaluate_layered


DIMS = ["输入理解与采信逻辑", "内容质量", "差异化与角色分工", "输出格式合规", "安全红线"]


def case(
    side: str,
    values: list[float],
    *,
    pair: str = "pair-1",
    stage: str | None = None,
    hard_fails: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "case_id": pair,
        "side": side,
        "evaluation_version": "eval-v1",
        "scene": "购物神评",
        "scores": {"LLM": {"dims": DIMS, "values": values, "avg": sum(values) / len(values)}},
        "metric_profile": "shopping-review-paired-ab-v1",
        "source_label": "A" if side == "A" else "B",
    }
    if stage:
        payload["prompt_stage"] = stage
    if hard_fails:
        payload["machine_hard_fails"] = hard_fails
    return payload


class LayeredRulesTests(unittest.TestCase):
    def test_l1_has_precedence_and_requires_full_fact_review(self) -> None:
        decisions = evaluate_layered(
            [
                case(
                    "A",
                    [5.5, 5.0, 5.0, 5.0, 8.0],
                    stage="优化后 Prompt",
                    hard_fails=["format constraint violated"],
                ),
                case("B", [8.0, 8.0, 8.0, 8.0, 8.0], stage="优化前 Prompt"),
            ]
        )
        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertEqual(decision.severity, "P0")
        self.assertEqual(decision.evidence["metric_level"], "L1")
        self.assertEqual(decision.evidence["review_strategy"], "FULL_FACT_REVIEW")
        self.assertEqual(decision.evidence["value_grade"], "PENDING_AGGREGATION")
        self.assertEqual(decision.evidence["triggers"][0]["rule"], "L1_MACHINE_HARD_FAIL")
        self.assertEqual(decision.evidence["l1_llm_supporting_evidence"][0]["dimension"], "输出格式合规")

    def test_l1_uses_llm_fallback_only_when_machine_hard_rules_are_absent(self) -> None:
        fallback = evaluate_layered(
            [
                case("A", [8.0, 7.0, 7.0, 5.0, 9.0], stage="优化后 Prompt"),
                case("B", [8.0, 7.0, 7.0, 9.0, 9.0], stage="优化前 Prompt"),
            ]
        )[0]
        self.assertEqual(fallback.evidence["metric_level"], "L1")
        self.assertEqual(fallback.evidence["l1_trigger_source"], "LLM_FALLBACK")
        self.assertEqual(fallback.evidence["triggers"][0]["rule"], "L1_LLM_FALLBACK_SCREEN")

        machine_available = case(
            "A",
            [8.0, 7.0, 7.0, 5.0, 9.0],
            stage="优化后 Prompt",
        )
        machine_available["machine_hard_fails"] = []
        machine_available["machine_pass"] = True
        decisions = evaluate_layered(
            [machine_available, case("B", [8.0, 7.0, 7.0, 9.0, 9.0], stage="优化前 Prompt")]
        )
        self.assertEqual(decisions, [])

    def test_l2_uses_candidate_minus_baseline_and_skips_baseline_record(self) -> None:
        decisions = evaluate_layered(
            [
                case("A", [5.8, 7.5, 7.5, 9.0, 9.0], stage="优化后 Prompt"),
                case("B", [7.5, 7.5, 7.5, 9.0, 9.0], stage="优化前 Prompt"),
            ]
        )
        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertEqual(decision.side, "A")
        self.assertEqual(decision.severity, "P1")
        self.assertEqual(decision.evidence["metric_level"], "L2")
        trigger = decision.evidence["triggers"][0]
        self.assertEqual(trigger["dimension"], "输入理解与采信逻辑")
        self.assertEqual(trigger["delta"], -1.7)
        self.assertEqual(decision.candidate_key, "pair-1::A::eval-v1::layered-v1")
        self.assertEqual(decision.evidence["rule_set_version"], RULE_SET_VERSION)

    def test_l3_uses_relative_drop_and_excellence_line(self) -> None:
        decisions = evaluate_layered(
            [
                case("A", [8.0, 6.0, 6.5, 9.0, 9.0], stage="优化后 Prompt"),
                case("B", [8.0, 7.0, 7.5, 9.0, 9.0], stage="优化前 Prompt"),
            ]
        )
        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertEqual(decision.severity, "P2")
        self.assertEqual(decision.evidence["metric_level"], "L3")
        rules = {item["rule"] for item in decision.evidence["triggers"]}
        self.assertIn("L3_RELATIVE_DEGRADATION_PRIORITY", rules)
        self.assertIn("L3_BELOW_EXCELLENCE", rules)
        self.assertEqual(decision.evidence["review_strategy"], "SAMPLE_10_TO_20_PERCENT")


if __name__ == "__main__":
    unittest.main()
