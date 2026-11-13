"""Compare two independently built sets of distribution files."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def compare(first: Path, second: Path) -> None:
    left = {p.name: p for p in first.iterdir() if p.is_file() and p.name != ".gitignore"}
    right = {p.name: p for p in second.iterdir() if p.is_file() and p.name != ".gitignore"}
    if set(left) != set(right) or len(left) != 2:
        raise ValueError("Builds must contain the same wheel and source distribution")
    if (
        sum(name.endswith(".whl") for name in left) != 1
        or sum(name.endswith(".tar.gz") for name in left) != 1
    ):
        raise ValueError("Builds must contain one wheel and one source distribution")
    for name in sorted(left):
        if (
            hashlib.sha256(left[name].read_bytes()).digest()
            != hashlib.sha256(right[name].read_bytes()).digest()
        ):
            raise ValueError(f"Build artifacts differ: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args()
    compare(args.first, args.second)
    print("Distribution builds are byte-for-byte reproducible")


if __name__ == "__main__":
    main()
