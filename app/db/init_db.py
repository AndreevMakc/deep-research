from app.db.migrate import upgrade_database


def initialize_application_tables() -> None:
    upgrade_database()


if __name__ == "__main__":
    initialize_application_tables()
    print("Application database migrations applied")
