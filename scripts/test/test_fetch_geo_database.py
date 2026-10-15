import gzip
import importlib.util
import io
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

SPEC = importlib.util.spec_from_file_location("geo_download", Path(__file__).resolve().parents[1] / "fetch-geo-database.py")
geo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(geo)
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


class Response(io.BytesIO):
    status = 200

    def __init__(self, payload, url, length=None):
        super().__init__(payload)
        self.url = url
        self.headers = {"Content-Length": str(len(payload) if length is None else length)}

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, responses):
        self.responses, self.urls = list(responses), []

    def open(self, request, timeout):
        self.urls.append(request.full_url)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return Response(value, request.full_url)


def valid_metadata(path, now):
    if path.read_bytes() != b"local test MMDB bytes":
        raise ValueError("Invalid test fixture")
    return {"databaseType": "DBIP-City-Lite"}


class GeoDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name) / "db.mmdb"
        self.output.write_bytes(b"previous valid database")
        self.payload = gzip.compress(b"local test MMDB bytes")

    def tearDown(self):
        self.tmp.cleanup()

    def test_current_release_validates_before_atomic_replace(self):
        opener = Opener([self.payload])
        data = geo.fetch_database(self.output, now=NOW, opener=opener, validator=valid_metadata)
        self.assertEqual(self.output.read_bytes(), b"local test MMDB bytes")
        self.assertEqual(data["release"], "2026-10")
        self.assertEqual(len(data["sha256"]), 64)
        self.assertEqual(opener.urls, ["https://download.db-ip.com/free/dbip-city-lite-2026-10.mmdb.gz"])

    def test_only_404_retries_previous_month(self):
        missing = urllib.error.HTTPError("", 404, "missing", {}, None)
        opener = Opener([missing, self.payload])
        data = geo.fetch_database(self.output, now=NOW, opener=opener, validator=valid_metadata)
        self.assertEqual(data["release"], "2026-09")
        self.assertEqual(len(opener.urls), 2)
        self.assertEqual(geo.release_months(datetime(2026, 1, 1, tzinfo=timezone.utc)), ["2026-01", "2025-12"])

    def test_server_failure_keeps_previous_database(self):
        failure = urllib.error.HTTPError("", 503, "failed", {}, None)
        opener = Opener([failure, self.payload])
        with self.assertRaises(urllib.error.HTTPError):
            geo.fetch_database(self.output, now=NOW, opener=opener, validator=valid_metadata)
        self.assertEqual(len(opener.urls), 1)
        self.assertEqual(self.output.read_bytes(), b"previous valid database")

    def test_corrupt_gzip_and_bad_metadata_keep_previous_database(self):
        for payload in (b"not gzip", gzip.compress(b"incorrect database")):
            with self.assertRaises((ValueError, gzip.BadGzipFile)):
                geo.fetch_database(self.output, now=NOW, opener=Opener([payload]), validator=valid_metadata)
            self.assertEqual(self.output.read_bytes(), b"previous valid database")

    def test_compressed_and_expanded_limits(self):
        with patch.object(geo, "MAX_COMPRESSED_BYTES", 3):
            with self.assertRaises(ValueError):
                geo.fetch_database(self.output, now=NOW, opener=Opener([self.payload]), validator=valid_metadata)
        with patch.object(geo, "MAX_DATABASE_BYTES", 3):
            with self.assertRaises(ValueError):
                geo.fetch_database(self.output, now=NOW, opener=Opener([self.payload]), validator=valid_metadata)
        self.assertEqual(self.output.read_bytes(), b"previous valid database")

    def test_redirect_is_rejected(self):
        with self.assertRaises(ValueError):
            geo.NoRedirect().redirect_request(None, None, 302, "", {}, "http://other.example/db")

    def test_mmdb_metadata_checks_with_in_memory_reader(self):
        self.output.write_bytes(b"test")
        metadata = SimpleNamespace(database_type="DBIP-City-Lite", binary_format_major_version=2,
                                   ip_version=6, node_count=10, build_epoch=int(NOW.timestamp()), languages=["en"])

        class Reader:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def metadata(self): return metadata

        with patch.object(geo, "MIN_DATABASE_BYTES", 1):
            self.assertEqual(geo.validate_database(self.output, NOW, lambda _: Reader())["ipVersion"], 6)
            for field, value in (("database_type", "Country-only"), ("ip_version", 5),
                                 ("node_count", 0), ("build_epoch", int(NOW.timestamp()) + 86401),
                                 ("languages", [])):
                previous = getattr(metadata, field)
                setattr(metadata, field, value)
                with self.subTest(field=field), self.assertRaises(ValueError):
                    geo.validate_database(self.output, NOW, lambda _: Reader())
                setattr(metadata, field, previous)


if __name__ == "__main__":
    unittest.main()
