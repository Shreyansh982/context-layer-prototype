"""Run a complete, versioned Excel-to-canonical-fact synchronization."""

import argparse
import json

from app.bootstrap import run_migrations
from app.services.enterprise_sync import enterprise_sync_service


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Reprocess an unchanged workbook snapshot")
    args = parser.parse_args()
    run_migrations()
    print(json.dumps(enterprise_sync_service.sync(force=args.force), indent=2))
