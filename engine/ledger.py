"""Tamper-evident audit ledger.

Append-only JSONL where every entry carries the SHA-256 of the previous
entry, forming a hash chain. `AuditLedger.verify()` re-walks the chain and
reports the first broken link editing, deleting or reordering any entry
after the fact is detectable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

GENESIS_HASH = "0" * 64


def _entry_hash(entry: dict) -> str:
    """Hash of the canonical form of an entry, excluding its own `hash` field."""
    material = {k: v for k, v in entry.items() if k != "hash"}
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class AuditLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._prev_hash = GENESIS_HASH
        self._fh = self.path.open("w", encoding="utf-8")

    def append(self, event: str, *, case_id: Optional[str] = None,
               at: Optional[datetime] = None, actor: str = "sanjeevani",
               payload: Optional[dict] = None) -> dict:
        entry = {
            "seq": self._seq,
            "at": at.isoformat() if at else None,
            "event": event,
            "case_id": case_id,
            "actor": actor,
            "payload": payload or {},
            "prev_hash": self._prev_hash,
        }
        entry["hash"] = _entry_hash(entry)
        self._fh.write(json.dumps(entry, separators=(",", ":"), default=str) + "\n")
        self._fh.flush()
        self._prev_hash = entry["hash"]
        self._seq += 1
        return entry

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    @staticmethod
    def read(path: Path) -> Iterator[dict]:
        with Path(path).open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    @staticmethod
    def verify(path: Path) -> tuple[bool, str]:
        """Walk the chain. Returns (ok, message)."""
        prev = GENESIS_HASH
        count = 0
        for entry in AuditLedger.read(path):
            if entry.get("prev_hash") != prev:
                return False, f"chain broken at seq={entry.get('seq')}: prev_hash mismatch"
            if _entry_hash(entry) != entry.get("hash"):
                return False, f"chain broken at seq={entry.get('seq')}: entry hash mismatch"
            if entry.get("seq") != count:
                return False, f"sequence gap at seq={entry.get('seq')} (expected {count})"
            prev = entry["hash"]
            count += 1
        if count == 0:
            return False, "ledger is empty"
        return True, f"intact: {count} entries, head {prev[:16]}…"
