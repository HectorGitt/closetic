#!/usr/bin/env python3
"""
Database migration script to add gender and country fields to users table
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.models import engine


def run_migration():
    """Add gender and country columns to users table"""

    # Migration SQL
    migration_sql = [
        "ALTER TABLE users ADD COLUMN gender VARCHAR(20);",
        "ALTER TABLE users ADD COLUMN country VARCHAR(100);",
    ]

    try:
        with engine.connect() as connection:
            for sql in migration_sql:
                try:
                    print(f"Executing: {sql}")
                    connection.execute(text(sql))
                    print("✓ Success")
                except Exception as e:
                    if (
                        "duplicate column name" in str(e).lower()
                        or "already exists" in str(e).lower()
                    ):
                        print(f"✓ Column already exists, skipping: {sql}")
                    else:
                        print(f"✗ Error: {e}")
                        raise

            connection.commit()
            print("\n✅ Migration completed successfully!")
            print("Added columns:")
            print("- gender VARCHAR(20) - For user's gender identity")
            print("- country VARCHAR(100) - For user's country/region")

    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        return False

    return True


if __name__ == "__main__":
    print("🚀 Starting migration to add gender and country fields...")
    success = run_migration()
    if success:
        print("\n🎉 Migration completed successfully!")
    else:
        print("\n💥 Migration failed!")
        sys.exit(1)
