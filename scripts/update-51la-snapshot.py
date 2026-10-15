#!/usr/bin/env python3
"""Export 51.LA session pageviews as public city aggregates, never raw visits.

Official contract: https://v6.51.la/doc/index.html#/访问明细/1
The documented POST is a read-only query, not a collection request. IP addresses
exist only in memory while looking up a local MMDB; no visit response is logged
or saved. Completed daily aggregates replace, rather than add to, prior days.
"""

import argparse
import copy
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

API_URL = "https://v6-open.51.la/open/visitor/detail/list"
PAGE_SIZE = 100
MAX_BYTES = 2 * 1024 * 1024
MAX_STATE_BYTES = 32 * 1024 * 1024
MAX_COUNT = 9_007_199_254_740_991
SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc
STAT_KEYS = {"places", "mappedPageviews", "unmappedPageviews", "startedAt", "updatedAt"}
DAY_KEYS = STAT_KEYS | {"sessionCount", "complete"}
STATE_KEYS = {"version", "source", "maskId", "cutoverAt", "startDate", "baseline",
              "days", "requests", "lastAttemptAt", "lastOutcome"}
OUTCOMES = {"initialized", "ok", "quota_exhausted", "run_limit", "request_failed",
            "api_rejected", "invalid_response", "geolocation_failed", "regression",
            "state_unavailable"}


class SyncError(Exception):
    """A fixed, public-safe reason: never include response fields or credentials."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class StateNotFound(SyncError):
    """A real HTTP 404, distinct from invalid content or a transport failure."""

    def __init__(self):
        super().__init__("state_unavailable")


def stamp(value):
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_stamp(value, now):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value):
        raise SyncError("invalid_response")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SyncError("invalid_response") from None
    if stamp(result) != value or result > now + timedelta(minutes=5):
        raise SyncError("invalid_response")
    return result


def parse_day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise SyncError("invalid_response")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise SyncError("invalid_response") from None
    if result.isoformat() != value:
        raise SyncError("invalid_response")
    return result


def count(value):
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise SyncError("invalid_response")
    return value


def text_value(value):
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value.strip())
    return value if 0 < len(value) <= 120 and not any(
        unicodedata.category(c) in {"Cc", "Cs"} for c in value) else ""


def coordinate(value, limit):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > limit:
        return None
    # Same coarse precision and JS rounding as the original city collector.
    return math.floor(value * 10 + 0.5) / 10


def place_from_mmdb(record):
    if not isinstance(record, dict):
        return None
    country, city, location = (record.get(key) or {} for key in ("country", "city", "location"))
    if not all(isinstance(part, dict) for part in (country, city, location)):
        return None
    names = city.get("names") or {}
    if not isinstance(names, dict):
        return None
    cc = country.get("iso_code")
    city_name = text_value(names.get("en") or names.get("zh-CN"))
    lat, lon = coordinate(location.get("latitude"), 90), coordinate(location.get("longitude"), 180)
    if (not isinstance(cc, str) or not re.fullmatch(r"[A-Z]{2}", cc) or cc == "XX" or
            not city_name or lat is None or lon is None or (lat == 0 and lon == 0)):
        return None
    return {"lat": lat, "lon": lon, "cc": cc, "city": city_name}


def public_ip(value):
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return str(address) if address.is_global and not address.is_multicast else None
    except (ValueError, TypeError):
        return None


def place_key(place):
    return place["cc"], place["city"], place["lat"], place["lon"]


def sorted_places(places):
    return sorted(places, key=lambda p: (-p["n"], *place_key(p)))


def validate_stats(value, now):
    if not isinstance(value, dict) or not isinstance(value.get("places"), list) or len(value["places"]) > 20000:
        raise SyncError("invalid_response")
    places, keys, mapped = [], set(), 0
    for item in value["places"]:
        if not isinstance(item, dict) or set(item) != {"lat", "lon", "cc", "city", "n"}:
            raise SyncError("invalid_response")
        lat, lon = item["lat"], item["lon"]
        if (coordinate(lat, 90) is None or coordinate(lon, 180) is None or (lat == 0 and lon == 0) or
                not isinstance(item["cc"], str) or not re.fullmatch(r"[A-Z]{2}", item["cc"]) or
                item["cc"] == "XX" or text_value(item["city"]) != item["city"] or not item["city"]):
            raise SyncError("invalid_response")
        n = count(item["n"])
        key = place_key(item)
        if n == 0 or key in keys:
            raise SyncError("invalid_response")
        keys.add(key)
        places.append({k: item[k] for k in ("lat", "lon", "cc", "city", "n")})
        mapped = count(mapped + n)
    if mapped != count(value.get("mappedPageviews")):
        raise SyncError("invalid_response")
    unmapped = count(value.get("unmappedPageviews"))
    total = count(mapped + unmapped)
    updated = parse_stamp(value.get("updatedAt"), now)
    started = value.get("startedAt")
    if started is not None or total:
        if parse_stamp(started, now) > updated:
            raise SyncError("invalid_response")
    return {"places": sorted_places(places), "mappedPageviews": mapped,
            "unmappedPageviews": unmapped, "startedAt": started, "updatedAt": stamp(updated)}


def validate_state(value, now, mask_id):
    if (not isinstance(value, dict) or set(value) != STATE_KEYS or type(value["version"]) is not int or value["version"] != 1 or
            value["source"] != "51la" or value["maskId"] != mask_id or
            value["lastOutcome"] not in OUTCOMES):
        raise SyncError("invalid_response")
    cutoff = parse_stamp(value["cutoverAt"], now)
    start = parse_day(value["startDate"])
    if start != cutoff.astimezone(SHANGHAI).date():
        raise SyncError("invalid_response")
    baseline = validate_stats(value["baseline"], now)
    if set(value["baseline"]) != STAT_KEYS or parse_stamp(baseline["updatedAt"], now) > cutoff:
        raise SyncError("invalid_response")
    if not isinstance(value["days"], dict) or len(value["days"]) > 36600:
        raise SyncError("invalid_response")
    today, days = now.astimezone(SHANGHAI).date(), {}
    if start > today or (today - start).days > 36600:
        raise SyncError("invalid_response")
    for day, daily in value["days"].items():
        day_date = parse_day(day)
        if not start <= day_date <= today or not isinstance(daily, dict) or set(daily) != DAY_KEYS:
            raise SyncError("invalid_response")
        summary = validate_stats(daily, now)
        fetched = parse_stamp(summary["updatedAt"], now)
        if fetched < cutoff or fetched.astimezone(SHANGHAI).date() < day_date or type(daily["complete"]) is not bool:
            raise SyncError("invalid_response")
        if daily["complete"] and day_date >= fetched.astimezone(SHANGHAI).date():
            raise SyncError("invalid_response")
        if summary["startedAt"]:
            first = parse_stamp(summary["startedAt"], now)
            if first < cutoff or first.astimezone(SHANGHAI).date() != day_date:
                raise SyncError("invalid_response")
        sessions = count(daily["sessionCount"])
        if sessions == 0 and summary["mappedPageviews"] + summary["unmappedPageviews"] > 0:
            raise SyncError("invalid_response")
        summary.update(sessionCount=sessions, complete=daily["complete"])
        days[day] = summary
    requests = value["requests"]
    if not isinstance(requests, dict) or len(requests) > 1200:
        raise SyncError("invalid_response")
    for month, used in requests.items():
        if not isinstance(month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise SyncError("invalid_response")
        count(used)
    if value["lastAttemptAt"] is not None:
        if parse_stamp(value["lastAttemptAt"], now) < cutoff:
            raise SyncError("invalid_response")
    return {"version": 1, "source": "51la", "maskId": mask_id, "cutoverAt": stamp(cutoff),
            "startDate": start.isoformat(), "baseline": baseline, "days": days,
            "requests": dict(requests), "lastAttemptAt": value["lastAttemptAt"],
            "lastOutcome": value["lastOutcome"]}


def initial_state(baseline, mask_id, cutoff, now, already_used=0):
    baseline = validate_stats(baseline, now)
    cutoff_date = parse_stamp(cutoff, now)
    if parse_stamp(baseline["updatedAt"], now) > cutoff_date:
        raise SyncError("invalid_response")
    return {"version": 1, "source": "51la", "maskId": mask_id, "cutoverAt": cutoff,
            "startDate": cutoff_date.astimezone(SHANGHAI).date().isoformat(),
            "baseline": baseline, "days": {},
            "requests": {now.astimezone(SHANGHAI).strftime("%Y-%m"): count(already_used)},
            "lastAttemptAt": None, "lastOutcome": "initialized"}


def merge_states(local, remote):
    """Merge independently restored *validated* aggregate state, never visits.

    The workflow must serialize runs. A request was already charged in either
    source, so monthly counters use max, never sum. A day is a replacement and
    uses its latest successful export; adding two exports would double count.
    """
    if local is None:
        return remote
    if (local["maskId"] != remote["maskId"] or local["cutoverAt"] != remote["cutoverAt"] or
            local["baseline"] != remote["baseline"]):
        raise SyncError("state_unavailable")
    result = copy.deepcopy(local)
    for month, used in remote["requests"].items():
        result["requests"][month] = max(result["requests"].get(month, 0), used)
    for day, daily in remote["days"].items():
        previous = result["days"].get(day)
        if previous is not None:
            if daily["updatedAt"] == previous["updatedAt"] and daily != previous:
                raise SyncError("state_unavailable")
            newer, older = (daily, previous) if daily["updatedAt"] > previous["updatedAt"] else (previous, daily)
            if (newer["sessionCount"] < older["sessionCount"] or
                    newer["mappedPageviews"] + newer["unmappedPageviews"] <
                    older["mappedPageviews"] + older["unmappedPageviews"]):
                raise SyncError("state_unavailable")
        if previous is None or daily["updatedAt"] > previous["updatedAt"]:
            result["days"][day] = copy.deepcopy(daily)
    if (remote["lastAttemptAt"] or "") > (result["lastAttemptAt"] or ""):
        result["lastAttemptAt"] = remote["lastAttemptAt"]
        result["lastOutcome"] = remote["lastOutcome"]
    return result


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, allow_nan=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_json(path):
    with Path(path).open("rb") as source:
        data = source.read(MAX_STATE_BYTES + 1)
    if len(data) > MAX_STATE_BYTES:
        raise SyncError("invalid_response")
    try:
        return json.loads(data)
    except (ValueError, UnicodeError):
        raise SyncError("invalid_response") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # In particular, never send account credentials to a redirect target.
        raise SyncError("request_failed")


def read_response(request, limit=MAX_BYTES, state_request=False):
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=25) as response:
            if response.status != 200:
                raise SyncError("request_failed")
            data = response.read(limit + 1)
    except SyncError:
        raise
    except urllib.error.HTTPError as error:
        if state_request and error.code == 404:
            raise StateNotFound() from None
        raise SyncError("request_failed") from None
    except (OSError, urllib.error.URLError):
        # Exception text or response bodies could contain IPs or account values.
        raise SyncError("request_failed") from None
    if len(data) > limit:
        raise SyncError("invalid_response")
    try:
        return json.loads(data)
    except (ValueError, UnicodeError):
        # A downloaded malformed document is not a network outage. In
        # particular it must not let a stale Git baseline replace live state.
        raise SyncError("invalid_response") from None


def fetch_state(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
        raise SyncError("state_unavailable")
    request = urllib.request.Request(url, headers={"Accept": "application/json", "Cache-Control": "no-cache"})
    return read_response(request, MAX_STATE_BYTES, state_request=True)


def signed_payload(access_key, secret_key, mask_id, day, page, now=None, nonce=None):
    now = now or datetime.now(UTC)
    nonce = nonce or "".join(secrets.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(4))
    milliseconds = str(int(now.timestamp() * 1000))
    source = f"accessKey={access_key}&nonce={nonce}&secretKey={secret_key}&timestamp={milliseconds}"
    signature = hashlib.sha256(source.encode("utf-8")).hexdigest().upper()
    return {"maskId": mask_id, "day": day, "page": page, "size": PAGE_SIZE,
            "accessKey": access_key, "nonce": nonce, "timestamp": milliseconds, "sign": signature}


class APIClient:
    def __init__(self, access_key, secret_key, mask_id):
        self.access_key, self.secret_key, self.mask_id = access_key, secret_key, mask_id

    def query(self, day, page):
        payload = signed_payload(self.access_key, self.secret_key, self.mask_id, day, page)
        request = urllib.request.Request(API_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json", "Accept": "application/json"})
        return read_response(request)


class Budget:
    def __init__(self, state, now, monthly_limit, run_limit, persist):
        self.state, self.now, self.persist = state, now, persist
        self.month = now.astimezone(SHANGHAI).strftime("%Y-%m")
        self.monthly_limit, self.run_limit, self.used_in_run = monthly_limit, run_limit, 0

    def available(self):
        return min(self.monthly_limit - self.state["requests"].get(self.month, 0), self.run_limit - self.used_in_run)

    def require(self, pages=1):
        if self.state["requests"].get(self.month, 0) + pages > self.monthly_limit:
            raise SyncError("quota_exhausted")
        if self.used_in_run + pages > self.run_limit:
            raise SyncError("run_limit")

    def charge(self):
        self.require()
        self.state["requests"][self.month] = self.state["requests"].get(self.month, 0) + 1
        self.used_in_run += 1
        self.state["lastAttemptAt"] = stamp(self.now)
        # Charge before attempting the network, even on timeouts/invalid bodies.
        # The caller must persist this state even if the workflow later fails.
        self.persist(self.state)


def validate_page(response, requested_page, expected=None):
    if not isinstance(response, dict) or response.get("success") is not True or response.get("code") != "0000":
        raise SyncError("api_rejected")
    total, pages = count(response.get("total")), count(response.get("pages"))
    if (response.get("curPage") != requested_page or type(response.get("curPage")) is not int or
            response.get("pageSize") != PAGE_SIZE or type(response.get("pageSize")) is not int or
            pages not in ({math.ceil(total / PAGE_SIZE)} if total else {0, 1}) or
            not isinstance(response.get("data"), list)):
        raise SyncError("invalid_response")
    if expected is not None and (total, pages) != expected:
        raise SyncError("invalid_response")
    required_length = max(0, min(PAGE_SIZE, total - (requested_page - 1) * PAGE_SIZE))
    if len(response["data"]) != required_length:
        raise SyncError("invalid_response")
    return total, pages


def session_time(record, day):
    raw = record.get("dateTime")
    if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", raw):
        raise SyncError("invalid_response")
    try:
        result = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI)
    except ValueError:
        raise SyncError("invalid_response") from None
    if result.date().isoformat() != day:
        raise SyncError("invalid_response")
    return result


def collect_day(day, client, lookup, cutoff, now, budget):
    places, seen, unmapped, first, included_sessions = {}, set(), 0, None, 0
    expected, page = None, 1
    while True:
        budget.charge()
        response = client.query(day, page)
        total, pages = validate_page(response, page, expected)
        if expected is None:
            expected = total, pages
            # Never fetch a partial remaining set when the known budget cannot
            # finish it. The first page only informs us how many calls are needed.
            budget.require(max(0, pages - 1))
        for record in response["data"]:
            if (not isinstance(record, dict) or not isinstance(record.get("ip"), str) or len(record["ip"]) > 64 or
                    not isinstance(record.get("uuid"), str) or not 0 < len(record["uuid"]) <= 256):
                raise SyncError("invalid_response")
            pv = count(record.get("pv"))
            happened = session_time(record, day)
            if happened > now.astimezone(SHANGHAI) + timedelta(minutes=5):
                raise SyncError("invalid_response")
            # Detect repeated/overlapping API pages. Hashes stay in memory too.
            fingerprint = hashlib.sha256(json.dumps([record["dateTime"], record["ip"], record["uuid"]],
                                                   ensure_ascii=True, separators=(",", ":")).encode()).digest()
            if fingerprint in seen:
                raise SyncError("invalid_response")
            seen.add(fingerprint)
            if happened < cutoff:
                continue
            included_sessions += 1
            if pv == 0:
                continue
            first = min(first, happened) if first else happened
            address = public_ip(record["ip"])
            try:
                place = place_from_mmdb(lookup(address)) if address else None
            except Exception:
                raise SyncError("geolocation_failed") from None
            if place:
                key = place_key(place)
                previous = places.get(key, {}).get("n", 0)
                places[key] = {**place, "n": count(previous + pv)}
            else:
                unmapped = count(unmapped + pv)
        # Do not retain the last response (it contains IP/uuid/page/referrer data).
        del response
        if page >= pages:
            break
        page += 1
    result = {"places": sorted_places(places.values()),
              "mappedPageviews": count(sum(place["n"] for place in places.values())),
              "unmappedPageviews": unmapped, "startedAt": stamp(first) if first else None,
              "updatedAt": stamp(now), "sessionCount": count(included_sessions),
              "complete": parse_day(day) < now.astimezone(SHANGHAI).date()}
    count(result["mappedPageviews"] + unmapped)
    return result


def days_to_query(state, now, include_today, lookback_days):
    today = now.astimezone(SHANGHAI).date()
    start = max(parse_day(state["startDate"]), today - timedelta(days=lookback_days))
    yesterday = today - timedelta(days=1)
    missing = []
    day = start
    while day < today:
        saved = state["days"].get(day.isoformat())
        if not saved or not saved["complete"]:
            missing.append(day.isoformat())
        day += timedelta(days=1)
    # Settle yesterday before refreshing a moving current-day session list.
    result = []
    previous = state["days"].get(yesterday.isoformat())
    if yesterday >= start and (not previous or parse_stamp(previous["updatedAt"], now).astimezone(SHANGHAI).date() < today):
        result.append(yesterday.isoformat())
    result += [day for day in missing if day not in result]
    if include_today and today >= start:
        result.append(today.isoformat())
    return result


def build_snapshot(state, now, monthly_limit):
    aggregates = [state["baseline"], *state["days"].values()]
    cities, unmapped, first = {}, 0, None
    for aggregate in aggregates:
        unmapped = count(unmapped + aggregate["unmappedPageviews"])
        if aggregate["startedAt"]:
            first = min(first, aggregate["startedAt"]) if first else aggregate["startedAt"]
        for place in aggregate["places"]:
            key = place_key(place)
            cities[key] = {**place, "n": count(cities.get(key, {}).get("n", 0) + place["n"])}
    today = now.astimezone(SHANGHAI).date()
    day, complete_through, missing, provisional = parse_day(state["startDate"]), None, [], []
    contiguous = True
    while day <= today:
        saved = state["days"].get(day.isoformat())
        if saved and saved["complete"]:
            if contiguous:
                complete_through = day.isoformat()
        else:
            contiguous = False
            if saved:
                provisional.append(day.isoformat())
            else:
                missing.append(day.isoformat())
        day += timedelta(days=1)
    month = now.astimezone(SHANGHAI).strftime("%Y-%m")
    result = {"places": sorted_places(cities.values()),
              "mappedPageviews": count(sum(p["n"] for p in cities.values())),
              "unmappedPageviews": unmapped, "startedAt": first,
              "updatedAt": max(a["updatedAt"] for a in aggregates),
              "coverage": {"source": "51la", "timeZone": "Asia/Shanghai", "cutoverAt": state["cutoverAt"],
                           "completeThrough": complete_through, "missingDays": missing,
                           "provisionalDays": provisional, "lastAttemptAt": state["lastAttemptAt"],
                           "lastOutcome": state["lastOutcome"], "quotaMonth": month,
                           "monthlyRequestsUsed": state["requests"].get(month, 0),
                           "monthlyRequestLimit": monthly_limit}}
    count(result["mappedPageviews"] + unmapped)
    return result


def sync(state, client, lookup, now, persist, monthly_limit=90, run_limit=3, include_today=False, lookback_days=14):
    budget = Budget(state, now, monthly_limit, run_limit, persist)
    refreshed, issues = [], []
    cutoff = parse_stamp(state["cutoverAt"], now)
    for day in days_to_query(state, now, include_today, lookback_days):
        try:
            daily = collect_day(day, client, lookup, cutoff, now, budget)
            old = state["days"].get(day)
            if old and (daily["sessionCount"] < old["sessionCount"] or
                        daily["mappedPageviews"] + daily["unmappedPageviews"] < old["mappedPageviews"] + old["unmappedPageviews"]):
                raise SyncError("regression")
            state["days"][day] = daily
            state["lastOutcome"] = "ok"
            persist(state)
            refreshed.append(day)
        except SyncError as error:
            state["lastOutcome"] = error.reason
            issues.append(error.reason)
            persist(state)
            # Network/authentication/rate issues should not trigger a request
            # storm. Keep the remaining daily summaries untouched.
            break
    snapshot = build_snapshot(state, now, monthly_limit)
    return snapshot, refreshed, issues


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mask-id", default=os.environ.get("LA_MASK_ID", "3RWElwTgQjmTbrIQ"))
    parser.add_argument("--mmdb", type=Path, help="Local DB-IP City Lite .mmdb; required for a query, no outbound geolocation")
    parser.add_argument("--state", type=Path, default=Path("visitor-51la-state.json"))
    parser.add_argument("--output", type=Path, default=Path("visitor-locations.json"))
    parser.add_argument("--state-url", help="Restore the last published aggregate state before querying; failure prevents API calls")
    parser.add_argument("--cache-state", type=Path, help="Also merge a separately restored Actions cache of aggregate state")
    parser.add_argument("--restore-only", action="store_true", help="Merge saved aggregate states and rebuild the public snapshot; never query API")
    parser.add_argument("--initialize", action="store_true", help="Explicit first-run initialization only")
    parser.add_argument("--baseline", type=Path, default=Path("visitor-locations.json"))
    parser.add_argument("--cutover-at", help="First-run boundary in millisecond ISO UTC format")
    parser.add_argument("--initial-monthly-used", type=int, default=0)
    parser.add_argument("--monthly-limit", type=int, default=90, help="Leave 10 of the provider's 100 calls for setup/other use")
    parser.add_argument("--max-requests-per-run", type=int, default=3)
    parser.add_argument("--include-today", action="store_true", help="Also query today's provisional data after closed days")
    parser.add_argument("--lookback-days", type=int, default=14, help="Recover missing days within the 15-day detail window")
    args = parser.parse_args(argv)
    if (not re.fullmatch(r"[A-Za-z0-9]{8,64}", args.mask_id) or not 1 <= args.monthly_limit <= 100 or
            not 1 <= args.max_requests_per_run <= 100 or not 0 <= args.initial_monthly_used <= 100 or
            not 1 <= args.lookback_days <= 14 or (args.initialize and (not args.cutover_at or args.state_url)) or
            (not args.restore_only and args.mmdb is None)):
        parser.error("Invalid configuration; initialization needs --cutover-at and cannot use --state-url")
    return args


def main(argv=None):
    args = parse_args(argv)
    now, state = datetime.now(UTC), None
    lock = args.state.with_name(args.state.name + ".lock")
    locked, cached_recovery = False, False
    try:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        locked = True
        if args.state.exists():
            state = validate_state(load_json(args.state), now, args.mask_id)
        if args.cache_state and args.cache_state.exists():
            try:
                cached = validate_state(load_json(args.cache_state), now, args.mask_id)
                state = merge_states(state, cached)
                atomic_json(args.state, state)
                cached_recovery = True
            except Exception:
                print("51.LA snapshot: cached aggregate state is invalid; refusing to reset its request ledger.", file=sys.stderr)
                return 1
        if args.initialize:
            if state is not None:
                raise SyncError("state_unavailable")
            state = initial_state(load_json(args.baseline), args.mask_id, args.cutover_at, now, args.initial_monthly_used)
            atomic_json(args.state, state)
        elif args.state_url:
            try:
                downloaded = fetch_state(args.state_url)
            except StateNotFound:
                initial_seed = (state is not None and not state["days"] and
                                not any(state["requests"].values()) and state["lastAttemptAt"] is None)
                if initial_seed or cached_recovery:
                    # The explicit, genuine seed may be deployed before the
                    # public state file exists. A 404 never causes an API query.
                    raise SyncError("state_unavailable") from None
                print("51.LA snapshot: published state is missing and no validated cache can recover it.", file=sys.stderr)
                return 1
            except SyncError as error:
                if error.reason == "request_failed" and cached_recovery:
                    raise SyncError("state_unavailable") from None
                print("51.LA snapshot: published state could not be safely restored; refusing to replace it.", file=sys.stderr)
                return 1
            except Exception:
                print("51.LA snapshot: unexpected state restoration failure; refusing to replace published history.", file=sys.stderr)
                return 1
            else:
                try:
                    remote = validate_state(downloaded, now, args.mask_id)
                    restored = merge_states(state, remote)
                except Exception:
                    # A successfully downloaded but invalid/conflicting ledger
                    # must never be overwritten by an older checkout/cache.
                    print("51.LA snapshot: published state is invalid or conflicts with saved history; deployment must stop.", file=sys.stderr)
                    return 1
                state = restored
                atomic_json(args.state, state)
        if state is None:
            raise SyncError("state_unavailable")
        if args.restore_only:
            atomic_json(args.output, build_snapshot(state, now, args.monthly_limit))
            print("51.LA snapshot: restored validated aggregate state; no API request made.")
            return 0
        access_key, secret_key = os.environ.get("LA_ACCESS_KEY"), os.environ.get("LA_SECRET_KEY")
        if not access_key or not secret_key:
            raise SyncError("api_rejected")
        try:
            import maxminddb
            reader = maxminddb.open_database(str(args.mmdb))
        except Exception:
            raise SyncError("geolocation_failed") from None
        with reader:
            snapshot, refreshed, issues = sync(
                state, APIClient(access_key, secret_key, args.mask_id), reader.get, now,
                lambda value: atomic_json(args.state, value), args.monthly_limit,
                args.max_requests_per_run, args.include_today, args.lookback_days)
        atomic_json(args.output, snapshot)
        # Deliberately excludes values from API records, request bodies or errors.
        print(f"51.LA snapshot: refreshed {len(refreshed)} day(s); outcome={state['lastOutcome']}; "
              f"requests this month={snapshot['coverage']['monthlyRequestsUsed']}/{args.monthly_limit}.")
        return 2 if issues else 0
    except (SyncError, FileExistsError, OSError) as error:
        reason = error.reason if isinstance(error, SyncError) else "state_unavailable"
        if state is not None:
            state["lastOutcome"] = reason
            try:
                atomic_json(args.state, state)
                atomic_json(args.output, build_snapshot(state, now, args.monthly_limit))
            except Exception:
                print("51.LA snapshot: could not persist a valid retained state.", file=sys.stderr)
                return 1
            print(f"51.LA snapshot retained; outcome={reason}.", file=sys.stderr)
            return 2
        print("51.LA snapshot: no valid durable state; no API request made.", file=sys.stderr)
        return 1
    except Exception:
        # Never print tracebacks or third-party exception messages containing IPs.
        print("51.LA snapshot: unexpected internal failure; inspect code/tests without logging visit data.", file=sys.stderr)
        return 1
    finally:
        if locked:
            lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
