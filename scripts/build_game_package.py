"""Export a game.json project, all declared assets and SQLite snapshots for every client."""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.game_package import build

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    manifest = build(args.source, args.output)
    print("Exported", manifest["title"], manifest["version"], manifest["revision"])
