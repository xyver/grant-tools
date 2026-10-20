import io
import os
import tempfile
import threading
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd

from runtime_paths import DATA_ROOT

try:
    import boto3
except ImportError:
    boto3 = None


# Filtered reads use DuckDB pushdown (read_parquet + WHERE), so only matching
# row groups are read, locally or from object storage over httpfs. The grant
# tools own this engine so they run without DaedalMap's app code. A host that
# already runs a bounded DuckDB pool (the DaedalMap hosted app) can install it
# with set_query_runner(); the grant tools still choose every URI themselves,
# from data_mode(), so the two can never disagree about local versus remote.

_thread_state = threading.local()


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _sql_literal(value) -> str:
    """Quote a value for a DuckDB SET statement, which takes no bound parameters."""
    return "'" + str(value).replace("'", "''") + "'"


def _new_connection():
    con = duckdb.connect(database=":memory:")
    con.execute(f"SET memory_limit={_sql_literal(os.environ.get('GRANT_DUCKDB_MEMORY_LIMIT', '256MB'))}")
    con.execute(f"SET threads={max(1, int(os.environ.get('GRANT_DUCKDB_THREADS', '2')))}")
    con.execute("SET preserve_insertion_order=false")
    if data_mode() == "object_store":
        extension_dir = Path(
            os.environ.get("GRANT_DUCKDB_EXTENSION_DIR", "")
            or Path(tempfile.gettempdir()) / "grant_duckdb_extensions"
        )
        extension_dir.mkdir(parents=True, exist_ok=True)
        con.execute(f"SET extension_directory={_sql_literal(extension_dir)}")
        try:
            con.execute("LOAD httpfs")
        except Exception:
            con.execute("INSTALL httpfs")
            con.execute("LOAD httpfs")
        endpoint = str(os.environ.get("S3_ENDPOINT_URL", "") or "").strip()
        endpoint = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
        if endpoint:
            con.execute(f"SET s3_endpoint={_sql_literal(endpoint)}")
        key = str(os.environ.get("AWS_ACCESS_KEY_ID", "") or "").strip()
        secret = str(os.environ.get("AWS_SECRET_ACCESS_KEY", "") or "").strip()
        if key:
            con.execute(f"SET s3_access_key_id={_sql_literal(key)}")
        if secret:
            con.execute(f"SET s3_secret_access_key={_sql_literal(secret)}")
        con.execute(f"SET s3_region={_sql_literal(str(os.environ.get('AWS_DEFAULT_REGION', '') or '').strip() or 'auto')}")
        con.execute("SET s3_url_style='path'")
        con.execute("SET enable_http_metadata_cache=true")
    return con


def _default_run_df(sql: str, params: list) -> pd.DataFrame:
    """Run on this thread's grant-owned connection; rebuild it after a transport error."""
    con = getattr(_thread_state, "connection", None)
    if con is None:
        con = _thread_state.connection = _new_connection()
    try:
        return con.execute(sql, params).df()
    except Exception as exc:
        if type(exc).__name__ in {"IOException", "HTTPException", "ConnectionException"}:
            _thread_state.connection = None
        raise


_query_runner: Callable[[str, list], pd.DataFrame] = _default_run_df


def set_query_runner(runner: Callable[[str, list], pd.DataFrame] | None) -> None:
    """Install a host-provided DuckDB runner, or None to restore the built-in one.

    The runner must accept (sql, params) and return a DataFrame, and must be able
    to read whatever URI data_mode() produces (local paths, or s3:// in
    object_store mode).
    """
    global _query_runner
    _query_runner = runner or _default_run_df


def run_df(sql: str, params: list) -> pd.DataFrame:
    return _query_runner(sql, params)


def _like_escape(value: str) -> str:
    """Escape LIKE/ILIKE wildcards so a literal substring stays literal.

    Mirrors the semantics of the old `str.contains(re.escape(value))` path:
    the value is matched as a literal substring, not a pattern.
    """
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def data_mode() -> str:
    explicit = str(os.environ.get("GRANT_MCP_DATA_MODE", "") or "").strip().lower()
    if explicit in {"local", "object_store"}:
        return explicit

    runtime_mode = str(os.environ.get("RUNTIME_MODE", "") or "").strip().lower()
    install_mode = str(os.environ.get("INSTALL_MODE", "") or "").strip().lower()
    if runtime_mode == "cloud" or install_mode == "cloud":
        return "object_store"
    return "local"


def _object_store_client():
    if boto3 is None:
        return None
    endpoint_url = str(os.environ.get("S3_ENDPOINT_URL", "") or "").strip() or None
    region = (
        str(os.environ.get("AWS_DEFAULT_REGION", "") or "").strip()
        or str(os.environ.get("AWS_REGION", "") or "").strip()
        or "auto"
    )
    return boto3.client("s3", endpoint_url=endpoint_url, region_name=region)


def _object_store_bucket() -> str:
    return str(os.environ.get("S3_BUCKET", "") or "").strip()


def _object_store_prefix() -> str:
    return (
        str(os.environ.get("GRANT_MCP_DATA_S3_PREFIX", "") or "").strip()
        or str(os.environ.get("S3_PUBLISHED_PREFIX", "") or "").strip()
        or str(os.environ.get("S3_PREFIX", "") or "").strip()
        or "published"
    ).strip("/")


def _relative_data_path(path: Path) -> str | None:
    try:
        return path.resolve().relative_to(DATA_ROOT.resolve()).as_posix()
    except Exception:
        return None


def _object_store_key_for_data_path(path: Path) -> str | None:
    relative = _relative_data_path(path)
    if not relative:
        return None
    prefix = _object_store_prefix()
    return f"{prefix}/{relative}" if prefix else relative


def _read_object_bytes(path: Path) -> bytes:
    key = _object_store_key_for_data_path(path)
    bucket = _object_store_bucket()
    client = _object_store_client()
    if not key or not bucket or client is None:
        raise FileNotFoundError(f"Local file not found and object-store access unavailable for {path}")
    obj = client.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()


def read_csv(path: Path, **kwargs):
    mode = data_mode()
    if mode == "local":
        if not path.exists():
            raise FileNotFoundError(f"Grant local data file not found: {path}")
        return pd.read_csv(path, **kwargs)
    raw = _read_object_bytes(path)
    return pd.read_csv(io.BytesIO(raw), **kwargs)


def read_parquet(path: Path, **kwargs):
    mode = data_mode()
    if mode == "local":
        if not path.exists():
            raise FileNotFoundError(f"Grant local data file not found: {path}")
        return pd.read_parquet(path, **kwargs)
    raw = _read_object_bytes(path)
    return pd.read_parquet(io.BytesIO(raw), **kwargs)


def exists(path: Path) -> bool:
    mode = data_mode()
    if mode == "local":
        return path.exists()
    key = _object_store_key_for_data_path(path)
    bucket = _object_store_bucket()
    client = _object_store_client()
    if not key or not bucket or client is None:
        return False
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Pushdown queries against USA.parquet (2.3M-row award file)
#
# These replace "read the whole file into pandas, then filter" with a DuckDB
# read_parquet(?) + WHERE so only matching row groups are read. Each returns a
# pandas DataFrame with exactly the columns the caller aggregates over, so the
# downstream logic in us_backend.py is unchanged (output stays byte-identical
# against the regression suite).
# ---------------------------------------------------------------------------

def _pushdown_uri(path: Path) -> str:
    """Resolve the DuckDB URI for a data file from data_mode() alone.

    The same object-store key serves the boto3 reads above and the pushdown
    queries here, so pandas reads and DuckDB reads always target the same
    object. (Before 2026-10-10 the pushdown URI came from DaedalMap's own
    cloud switch, which could disagree with data_mode() and crashed every
    phase-1 analysis in the hosted bundle on 2026-07-19.)
    """
    if data_mode() == "local":
        if not path.exists():
            raise FileNotFoundError(f"Grant local data file not found: {path}")
        return str(path)
    key = _object_store_key_for_data_path(path)
    bucket = _object_store_bucket()
    if not key or not bucket:
        raise RuntimeError(
            f"Grant data mode is 'object_store' but no object-store key could be built for {path}. "
            "Set S3_BUCKET, keep the file under DATA_ROOT, or set GRANT_MCP_DATA_MODE=local."
        )
    return f"s3://{bucket}/{key}"


def query_prior_awards_by_name(path: Path, org_name: str) -> pd.DataFrame:
    """Awards whose recipient_name contains `org_name` (case-insensitive substring).

    Equivalent to the old `recipient_name.str.contains(re.escape(org_name),
    case=False)`, but pushed into DuckDB. Caller must still guard empty names.
    """
    uri = _pushdown_uri(path)
    sql = (
        "SELECT recipient_name, cfda_numbers_and_titles, total_obligated_amount, "
        "award_base_action_date "
        "FROM read_parquet(?) "
        "WHERE recipient_name ILIKE ? ESCAPE '\\'"
    )
    return run_df(sql, [uri, f"%{_like_escape(org_name)}%"])


def query_country_precedent(path: Path, cfda_code: str, country_code: str) -> pd.DataFrame:
    """Awards for a CFDA code with place of performance in a specific country.

    Equivalent to the old `cfda_numbers_and_titles.str.contains(re.escape(
    cfda_code)) & (place_of_performance_country_code == country_code)`.
    cfda match is case-sensitive (LIKE) to preserve prior behavior.
    """
    uri = _pushdown_uri(path)
    sql = (
        "SELECT cfda_numbers_and_titles, place_of_performance_country_code, "
        "total_obligated_amount, recipient_business_type_description, "
        "award_description, award_base_action_date "
        "FROM read_parquet(?) "
        "WHERE place_of_performance_country_code = ? "
        "AND cfda_numbers_and_titles LIKE ? ESCAPE '\\'"
    )
    return run_df(sql, [uri, country_code, f"%{_like_escape(cfda_code)}%"])


def query_precedent_abstracts(
    path: Path, text_col: str, keyword_col: str, title_col: str, keyword: str | None = None
) -> pd.DataFrame:
    """Non-empty precedent abstracts, optionally filtered to a keyword substring.

    Equivalent to `df[df[text_col].notna() & (df[text_col].str.len() > 0)]`
    followed by an optional case-insensitive `keyword_col.str.contains(keyword)`.
    """
    uri = _pushdown_uri(path)
    tcol = quote_ident(text_col)
    select_cols = ", ".join(quote_ident(c) for c in (text_col, keyword_col, title_col))
    sql = (
        f"SELECT {select_cols} FROM read_parquet(?) "
        f"WHERE {tcol} IS NOT NULL AND length({tcol}) > 0"
    )
    params: list = [uri]
    if keyword:
        sql += f" AND {quote_ident(keyword_col)} ILIKE ? ESCAPE '\\'"
        params.append(f"%{_like_escape(keyword)}%")
    return run_df(sql, params)


def warm_object_store_reader() -> None:
    """Warm one DuckDB httpfs connection so the first real pushdown query does
    not pay the extension install/load + credential setup cost. No-op unless in
    object-store mode. A trivial query builds and configures this thread's
    connection; the httpfs extension install is written to disk once and
    reused by every worker thread's connection afterward. Deliberately does not
    read USA.parquet - its footer/http-metadata cache is per-thread and cannot
    be shared from a startup thread anyway."""
    if data_mode() != "object_store":
        return
    run_df("SELECT 1", [])
