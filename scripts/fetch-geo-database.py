#!/usr/bin/env python3
"""Fetch and validate an offline DB-IP City Lite database; no visitor data is sent."""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.request

DOWNLOAD_ORIGIN = "https://download.db-ip.com"
MAX_COMPRESSED_BYTES = 96 * 1024 * 1024
MAX_DATABASE_BYTES = 256 * 1024 * 1024
MIN_DATABASE_BYTES = 1024 * 1024
CHUNK_BYTES = 128 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("GeoIP download redirect rejected")


def release_months(now):
    """Only the requested current release and its immediate predecessor."""
    previous_year = now.year if now.month > 1 else now.year - 1
    previous_month = now.month - 1 if now.month > 1 else 12
    return [f"{now.year:04d}-{now.month:02d}", f"{previous_year:04d}-{previous_month:02d}"]


def limited_copy(source, destination, limit):
    total = 0
    while True:
        chunk = source.read(min(CHUNK_BYTES, limit - total + 1))
        if not chunk:
            return total
        total += len(chunk)
        if total > limit:
            raise ValueError("GeoIP download exceeds size limit")
        destination.write(chunk)


def validate_database(filename, now, open_database=None):
    if not MIN_DATABASE_BYTES <= Path(filename).stat().st_size <= MAX_DATABASE_BYTES:
        raise ValueError("GeoIP database size is invalid")
    if open_database is None:
        import maxminddb
        open_database = maxminddb.open_database
    with open_database(str(filename)) as reader:
        metadata = reader.metadata()
        database_type = str(metadata.database_type)
        if database_type.lower() != "dbip-city-lite":
            raise ValueError("Unexpected GeoIP database type")
        if metadata.binary_format_major_version != 2 or metadata.ip_version not in (4, 6):
            raise ValueError("Unsupported GeoIP database format")
        if type(metadata.node_count) is not int or metadata.node_count <= 0:
            raise ValueError("GeoIP database contains no search nodes")
        if type(metadata.build_epoch) is not int or not 1 <= metadata.build_epoch <= int(now.timestamp()) + 86400:
            raise ValueError("GeoIP database build date is invalid")
        if not isinstance(metadata.languages, (list, tuple)) or "en" not in metadata.languages:
            raise ValueError("GeoIP database has no English names")
        return {"databaseType": database_type, "buildEpoch": metadata.build_epoch,
                "ipVersion": metadata.ip_version, "bytes": Path(filename).stat().st_size}


def fetch_database(output, *, now=None, opener=None, validator=validate_database):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Build time must include a timezone")
    opener = opener or urllib.request.build_opener(NoRedirect())
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Temp files are outside the static publishing directory and never artifacts.
    with tempfile.TemporaryDirectory(prefix="geoip-download-", dir=output.parent) as directory:
        compressed = Path(directory) / "database.mmdb.gz"
        expanded = Path(directory) / "database.mmdb"
        for index, month in enumerate(release_months(now)):
            url = DOWNLOAD_ORIGIN + "/free/dbip-city-lite-" + month + ".mmdb.gz"
            request = urllib.request.Request(url, headers={"User-Agent": "homepage-offline-city-snapshot/1.0"})
            try:
                with opener.open(request, timeout=30) as response:
                    if response.status != 200 or response.geturl() != url:
                        raise ValueError("Unexpected GeoIP download response")
                    declared = response.headers.get("Content-Length")
                    if declared is not None and (not declared.isdigit() or int(declared) > MAX_COMPRESSED_BYTES):
                        raise ValueError("GeoIP compressed download is too large")
                    with compressed.open("wb") as target:
                        downloaded = limited_copy(response, target, MAX_COMPRESSED_BYTES)
                    if declared is not None and downloaded != int(declared):
                        raise ValueError("GeoIP compressed download is incomplete")
                with gzip.open(compressed, "rb") as source, expanded.open("wb") as target:
                    limited_copy(source, target, MAX_DATABASE_BYTES)
                metadata = validator(expanded, now)
                digest = hashlib.sha256()
                with expanded.open("rb") as source:
                    for block in iter(lambda: source.read(CHUNK_BYTES), b""):
                        digest.update(block)
                metadata.update({"release": month, "sha256": digest.hexdigest()})
                os.replace(expanded, output)
                return metadata
            except urllib.error.HTTPError as error:
                # A release may not yet exist on the first day of a new month.
                # Do not mask server errors, forbidden responses or bad data by
                # silently switching sources or retrying paid/API requests.
                if error.code == 404 and index == 0:
                    continue
                raise
    raise RuntimeError("No GeoIP release was available")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        metadata = fetch_database(args.output)
    except Exception:
        # Do not expose arbitrary provider responses. Existing maps remain the
        # responsibility of the snapshot updater; this script never edits them.
        print("Offline GeoIP database unavailable; retain the existing visitor map.")
        raise SystemExit(1)
    print("Validated offline GeoIP database: " + json.dumps(metadata, separators=(",", ":")))


if __name__ == "__main__":
    main()
