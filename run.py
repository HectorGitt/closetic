#!/usr/bin/env python3
"""
Fashion Check Application Startup Script
"""

import uvicorn
import os
import sys
from pathlib import Path


def main():
    """Start the Fashion Check application"""

    # Add the current directory to Python path
    current_dir = Path(__file__).parent
    sys.path.insert(0, str(current_dir))

    # Create necessary directories
    os.makedirs("static/uploads", exist_ok=True)
    os.makedirs("templates", exist_ok=True)

    # Check for .env file
    env_file = current_dir / ".env"
    if not env_file.exists():
        print("⚠️  Warning: .env file not found!")
        print("📝 Please copy .env.example to .env and add your OpenAI API key")
        print("🔑 OPENAI_API_KEY=your_api_key_here")
        print()

    # Start the application
    print("🚀 Starting Fashion Check Application...")
    print("📱 Features available:")
    print("   • Upload photo analysis")
    print("   • Live camera analysis")
    print("   • Personalized style recommendations")
    print("   • User profile management")
    print("   • Admin analytics dashboard")
    print()
    print("🌐 Application will be available at: http://localhost:8000")
    print("📚 API documentation at: http://localhost:8000/docs")
    print()

    try:
        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=8000,
            reload=True,
            log_level="info",
            reload_dirs=["app", "templates", "static"],
        )
    except KeyboardInterrupt:
        print("\n👋 Fashion Check application stopped.")
    except Exception as e:
        print(f"❌ Error starting application: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
