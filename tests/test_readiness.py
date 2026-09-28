from __future__ import annotations

import unittest
from dataclasses import fields

from trading_platform.readiness import ReadinessEvidence, review_readiness


class ReadinessTests(unittest.TestCase):
    def test_default_report_is_no_go_with_every_evidence_gate_blocking(self) -> None:
        report = review_readiness()
        self.assertEqual(report.status, "NOT_READY")
        self.assertFalse(report.live_mode_available)
        self.assertEqual(len(report.blockers), len(fields(ReadinessEvidence)))

    def test_completed_evidence_only_allows_a_separate_review(self) -> None:
        evidence = ReadinessEvidence(**{item.name: True for item in fields(ReadinessEvidence)})
        report = review_readiness(evidence)
        self.assertEqual(report.status, "READY_FOR_SEPARATE_REVIEW")
        self.assertFalse(report.live_mode_available)


if __name__ == "__main__":
    unittest.main()
