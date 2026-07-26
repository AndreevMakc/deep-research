import unittest
import uuid

from app.operations import render_obsidian_note


class OperationsTests(unittest.TestCase):
    def test_obsidian_note_contains_only_supplied_claims(
        self,
    ) -> None:
        run_id = uuid.uuid4()
        note = render_obsidian_note(
            run_id=run_id,
            question="Что проверено?",
            claims=[
                {
                    "claim_id": "claim-approved",
                    "text": "Одобренное утверждение",
                    "verdict": "supported",
                    "confidence": 0.9,
                    "source_url": "https://example.com",
                    "source_snapshot_id": "snapshot-1",
                    "evidence_quote": "Точная цитата",
                    "review_status": "approved",
                }
            ],
        )

        self.assertIn("claim-approved", note)
        self.assertIn("Точная цитата", note)
        self.assertIn("review_status: approved", note)
        self.assertNotIn("pending", note)

    def test_obsidian_note_is_valid_when_no_claims(
        self,
    ) -> None:
        note = render_obsidian_note(
            run_id=uuid.uuid4(),
            question="Пустой отчёт",
            claims=[],
        )
        self.assertIn(
            "Нет одобренных claims",
            note,
        )


if __name__ == "__main__":
    unittest.main()
