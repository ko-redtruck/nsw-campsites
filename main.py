from __future__ import annotations

from http import cookiejar
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import requests

import folium
from folium.plugins import MarkerCluster
from requests_cache import CachedSession


ROOT_DIR = Path(__file__).parent.resolve()
ALL_CAMPGROUNDS_PATH = ROOT_DIR / "all_campground.json"
OUTPUT_MAP_PATH = "index.html"


AVAILABILITY_URL_TEMPLATE  = (
    "https://www.nationalparks.nsw.gov.au/npws/ReservationApi/AvailabilityDates"
)


def load_campgrounds(json_path: Path) -> List[Dict[str, Any]]:
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Expecting a list of dicts
    if not isinstance(data, list):
        raise ValueError("all_campground.json: expected a list of objects")
    return data


def extract_context_id(raw_id: str) -> Optional[str]:
    if not raw_id:
        return None
    # Remove leading/trailing braces if present
    cleaned = raw_id.strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        cleaned = cleaned[1:-1]
    return cleaned or None


def build_cached_session() -> CachedSession:
    # Cache for 6 hours to avoid hammering the API
    return CachedSession(
        cache_name=str(ROOT_DIR / ".http_cache"),
        backend="sqlite",
        expire_after=6 * 60 * 60,
    )

def fetch_cookies():
    r = requests.get("https://www.nationalparks.nsw.gov.au/")
    return r.cookies

def fetch_availability_dates(session: CachedSession, context_id: str, cookies) -> Optional[List[str]]:
    try:
        headers  = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ",
                #   "AppleWebKit/537.36 (KHTML, like Gecko) "
                #   "Chrome/117.0.0.0 Safari/537.36"
}
        resp = session.get(AVAILABILITY_URL_TEMPLATE, timeout=20, params=dict(contextItemId=context_id, adults=2,
    children=0,
    infants=0), headers=headers, cookies=cookies)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return None

    # API is expected to provide a key named "dates" (list[str])
    if isinstance(payload, dict):
        dates = payload.get("dates") or payload.get("Dates")
        if isinstance(dates, list) and all(isinstance(d, str) for d in dates):
            return dates
    # Some endpoints might return a bare list
    if isinstance(payload, list) and all(isinstance(d, str) for d in payload):
        return payload
    return None


def parse_ddmmyyyy(date_str: str) -> Optional[datetime]:
    try:
        return datetime.strptime(date_str, "%d/%m/%Y")
    except Exception:
        return None


def determine_availability(today: datetime, dates: Optional[List[str]]) -> str:
    """Return one of: 'available', 'unavailable', 'unknown'."""
    if not dates:
        return "unknown"
    today_str = today.strftime("%d/%m/%Y")
    return "available" if today_str in dates else "unavailable"


def create_map(campgrounds: Iterable[Dict[str, Any]]) -> folium.Map:
    # Default center over NSW roughly
    base_map = folium.Map(location=[-32.0, 147.0], zoom_start=6, tiles="OpenStreetMap")
    cluster = MarkerCluster().add_to(base_map)

    bounds: List[Tuple[float, float]] = []

    for cg in campgrounds:
        coords = cg.get("coords") or {}
        lat_raw = coords.get("lat")
        lon_raw = coords.get("lon")
        try:
            lat = float(lat_raw)
            lon = float(lon_raw)
        except Exception:
            continue

        status = cg.get("_availability", "unknown")
        title = cg.get("title", "Unknown campground")
        context_id = cg.get("_context_id")

        color = {
            "available": "green",
            "unavailable": "red",
            "unknown": "gray",
        }.get(status, "gray")

        popup_lines = [title]
        if context_id:
            booking_url = (
                "https://www.nationalparks.nsw.gov.au/camping-and-accommodation"
            )
            popup_lines.append(f"ID: {context_id}")
            popup_lines.append(f"Status: {status}")
        else:
            popup_lines.append(f"Status: {status}")

        folium.Marker(
            location=[lat, lon],
            popup=folium.Popup("<br/>".join(popup_lines), max_width=300),
            icon=folium.Icon(color=color, icon="info-sign"),
        ).add_to(cluster)

        bounds.append((lat, lon))

    if bounds:
        base_map.fit_bounds(bounds, padding=(20, 20))

    return base_map


def main() -> int:
    if not ALL_CAMPGROUNDS_PATH.exists():
        print(f"Missing file: {ALL_CAMPGROUNDS_PATH}", file=sys.stderr)
        return 1

    campgrounds = load_campgrounds(ALL_CAMPGROUNDS_PATH)
    session = build_cached_session()
    today = datetime.today()
    cookies = fetch_cookies()
    enriched: List[Dict[str, Any]] = []
    for cg in campgrounds:
        raw_id = cg.get("id")
        context_id = extract_context_id(raw_id) if isinstance(raw_id, str) else None
        dates = None
        if context_id:
            dates = fetch_availability_dates(session, context_id, cookies)
        status = determine_availability(today, dates)

        cg_copy = dict(cg)
        cg_copy["_context_id"] = context_id
        cg_copy["_dates"] = dates
        cg_copy["_availability"] = status
        enriched.append(cg_copy)

    fmap = create_map(enriched)
    fmap.save(str(OUTPUT_MAP_PATH))
    print(f"Wrote map to {OUTPUT_MAP_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


