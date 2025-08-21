#!/usr/bin/env python3
"""
Database migration script to add pricing tier fields to the User table.
Run this script to update existing database with new pricing tier columns.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import sys
import os

# Add the app directory to the path so we can import models
sys.path.append(os.path.join(os.path.dirname(__file__), "app"))


def run_migration():
    """Add pricing tier fields to the User table"""

    # Database configuration
    DATABASE_URL = os.getenv("DATABASE_URL", default="sqlite:///./fashcheck.db")

    # Create engine
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    )

    # Create session
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        print("🔄 Starting database migration for pricing tiers...")

        # Add pricing tier columns to users table
        migration_queries = [
            """
            ALTER TABLE users 
            ADD COLUMN pricing_tier VARCHAR(20) DEFAULT 'free'
            """,
            """
            ALTER TABLE users 
            ADD COLUMN subscription_start_date DATETIME
            """,
            """
            ALTER TABLE users 
            ADD COLUMN subscription_end_date DATETIME
            """,
            """
            ALTER TABLE users 
            ADD COLUMN subscription_status VARCHAR(20) DEFAULT 'active'
            """,
        ]

        for query in migration_queries:
            try:
                db.execute(text(query))
                print(f"✅ Executed: {query.strip()[:50]}...")
            except Exception as e:
                if (
                    "duplicate column name" in str(e).lower()
                    or "already exists" in str(e).lower()
                ):
                    print(
                        f"⚠️  Column already exists, skipping: {query.strip()[:50]}..."
                    )
                else:
                    print(f"❌ Error executing query: {e}")
                    raise

        # Commit the changes
        db.commit()
        print("✅ Migration completed successfully!")

        # Update all existing users to have 'free' tier if they don't already have one
        update_query = """
        UPDATE users 
        SET pricing_tier = 'free', subscription_status = 'active' 
        WHERE pricing_tier IS NULL OR pricing_tier = ''
        """
        db.execute(text(update_query))
        db.commit()
        print("✅ Updated existing users to 'free' tier")

        # Show summary
        result = db.execute(
            text("SELECT COUNT(*) as total_users FROM users")
        ).fetchone()
        print(f"📊 Total users in database: {result.total_users}")

        result = db.execute(
            text("""
            SELECT pricing_tier, COUNT(*) as count 
            FROM users 
            GROUP BY pricing_tier
        """)
        ).fetchall()

        print("📊 Users by pricing tier:")
        for row in result:
            print(f"   {row.pricing_tier}: {row.count} users")

    except Exception as e:
        print(f"❌ Migration failed: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_migration()
    print(
        "\n🎉 Pricing tier migration completed! You can now use the new pricing endpoints."
    )
    print("\n📚 Available endpoints:")
    print("   GET  /users/pricing-tier          - Get user's current tier")
    print("   POST /users/pricing-tier/upgrade  - Upgrade user's tier")
    print("   GET  /users/pricing-tiers/all     - Get all available tiers")
    print(
        "   GET  /users/tier-limits/{action}  - Check tier limits for specific actions"
    )
