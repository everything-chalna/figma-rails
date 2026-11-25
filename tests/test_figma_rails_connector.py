import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "figma-rails"
RUNS = SKILL / "tests" / "connector-runs"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(ROOT))

import connector


def load(run, name):
    return json.loads((RUNS / run / name).read_text(encoding="utf-8"))


def build(run, **kw):
    return connector.build_result(load(run, "command.json"), load(run, "before.json"), load(run, "after.json"), **kw)


class ReadbackTest(unittest.TestCase):
    """Snapshots captured from the live connector run in a scratch file (2026-11-25)."""

    def test_live_create_matches_plan(self):
        out = build("cmd_create_live", reported=["2:2", "2:3", "2:4"])
        self.assertTrue(out["ok"], out["verification"])
        self.assertEqual([c["id"] for c in out["changes"]["created"]], ["2:2", "2:3", "2:4"])

    def test_self_report_that_disagrees_with_readback_fails(self):
        out = build("cmd_create_live", reported=["2:2"])
        self.assertFalse(out["ok"])
        self.assertIn("report-differs-from-readback", {u["kind"] for u in out["verification"]["unexpectedChanges"]})

    def test_hug_parent_resize_is_caught_even_though_the_code_reported_one_node(self):
        out = build("cmd_edit_body")
        self.assertFalse(out["ok"])
        self.assertEqual(out["verification"]["unexpectedChanges"], [{"kind": "unplanned-update", "id": "2:2"}])

    def test_cleanup_counts_children_of_a_planned_delete(self):
        out = build("cmd_cleanup")
        self.assertTrue(out["ok"], out["verification"])
        self.assertEqual(out["changes"]["deleted"], ["2:2"])

    def test_dry_run_that_changed_the_file_is_a_violation(self):
        command = dict(load("cmd_create_live", "command.json"), dryRun=True)
        out = connector.build_result(command, load("cmd_create_live", "before.json"), load("cmd_create_live", "after.json"))
        self.assertEqual(out["status"], "dry_run_violation")
        self.assertFalse(out["ok"])

    def test_unprefixed_top_level_node_fails(self):
        after = load("cmd_create_live", "after.json")
        after["nodes"][0]["name"] = "Card"
        command = load("cmd_create_live", "command.json")
        command["expectedChanges"]["createdNames"] = ["Card", "Title", "Body"]
        out = connector.build_result(command, load("cmd_create_live", "before.json"), after)
        self.assertIn("created-without-prefix", {u["kind"] for u in out["verification"]["unexpectedChanges"]})


class RenderTest(unittest.TestCase):
    def run_render(self, snippet_text, *extra):
        tmp = Path(tempfile.mkdtemp())
        snippet = tmp / "snippet.js"
        snippet.write_text(snippet_text, encoding="utf-8")
        out = tmp / "runs" / "x" / "code.js"
        proc = subprocess.run([sys.executable, str(SKILL / "connector.py"), "render", str(snippet), "-o", str(out), *extra], capture_output=True, text=True)
        return proc, out

    def test_function_snippet_is_called_with_args(self):
        proc, out = self.run_render((SKILL / "snippets" / "snapshot.js").read_text(), "--args", '{"rootId": "0:1"}')
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        code = out.read_text()
        self.assertIn('const args = {"rootId": "0:1"};', code)
        self.assertIn("return await (async ({ rootId", code)

    def test_bad_snippet_is_blocked_and_not_written(self):
        proc, out = self.run_render('const f = figma.createFrame();\n$fig.query(\'FRAME[name="Card"]\').remove();\n')
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(out.exists())
        for rule in ("fc-raw-create", "fc-write-guard", "fc-delete-by-name"):
            self.assertIn(rule, proc.stdout)

    def test_output_must_be_the_envelope_script(self):
        envelope = SKILL / "tests" / "fixtures" / "envelope" / "pass" / "connector-create-live.json"
        proc, out = self.run_render("return 1;\n", "--command", str(envelope))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("is not the envelope's script", proc.stderr)


if __name__ == "__main__":
    unittest.main()
