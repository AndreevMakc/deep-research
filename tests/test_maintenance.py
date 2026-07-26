import unittest

from sqlalchemy.engine import make_url

from app.maintenance import _connection_args


class MaintenanceTests(unittest.TestCase):
    def test_rejects_system_database(self) -> None:
        with self.assertRaises(RuntimeError):
            _connection_args(
                make_url(
                    "postgresql://user:pass@localhost/postgres"
                )
            )

    def test_builds_safe_postgres_arguments(self) -> None:
        arguments, environment = _connection_args(
            make_url(
                "postgresql://user:pass@db:5433/research"
            )
        )
        self.assertIn("research", arguments)
        self.assertEqual(environment["PGPASSWORD"], "pass")


if __name__ == "__main__":
    unittest.main()
