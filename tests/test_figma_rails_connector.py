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
            data = load("cmd_full_noop", "before.json")
            data["readGroups"] = data["readGroups"][:-1]
            old.write_text(json.dumps(data))
            proc = run_cli("ingest", old, "-o", Path(tmp, "out.json"))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("did not read other", proc.stderr)

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
        self.assertEqual(node["effects"][0], progressive)
        self.assertEqual(node["effects"][1]["boundVariables"], {"radius": {"type": "VARIABLE_ALIAS", "id": "VariableID:1:2"}})
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


class FullPropertyReadbackTest(unittest.TestCase):
    """Live v3 snapshots from a scratch file (2026-11-25): Swatch opacity 1 -> 0.5, blendMode PASS_THROUGH -> MULTIPLY, Spinner rotation 0 -> 15, Pinned constraints MIN/MIN -> MAX/CENTER, and a dry run with no writes."""

    def test_properties_the_allowlist_missed_fail_unless_planned(self):
        for run, prop in (("cmd_full_opacity", "opacity"), ("cmd_full_blend", "blendMode"), ("cmd_full_rotation", "rotation"), ("cmd_full_constraints", "constraints")):
            with self.subTest(run):
                self.assertEqual(kinds(build(run)), [("unplanned-field", [f"other:{prop}"])])
                self.assertTrue(build_planned(run, reported=connector.reported_ids(load(run, "returned.json")))["ok"])

    def test_read_only_run_has_no_diff(self):
        out = build("cmd_full_noop")
        self.assertEqual((out["status"], out["verification"]["fieldChanges"]), ("dry-run", {}))

    def test_ignore_list_keeps_a_planned_rotation_passing(self):
        command = load("cmd_full_rotation", "command.planned.json")
        control = connector.build_result(command, load("cmd_full_rotation", "control-no-ignore.before.json"), load("cmd_full_rotation", "control-no-ignore.after.json"))
        self.assertEqual(kinds(control), [("unplanned-field", ["other:absoluteBoundingBox", "other:absoluteRenderBounds", "other:absoluteTransform"])])
        self.assertTrue(build_planned("cmd_full_rotation")["ok"])

    def test_unmapped_property_fails_closed(self):
        before, after = load("cmd_full_opacity", "before.json"), load("cmd_full_opacity", "before.json")
        next(n for n in after["nodes"] if n["id"] == "9:6")["someFutureProperty"] = 1
        out = connector.build_result(load("cmd_full_opacity", "command.planned.json"), before, after)
        self.assertEqual(kinds(out), [("unplanned-field", ["other:someFutureProperty"])])
        plain = dict(load("cmd_full_opacity", "command.planned.json"), expectedChanges={"mutatedNodeIds": ["9:6"], "createdNames": []})
        self.assertEqual(kinds(connector.build_result(plain, before, after)), [("unplanned-field", ["other:someFutureProperty"])])

    def test_old_allowlist_fixtures_still_diff_the_same(self):
        self.assertEqual(kinds(build("cmd_layout_padding")), [("unplanned-field", ["layout"])])
        self.assertEqual(kinds(build("cmd_edge_absolute")), [("unplanned-field", ["layout"])])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_snapshot_records_unknown_props_and_drops_ignored_ones(self):
        base = {"opacity": 1, "absoluteRenderBounds": {"x": 0, "y": 0, "width": 160, "height": 80}}
        a = snapshot_node(base)
        b = snapshot_node(dict(base, absoluteRenderBounds={"x": 0.3, "y": 0, "width": 159.4, "height": 80}))
        self.assertNotIn("absoluteRenderBounds", a)
        self.assertEqual(connector.changed_groups(a, b), [])
        c = snapshot_node(dict(base, someFutureProperty="on"))
        self.assertEqual(connector.changed_groups(a, c), ["other:someFutureProperty"])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_snapshot_bounds_big_values_and_long_text(self):
        node = snapshot_node({"vectorPaths": [{"windingRule": "NONZERO", "data": "M0 0 " + "L1 1 " * 2000}]})
        self.assertEqual(set(node["vectorPaths"]), {"__digest__", "length"})
        text = snapshot_node({"type": "TEXT", "characters": "a" * 300})
        longer = snapshot_node({"type": "TEXT", "characters": "a" * 299 + "b"})
        self.assertEqual(text["characters"], longer["characters"])
        self.assertEqual(connector.changed_groups(text, longer), ["characters"])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_component_swap_fails_closed(self):
        a, b = snapshot_node({"type": "INSTANCE", "__main": "1:10"}), snapshot_node({"type": "INSTANCE", "__main": "1:11"})
        self.assertEqual(connector.changed_groups(a, b), ["other:mainComponentId"])

    def test_every_ignored_property_has_a_reason(self):
        src = (SKILL / "snippets" / "snapshot.js").read_text()
        block = re.search(r"const IGNORED = \{(.*?)\n  \};", src, re.S).group(1)
        entries = re.findall(r"^\s*(\w+): \"([^\"]*)\",$", block, re.M)
        self.assertGreater(len(entries), 20)
        self.assertEqual(len(entries), len([l for l in block.splitlines() if l.strip()]))
        self.assertTrue(all(len(reason) > 10 for _, reason in entries))


SNAPSHOT_HARNESS = """
const src = require("fs").readFileSync(process.argv[1], "utf8");
const props = JSON.parse(process.argv[2]);
const mainId = props.__main;
delete props.__main;
const page = { id: "0:1", type: "PAGE", children: [] };
const node = Object.assign({ id: "6:2", name: "card", type: "FRAME", parent: page, removed: false, x: 0, y: 0, width: 160, height: 80, visible: true, locked: false, effects: [] }, props);
if (node.type === "TEXT") node.getStyledTextSegments = () => [];
if (node.type === "INSTANCE") node.getMainComponentAsync = async () => ({ id: mainId });
page.children.push(node);
globalThis.figma = { mixed: Symbol("mixed"), getNodeByIdAsync: async () => page, setCurrentPageAsync: async () => {} };
eval(src)({ rootId: "0:1" }).then((out) => console.log(JSON.stringify(out.nodes[0])));
"""


def snapshot_node(props):
    proc = subprocess.run(["node", "-e", SNAPSHOT_HARNESS, str(SKILL / "snippets" / "snapshot.js"), json.dumps(props)],
                          capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


TREE_HARNESS = """
const src = require("fs").readFileSync(process.argv[1], "utf8");
const spec = JSON.parse(process.argv[2]);
const figma = { mixed: Symbol("mixed"), skipInvisibleInstanceChildren: true, setCurrentPageAsync: async () => {} };
globalThis.figma = figma;
const build = (t, parent, inInstance) => {
  const n = Object.assign({ name: "n", type: "FRAME", parent, removed: false, x: 0, y: 0, width: 10, height: 10, visible: true }, t);
  delete n.children; delete n.__main;
  if (n.type === "INSTANCE") n.getMainComponentAsync = async () => ({ id: t.__main });
  if (n.type === "TEXT") n.getStyledTextSegments = () => [];
  if (t.children) {
    const inside = inInstance || n.type === "INSTANCE";
    const kids = t.children.map((k) => build(k, n, inside));
    Object.defineProperty(n, "children", { get: () => kids.filter((c) => !(inside && figma.skipInvisibleInstanceChildren && !c.visible)) });
  }
  return n;
};
const page = build({ id: "0:1", type: "PAGE", children: spec.nodes }, null, false);
figma.getNodeByIdAsync = async () => page;
const code = spec.captureFull ? src.replace("  const snapshotDigest =", "  globalThis.__full = nodes;\\n  const snapshotDigest =") : src;
eval(code)(Object.assign({ rootId: "0:1" }, spec.args || {})).then((out) => console.log(JSON.stringify({ out, full: globalThis.__full, skipInvisibleAfter: figma.skipInvisibleInstanceChildren })));
"""


def snapshot_tree(nodes, capture_full=False, **args):
    proc = subprocess.run(["node", "-e", TREE_HARNESS, str(SKILL / "snippets" / "snapshot.js"), json.dumps({"nodes": nodes, "args": args, "captureFull": capture_full})],
                          capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def snapshot_with_effects(effects):
    return snapshot_node({"effects": effects})


class InstanceSwapReadbackTest(unittest.TestCase):
    """Live v3 snapshots from a scratch file (2026-11-25): instance 14:8 swapped from comp A (14:2) to comp B (14:5), captured with the snippet that still recorded masterComponent, then swapped back with the fixed snippet."""

    def test_planned_swap_passes_and_its_new_sublayers_are_not_creates(self):
        run = "cmd_comp_swap_back"
        out = build_planned(run, reported=connector.reported_ids(load(run, "returned.json")))
        self.assertTrue(out["ok"], out["verification"])
        self.assertEqual(out["changes"]["created"], [])
        self.assertEqual(out["changes"]["swappedInstanceSublayers"], {"created": [{"id": "I14:8;14:3", "name": "Label"}, {"id": "I14:8;14:4", "name": "Dot"}], "deleted": ["I14:8;14:6", "I14:8;14:7"]})

    def test_unplanned_swap_fails_on_the_main_component_and_the_rebuilt_sublayers(self):
        found = kinds(build("cmd_comp_swap_back"))
        self.assertIn(("unplanned-field", ["fill", "other:componentProperties", "other:mainComponentId"]), found)
        self.assertTrue({"created-names", "created-without-prefix", "over-maxCreatedNodes", "unplanned-delete"} <= {k for k, _ in found})

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_masterComponent_alias_is_not_recorded(self):
        a = snapshot_node({"type": "INSTANCE", "__main": "1:10", "masterComponent": {"id": "1:10"}})
        b = snapshot_node({"type": "INSTANCE", "__main": "1:11", "masterComponent": {"id": "1:11"}})
        self.assertNotIn("masterComponent", a)
        self.assertEqual(connector.changed_groups(a, b), ["other:mainComponentId"])
        self.assertFalse(any("masterComponent" in n for n in load("cmd_comp_swap_back", "after.json")["nodes"]))

    def test_old_capture_failed_a_planned_swap_only_on_masterComponent(self):
        # Real past mistake: the A -> B capture, judged with the swap plan. Before this fix it also
        # failed on created-names, created-without-prefix, over-maxCreatedNodes and two unplanned deletes.
        self.assertEqual(kinds(build_planned("cmd_comp_swap_old_snapshot")), [("unplanned-field", ["other:masterComponent"])])


class HiddenInstanceLayerTest(unittest.TestCase):
    """Live v3 snapshots from a scratch file (2026-11-25): boolean property Show Dot on instance 14:8 set to false (with the old and the fixed snippet), then back to true."""

    def test_old_snippet_read_a_hidden_layer_as_a_delete(self):
        # Real past mistake: use_figma's skipInvisibleInstanceChildren hid the Dot from the walk.
        run = "cmd_comp_hide_layer"
        out = connector.build_result(load(run, "command.planned.json"), load(run, "before.json"), load(run, "after.old-snippet.json"))
        self.assertEqual(kinds(out), [("unplanned-delete", None)])
        fixed = build_planned(run)
        self.assertNotIn("unplanned-delete", {k for k, _ in kinds(fixed)})
        self.assertEqual(fixed["verification"]["fieldChanges"]["I14:8;14:4"], ["geometry", "visibility"])

    def test_showing_the_layer_passes_planned_and_fails_unplanned(self):
        run = "cmd_comp_show_layer"
        self.assertTrue(build_planned(run, reported=connector.reported_ids(load(run, "returned.json")))["ok"])
        self.assertEqual(kinds(build(run)), [("unplanned-field", ["other:componentProperties"]), ("unplanned-field", ["visibility"])])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_snapshot_walks_hidden_instance_children_and_restores_the_flag(self):
        tree = [{"id": "5:1", "type": "INSTANCE", "__main": "1:1", "children": [{"id": "I5:1;1:2", "type": "TEXT", "characters": "x"}, {"id": "I5:1;1:3", "type": "RECTANGLE", "visible": False}]}]
        got = snapshot_tree(tree)
        self.assertEqual([n["id"] for n in got["out"]["nodes"]], ["5:1", "I5:1;1:2", "I5:1;1:3"])
        self.assertIs(got["skipInvisibleAfter"], True)


def card_tree(cards):
    kids = []
    for i in range(cards):
        kids.append({"id": f"7:{i}", "name": f"Card {i}", "type": "FRAME", "opacity": 0.5 if i == 3 else 1, "children": [
            {"id": f"7:{i}:t", "name": "Title", "type": "TEXT", "characters": f"Card {i}", "fontSize": 13, **({} if i == 5 else {"hyperlink": None})},
            {"id": f"7:{i}:r", "name": "Swatch", "type": "RECTANGLE", "fills": [{"type": "SOLID", "color": {"r": i % 2, "g": 0, "b": 0}}]}]})
    return [{"id": "7:grid", "name": "grid", "type": "FRAME", "layoutWrap": "WRAP", "children": kids}]


class PagedSnapshotTest(unittest.TestCase):
    """use_figma refuses a return value over 48,000 bytes; a live 498-node v3 snapshot was 1,197,957 bytes (2026-11-25)."""

    def pages(self, tree, **args):
        first = snapshot_tree(tree, **args)["out"]
        rest = [snapshot_tree(tree, page=p, **args)["out"] for p in range(1, first["pages"])]
        tmp = Path(tempfile.mkdtemp())
        paths = []
        for d in [first, *rest]:
            paths.append(tmp / f"p{d['page']}.json")
            paths[-1].write_text(json.dumps(d))
        return paths

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_pages_merge_back_to_every_node_exactly(self):
        tree = card_tree(12)
        full = json.loads(json.dumps(snapshot_tree(tree, capture_full=True)["full"]))
        paths = self.pages(tree, pageBytes=600)
        self.assertGreater(len(paths), 2)
        merged = connector.load_snapshot(*paths)
        self.assertEqual(merged["nodes"], full)
        self.assertNotIn("hyperlink", next(n for n in merged["nodes"] if n["id"] == "7:5:t"))
        self.assertEqual(next(n for n in merged["nodes"] if n["id"] == "7:3")["opacity"], 0.5)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_every_page_fits_the_use_figma_return_limit(self):
        tree = card_tree(150)
        self.assertGreater(len(json.dumps(snapshot_tree(tree, capture_full=True)["full"])), 48000)
        for p in self.pages(tree):
            self.assertLess(len(p.read_text()), 48000, p.name)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_incomplete_or_mixed_pages_are_refused(self):
        paths = self.pages(card_tree(12), pageBytes=600)
        for case, files, message in (("one page", paths[:1], "page 0 of"), ("missing page", paths[:-1], "pass every page"),
                                     ("duplicate page", [*paths[:-1], paths[0]], "pass every page")):
            with self.subTest(case):
                proc = run_cli("ingest", *files, "-o", Path(tempfile.mkdtemp(), "out.json"))
                self.assertEqual(proc.returncode, 1)
                self.assertIn(message, proc.stderr)
        other = json.loads(paths[1].read_text())
        paths[1].write_text(json.dumps(dict(other, snapshotDigest="00000000")))
        proc = run_cli("ingest", *paths, "-o", Path(tempfile.mkdtemp(), "out.json"))
        self.assertIn("come from different snapshots", proc.stderr)

    def test_live_500_node_edit_is_judged_from_pages(self):
        run = RUNS / "cmd_speed_500_edit"
        before = connector.load_snapshot(*sorted(run.glob("before.page*.json")))
        after = connector.load_snapshot(*sorted(run.glob("after.page*.json")))
        self.assertEqual(len(after["nodes"]), 498)
        planned = connector.build_result(load("cmd_speed_500_edit", "command.planned.json"), before, after, reported=connector.reported_ids(load("cmd_speed_500_edit", "returned.json")))
        self.assertTrue(planned["ok"], planned["verification"])
        unplanned = connector.build_result(load("cmd_speed_500_edit", "command.json"), before, after)
        self.assertEqual(kinds(unplanned), [("unplanned-field", ["other:opacity"])])


class InstanceOverrideReadbackTest(unittest.TestCase):
    """Live v3 snapshots from a scratch file (2026-11-25): text property Label on instance 14:8 set to a longer string, which also moves the Dot sublayer."""

    def test_planned_override_passes(self):
        run = "cmd_comp_override"
        self.assertTrue(build_planned(run, reported=connector.reported_ids(load(run, "returned.json")))["ok"])

    def test_unplanned_override_fails_on_the_property_and_the_text(self):
        self.assertEqual(kinds(build("cmd_comp_override")), [("unplanned-field", ["other:componentProperties"]), ("unplanned-field", ["characters"])])

    def test_the_moved_sibling_must_be_planned_too(self):
        run = "cmd_comp_override"
        out = connector.build_result(load(run, "command.sibling-unplanned.json"), load(run, "before.json"), load(run, "after.json"))
        self.assertEqual([(u["kind"], u["id"]) for u in out["verification"]["unexpectedChanges"]], [("unplanned-update", "I14:8;14:4")])


def run_cli(*args):
    return subprocess.run([sys.executable, str(SKILL / "connector.py"), *map(str, args)], capture_output=True, text=True)


class IngestTest(unittest.TestCase):
    def test_wrapped_snapshot_becomes_the_plain_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "after.json")
            proc = run_cli("ingest", RUNS / "cmd_full_constraints" / "after.wrapped.txt", "-o", out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(out.read_text()), load("cmd_full_constraints", "after.json"))

    def test_plain_snapshot_passes_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "before.json")
            self.assertEqual(run_cli("ingest", RUNS / "cmd_full_constraints" / "before.json", "-o", out).returncode, 0)
            self.assertEqual(json.loads(out.read_text()), load("cmd_full_constraints", "before.json"))

    def test_wrapped_return_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "returned.json")
            self.assertEqual(run_cli("ingest", "--returned", RUNS / "cmd_style_fill" / "dry-run" / "returned.wrapped.txt", "-o", out).returncode, 0)
            self.assertEqual(json.loads(out.read_text())["plan"]["fills"], ["#B00020"])

    def test_allowlist_snapshots_are_refused(self):
        for old in ("figma-sink-snapshot/v1", "figma-sink-snapshot/v2"):
            with self.subTest(old), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp, "old.json")
                path.write_text(json.dumps(dict(load("cmd_style_fill", "before.json"), schemaVersion=old)))
                proc = run_cli("ingest", path, "-o", Path(tmp, "out.json"))
                self.assertEqual(proc.returncode, 1)
                self.assertIn("hand-picked field list and misses opacity, rotation, blend mode, constraints", proc.stderr)

    def test_result_reads_a_wrapped_after_snapshot_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli("result", "--command", RUNS / "cmd_full_constraints" / "command.planned.json", "--before", RUNS / "cmd_full_constraints" / "before.json",
                           "--after", RUNS / "cmd_full_constraints" / "after.wrapped.txt", "--reported", RUNS / "cmd_full_constraints" / "returned.json", "-o", Path(tmp, "result.json"))
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
