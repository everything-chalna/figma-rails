#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check

READ_ONLY_COMMAND = {"dryRun": True, "guards": {"allowMutation": False}}
FUNCTION_SNIPPET_RE = re.compile(r"^\s*async\s*\(\s*(\{[^)]*\}|\w+)?\s*\)\s*=>")
SNAPSHOT_SCHEMA, FIELD_GROUPS, EXPLICIT_GROUPS = check.SNAPSHOT_SCHEMA, check.FIELD_GROUPS, check.EXPLICIT_GROUPS
NOT_PROPERTIES = {"id", "segments", "segmentsTruncated"}


def fail(message, code=1):
    print(f"connector: {message}", file=sys.stderr)
    sys.exit(code)


def show(finding, label):
    return f"{label}:{finding.line}: [{finding.rule}] {finding.message}"


def report(findings, label, kind):
    for f in findings:
        print(show(f, label))
    if findings:
        fail(f"{len(findings)} finding(s) for --kind {kind}; nothing was written.")


PLAN_KEYS_IGNORED = ("dryRun", "guards")


def same_plan(dry, live):
    strip = lambda c: {k: v for k, v in c.items() if k not in PLAN_KEYS_IGNORED}
    guards = lambda c: {k: v for k, v in (c.get("guards") or {}).items() if k != "allowMutation"}
    return strip(dry) == strip(live) and guards(dry) == guards(live)


def dry_run_path(script):
    p = Path(script)
    return (p.parent / "dry-run" / p.name).as_posix()


def render(args):
    snippet = Path(args.snippet).read_text(encoding="utf-8").strip()
    command = READ_ONLY_COMMAND
    if args.command:
        text = Path(args.command).read_text(encoding="utf-8")
        report(check.CHECKER.run(text, "envelope"), args.command, "envelope")
        command = json.loads(text)
        if command.get("transport") != check.CONNECTOR_TRANSPORT:
            fail(f"{args.command} transport is {command.get('transport')!r}; render only connector commands.")
        dry = command.get("dryRun") is not False
        script = dry_run_path(command["script"]) if dry else command["script"]
        out = Path(args.out).as_posix()
        if not out.endswith(script):
            hint = "dry-run code goes under runs/<commandId>/dry-run/ so the live render never overwrites it" if dry else "the file that is linted must be the file that is sent"
            fail(f"-o {args.out} is not {script}; {hint}.")
        dry_dir = Path(out[: -len(script)] or ".") / Path(dry_run_path(command["script"])).parent
        if dry:
            dry_dir.mkdir(parents=True, exist_ok=True)
            (dry_dir / "command.json").write_text(text, encoding="utf-8")
        elif (dry_dir / "command.json").is_file():
            if not same_plan(json.loads((dry_dir / "command.json").read_text(encoding="utf-8")), command):
                fail(f"{args.command} plans something other than the dry run in {dry_dir}/command.json (only dryRun and guards.allowMutation may differ); dry-run the new plan first.")
        else:
            print(f"WARN: no {dry_dir}/command.json; this live run has no recorded dry run.", file=sys.stderr)
    call_args = json.loads(args.args) if args.args else {}
    body = f"return await ({snippet})(args);" if FUNCTION_SNIPPET_RE.match(snippet) else snippet
    code = f"const command = {json.dumps(command, ensure_ascii=False)};\nconst args = {json.dumps(call_args, ensure_ascii=False)};\n{body}\n"
    report(check.CHECKER.run(code, "code"), args.out, "code")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(code, encoding="utf-8")
    print(f"PASS: rendered {args.out}; send it with use_figma code='$file:{Path(args.out).resolve()}'")


DECODER = json.JSONDecoder()


def json_values(value, depth=0):
    if depth > 6:
        return
    if isinstance(value, str):
        text = value.strip()
        try:
            yield from json_values(json.loads(text), depth + 1)
            return
        except ValueError:
            pass
        i = text.find("{")
        while i != -1:
            try:
                found, end = DECODER.raw_decode(text, i)
            except ValueError:
                i = text.find("{", i + 1)
                continue
            yield from json_values(found, depth + 1)
            i = text.find("{", end)
        return
    yield value
    if isinstance(value, dict):
        for v in value.values():
            if isinstance(v, (dict, list, str)):
                yield from json_values(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            if isinstance(v, (dict, list, str)):
                yield from json_values(v, depth + 1)


def unwrap(path, want):
    text = Path(path).read_text(encoding="utf-8")
    for value in json_values(text):
        if isinstance(value, dict) and want(value):
            return value
    return None


def is_snapshot(value):
    return str(value.get("schemaVersion", "")).startswith("figma-sink-snapshot/") and isinstance(value.get("nodes"), list)


PAGES_TRANSPORT = "figma-sink-pages/v1"
PAGE_KEYS = ("transport", "snapshotDigest", "page", "pages", "total", "defaults")


def unpack(data, defaults):
    def node(n):
        full = dict(defaults.get(n.get("type"), {}))
        for k in n.get("__absent__") or []:
            full.pop(k, None)
        full.update({k: v for k, v in n.items() if k != "__absent__"})
        return full
    out = {k: v for k, v in data.items() if k not in PAGE_KEYS}
    out["snapshotDigest"] = data["snapshotDigest"]
    out["nodes"] = [node(n) for n in data["nodes"]]
    return out


def merge_pages(paths, datas):
    head = datas[0]
    for path, d in zip(paths, datas):
        for key in ("snapshotDigest", "pages", "total", "rootId"):
            if d.get(key) != head.get(key):
                fail(f"{path} has {key}={d.get(key)!r} but {paths[0]} has {head.get(key)!r}; the pages come from different snapshots (the canvas changed between page calls, or a page of another run was mixed in). Snapshot every page again.")
    seen = sorted(d.get("page") for d in datas)
    if seen != list(range(head["pages"])):
        fail(f"got pages {seen} of {head['pages']}; pass every page (0..{head['pages'] - 1}) exactly once.")
    datas = sorted(datas, key=lambda d: d["page"])
    nodes = [n for d in datas for n in d["nodes"]]
    if len(nodes) != head["total"]:
        fail(f"the pages hold {len(nodes)} nodes but the snapshot had {head['total']}.")
    return unpack(dict(head, nodes=nodes), datas[0].get("defaults") or {})


def load_snapshot(path, *more):
    paths = [path, *more]
    datas = [load_snapshot_page(p) for p in paths]
    if datas[0].get("transport") != PAGES_TRANSPORT:
        if more:
            fail(f"{path} is a whole snapshot; pass it alone.")
        return datas[0]
    if len(paths) == 1 and datas[0]["pages"] != 1:
        fail(f"{path} is page {datas[0]['page']} of {datas[0]['pages']}; run snippets/snapshot.js once per page (`page` 0..{datas[0]['pages'] - 1}) and `connector.py ingest` every page file together into one snapshot.")
    return merge_pages(paths, datas)


def load_snapshot_page(path):
    data = unwrap(path, is_snapshot)
    if data is None:
        fail(f"{path} holds no snippets/snapshot.js result ({SNAPSHOT_SCHEMA}), plain or wrapped by use_figma write_to_file.")
    if data["schemaVersion"] != SNAPSHOT_SCHEMA:
        fail(f"{path} is {data['schemaVersion']}, which records a hand-picked field list and misses opacity, rotation, blend mode, constraints and any property Figma adds; re-run the current snippets/snapshot.js ({SNAPSHOT_SCHEMA}; a stale copy in a run folder is the usual cause).")
    missing = [g for g in FIELD_GROUPS if g not in (data.get("readGroups") or [])]
    if missing:
        fail(f"{path} did not read {', '.join(missing)}; it came from an older snippets/snapshot.js. Re-run the current one (a stale copy in a run folder is the usual cause).")
    return data


def ingest(args):
    if args.returned:
        if len(args.input) != 1:
            fail("--returned reads one file.")
        data = unwrap(args.input[0], lambda v: True)
        if data is None:
            fail(f"{args.input[0]} holds no JSON object.")
    else:
        data = load_snapshot(*args.input)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    label = "returned value" if args.returned else f"{data['schemaVersion']} with {len(data['nodes'])} nodes"
    print(f"PASS: wrote {args.out} ({label})")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runs(segments, key, ranges):
    out = [((s.get("start"), s.get("end")) if ranges else (), s.get(key)) for s in segments or [] if key in s]
    return out if ranges else [v for i, v in enumerate(out) if i == 0 or out[i - 1] != v]


def changed_groups(old, new):
    groups = {check.group_of(k) for k in (old.keys() | new.keys()) - NOT_PROPERTIES if old.get(k) != new.get(k)}
    if old.get("segments") != new.get("segments") or old.get("segmentsTruncated") != new.get("segmentsTruncated"):
        ranges = old.get("characters") == new.get("characters")
        keys = {k for s in (old.get("segments") or []) + (new.get("segments") or []) for k in s} - {"start", "end"}
        groups |= {check.group_of(k) for k in keys if runs(old.get("segments"), k, ranges) != runs(new.get("segments"), k, ranges)}
        if old.get("segmentsTruncated") != new.get("segmentsTruncated"):
            groups.add("font")
    return sorted(groups)


def stale_layer_name(old, new):
    name = old.get("name") or ""
    was_auto = old.get("type") == "TEXT" and len(name.strip()) >= 3 and (old.get("characters") or "").startswith(name)
    return was_auto and new.get("name") == name and old.get("characters") != new.get("characters") and not (new.get("characters") or "").startswith(name)


def build_result(command, before, after, reported=None, run_dir=None, artifacts=()):
    if before["rootId"] != after["rootId"]:
        fail(f"before ({before['rootId']}) and after ({after['rootId']}) snapshots cover different roots.")
    b = {n["id"]: n for n in before["nodes"]}
    a = {n["id"]: n for n in after["nodes"]}
    expected = command.get("expectedChanges") or {}
    planned_props = expected.get("mutatedProperties") or {}
    planned_update = list(expected.get("mutatedNodeIds") or [])
    # A swap rebuilds the instance's sublayers under new ids (I<instance>;<main child>). For a swap
    # planned as other:mainComponentId those come and go with the instance, not as creates or deletes.
    swapped = [i for i in a if i in b and a[i].get("mainComponentId") != b[i].get("mainComponentId")
               and i in planned_update and "other:mainComponentId" in (planned_props.get(i) or [])]
    sublayer = tuple((i if i.startswith("I") else "I" + i) + ";" for i in swapped)
    is_sublayer = lambda node_id: bool(sublayer) and node_id.startswith(sublayer)
    created = [a[i] for i in a if i not in b and not is_sublayer(i)]
    deleted = [i for i in b if i not in a and not is_sublayer(i)]
    sublayers = {"created": [{"id": i, "name": a[i]["name"]} for i in a if i not in b and is_sublayer(i)], "deleted": [i for i in b if i not in a and is_sublayer(i)]}
    groups = {i: changed_groups(b[i], a[i]) for i in a if i in b}
    updated = [i for i in a if groups.get(i)]
    guards = command.get("guards") or {}
    prefix = guards.get("generatedPrefix") or ""
    planned_names = list(expected.get("createdNames") or [])
    planned_delete = [check.entry_id(e) for e in ((command.get("args") or {}).get("delete") or [])]
    dry = command.get("dryRun") is not False
    unexpected = []
    if not dry and Counter(n["name"] for n in created) != Counter(planned_names):
        unexpected.append({"kind": "created-names", "expected": sorted(planned_names), "actual": sorted(n["name"] for n in created)})
    created_ids = {n["id"] for n in created}
    for n in created:
        if n.get("parentId") not in created_ids and not n["name"].startswith(prefix or "\0"):
            unexpected.append({"kind": "created-without-prefix", "id": n["id"], "name": n["name"]})
    if len(created) > (guards.get("maxCreatedNodes") or 0):
        unexpected.append({"kind": "over-maxCreatedNodes", "count": len(created), "max": guards.get("maxCreatedNodes") or 0})
    for i in updated:
        if i not in planned_update:
            unexpected.append({"kind": "unplanned-update", "id": i, "fields": groups[i]})
            continue
        allowed = planned_props.get(i)
        extra = [g for g in groups[i] if (g not in allowed if isinstance(allowed, list) else check.is_explicit_group(g))]
        if extra:
            unexpected.append({"kind": "unplanned-field", "id": i, "fields": extra, "planned": allowed if isinstance(allowed, list) else f"any group except {', '.join(EXPLICIT_GROUPS)} (list those in expectedChanges.mutatedProperties)"})
    def under_planned_delete(node_id):
        while node_id in b:
            if node_id in planned_delete:
                return True
            node_id = b[node_id].get("parentId")
        return False

    unexpected += [{"kind": "unplanned-delete", "id": i} for i in deleted if not under_planned_delete(i)]
    if isinstance(reported, list):
        reported = {"created": reported}
    if reported is not None:
        reported_ids = {i for i in reported.get("created") or [] if not is_sublayer(i)}
        if reported_ids != created_ids:
            unexpected.append({"kind": "report-differs-from-readback", "field": "created", "reported": sorted(reported_ids), "readback": sorted(created_ids)})
        if reported.get("updated") is not None:
            said = set(reported["updated"])
            missed = [i for i in updated if i not in said and i not in planned_update]
            phantom = sorted(said - set(updated) - created_ids)
            if missed:
                unexpected.append({"kind": "report-missed-change", "field": "updated", "ids": missed, "fields": {i: groups[i] for i in missed}})
            if phantom:
                unexpected.append({"kind": "report-claims-unseen-change", "field": "updated", "ids": phantom})
        if reported.get("deleted") is not None:
            said = set(reported["deleted"])
            def under_reported(node_id):
                while node_id in b:
                    if node_id in said:
                        return True
                    node_id = b[node_id].get("parentId")
                return False
            missed = [i for i in deleted if not under_reported(i)]
            phantom = sorted(said - set(deleted) - set(sublayers["deleted"]))
            if missed:
                unexpected.append({"kind": "report-missed-change", "field": "deleted", "ids": missed})
            if phantom:
                unexpected.append({"kind": "report-claims-unseen-change", "field": "deleted", "ids": phantom})
    changed = bool(created or deleted or updated)
    ok = not unexpected and not (dry and changed)
    result = {
        "schemaVersion": "figma-cli-result/v1",
        "commandId": command.get("commandId"),
        "transport": command.get("transport"),
        "ok": ok,
        "status": ("dry-run" if dry else "passed") if ok else ("dry_run_violation" if dry and changed else "failed"),
        "plan": {"create": planned_names, "update": planned_update, "delete": planned_delete, "reparent": []},
        "changes": {"created": [{"id": n["id"], "name": n["name"]} for n in created], "updated": updated, "deleted": [i for i in deleted if i in planned_delete or not under_planned_delete(i)], "reparented": [], "selection": [], "swappedInstanceSublayers": sublayers},
        "verification": {"ok": ok, "source": "snapshot-diff", "snapshotSchema": after["schemaVersion"], "rootId": after["rootId"], "fieldChanges": {i: groups[i] for i in updated}, "unexpectedChanges": unexpected},
        "artifacts": [],
        "warnings": [{"kind": "stale-layer-name", "id": i, "name": a[i]["name"], "characters": a[i].get("characters")} for i in updated if stale_layer_name(b[i], a[i])],
        "retryable": False,
        "errorCode": None if ok else "verification_failed",
        "error": None,
    }
    for path in artifacts:
        rel = Path(path).resolve().relative_to(Path(run_dir).resolve().parent.parent) if run_dir else Path(path)
        result["artifacts"].append({"type": Path(path).suffix.lstrip(".") or "file", "path": rel.as_posix(), "sha256": sha256(path)})
    return result


REPORT_KEYS = {"created": ("createdNodeIds", "created"), "updated": ("updatedNodeIds", "updated"), "deleted": ("deletedNodeIds", "deleted")}


def reported_ids(data):
    out = {"created": []}
    for field, keys in REPORT_KEYS.items():
        for key in keys:
            value = data.get(key) if isinstance(data, dict) else None
            if isinstance(value, dict):
                out[field] = list(value)
                break
            if isinstance(value, list):
                out[field] = [check.entry_id(v) for v in value]
                break
    return out


def load_reported(path):
    data = unwrap(path, lambda v: any(k in v for keys in REPORT_KEYS.values() for k in keys))
    return reported_ids(data or {})


def result_cmd(args):
    command = json.loads(Path(args.command).read_text(encoding="utf-8"))
    out = build_result(command, load_snapshot(args.before), load_snapshot(args.after),
                       load_reported(args.reported) if args.reported else None,
                       run_dir=Path(args.out).parent, artifacts=args.artifact or [])
    text = json.dumps(out, indent=2, ensure_ascii=False)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    findings = check.CHECKER.run(text, "result")
    for f in findings:
        print(show(f, args.out))
    print(json.dumps({"ok": out["ok"], "status": out["status"], "unexpectedChanges": out["verification"]["unexpectedChanges"]}, ensure_ascii=False))
    sys.exit(0 if out["ok"] and not findings else 1)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="connector.py", description="figma-rails connector transport: render linted use_figma code, and build a readback-verified result.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="snippet (+ envelope) -> runs/<commandId>/<name>.js (live) or runs/<commandId>/dry-run/<name>.js (dry run) that passed --kind code")
    r.add_argument("snippet")
    r.add_argument("--command", help="figma-cli-command/v1 envelope; omit for a read-only snippet")
    r.add_argument("--args", help="JSON passed as `args` to a function snippet")
    r.add_argument("-o", "--out", required=True)
    r.set_defaults(func=render)
    s = sub.add_parser("result", help="before/after snapshots -> figma-cli-result/v1 checked with --kind result")
    s.add_argument("--command", required=True)
    s.add_argument("--before", required=True)
    s.add_argument("--after", required=True)
    s.add_argument("--reported", help="use_figma's own return value (createdNodeIds, updatedNodeIds, deletedNodeIds), compared with the readback")
    s.add_argument("--artifact", action="append")
    s.add_argument("-o", "--out", required=True)
    s.set_defaults(func=result_cmd)
    g = sub.add_parser("ingest", help="unwrap use_figma write_to_file results into plain JSON (a snapshot by default; pages are merged)")
    g.add_argument("input", nargs="+", help="one snapshot file, or every page file of a paged snapshot")
    g.add_argument("--returned", action="store_true", help="unwrap use_figma's return value (returned.json) instead of a snapshot")
    g.add_argument("-o", "--out", required=True)
    g.set_defaults(func=ingest)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
