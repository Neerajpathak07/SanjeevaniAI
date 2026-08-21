"""The audit trail must be tamper-evident, not just append-only."""

import json
import tempfile
import unittest
from pathlib import Path

from engine.ledger import AuditLedger


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "ledger.jsonl"

    def _write(self, n=5):
        ledger = AuditLedger(self.path)
        for i in range(n):
            ledger.append("TEST_EVENT", case_id=f"case_{i}", payload={"i": i})
        ledger.close()

    def test_intact_chain_verifies(self):
        self._write()
        ok, msg = AuditLedger.verify(self.path)
        self.assertTrue(ok, msg)
        self.assertIn("5 entries", msg)

    def test_edited_entry_is_detected(self):
        self._write()
        lines = self.path.read_text().splitlines()
        entry = json.loads(lines[2])
        entry["payload"]["i"] = 999  # cook the books
        lines[2] = json.dumps(entry, separators=(",", ":"))
        self.path.write_text("\n".join(lines) + "\n")
        ok, msg = AuditLedger.verify(self.path)
        self.assertFalse(ok)
        self.assertIn("seq=2", msg)

    def test_deleted_entry_is_detected(self):
        self._write()
        lines = self.path.read_text().splitlines()
        del lines[1]
        self.path.write_text("\n".join(lines) + "\n")
        ok, _ = AuditLedger.verify(self.path)
        self.assertFalse(ok)

    def test_reordered_entries_are_detected(self):
        self._write()
        lines = self.path.read_text().splitlines()
        lines[1], lines[2] = lines[2], lines[1]
        self.path.write_text("\n".join(lines) + "\n")
        ok, _ = AuditLedger.verify(self.path)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
