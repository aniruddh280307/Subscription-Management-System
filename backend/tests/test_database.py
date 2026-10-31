from sqlalchemy import inspect, text
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.migration import MigrationContext

from app.database import engine


def test_database_connection():
    """Verify live Supabase PostgreSQL connectivity."""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1")).scalar()
        assert result == 1


def test_required_tables_exist():
    """Verify that all current application tables are present in Supabase."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    required_tables = [
        "users",
        "customers",
        "products",
        "subscription_plans",
        "subscriptions",
        "invoices",
        "invoice_items",
        "payments",
        "audit_logs",
        "product_variants",
        "quotation_templates",
        "quotation_template_lines",
        "quotations",
        "quotation_lines",
        "tax_rules",
        "discount_rules",
        "guardian_findings",
    ]
    for table in required_tables:
        assert table in tables, f"Expected table '{table}' was not found in database"


def test_alembic_current_revision():
    """Verify the commercial workflow migration is the current database head."""
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        current_rev = context.get_current_revision()
        assert current_rev == "1b0a8fa0a82d"
