"""Plan usage column (2026-10-06): Claude limits come from the status line JSON saved in the state
dir, Codex/ChatGPT limits from the live usage endpoint or, failing that, the last ``rate_limits``
snapshot in the newest Codex session log. Missing sources mean no usage, never an error."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("AGENTSMON_STATE", tempfile.mkdtemp(prefix="agentsmon-test-state-"))
from agentsmon import usage   # noqa: E402  (state dir must be set before import)


class ClaudeUsage(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {"AGENTSMON_STATE": str(self.state)})
        self.env.start()
        usage._cache.clear()

    def tearDown(self):
        self.env.stop()

    def test_reads_status_line_json(self):
        (self.state / "usage").mkdir()
        (self.state / "usage" / "claude-status.json").write_text(json.dumps({"rate_limits": {
            "five_hour": {"used_percentage": 12.4, "resets_at": 1791321600},
            "seven_day": {"used_percentage": 18, "resets_at": 1791489600}}}))
        u = usage.for_vendor("anthropic")
        self.assertEqual(u["windows"], [
            {"label": "5h", "used_pct": 12, "resets_at": 1791321600},
            {"label": "7d", "used_pct": 18, "resets_at": 1791489600}])

    def test_missing_file_or_limits_is_none(self):
        self.assertIsNone(usage.claude())
        (self.state / "usage").mkdir()
        (self.state / "usage" / "claude-status.json").write_text(json.dumps({"model": {}}))
        self.assertIsNone(usage.claude())

    def test_unknown_vendor_is_none(self):
        self.assertIsNone(usage.for_vendor("google"))
        self.assertIsNone(usage.for_vendor(None))


class CodexUsage(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.patch = mock.patch.object(Path, "home", return_value=self.home)
        self.patch.start()
        usage._cache.clear()

    def tearDown(self):
        self.patch.stop()

    def test_falls_back_to_rollout_snapshot(self):
        d = self.home / ".codex/sessions/2026/10/06"
        d.mkdir(parents=True)
        ev = {"timestamp": "2026-10-06T11:36:32.355Z", "payload": {"type": "token_count", "rate_limits": {
            "primary": {"used_percent": 39.0, "window_minutes": 300, "resets_at": 1791302419},
            "secondary": {"used_percent": 40.0, "window_minutes": 10080, "resets_at": 1791752581}}}}
        (d / "rollout-x.jsonl").write_text('{"type":"other"}\n' + json.dumps(ev) + "\n")
        with mock.patch.object(usage, "_codex_live", return_value=None):
            u = usage.codex()
        self.assertEqual([(w["label"], w["used_pct"]) for w in u["windows"]], [("5h", 39), ("7d", 40)])
        self.assertEqual(u["source"], "Codex session log")

    def test_no_codex_at_all_is_none(self):
        self.assertIsNone(usage.codex())


if __name__ == "__main__":
    unittest.main()
