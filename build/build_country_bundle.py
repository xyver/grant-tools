"""Build one country's data bundle ZIP and its download-page control file.

    python build/build_country_bundle.py --country can --version 0.2.0 --out <folder>

Reads the tables listed for that country in build/bundle_sources.json from
GRANT_DATA_ROOT, and writes to --out:

    grant-<country>-<version>.zip   countries/<ISO3>/... plus that country's
                                    grant_data_manifest.json (with schema_version)
    current.json                    the control the project page reads
    build_receipt.json              sizes, SHA-256s, and the checks that passed

A file entry is a path, or {"path": ..., "columns": [...]} to ship only
those columns (the tools' lookup columns plus a little context) when the
full table is large; the full table stays a pipeline build.

It never uploads. Publishing the ZIP and then current.json to the downloads
bucket is a separate, manual step. Fails without writing a ZIP if a listed
file is missing, a table is unreadable, or the control would not pass the
project page's own validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import data_bundle  # noqa: E402
from runtime_paths import DATA_ROOT  # noqa: E402

SOURCES_FILE = Path(__file__).resolve().parent / "bundle_sources.json"
DOWNLOAD_BASE = "https://downloads.daedalmap.com/downloadable/grants"
ISO3 = {"usa": "USA", "can": "CAN"}
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_table(path: Path) -> int:
    """Row count, proving the table opens. Raises if it cannot be read."""
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        return pq.ParquetFile(path).metadata.num_rows
    if path.suffix == ".csv":
        with path.open("r", encoding="utf-8") as handle:
            return max(sum(1 for _ in handle) - 1, 0)
    raise ValueError(f"Unsupported table type: {path}")


def trimmed_copy(path: Path, columns: list[str], work_dir: Path) -> Path:
    """A parquet with only `columns`, for shipping a slim bundle."""
    import pyarrow.parquet as pq

    missing = sorted(set(columns) - set(pq.ParquetFile(path).schema_arrow.names))
    if missing:
        raise ValueError(f"{path.name} has no column(s): {', '.join(missing)}")
    out = work_dir / f"{len(list(work_dir.iterdir()))}_{path.name}"
    pq.write_table(pq.read_table(path, columns=columns), out, compression="zstd")
    return out


def validate_control(control: dict, country: str) -> list[str]:
    """The same rules as the project page's validRelease() check."""
    problems = []
    if control.get("status") != "published" or control.get("country") != country:
        problems.append("status/country")
    if not VERSION_PATTERN.match(str(control.get("version") or "")):
        problems.append("version")
    coverage = str(control.get("coverage") or "")
    if not coverage.strip() or len(coverage) > 160:
        problems.append("coverage must be 1-160 characters")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(control.get("as_of") or "")):
        problems.append("as_of")
    sources = control.get("sources") or []
    if not sources or not all(isinstance(s, str) and len(s) <= 100 for s in sources):
        problems.append("sources")
    if not re.match(r"^[a-f0-9]{64}$", str(control.get("sha256") or "")):
        problems.append("sha256")
    if not isinstance(control.get("size_bytes"), int) or control["size_bytes"] <= 0:
        problems.append("size_bytes")
    url = str(control.get("download_url") or "")
    if not (url.startswith(f"{DOWNLOAD_BASE}/{country}/") and url.endswith(".zip") and "?" not in url):
        problems.append("download_url")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Grant Tools country data bundle")
    parser.add_argument("--country", required=True, choices=sorted(ISO3))
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--sources", type=Path, default=SOURCES_FILE, help="Release metadata (default: build/bundle_sources.json)")
    args = parser.parse_args(argv)

    if not VERSION_PATTERN.match(args.version):
        parser.error("version must match the project page's pattern, e.g. 0.2.0")
    config = json.loads(args.sources.read_text(encoding="utf-8"))[args.country]
    iso3 = ISO3[args.country]

    work = tempfile.TemporaryDirectory()
    work_dir = Path(work.name)
    files, source_details, staged = [], [], {}
    for source in config["sources"]:
        detail = {k: source[k] for k in ("name", "url", "licence", "vintage")}
        if source.get("also_from"):
            detail["also_from"] = source["also_from"]
        source_details.append(detail)
        for item in source["files"]:
            rel = item if isinstance(item, str) else item["path"]
            columns = None if isinstance(item, str) else item.get("columns")
            path = args.data_root / rel
            if not path.is_file():
                print(f"Missing {rel} under {args.data_root}; nothing written.")
                return 1
            if not rel.startswith(f"countries/{iso3}/"):
                print(f"{rel} is outside countries/{iso3}/; nothing written.")
                return 1
            if columns:
                try:
                    path = trimmed_copy(path, columns, work_dir)
                except ValueError as exc:
                    print(f"{exc}; nothing written.")
                    return 1
            staged[rel] = path
            entry = {
                "path": rel,
                "bytes": path.stat().st_size,
                "sha256": sha256_of(path),
                "rows": check_table(path),
                "source": source["name"],
            }
            if columns:
                entry["columns"] = columns
                entry["note"] = "Trimmed to these columns; build the full table with the pipeline."
            files.append(entry)

    vintages = sorted(s["vintage"] for s in config["sources"])
    manifest = {
        "name": "Grant Tools country data",
        "country": args.country,
        "version": args.version,
        "schema_version": data_bundle.BUNDLE_SCHEMA_VERSION,
        "source_vintage_through": vintages[-1],
        "coverage_summary": config["coverage"],
        "sources": [s["name"] for s in config["sources"]],
        "source_details": source_details,
        "files": files,
        "note": "Research tables; not a DaedalMap map data pack. Unzip so countries/ is under GRANT_DATA_ROOT.",
    }

    args.out.mkdir(parents=True, exist_ok=True)
    zip_name = f"grant-{args.country}-{args.version}.zip"
    zip_path = args.out / zip_name
    manifest_arcname = f"countries/{iso3}/{data_bundle.MANIFEST_NAME}"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for entry in files:
            bundle.write(staged[entry["path"]], entry["path"])
        bundle.writestr(manifest_arcname, json.dumps(manifest, indent=2) + "\n")
    with zipfile.ZipFile(zip_path) as bundle:
        bad = bundle.testzip()
        names = bundle.namelist()
    if bad or sorted(names) != sorted([f["path"] for f in files] + [manifest_arcname]):
        zip_path.unlink()
        print(f"ZIP verification failed ({bad or 'member list mismatch'}); nothing kept.")
        return 1

    control = {
        "status": "published",
        "country": args.country,
        "version": args.version,
        "download_url": f"{DOWNLOAD_BASE}/{args.country}/{zip_name}",
        "size_bytes": zip_path.stat().st_size,
        "sha256": sha256_of(zip_path),
        "coverage": config["coverage"],
        "as_of": vintages[-1],
        "sources": [s["name"] for s in config["sources"]],
    }
    problems = validate_control(control, args.country)
    if problems:
        zip_path.unlink()
        print(f"Control would fail the project page check ({', '.join(problems)}); nothing kept.")
        return 1
    (args.out / "current.json").write_text(json.dumps(control, indent=2) + "\n", encoding="utf-8")
    receipt = {
        "built": date.today().isoformat(),
        "zip": zip_name,
        "size_bytes": control["size_bytes"],
        "sha256": control["sha256"],
        "schema_version": manifest["schema_version"],
        "files": files,
        "checks": ["all listed files present", "every table opens", "zip members verified",
                   "control passes the project page check"],
    }
    (args.out / "build_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"Built {zip_path} ({control['size_bytes']:,} bytes, sha256 {control['sha256']})")
    for entry in files:
        print(f"  {entry['path']}: {entry['rows']:,} rows, {entry['bytes']:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
