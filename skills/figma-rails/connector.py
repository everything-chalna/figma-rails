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
SNAPSHOT_FIELDS = ("name", "type", "parentId", "x", "y", "width", "height", "visible", "locked", "characters")


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


def render(args):
    snippet = Path(args.snippet).read_text(encoding="utf-8").strip()
    command = READ_ONLY_COMMAND
    if args.command:
        text = Path(args.command).read_text(encoding="utf-8")
        report(check.CHECKER.run(text, "envelope"), args.command, "envelope")
        command = json.loads(text)
        if command.get("transport") != check.CONNECTOR_TRANSPORT:
            fail(f"{args.command} transport is {command.get('transport')!r}; render only connector commands.")
        if not Path(args.out).as_posix().endswith(command["script"]):
            fail(f"-o {args.out} is not the envelope's script {command['script']}; the file that is linted must be the file that is sent.")
    call_args = json.loads(args.args) if args.args else {}
    body = f"return await ({snippet})(args);" if FUNCTION_SNIPPET_RE.match(snippet) else snippet
    code = f"const command = {json.dumps(command, ensure_ascii=False)};\nconst args = {json.dumps(call_args, ensure_ascii=False)};\n{body}\n"
    report(check.CHECKER.run(code, "code"), args.out, "code")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(code, encoding="utf-8")
    print(f"PASS: rendered {args.out}; send it with use_figma code='$file:{Path(args.out).resolve()}'")


def load_snapshot(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, str):
        data = json.loads(data)
    if data.get("schemaVersion") != "figma-sink-snapshot/v1" or not isinstance(data.get("nodes"), list):
        fail(f"{path} is not a snippets/snapshot.js result (figma-sink-snapshot/v1).")
    return data


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_result(command, before, after, reported=None, run_dir=None, artifacts=()):
    if before["rootId"] != after["rootId"]:
        fail(f"before ({before['rootId']}) and after ({after['rootId']}) snapshots cover different roots.")
    b = {n["id"]: n for n in before["nodes"]}
    a = {n["id"]: n for n in after["nodes"]}
    created = [a[i] for i in a if i not in b]
    deleted = [i for i in b if i not in a]
    updated = [i for i in a if i in b and any(a[i].get(k) != b[i].get(k) for k in SNAPSHOT_FIELDS)]
    expected = command.get("expectedChanges") or {}
    guards = command.get("guards") or {}
    prefix = guards.get("generatedPrefix") or ""
    planned_names = list(expected.get("createdNames") or [])
    planned_update = list(expected.get("mutatedNodeIds") or [])
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
    unexpected += [{"kind": "unplanned-update", "id": i} for i in updated if i not in planned_update]
    def under_planned_delete(node_id):
        while node_id in b:
            if node_id in planned_delete:
                return True
            node_id = b[node_id].get("parentId")
        return False

    unexpected += [{"kind": "unplanned-delete", "id": i} for i in deleted if not under_planned_delete(i)]
    if reported is not None:
        reported_ids = set(reported) if isinstance(reported, list) else set()
        if reported_ids != created_ids:
            unexpected.append({"kind": "report-differs-from-readback", "reported": sorted(reported_ids), "readback": sorted(created_ids)})
    changed = bool(created or deleted or updated)
    ok = not unexpected and not (dry and changed)
    result = {
        "schemaVersion": "figma-cli-result/v1",
        "commandId": command.get("commandId"),
        "transport": command.get("transport"),
        "ok": ok,
        "status": ("dry-run" if dry else "passed") if ok else ("dry_run_violation" if dry and changed else "failed"),
        "plan": {"create": planned_names, "update": planned_update, "delete": planned_delete, "reparent": []},
        "changes": {"created": [{"id": n["id"], "name": n["name"]} for n in created], "updated": updated, "deleted": [i for i in deleted if i in planned_delete or not under_planned_delete(i)], "reparented": [], "selection": []},
        "verification": {"ok": ok, "source": "snapshot-diff", "rootId": after["rootId"], "unexpectedChanges": unexpected},
        "artifacts": [],
        "warnings": [],
        "retryable": False,
        "errorCode": None if ok else "verification_failed",
        "error": None,
    }
    for path in artifacts:
        rel = Path(path).resolve().relative_to(Path(run_dir).resolve().parent.parent) if run_dir else Path(path)
        result["artifacts"].append({"type": Path(path).suffix.lstrip(".") or "file", "path": rel.as_posix(), "sha256": sha256(path)})
    return result


def reported_created(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("createdNodeIds", "created"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, dict):
            return list(value)
        if isinstance(value, list):
            return [check.entry_id(v) for v in value]
    return []


def result_cmd(args):
    command = json.loads(Path(args.command).read_text(encoding="utf-8"))
    out = build_result(command, load_snapshot(args.before), load_snapshot(args.after),
                       reported_created(args.reported) if args.reported else None,
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
    r = sub.add_parser("render", help="snippet (+ envelope) -> runs/<commandId>/<name>.js that passed --kind code")
    r.add_argument("snippet")
    r.add_argument("--command", help="figma-cli-command/v1 envelope; omit for a read-only snippet")
    r.add_argument("--args", help="JSON passed as `args` to a function snippet")
    r.add_argument("-o", "--out", required=True)
    r.set_defaults(func=render)
    s = sub.add_parser("result", help="before/after snapshots -> figma-cli-result/v1 checked with --kind result")
    s.add_argument("--command", required=True)
    s.add_argument("--before", required=True)
    s.add_argument("--after", required=True)
    s.add_argument("--reported", help="use_figma's own return value, compared with the readback")
    s.add_argument("--artifact", action="append")
    s.add_argument("-o", "--out", required=True)
    s.set_defaults(func=result_cmd)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
