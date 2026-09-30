"""Create and version the Unity Catalog Delta metadata tables."""

from pathlib import Path

from race.config import Settings
from race.db import Database
from race.repository import now

ROOT = Path(__file__).resolve().parent.parent


def migrate(db: Database):
    """Apply the idempotent Delta DDL and record schema version 1."""
    script = (ROOT / "migrations" / "001_metadata.sql").read_text()
    namespace = f"`{db.settings.metadata_catalog}`.`{db.settings.metadata_schema}`"
    script = script.replace("{{namespace}}", namespace)
    statements = [statement.strip() for statement in script.split(";") if statement.strip()]
    with db.session() as session:
        for statement in statements:
            session.execute(statement)
        versions = db.table("schema_versions")
        if not session.execute(f"SELECT version FROM {versions} WHERE version=1"):
            session.execute(
                f"INSERT INTO {versions} (version, applied_at) VALUES (:version, :applied_at)",
                {"version": 1, "applied_at": now()},
            )


def main():
    settings = Settings()
    migrate(Database(settings))
    print(
        "Delta metadata initialized in "
        f"{settings.metadata_catalog}.{settings.metadata_schema}."
    )


if __name__ == "__main__":
    main()
