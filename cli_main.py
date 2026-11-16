"""
Entry point for the CLI.

Usage:
  python cli_main.py --help
  python cli_main.py prospect search --title "VP of Sales" --industry SaaS --limit 50
  python cli_main.py campaign create --name "Q1 SaaS Push" --goal "book discovery call" --value-prop "..."
  python cli_main.py style sync
  python cli_main.py inbox list
"""
import sys
from pathlib import Path

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from src.db.database import init_db
init_db()

from src.cli.commands import cli

if __name__ == "__main__":
    cli()
