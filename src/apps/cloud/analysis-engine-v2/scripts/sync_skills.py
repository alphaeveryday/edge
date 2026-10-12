"""Publish the two user-maintained skill documents into the worker package."""
import argparse
from pathlib import Path


NAMES = ('hypothesis-analysis-workflow', 'etf-hypothesis-analysis')


def sync(source, target):
    # Read every source first so a missing document cannot partially update the package.
    documents = {name: (source/name/'SKILL.md').read_bytes() for name in NAMES}
    if any(not content.strip() for content in documents.values()):
        raise ValueError('Skill documents must not be empty')
    for name, content in documents.items():
        path = target/name/'SKILL.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', required=True, type=Path)
    args = parser.parse_args()
    sync(args.source_dir, Path(__file__).parents[1]/'src/edge_analysis_v2/skills')
