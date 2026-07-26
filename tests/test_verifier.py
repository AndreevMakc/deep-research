import hashlib
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.agents.verifier import (
    verify_claim_record,
    verify_claims,
)
from app.db.models import (
    Claim,
    Source,
    SourceSnapshot,
    VerificationVerdict,
)
from app.schemas.verification import VerificationResult


SOURCE_URL = "https://example.com/verifier-source"
SOURCE_TEXT = (
    "Verifier source\n"
    "The system processed 42 records in the documented test."
)
EVIDENCE_QUOTE = (
    "The system processed 42 records in the documented test."
)


def make_claim(
    *,
    text: str = "The system processed 42 records.",
    evidence_quote: str = EVIDENCE_QUOTE,
    quote_start: int | None = None,
) -> tuple[Claim, tempfile.TemporaryDirectory]:
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "source.txt"
    path.write_text(SOURCE_TEXT, encoding="utf-8")
    source = Source(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        url=SOURCE_URL,
        canonical_url=SOURCE_URL,
    )
    snapshot = SourceSnapshot(
        id=uuid.uuid4(),
        source_id=source.id,
        run_id=source.run_id,
        final_url=SOURCE_URL,
        content_hash=hashlib.sha256(
            SOURCE_TEXT.encode("utf-8")
        ).hexdigest(),
        mime_type="text/plain",
        local_path=str(path),
        content_length=len(
            SOURCE_TEXT.encode("utf-8")
        ),
        metadata_json={},
    )
    snapshot.source = source
    actual_start = SOURCE_TEXT.index(EVIDENCE_QUOTE)
    claim = Claim(
        id=uuid.uuid4(),
        run_id=source.run_id,
        source_snapshot_id=snapshot.id,
        text=text,
        evidence_quote=evidence_quote,
        quote_start=(
            actual_start
            if quote_start is None
            else quote_start
        ),
        quote_end=(
            (
                actual_start
                if quote_start is None
                else quote_start
            )
            + len(evidence_quote)
        ),
        locator={},
        scope="Documented test only",
        created_by_agent="researcher-v1",
    )
    claim.source_snapshot = snapshot

    return claim, directory


class VerifierTests(unittest.TestCase):
    def test_supported_claim_uses_independent_model_result(
        self,
    ) -> None:
        claim, directory = make_claim()
        packets = []

        def fake_generate(packet):
            packets.append(packet)
            return VerificationResult(
                verdict=VerificationVerdict.SUPPORTED,
                confidence=0.93,
                reason=(
                    "Цитата прямо подтверждает заявленное "
                    "количество записей."
                ),
            )

        try:
            result = verify_claim_record(
                claim,
                generate_fn=fake_generate,
            )
        finally:
            directory.cleanup()

        self.assertEqual(
            result.verdict,
            VerificationVerdict.SUPPORTED,
        )
        self.assertEqual(len(packets), 1)
        self.assertEqual(
            packets[0].source_snapshot_id,
            str(claim.source_snapshot_id),
        )

    def test_citation_mismatch_skips_model(
        self,
    ) -> None:
        claim, directory = make_claim(quote_start=0)

        try:
            with patch(
                "app.agents.verifier."
                "generate_verification_result"
            ) as generate:
                result = verify_claim_record(claim)
        finally:
            directory.cleanup()

        self.assertEqual(
            result.verdict,
            VerificationVerdict.CITATION_MISMATCH,
        )
        generate.assert_not_called()

    def test_unconfirmed_number_skips_model(
        self,
    ) -> None:
        claim, directory = make_claim(
            text="The system processed 99 records."
        )

        try:
            with patch(
                "app.agents.verifier."
                "generate_verification_result"
            ) as generate:
                result = verify_claim_record(claim)
        finally:
            directory.cleanup()

        self.assertEqual(
            result.verdict,
            VerificationVerdict.INSUFFICIENT_EVIDENCE,
        )
        self.assertIn("99", result.reason)
        generate.assert_not_called()

    def test_one_failed_claim_does_not_stop_others(
        self,
    ) -> None:
        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        supported = VerificationResult(
            verdict=VerificationVerdict.SUPPORTED,
            confidence=0.9,
            reason="Источник прямо подтверждает утверждение.",
        )

        with (
            patch(
                "app.agents.verifier.verify_claim",
                side_effect=[
                    RuntimeError("model failed"),
                    supported,
                ],
            ),
            self.assertLogs(
                "app.agents.verifier",
                level="ERROR",
            ),
        ):
            results = verify_claims(
                [str(first_id), str(second_id)]
            )

        self.assertEqual(
            results[0]["error"]["message"],
            "model failed",
        )
        self.assertEqual(
            results[1]["verdict"],
            VerificationVerdict.SUPPORTED.value,
        )


if __name__ == "__main__":
    unittest.main()
