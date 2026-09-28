import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def test_initial_migration_up_and_down(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory, "migration.sqlite3")
            config = Config(str(ROOT / "alembic.ini"))
            with patch.dict(os.environ, {"TRADING_PLATFORM_DATABASE_URL": f"sqlite:///{database}"}):
                command.upgrade(config, "head")
                engine = create_engine(f"sqlite:///{database}")
                table_names = set(inspect(engine).get_table_names())
                self.assertTrue(
                    {
                        "accounts",
                        "symbols",
                        "strategy_versions",
                        "signals",
                        "risk_reservations",
                        "orders",
                        "positions",
                        "trades",
                        "audit_events",
                        "backtests",
                        "optimization_runs",
                    }.issubset(table_names)
                )
                mapping_columns = {
                    column["name"]: column
                    for column in inspect(engine).get_columns("symbol_mappings")
                }
                self.assertFalse(mapping_columns["account_id"]["nullable"])
                position_checks = {
                    constraint["name"]
                    for constraint in inspect(engine).get_check_constraints("positions")
                }
                self.assertIn("ck_position_volume_positive", position_checks)
                self.assertIn("ck_position_direction", position_checks)
                command.downgrade(config, "base")
                self.assertEqual(inspect(engine).get_table_names(), ["alembic_version"])
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
