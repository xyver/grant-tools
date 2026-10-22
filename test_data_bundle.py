"""Bundle version checks and the bundle builder."""
import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import data_bundle  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "build_country_bundle", Path(__file__).resolve().parent / "build" / "build_country_bundle.py"
)
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class BundleStatusTests(unittest.TestCase):
    def test_missing_bundle_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            status = data_bundle.bundle_status("CAN", Path(root))
        self.assertFalse(status["installed"])
        self.assertIn("No CAN data", status["note"])

    def test_legacy_root_manifest_reads_as_schema_one(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "countries" / "CAN").mkdir(parents=True)
            _write(Path(root) / data_bundle.MANIFEST_NAME, {"country": "can", "version": "0.1.0"})
            status = data_bundle.bundle_status("CAN", Path(root))
        self.assertTrue(status["compatible"])
        self.assertEqual(status["schema_version"], 1)
        self.assertEqual(status["version"], "0.1.0")

    def test_legacy_manifest_for_another_country_is_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "countries" / "USA").mkdir(parents=True)
            _write(Path(root) / data_bundle.MANIFEST_NAME, {"country": "can", "version": "0.1.0"})
            status = data_bundle.bundle_status("US", Path(root))
        self.assertIsNone(status["version"])

    def test_per_country_manifest_and_newer_schema(self):
        with tempfile.TemporaryDirectory() as root:
            _write(Path(root) / "countries" / "CAN" / data_bundle.MANIFEST_NAME,
                   {"country": "can", "version": "9.0.0", "schema_version": data_bundle.BUNDLE_SCHEMA_VERSION + 1})
            status = data_bundle.bundle_status("CAN", Path(root))
        self.assertFalse(status["compatible"])
        self.assertIn("Update the grant-tools code", status["note"])


class BundleBuilderTests(unittest.TestCase):
    def test_builds_zip_manifest_and_valid_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root, out = Path(tmp) / "data", Path(tmp) / "out"
            table = data_root / "countries" / "CAN" / "example" / "table.csv"
            table.parent.mkdir(parents=True)
            table.write_text("a,b\n1,2\n", encoding="utf-8")
            sources = Path(tmp) / "sources.json"
            _write(sources, {"can": {"coverage": "Test coverage", "sources": [{
                "name": "Example", "url": "https://example.org", "licence": "Test", "vintage": "2026-10-21",
                "files": ["countries/CAN/example/table.csv"]}]}})
            code = builder.main(["--country", "can", "--version", "9.9.9", "--out", str(out),
                                 "--data-root", str(data_root), "--sources", str(sources)])
            self.assertEqual(code, 0)
            control = json.loads((out / "current.json").read_text(encoding="utf-8"))
            self.assertEqual(builder.validate_control(control, "can"), [])
            with zipfile.ZipFile(out / "grant-can-9.9.9.zip") as bundle:
                manifest = json.loads(bundle.read(f"countries/CAN/{data_bundle.MANIFEST_NAME}"))
            self.assertEqual(manifest["schema_version"], data_bundle.BUNDLE_SCHEMA_VERSION)
            self.assertEqual(manifest["files"][0]["rows"], 1)

    def test_columns_trim_the_shipped_table(self):
        import pandas as pd
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as tmp:
            data_root, out = Path(tmp) / "data", Path(tmp) / "out"
            table = data_root / "countries" / "CAN" / "big" / "CAN.parquet"
            table.parent.mkdir(parents=True)
            pd.DataFrame({"keep": [1, 2], "drop": ["long text", "more text"]}).to_parquet(table)
            sources = Path(tmp) / "sources.json"
            _write(sources, {"can": {"coverage": "x", "sources": [{
                "name": "Big", "url": "u", "licence": "l", "vintage": "2026-10-21",
                "files": [{"path": "countries/CAN/big/CAN.parquet", "columns": ["keep"]}]}]}})
            code = builder.main(["--country", "can", "--version", "1.0.0", "--out", str(out),
                                 "--data-root", str(data_root), "--sources", str(sources)])
            self.assertEqual(code, 0)
            extracted = Path(tmp) / "x"
            with zipfile.ZipFile(out / "grant-can-1.0.0.zip") as bundle:
                bundle.extractall(extracted)
                manifest = json.loads(bundle.read(f"countries/CAN/{data_bundle.MANIFEST_NAME}"))
            self.assertEqual(pq.ParquetFile(extracted / "countries/CAN/big/CAN.parquet").schema_arrow.names, ["keep"])
            self.assertEqual(manifest["files"][0]["columns"], ["keep"])

    def test_missing_table_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            sources = Path(tmp) / "sources.json"
            _write(sources, {"can": {"coverage": "x", "sources": [{
                "name": "Example", "url": "u", "licence": "l", "vintage": "2026-10-21",
                "files": ["countries/CAN/missing.csv"]}]}})
            code = builder.main(["--country", "can", "--version", "1.0.0", "--out", str(Path(tmp) / "out"),
                                 "--data-root", str(Path(tmp) / "data"), "--sources", str(sources)])
            self.assertEqual(code, 1)
            self.assertFalse((Path(tmp) / "out").exists() and any((Path(tmp) / "out").iterdir()))


class TriAgencyPrecedentTests(unittest.TestCase):
    def test_council_record_counts_distinct_awards_across_years(self):
        import os
        from unittest import mock

        import pandas as pd

        import can_backend

        os.environ["GRANT_MCP_DATA_MODE"] = "local"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CAN.parquet"
            pd.DataFrame({
                "ProgramNameEN_NomProgrammeAN": ["Project Grant", "Project Grant", "Project Grant", "Other"],
                "FundingReferenceNumber_NumeroReferenceFinancement": ["a", "a", "b", "c"],
                "AmountPaidFY_MontantPayeAF": [100.0, 100.0, 50.0, 7.0],
                "FiscalYear_AnneeFinanciere": ["201718", "201819", "201819", "201819"],
            }).to_parquet(path)
            spec = {**can_backend.TRI_AGENCY_TABLES["cihr-irsc"], "path": path}
            with mock.patch.dict(can_backend.TRI_AGENCY_TABLES, {"cihr-irsc": spec}):
                can_backend._tri_agency_summary.cache_clear()
                try:
                    result = can_backend.get_can_precedent("can_cihr_project_grant")
                finally:
                    can_backend._tri_agency_summary.cache_clear()
        self.assertTrue(result["found"])
        self.assertEqual(result["source"], "CIHR investments")
        self.assertEqual(result["award_count"], 2)  # grant "a" spans two fiscal years
        self.assertEqual(result["total_agreement_value"], 250.0)
        self.assertEqual(result["years_covered"], "201718 to 201819")

    def test_precedent_name_matches_exactly(self):
        import os
        from unittest import mock

        import pandas as pd

        import can_backend

        os.environ["GRANT_MCP_DATA_MODE"] = "local"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CAN.parquet"
            pd.DataFrame({
                "program_name_en": ["Discovery Grants Program - Individual",
                                    "Discovery Grants Program - Accelerator Supplements"],
                "application_id": ["1", "2"],
                "award_amount": [10.0, 5.0],
                "fiscal_year": ["2020", "2020"],
            }).to_parquet(path)
            spec = {**can_backend.TRI_AGENCY_TABLES["nserc-crsng"], "path": path}
            with mock.patch.dict(can_backend.TRI_AGENCY_TABLES, {"nserc-crsng": spec}):
                can_backend._tri_agency_summary.cache_clear()
                try:
                    result = can_backend.get_can_precedent("can_nserc_discovery")
                finally:
                    can_backend._tri_agency_summary.cache_clear()
        # The supplements are add-ons to individual grants; counting them double-counts.
        self.assertEqual(result["matched_variant_names"], ["Discovery Grants Program - Individual"])
        self.assertEqual(result["award_count"], 1)


if __name__ == "__main__":
    unittest.main()
