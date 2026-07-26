import unittest

from app.db.models import ApiIdentity, ApiRole
from app.multitenancy import (
    authorize_api,
    hash_api_token,
    reviewer_subject,
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
