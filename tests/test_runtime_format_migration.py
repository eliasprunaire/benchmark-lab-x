"""Read-only compatibility for immutable historical result formats."""

import json
import unittest
from unittest import mock

from benchmark.prototype import __main__ as demo
from tests import test_historical_reader as fixtures


class FormatMigrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.HistoricalReaderTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_new_formats_and_historical_readers_preserve_results(self):
        run = self.fixture.sealed()
        for name, kind in [
            ("results.json", "results"),
            ("final-seal.json", "final-seal"),
        ]:
            self.assertEqual(demo._load_json(run / name)["schema"], f"benchmark-lab-x-{kind}-1")

        # Synthetic historical fixture, never a rewrite of actual campaign evidence
        results = demo._load_json(run / "results.json")
        results["schema"] = "benchmark-lab-x-v2-alpha-results-1"
        (run / "results.json").write_text(json.dumps(results))
        seal = demo._load_json(run / "final-seal.json")
        seal["schema"] = "benchmark-lab-x-v2-alpha-final-seal-1"
        seal["files"]["results.json"] = demo._sha(run / "results.json")
        (run / "final-seal.json").write_text(json.dumps(seal))
        original = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}

        presentation = self.fixture.root / "runs" / "presentation"
        with mock.patch.object(demo.subprocess, "run", return_value=mock.Mock(returncode=0)):
            demo.show(run, self.fixture.root)
        demo.present(run, presentation, self.fixture.root)
        self.assertEqual((presentation / "results.json").read_bytes(), original[run.joinpath("results.json").relative_to(run)])
        self.assertEqual(demo._load_json(presentation / "presentation-seal.json")["schema"], "benchmark-lab-x-presentation-seal-1")
        self.assertEqual(demo._load_json(presentation / "source.json")["schema"], "benchmark-lab-x-presentation-source-1")

        source = demo._load_json(presentation / "source.json")
        source["schema"] = "benchmark-lab-x-v2-alpha-presentation-source-1"
        (presentation / "source.json").write_text(json.dumps(source))
        seal = demo._load_json(presentation / "presentation-seal.json")
        seal["schema"] = "benchmark-lab-x-v2-alpha-presentation-seal-1"
        seal["source_sha256"] = seal["files"]["source.json"] = demo._sha(presentation / "source.json")
        (presentation / "presentation-seal.json").write_text(json.dumps(seal))
        with mock.patch.object(demo.subprocess, "run", return_value=mock.Mock(returncode=0)):
            demo.show(presentation, self.fixture.root)
        self.assertEqual(original, {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()})

        (run / "results.json").write_bytes(b"tampered")
        with mock.patch.object(demo.subprocess, "run") as opener:
            with self.assertRaisesRegex(ValueError, "altéré"):
                demo.show(presentation, self.fixture.root)
            opener.assert_not_called()

    def test_historical_preparation_cannot_be_presented(self):
        run = self.fixture.root / "runs" / "prepared"
        run.mkdir(mode=0o700)
        self.fixture.write(run / "seal.json", {"schema": "benchmark-lab-x-v2-alpha-seal-1"})
        target = self.fixture.root / "runs" / "presentation"
        original = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
        with mock.patch.object(demo.subprocess, "Popen") as launch:
            with self.assertRaises(FileNotFoundError):
                demo.present(run, target, self.fixture.root)
            launch.assert_not_called()
        self.assertFalse(target.exists())
        self.assertEqual(original, {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()})

    def test_unknown_result_format_is_rejected_before_presentation(self):
        run = self.fixture.sealed()
        results = demo._load_json(run / "results.json")
        results["schema"] = "benchmark-lab-x-results-999"
        (run / "results.json").write_text(json.dumps(results))
        seal = demo._load_json(run / "final-seal.json")
        seal["files"]["results.json"] = demo._sha(run / "results.json")
        (run / "final-seal.json").write_text(json.dumps(seal))
        target = self.fixture.root / "runs" / "unknown"
        with self.assertRaisesRegex(ValueError, "résultats source invalides"):
            demo.present(run, target, self.fixture.root)
        self.assertFalse(target.exists())
