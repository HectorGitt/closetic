#!/usr/bin/env python3
"""
Database migration script to add PersonalStyleGuide table.
Run this script to create the new table for storing user style guides.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from decouple import config
import sys
import os


def run_migration():
    """Add PersonalStyleGuide table to the database"""

    # Database configuration
    DATABASE_URL = config("DATABASE_URL", default="sqlite:///./fashcheck.db")

    # Create engine
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    )

    # Create session
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        print("🔄 Starting database migration for PersonalStyleGuide table...")

        # Create PersonalStyleGuide table
        migration_query = """
        CREATE TABLE IF NOT EXISTS personal_style_guides (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            style_principles TEXT,
            color_palette TEXT,
            essential_pieces TEXT,
            shopping_priorities TEXT,
            styling_tips TEXT,
            preferences_snapshot TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME,
            is_active BOOLEAN DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """

        db.execute(text(migration_query))
        db.commit()
        print("✅ Created personal_style_guides table")

        # Create index for user_id
        index_query = """
        CREATE INDEX IF NOT EXISTS idx_style_guides_user_id 
        ON personal_style_guides(user_id)
        """

        db.execute(text(index_query))
        db.commit()
        print("✅ Created index on user_id")

        # Create index for is_active
        active_index_query = """
        CREATE INDEX IF NOT EXISTS idx_style_guides_user_active 
        ON personal_style_guides(user_id, is_active)
        """

        db.execute(text(active_index_query))
        db.commit()
        print("✅ Created composite index on user_id and is_active")

        print("✅ Migration completed successfully!")

        # Show summary
        result = db.execute(
            text("SELECT COUNT(*) as total_users FROM users")
        ).fetchone()
        print(
            f"📊 Ready to serve {result.total_users} users with personal style guides"
        )

    except Exception as e:
        print(f"❌ Migration failed: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_migration()
    print("\n🎉 PersonalStyleGuide table migration completed!")
    print("\n📚 New functionality:")
    print("   - Personal style guides are now saved to database")
    print("   - Each user can have one active style guide")
    print(
        "   - Style guides include principles, colors, essentials, priorities, and tips"
    )
    print("   - Previous style guides are retained for history")
