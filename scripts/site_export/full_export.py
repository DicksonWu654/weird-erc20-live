"""Shared publication readers extracted from the measurement exporter."""
import csv, math
from pathlib import Path
from typing import Any

def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))

def compact_number(value: Any) -> Any:
    if value is None or value == "":
        return None
    try:
        n = float(value)
        if not math.isfinite(n):
            return None
        if n.is_integer():
            return int(n)
        return round(n, 8)
    except (TypeError, ValueError):
        return value
