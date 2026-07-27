import unittest

from app.db.models import ApiIdentity, ApiRole
from app.multitenancy import (
    authorize_api,
    hash_api_token,
    hash_password,
    reviewer_subject,
    verify_password,
)
from app.webhooks import signature, validate_webhook_url


class MultitenancyTests(unittest.TestCase):
    def test_hashes_tokens_deterministically(self) -> None:
        first = hash_api_token("token")
        second = hash_api_token("token")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertNotIn("token", first)

    def test_api_role_permissions(self) -> None:
        identity = ApiIdentity(
            subject="reviewer",
            role=ApiRole.REVIEWER,
            token_hash="a" * 64,
            active=True,
        )
        authorize_api(identity, "review_claim")

        with self.assertRaises(PermissionError):
            authorize_api(identity, "create_run")

    def test_password_hash_is_salted_and_verifiable(
        self,
    ) -> None:
        first = hash_password("correct horse battery staple")
        second = hash_password(
            "correct horse battery staple"
        )

        self.assertNotEqual(first, second)
        self.assertTrue(
            verify_password(
                "correct horse battery staple",
                first,
            )
        )
        self.assertFalse(
            verify_password("wrong password", first)
        )
        self.assertFalse(
            verify_password(
                "correct horse battery staple",
                "not-a-valid-hash",
            )
        )

    def test_provenance_requires_analyst_role(
        self,
    ) -> None:
        researcher = ApiIdentity(
            subject="researcher",
            role=ApiRole.RESEARCHER,
            token_hash="a" * 64,
            active=True,
        )
        reviewer = ApiIdentity(
            subject="reviewer",
            role=ApiRole.REVIEWER,
            token_hash="b" * 64,
            active=True,
        )

        with self.assertRaises(PermissionError):
            authorize_api(
                researcher,
                "view_provenance",
            )

        authorize_api(reviewer, "view_provenance")

    def test_reviewer_subject_is_tenant_scoped(self) -> None:
        self.assertNotEqual(
            reviewer_subject("tenant-a", "alice"),
            reviewer_subject("tenant-b", "alice"),
        )

    def test_webhook_signature_is_stable(self) -> None:
        value = signature("secret", b'{"event":"done"}')
        self.assertTrue(value.startswith("sha256="))
        self.assertEqual(
            value,
            signature("secret", b'{"event":"done"}'),
        )
        self.assertNotEqual(
            value,
            signature("other", b'{"event":"done"}'),
        )

    def test_webhook_rejects_private_target(self) -> None:
        with self.assertRaises(ValueError):
            validate_webhook_url(
                "http://127.0.0.1/internal"
            )


if __name__ == "__main__":
    unittest.main()
