"""
Rebuild the active semantic-memory collection from all episodes in SQLite.

The GUI does this by itself when the memory model is switched (P126); this script is the manual
way. Must run as a user that can read state/system_master.key (personal episodes are encrypted).

Usage:
  sudo -u kaare PYTHONPATH=/kaare venv/bin/python scripts/reindex_episodes.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kaare_core.memory.memory_embed_models import active_model
from kaare_core.memory.semantic_reindex import reindex_all


async def main() -> None:
    model = active_model()
    print(f"Reindexing all episodes with '{model['key']}' into '{model['collection']}'...")
    result = await reindex_all(model)
    print(f"Done: {result}")


if __name__ == "__main__":
    asyncio.run(main())
