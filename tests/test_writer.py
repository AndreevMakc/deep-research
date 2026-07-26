import unittest
from unittest.mock import patch

from app.agents.writer import (
    finalize_report,
    generate_writer_draft,
    render_report_markdown,
    validate_writer_draft,
)
from app.db.models import VerificationVerdict
from app.schemas.writer import (
    CitedStatement,
    ReportSection,
    WriterClaimEvidence,
    WriterDraft,
    WriterPacket,
)


def make_claim(
    claim_id: str,
    *,
    verdict: VerificationVerdict = (
        VerificationVerdict.SUPPORTED
    ),
    statement: str = (
        "The system processed 42 verified records."
    ),
) -> WriterClaimEvidence:
    return WriterClaimEvidence(
        claim_id=claim_id,
        statement=statement,
        evidence_quote=(
            "The system processed 42 verified records."
        ),
        scope="Documented test",
        verdict=verdict,
        confidence=0.9,
        verification_reason=(
            "The source directly supports the claim."
        ),
        source_snapshot_id=f"snapshot-{claim_id}",
        source_url=f"https://example.com/{claim_id}",
        source_title="Test source",
    )


def make_packet(
    *,
    accepted: list[WriterClaimEvidence] | None = None,
    rejected: list[WriterClaimEvidence] | None = None,
) -> WriterPacket:
    return WriterPacket(
        run_id="run-test",
        question="What did the system process?",
        accepted_claims=(
            accepted
            if accepted is not None
            else [make_claim("claim-1")]
        ),
        rejected_claims=rejected or [],
        known_unanswered_questions=[
            "Was the test independently replicated?"
        ],
    )


class WriterTests(unittest.TestCase):
    def test_generates_schema_constrained_draft(
        self,
    ) -> None:
        packet = make_packet()
        expected = WriterDraft(
            short_answer=[
                CitedStatement(
                    text=(
                        "The system processed "
                        "42 verified records."
                    ),
                    claim_ids=["claim-1"],
                )
            ]
        )

        class FakeStructuredModel:
            def invoke(self, _messages):
                return expected

        class FakeModel:
            def __init__(self):
                self.schema = None
                self.method = None

            def with_structured_output(
                self,
                schema,
                method,
            ):
                self.schema = schema
                self.method = method
                return FakeStructuredModel()

        model = FakeModel()

        with patch(
            "app.agents.writer.create_writer_model",
            return_value=model,
        ):
            result = generate_writer_draft(packet)

        self.assertEqual(result, expected)
        self.assertIs(model.schema, WriterDraft)
        self.assertEqual(model.method, "json_schema")

    def test_rejects_rejected_claim_in_main_report(
        self,
    ) -> None:
        packet = make_packet(
            rejected=[
                make_claim(
                    "claim-rejected",
                    verdict=(
                        VerificationVerdict.CONTRADICTED
                    ),
                )
            ]
        )
        draft = WriterDraft(
            short_answer=[
                CitedStatement(
                    text="The rejected claim is true.",
                    claim_ids=["claim-rejected"],
                )
            ],
            contradictions=[
                CitedStatement(
                    text="The claim was contradicted.",
                    claim_ids=["claim-rejected"],
                )
            ],
        )

        with self.assertRaisesRegex(
            ValueError,
            "rejected or unknown",
        ):
            validate_writer_draft(draft, packet)

    def test_rejects_unsupported_numeric_value(
        self,
    ) -> None:
        packet = make_packet()
        draft = WriterDraft(
            short_answer=[
                CitedStatement(
                    text=(
                        "The system processed "
                        "99 verified records."
                    ),
                    claim_ids=["claim-1"],
                )
            ]
        )

        with self.assertRaisesRegex(
            ValueError,
            "unsupported numeric",
        ):
            validate_writer_draft(draft, packet)

    def test_partial_claim_requires_qualification(
        self,
    ) -> None:
        packet = make_packet(
            accepted=[
                make_claim(
                    "claim-partial",
                    verdict=(
                        VerificationVerdict
                        .PARTIALLY_SUPPORTED
                    ),
                )
            ]
        )
        draft = WriterDraft(
            short_answer=[
                CitedStatement(
                    text=(
                        "The system processed "
                        "42 verified records."
                    ),
                    claim_ids=["claim-partial"],
                )
            ]
        )

        with self.assertRaisesRegex(
            ValueError,
            "no qualification",
        ):
            validate_writer_draft(draft, packet)

    def test_final_report_renders_inline_citation(
        self,
    ) -> None:
        packet = make_packet()
        statement = CitedStatement(
            text=(
                "The system processed 42 verified records."
            ),
            claim_ids=["claim-1"],
        )
        draft = WriterDraft(
            short_answer=[statement],
            sections=[
                ReportSection(
                    heading="Evidence",
                    statements=[statement],
                )
            ],
        )

        validate_writer_draft(draft, packet)
        report = finalize_report(draft, packet)
        markdown = render_report_markdown(report)

        self.assertEqual(report.overall_confidence, 0.9)
        self.assertEqual(len(report.sources), 1)
        self.assertIn(
            "[C1](https://example.com/claim-1)",
            markdown,
        )
        self.assertIn(
            "source_snapshot_id=snapshot-claim-1",
            markdown,
        )

    def test_no_accepted_claims_skips_model(
        self,
    ) -> None:
        packet = make_packet(accepted=[])

        with patch(
            "app.agents.writer.create_writer_model",
        ) as model_factory:
            draft = generate_writer_draft(packet)

        model_factory.assert_not_called()
        self.assertEqual(draft.short_answer, [])
        self.assertTrue(draft.limitations)


if __name__ == "__main__":
    unittest.main()
