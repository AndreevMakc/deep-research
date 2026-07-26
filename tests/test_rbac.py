import unittest
from unittest.mock import MagicMock

from app.db.models import ReviewerIdentity, ReviewerRole
from app.rbac import authorize


class RbacTests(unittest.TestCase):
    def test_reviewer_can_review_but_not_publish(
        self,
    ) -> None:
        session = MagicMock()
        session.scalar.return_value = ReviewerIdentity(
            subject="reviewer",
            display_name="Reviewer",
            role=ReviewerRole.REVIEWER,
            active=True,
        )

        identity = authorize(
            session,
            "reviewer",
            "review_claim",
        )
        self.assertEqual(identity.subject, "reviewer")

        with self.assertRaises(PermissionError):
            authorize(session, "reviewer", "publish")

    def test_disabled_identity_is_rejected(self) -> None:
        session = MagicMock()
        session.scalar.return_value = ReviewerIdentity(
            subject="disabled",
            display_name="Disabled",
            role=ReviewerRole.ADMIN,
            active=False,
        )

        with self.assertRaises(PermissionError):
            authorize(session, "disabled", "view")

    def test_unknown_identity_is_rejected(self) -> None:
        session = MagicMock()
        session.scalar.return_value = None

        with self.assertRaises(PermissionError):
            authorize(session, "unknown", "view")


if __name__ == "__main__":
    unittest.main()
