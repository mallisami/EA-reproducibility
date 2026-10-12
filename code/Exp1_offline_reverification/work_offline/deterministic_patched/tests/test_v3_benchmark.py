
import unittest, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEV=ROOT/"scenarios/dev"; HELD=ROOT/"scenarios/heldout"

def load(p): return json.loads(p.read_text(encoding="utf-8"))

class V3BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dev=[load(p) for p in sorted(DEV.glob("*.json"))]
        cls.held=[load(p) for p in sorted(HELD.glob("*.json"))]
        cls.all=cls.dev+cls.held

    def test_counts_and_split(self):
        self.assertEqual(len(self.dev),6)
        self.assertEqual(len(self.held),24)
        self.assertEqual(len(self.all),30)
        self.assertEqual(len({s["scenario_id"] for s in self.all}),30)

    def test_family_counts(self):
        counts={f:0 for f in ["F1","F2","F3","F4","F5"]}
        for s in self.all: counts[s["family"]]+=1
        self.assertEqual(counts,{"F1":6,"F2":6,"F3":6,"F4":6,"F5":6})

    def test_locked_dev_ids(self):
        self.assertEqual(
            {s["scenario_id"] for s in self.dev},
            {"F1.1","F2.1","F3.1","F4.1","F5.1","F5.5"}
        )

    def test_heldout_ids(self):
        expected={
            "F1.2","F1.3","F1.4","F1.5","F1.6",
            "F2.2","F2.3","F2.4","F2.5","F2.6",
            "F3.2","F3.3","F3.4","F3.5","F3.6",
            "F4.2","F4.3","F4.4","F4.5","F4.6",
            "F5.2","F5.3","F5.4","F5.6",
        }
        self.assertEqual({s["scenario_id"] for s in self.held},expected)

    def test_f56_negative_control(self):
        s=next(x for x in self.held if x["scenario_id"]=="F5.6")
        self.assertEqual(s["scenario_role"],"negative_exploitability_control")
        self.assertEqual(s["worlds"]["W2"]["expected_commit"],"EXECUTE")
        self.assertEqual(s["worlds"]["W2"]["control_semantics"],"adversarial_negative_control")

    def test_heldout_results_are_postfreeze_and_inputs_remain_frozen(self):
        result_files=sorted(p.name for p in (ROOT/"results/heldout").glob("*.json"))
        self.assertEqual(result_files,[
            "e10_incomplete_transition_model.json",
            "e11_telemetry_lag.json",
            "e1_oracle_vulnerability.json",
            "e3_delay_sensitivity.json",
            "e3_long_horizon.json",
            "e4_capability_sensitivity.json",
            "e5_defense_comparison.json",
        ])
        self.assertTrue((ROOT/"CONTROLLED_INPUTS_V8.sha256.json").is_file())
        self.assertTrue((ROOT/"configs/CONTROLLED_STUDY_PROTOCOL_V8.json").is_file())
        protocol=load(ROOT/"configs/CONTROLLED_STUDY_PROTOCOL_V8.json")
        self.assertEqual(protocol["status"],"RESULTS_LOCKED_DO_NOT_RERUN")

if __name__=="__main__": unittest.main()
