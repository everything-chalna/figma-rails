import json
import re
import shutil
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
    """Geometry and text captured from the live connector run in a scratch file (2026-11-25, v1); style fields copied from the v2 capture of the same create-card.js snippet."""

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
        self.assertEqual(out["verification"]["unexpectedChanges"], [{"kind": "unplanned-update", "id": "2:2", "fields": ["geometry"]}])

    def test_report_that_omits_the_resized_parent_is_named(self):
        out = build("cmd_edit_body", reported={"created": [], "updated": ["2:4"]})
        kinds = {u["kind"]: u for u in out["verification"]["unexpectedChanges"]}
        self.assertEqual(kinds["report-missed-change"]["ids"], ["2:2"])

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


def build_planned(run, **kw):
    return connector.build_result(load(run, "command.planned.json"), load(run, "before.json"), load(run, "after.json"), **kw)


def kinds(out):
    return [(u["kind"], u.get("fields")) for u in out["verification"]["unexpectedChanges"]]


class StyleReadbackTest(unittest.TestCase):
    """Live v2 snapshots from a scratch file (2026-11-25): Body fill -> #B00020, Title Inter Bold -> Medium, then a mixed bold/blue first word."""

    def test_fill_only_change_fails_unless_planned(self):
        self.assertEqual(kinds(build("cmd_style_fill")), [("unplanned-field", ["fill"])])
        self.assertTrue(build_planned("cmd_style_fill")["ok"])

    def test_font_change_fails_when_only_characters_were_planned(self):
        out = build("cmd_style_font", reported=connector.reported_ids(load("cmd_style_font", "returned.json")))
        self.assertEqual(kinds(out), [("unplanned-field", ["font", "geometry"])])
        self.assertTrue(build_planned("cmd_style_font")["ok"])

    def test_font_size_only_change_on_an_unplanned_node_fails(self):
        before = load("cmd_style_fill", "before.json")
        after = json.loads(json.dumps(before))
        next(n for n in after["nodes"] if n["id"] == "1:3")["fontSize"] = 24
        command = load("cmd_style_fill", "command.json")
        self.assertEqual(kinds(connector.build_result(command, before, after)), [("unplanned-update", ["font"])])
        command["expectedChanges"] = {"mutatedNodeIds": ["1:3"], "createdNames": [], "mutatedProperties": {"1:3": ["font"]}}
        self.assertTrue(connector.build_result(command, before, after)["ok"])

    def test_mixed_ranges_are_compared_without_throwing(self):
        node = next(n for n in load("cmd_style_mixed", "after.json")["nodes"] if n["id"] == "1:4")
        self.assertEqual(node["fontName"], "__mixed__")
        self.assertEqual([s["fontName"]["style"] for s in node["segments"]], ["Bold", "Regular"])
        self.assertEqual(kinds(build("cmd_style_mixed")), [("unplanned-field", ["fill", "font"])])
        self.assertTrue(build_planned("cmd_style_mixed")["ok"])

    def test_text_edit_that_only_shifts_ranges_is_not_a_font_change(self):
        old = next(n for n in load("cmd_style_mixed", "after.json")["nodes"] if n["id"] == "1:4")
        new = json.loads(json.dumps(old))
        new["characters"] = "Linted " + old["characters"]
        new["segments"][1]["start"], new["segments"][1]["end"] = 6, 73
        self.assertEqual(connector.changed_groups(old, new), ["characters"])

    def test_report_that_claims_an_unseen_change_fails(self):
        out = build_planned("cmd_style_fill", reported={"created": [], "updated": ["1:4", "1:3"]})
        self.assertIn(("report-claims-unseen-change", None), kinds(out))

    def test_live_figdone_result_matches_readback(self):
        reported = connector.load_reported(RUNS / "cmd_style_fill" / "returned.wrapped.txt")
        self.assertEqual(reported["updated"], ["1:4"])
        self.assertTrue(build_planned("cmd_style_fill", reported=reported)["ok"])

    def test_stale_auto_layer_name_is_a_warning_not_a_failure(self):
        before = load("cmd_style_fill", "before.json")
        after = json.loads(json.dumps(before))
        for snap, text in ((before, "Revenue up"), (after, "Costs down")):
            node = next(n for n in snap["nodes"] if n["id"] == "1:4")
            node["characters"] = text
        next(n for n in before["nodes"] if n["id"] == "1:4")["name"] = "Revenue up"
        next(n for n in after["nodes"] if n["id"] == "1:4")["name"] = "Revenue up"
        command = load("cmd_style_fill", "command.json")
        out = connector.build_result(command, before, after)
        self.assertTrue(out["ok"], out["verification"])
        self.assertEqual([w["kind"] for w in out["warnings"]], ["stale-layer-name"])

    def test_result_records_snapshot_schema_for_the_checker(self):
        out = build_planned("cmd_style_fill")
        self.assertEqual(out["verification"]["snapshotSchema"], "figma-sink-snapshot/v2")


class LayoutReadbackTest(unittest.TestCase):
    """Live snapshots from a scratch file (2026-11-25): card drop shadow, card top-left radius 12 -> 0, chip vertical padding 4 -> 12 inside a hugging card."""

    def test_effect_change_fails_unless_planned(self):
        self.assertEqual(kinds(build("cmd_layout_effect")), [("unplanned-field", ["effect"])])
        self.assertTrue(build_planned("cmd_layout_effect")["ok"])

    def test_radius_change_to_mixed_corners_fails_unless_planned(self):
        node = next(n for n in load("cmd_layout_radius", "after.json")["nodes"] if n["id"] == "3:2")
        self.assertEqual((node["cornerRadius"], node["topLeftRadius"], node["topRightRadius"]), ("__mixed__", 0, 12))
        self.assertEqual(kinds(build("cmd_layout_radius")), [("unplanned-field", ["radius"])])
        self.assertTrue(build_planned("cmd_layout_radius")["ok"])

    def test_padding_change_fails_unless_layout_is_planned(self):
        self.assertEqual(kinds(build("cmd_layout_padding")), [("unplanned-field", ["layout"])])
        self.assertTrue(build_planned("cmd_layout_padding")["ok"])

    def test_padding_that_grows_the_hug_parent_is_caught(self):
        command = load("cmd_layout_padding", "command.hug-unplanned.json")
        out = connector.build_result(command, load("cmd_layout_padding", "before.json"), load("cmd_layout_padding", "after.json"))
        self.assertEqual(kinds(out), [("unplanned-update", ["geometry"])])
        self.assertEqual(out["verification"]["unexpectedChanges"][0]["id"], "3:2")

    def test_figdone_report_of_the_direct_edit_passes_with_planned_side_effects(self):
        reported = connector.reported_ids(load("cmd_layout_padding", "returned.json"))
        self.assertEqual(reported["updated"], ["3:5"])
        self.assertTrue(build_planned("cmd_layout_padding", reported=reported)["ok"])

    def test_snapshot_without_new_groups_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp, "old.json")
            data = load("cmd_layout_effect", "before.json")
            data["readGroups"] = data["readGroups"][:9]
            old.write_text(json.dumps(data))
            proc = run_cli("ingest", old, "-o", Path(tmp, "out.json"))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("did not read effect, radius, layout", proc.stderr)

    def test_snapshot_snippet_reads_every_checker_group(self):
        src = (SKILL / "snippets" / "snapshot.js").read_text()
        groups = json.loads(re.search(r"const READ_GROUPS = (\[[^\]]*\]);", src).group(1))
        self.assertEqual(groups, list(connector.FIELD_GROUPS))

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_snapshot_keeps_every_effect_field(self):
        # Raw effects read from the live scratch card (2026-11-25): a normal -> progressive blur switch kept type and radius.
        progressive = {"type": "LAYER_BLUR", "visible": True, "radius": 8, "boundVariables": {}, "blurType": "PROGRESSIVE",
                       "startRadius": 0, "startOffset": {"x": 0.5, "y": 0}, "endOffset": {"x": 0.5, "y": 1}}
        shadow = {"type": "DROP_SHADOW", "visible": True, "blendMode": "NORMAL", "color": {"r": 0, "g": 0, "b": 0, "a": 0.2},
                  "offset": {"x": 0, "y": 4}, "radius": 12, "spread": 0, "showShadowBehindNode": False,
                  "boundVariables": {"radius": {"type": "VARIABLE_ALIAS", "id": "VariableID:1:2"}}}
        node = snapshot_with_effects([progressive, shadow])
        self.assertEqual(node["effects"][0], {"visible": True, "type": "LAYER_BLUR", "radius": 8, "blurType": "PROGRESSIVE",
                                              "startRadius": 0, "startOffset": {"x": 0.5, "y": 0}, "endOffset": {"x": 0.5, "y": 1}})
        self.assertEqual(node["effects"][1]["boundVariables"], {"radius": "VariableID:1:2"})
        self.assertEqual(node["effects"][1]["color"], {"r": 0, "g": 0, "b": 0, "a": 0.2})


class EdgeReadbackTest(unittest.TestCase):
    """Live snapshots from a scratch file (2026-11-25): progressive -> normal layer blur, cornerSmoothing 0 -> 0.6, NO_WRAP -> WRAP with counterAxisSpacing 8, and a stack child set to ABSOLUTE."""

    def test_blur_type_change_fails_unless_planned(self):
        before = next(n for n in load("cmd_edge_blur_type", "before.json")["nodes"] if n["id"] == "6:2")
        self.assertEqual(before["effects"][0]["blurType"], "PROGRESSIVE")
        self.assertEqual(kinds(build("cmd_edge_blur_type")), [("unplanned-field", ["effect"])])
        self.assertTrue(build_planned("cmd_edge_blur_type")["ok"])

    def test_corner_smoothing_change_fails_unless_planned(self):
        self.assertEqual(kinds(build("cmd_edge_smoothing")), [("unplanned-field", ["radius"])])
        self.assertTrue(build_planned("cmd_edge_smoothing")["ok"])

    def test_wrap_fails_unless_planned_and_catches_moved_children(self):
        self.assertEqual(kinds(build("cmd_edge_wrap")), [("unplanned-field", ["layout"])])
        self.assertTrue(build_planned("cmd_edge_wrap", reported=connector.reported_ids(load("cmd_edge_wrap", "returned.json")))["ok"])
        out = connector.build_result(load("cmd_edge_wrap", "command.children-unplanned.json"), load("cmd_edge_wrap", "before.json"), load("cmd_edge_wrap", "after.json"))
        self.assertEqual([u["id"] for u in out["verification"]["unexpectedChanges"]], ["6:8", "6:9", "6:10"])

    def test_absolute_child_fails_unless_planned_and_catches_the_shift(self):
        self.assertEqual(kinds(build("cmd_edge_absolute")), [("unplanned-field", ["layout"])])
        self.assertTrue(build_planned("cmd_edge_absolute")["ok"])
        out = connector.build_result(load("cmd_edge_absolute", "command.siblings-unplanned.json"), load("cmd_edge_absolute", "before.json"), load("cmd_edge_absolute", "after.json"))
        self.assertEqual(kinds(out), [("unplanned-update", ["geometry"])] * 2)


SNAPSHOT_HARNESS = """
const src = require("fs").readFileSync(process.argv[1], "utf8");
const effects = JSON.parse(process.argv[2]);
const page = { id: "0:1", type: "PAGE", children: [] };
page.children.push({ id: "6:2", name: "card", type: "FRAME", parent: page, x: 0, y: 0, width: 160, height: 80, visible: true, locked: false,
  fills: [], fillStyleId: "", strokes: [], strokeStyleId: "", strokeWeight: 1, effects, effectStyleId: "", cornerRadius: 16, cornerSmoothing: 0, layoutMode: "NONE" });
globalThis.figma = { mixed: Symbol("mixed"), getNodeByIdAsync: async () => page, setCurrentPageAsync: async () => {} };
eval(src)({ rootId: "0:1" }).then((out) => console.log(JSON.stringify(out.nodes[0])));
"""


def snapshot_with_effects(effects):
    proc = subprocess.run(["node", "-e", SNAPSHOT_HARNESS, str(SKILL / "snippets" / "snapshot.js"), json.dumps(effects)],
                          capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def run_cli(*args):
    return subprocess.run([sys.executable, str(SKILL / "connector.py"), *map(str, args)], capture_output=True, text=True)


class IngestTest(unittest.TestCase):
    def test_wrapped_snapshot_becomes_the_plain_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "after.json")
            proc = run_cli("ingest", RUNS / "cmd_style_fill" / "after.wrapped.txt", "-o", out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(out.read_text()), load("cmd_style_fill", "after.json"))

    def test_plain_snapshot_passes_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "before.json")
            self.assertEqual(run_cli("ingest", RUNS / "cmd_style_fill" / "before.json", "-o", out).returncode, 0)
            self.assertEqual(json.loads(out.read_text()), load("cmd_style_fill", "before.json"))

    def test_wrapped_return_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "returned.json")
            self.assertEqual(run_cli("ingest", "--returned", RUNS / "cmd_style_fill" / "dry-run" / "returned.wrapped.txt", "-o", out).returncode, 0)
            self.assertEqual(json.loads(out.read_text())["plan"]["fills"], ["#B00020"])

    def test_v1_snapshot_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            v1 = Path(tmp, "v1.json")
            v1.write_text(json.dumps(dict(load("cmd_style_fill", "before.json"), schemaVersion="figma-sink-snapshot/v1")))
            proc = run_cli("ingest", v1, "-o", Path(tmp, "out.json"))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("no font, fill, stroke or text-style fields", proc.stderr)

    def test_result_reads_a_wrapped_after_snapshot_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli("result", "--command", RUNS / "cmd_style_fill" / "command.planned.json", "--before", RUNS / "cmd_style_fill" / "before.json",
                           "--after", RUNS / "cmd_style_fill" / "after.wrapped.txt", "--reported", RUNS / "cmd_style_fill" / "returned.wrapped.txt", "-o", Path(tmp, "result.json"))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class DryRunArtifactsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.snippet = self.tmp / "edit.js"
        self.snippet.write_text("const plan = { update: command.expectedChanges.mutatedNodeIds };\nif (command.dryRun !== false || command.guards.allowMutation !== true) return { plan };\n$fig.get(command.target.nodeId).set({ fills: [figma.util.solidPaint('#B00020')] });\n")
        self.dry = self.tmp / "dry.json"
        self.live = self.tmp / "live.json"
        self.dry.write_text((RUNS / "cmd_style_fill" / "dry-run" / "command.json").read_text())
        self.live.write_text((RUNS / "cmd_style_fill" / "command.json").read_text())
        self.run_dir = self.tmp / "runs" / "cmd_style_fill"

    def test_dry_run_must_render_under_dry_run(self):
        proc = run_cli("render", self.snippet, "--command", self.dry, "-o", self.run_dir / "edit.js")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("dry-run/edit.js", proc.stderr)

    def test_live_render_keeps_the_dry_run_files(self):
        self.assertEqual(run_cli("render", self.snippet, "--command", self.dry, "-o", self.run_dir / "dry-run" / "edit.js").returncode, 0)
        (self.run_dir / "dry-run" / "returned.json").write_text('{"plan": {}}')
        dry_code = (self.run_dir / "dry-run" / "edit.js").read_text()
        proc = run_cli("render", self.snippet, "--command", self.live, "-o", self.run_dir / "edit.js")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.run_dir / "dry-run" / "edit.js").read_text(), dry_code)
        self.assertIn('"dryRun": true', dry_code)
        self.assertIn('"dryRun": false', (self.run_dir / "edit.js").read_text())
        self.assertTrue((self.run_dir / "dry-run" / "returned.json").exists())
        self.assertEqual(json.loads((self.run_dir / "dry-run" / "command.json").read_text())["dryRun"], True)

    def test_live_plan_that_differs_from_the_dry_run_is_refused(self):
        run_cli("render", self.snippet, "--command", self.dry, "-o", self.run_dir / "dry-run" / "edit.js")
        self.live.write_text((RUNS / "cmd_style_fill" / "command.planned.json").read_text())
        proc = run_cli("render", self.snippet, "--command", self.live, "-o", self.run_dir / "edit.js")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("plans something other than the dry run", proc.stderr)
        self.assertFalse((self.run_dir / "edit.js").exists())


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
        self.assertIn("the file that is linted must be the file that is sent", proc.stderr)


if __name__ == "__main__":
    unittest.main()
