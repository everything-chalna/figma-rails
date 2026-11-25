import importlib.util
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "figma-rails"


def load_checker(skill_dir):
    path = Path(skill_dir) / "check.py"
    spec = importlib.util.spec_from_file_location("figma_rails_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CHECKER


CHECKER = load_checker(SKILL)
FIXTURES = SKILL / "tests" / "fixtures"


def png(width, height):
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


class FixtureTest(unittest.TestCase):
    """Runs this skill's fixtures from the checkout, with no external checker package."""

    def test_every_kind_has_fixtures_and_every_rule_a_failing_example(self):
        for kind, spec in CHECKER.kinds.items():
            passes = sorted((FIXTURES / kind / "pass").glob("*"))
            fails = sorted((FIXTURES / kind / "fail").glob("*"))
            self.assertTrue(passes, f"{kind}: no pass fixture")
            covered = {p.name.split("__", 1)[0] for p in fails}
            self.assertEqual(set(spec.rules) - covered, set(), f"{kind}: rules without a failing fixture")

    def test_pass_fixtures_pass(self):
        for kind in CHECKER.kinds:
            for path in sorted((FIXTURES / kind / "pass").glob("*")):
                with self.subTest(path=path.name):
                    self.assertEqual(CHECKER.run(path.read_text(encoding="utf-8"), kind), [])

    def test_fail_fixtures_hit_their_rule(self):
        for kind in CHECKER.kinds:
            for path in sorted((FIXTURES / kind / "fail").glob("*")):
                rule = path.name.split("__", 1)[0]
                with self.subTest(path=path.name):
                    self.assertIn(rule, {f.rule for f in CHECKER.run(path.read_text(encoding="utf-8"), kind)})


class ImageFileTest(unittest.TestCase):
    def test_reads_real_png_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            tall, ok = Path(tmp, "full-page.png"), Path(tmp, "band-1.png")
            tall.write_bytes(png(1440, 5801))
            ok.write_bytes(png(1440, 4096))
            found = CHECKER.run(f"import {tall}\nimport {ok}\n", "image")
            self.assertEqual([(f.rule, f.line) for f in found], [("fi-import", 1)])
            self.assertIn("2 tiles", found[0].message)

    def test_rejects_non_image_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            webp = Path(tmp, "x.webp")
            webp.write_bytes(b"RIFF0000WEBPVP8 ")
            self.assertEqual([f.rule for f in CHECKER.run(f"import {webp}\n", "image")], ["fi-format"])


class CliTest(unittest.TestCase):
    def test_exit_codes(self):
        good = FIXTURES / "envelope" / "pass" / "select-node-dry-run.json"
        bad = FIXTURES / "envelope" / "fail" / "fe-target__url-node-id.json"
        run = lambda p: subprocess.run([sys.executable, str(SKILL / "check.py"), str(p), "--kind", "envelope"], capture_output=True, text=True)
        self.assertEqual(run(good).returncode, 0)
        result = run(bad)
        self.assertEqual(result.returncode, 1)
        self.assertIn("712:538", result.stdout)


if __name__ == "__main__":
    unittest.main()
