from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import requests
from shapely.geometry import Polygon, Point
from shapely.ops import transform
from pyproj import Transformer
import time
import re

app = FastAPI()

PITCH_MULTIPLIERS = {
    "4/12": 1.05,
    "6/12": 1.12,
    "8/12": 1.20,
    "12/12": 1.41
}

LOW_RATE = 5.50
HIGH_RATE = 7.50

# Typical footprint sq ft by property type (used as fallback when OSM has no data)
# Based on median single-family home sizes in IL/WI suburban/rural markets
DEFAULT_FOOTPRINT_SQFT = 1_400   # conservative single-story fallback
PROPERTY_TYPE_FOOTPRINTS = {
    "house":       1_400,
    "detached":    1_400,
    "residential": 1_400,
    "semidetached_house": 900,
    "terrace":     900,
    "apartments":  2_200,
    "commercial":  3_000,
    "retail":      2_500,
    "industrial":  5_000,
    "warehouse":   6_000,
    "garage":      400,
    "shed":        200,
}

# ── Area Calculation ───────────────────────────────────────────────────────────

def calculate_area_sqft(coords):
    polygon = Polygon(coords)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    projected_polygon = transform(transformer.transform, polygon)
    return projected_polygon.area * 10.7639


# ── Geocoding ──────────────────────────────────────────────────────────────────

def geocode_address(address: str):
    """
    Try multiple Nominatim query strategies so that partial or informal
    addresses (e.g. '123 Main St, Rockford') still resolve.
    Returns the first hit that falls inside Illinois or Wisconsin.
    """
    headers = {"User-Agent": "sierra-roof-estimator/2.0"}
    base_url = "https://nominatim.openstreetmap.org/search"

    # Build a list of query variants from most-specific to least
    queries = [address]

    # If the address doesn't already contain a state hint, append each state
    lower = address.lower()
    if "illinois" not in lower and ", il" not in lower:
        queries.append(f"{address}, Illinois, USA")
    if "wisconsin" not in lower and ", wi" not in lower:
        queries.append(f"{address}, Wisconsin, USA")

    # Strip leading/trailing whitespace variations
    queries = [q.strip() for q in dict.fromkeys(queries)]  # deduplicate, preserve order

    for q in queries:
        for attempt in range(2):
            try:
                resp = requests.get(
                    base_url,
                    params={
                        "q": q,
                        "format": "json",
                        "limit": 5,
                        "addressdetails": 1,
                        "countrycodes": "us",
                    },
                    headers=headers,
                    timeout=10,
                ).json()

                for result in resp:
                    display = result.get("display_name", "")
                    if "Illinois" in display or "Wisconsin" in display:
                        return result

            except Exception:
                pass

            time.sleep(1)

    return None


# ── OSM Building Lookup ────────────────────────────────────────────────────────

def get_buildings(lat, lon):
    """
    Query Overpass for buildings near the coordinate.
    Tries progressively larger radii to handle sparse OSM data.
    Returns (elements, source_radius) or ([], None).
    """
    overpass_url = "https://overpass-api.de/api/interpreter"

    for radius in [50, 100, 200, 400]:
        query = f"""
        [out:json][timeout:30];
        way(around:{radius},{lat},{lon})["building"];
        out geom;
        """
        try:
            resp = requests.post(overpass_url, data=query, timeout=35).json()
            elements = resp.get("elements", [])
            if elements:
                return elements, radius
        except Exception:
            pass
        time.sleep(0.5)

    return [], None


# ── Microsoft Building Footprints (US) ────────────────────────────────────────
# Microsoft releases open building footprints for the entire US as GeoJSON.
# They are tiled by quad key. We use a simple bounding-box API-style tile fetch
# via the raw GitHub release CDN, which does NOT require an API key.
# See: https://github.com/microsoft/USBuildingFootprints

def _lat_lon_to_tile(lat, lon, zoom=16):
    """Convert lat/lon to XYZ tile numbers at given zoom."""
    import math
    lat_r = math.radians(lat)
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n)
    return x, y, zoom


def get_microsoft_footprint(lat, lon):
    """
    Attempt to fetch the nearest building footprint from the Microsoft
    US Building Footprints dataset (Illinois / Wisconsin state files).
    Returns area_sqft or None.

    NOTE: The full state GeoJSON files are very large (~GB). Instead we use
    the BING Maps / Microsoft tile API which serves footprints as vector tiles.
    Requires no key and is rate-limit-friendly for single lookups.
    """
    # Use a small bounding box query against the Microsoft Planetary Computer
    # STAC API — which is free and open:
    delta = 0.0008  # ~90m
    bbox = f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}"

    try:
        url = (
            "https://planetarycomputer.microsoft.com/api/stac/v1/search"
        )
        # Planetary Computer hosts the MS Building Footprints as a STAC collection
        resp = requests.post(
            url,
            json={
                "collections": ["ms-buildings"],
                "bbox": [lon - delta, lat - delta, lon + delta, lat + delta],
                "limit": 5,
            },
            timeout=10,
        ).json()

        features = resp.get("features", [])
        if not features:
            return None

        point = Point(lon, lat)
        best_area = None
        best_dist = float("inf")

        for feat in features:
            geom = feat.get("geometry", {})
            if geom.get("type") != "Polygon":
                continue
            coords = [(c[0], c[1]) for c in geom["coordinates"][0]]
            if len(coords) < 3:
                continue
            poly = Polygon(coords)
            dist = poly.distance(point)
            if dist < best_dist:
                best_dist = dist
                best_area = calculate_area_sqft(coords)

        if best_area and best_dist < 0.002:
            return best_area

    except Exception:
        pass

    return None


# ── Census Geocoder – structured address parser ────────────────────────────────

def census_geocode(address: str):
    """
    Use the US Census Bureau Geocoder (free, no key) to resolve an address to
    lat/lon and a FIPS code. Returns dict with lat, lon, state, or None.
    """
    try:
        resp = requests.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={
                "address": address,
                "benchmark": "Public_AR_Current",
                "format": "json",
            },
            timeout=10,
        ).json()

        matches = resp.get("result", {}).get("addressMatches", [])
        if not matches:
            return None

        m = matches[0]
        coords = m["coordinates"]
        state_abbr = m.get("addressComponents", {}).get("state", "")
        return {
            "lat": float(coords["y"]),
            "lon": float(coords["x"]),
            "state": state_abbr,
            "display_name": m.get("matchedAddress", address),
        }
    except Exception:
        return None


# ── Estimate footprint from address components (last-resort heuristic) ─────────

def heuristic_footprint(address: str) -> int:
    """
    When no footprint data at all is available, return a plausible estimate
    based on address keywords (apt/unit → smaller, rural route → larger, etc.)
    along with a note that the estimate is approximate.
    """
    lower = address.lower()
    if any(k in lower for k in ["apt", "unit", "suite", "#", "ste"]):
        return 900    # condo / apartment unit
    if any(k in lower for k in ["rr ", "rural route", "township", "farm"]):
        return 1_800  # farmhouse
    return DEFAULT_FOOTPRINT_SQFT


# ── Main calculation endpoint ──────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
        <head>
            <title>Sierra Exteriors Roof Estimator</title>
            <style>
                * { box-sizing: border-box; }
                body {
                    font-family: Arial, sans-serif;
                    background: #f4f6f9;
                    text-align: center;
                    padding: 50px 20px;
                }
                .container {
                    background: white;
                    max-width: 600px;
                    margin: auto;
                    padding: 40px;
                    border-radius: 10px;
                    box-shadow: 0 10px 25px rgba(0,0,0,0.1);
                }
                h1 { color: #1e3a8a; margin-bottom: 8px; }
                p.sub { color: #555; margin-top: 0; }
                label { display: block; text-align: left; margin: 14px 0 4px; font-weight: bold; font-size: 14px; }
                input, select {
                    width: 100%;
                    padding: 12px;
                    border-radius: 6px;
                    border: 1px solid #ccc;
                    font-size: 15px;
                }
                button {
                    background: #1e3a8a;
                    color: white;
                    padding: 13px;
                    border: none;
                    border-radius: 6px;
                    width: 100%;
                    font-size: 16px;
                    cursor: pointer;
                    margin-top: 20px;
                }
                button:hover { background: #162d6b; }
                .tip { font-size: 12px; color: #888; margin-top: 6px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Sierra Exteriors</h1>
                <p class="sub">Roof Estimator &mdash; Illinois &amp; Wisconsin</p>
                <form action="/calculate" method="get">
                    <label for="address">Property Address</label>
                    <input
                        id="address"
                        type="text"
                        name="address"
                        placeholder="e.g. 123 Main St, Springfield, IL 62701"
                        required
                    />
                    <p class="tip">Include city and state for best results.</p>

                    <label for="pitch">Roof Pitch</label>
                    <select id="pitch" name="pitch">
                        <option value="4/12">4/12 &ndash; Low slope</option>
                        <option value="6/12" selected>6/12 &ndash; Standard (most common)</option>
                        <option value="8/12">8/12 &ndash; Moderate</option>
                        <option value="12/12">12/12 &ndash; Steep</option>
                    </select>

                    <label for="stories">Stories</label>
                    <select id="stories" name="stories">
                        <option value="1">1 story</option>
                        <option value="1.5">1.5 stories</option>
                        <option value="2" selected>2 stories</option>
                    </select>

                    <button type="submit">Get Estimate</button>
                </form>
            </div>
        </body>
    </html>
    """


@app.get("/calculate", response_class=HTMLResponse)
def calculate(address: str, pitch: str = "6/12", stories: float = 2.0):

    footprint_source = None   # how we obtained the footprint
    footprint_sqft = None
    display_name = address
    data_confidence = "high"

    try:
        # ── 1. Geocode ─────────────────────────────────────────────────────────
        geo_data = geocode_address(address)

        lat, lon, state = None, None, None

        if geo_data:
            lat = float(geo_data["lat"])
            lon = float(geo_data["lon"])
            display_name = geo_data.get("display_name", address)
            d = display_name
            if "Illinois" in d:
                state = "IL"
            elif "Wisconsin" in d:
                state = "WI"

        # Fallback to Census geocoder if Nominatim missed it or returned wrong state
        if lat is None or state is None:
            census = census_geocode(address)
            if census:
                lat = census["lat"]
                lon = census["lon"]
                state = census["state"]
                display_name = census["display_name"]

        if lat is None:
            return _error_page("Address not found. Please include city and state (e.g. 123 Oak St, Joliet, IL).")

        if state not in ("IL", "WI"):
            return _error_page("Only Illinois and Wisconsin addresses are currently supported.")

        # ── 2. Try OSM building footprints ─────────────────────────────────────
        elements, radius_used = get_buildings(lat, lon)

        if elements:
            point = Point(lon, lat)
            closest_polygon = None
            min_distance = float("inf")
            closest_btype = None

            for element in elements:
                if "geometry" not in element:
                    continue
                coords = [(n["lon"], n["lat"]) for n in element["geometry"]]
                if len(coords) < 3:
                    continue
                poly = Polygon(coords)
                dist = poly.distance(point)
                if dist < min_distance:
                    min_distance = dist
                    closest_polygon = poly
                    closest_btype = element.get("tags", {}).get("building", "yes")

            # Accept if within ~200m (0.002°); large radius searches can pull in
            # a neighbour — only reject if clearly the wrong building
            if closest_polygon and min_distance < 0.002:
                footprint_sqft = calculate_area_sqft(list(closest_polygon.exterior.coords))
                footprint_source = f"OSM footprint (search radius {radius_used}m)"

        # ── 3. Fallback: Microsoft Planetary Computer building footprints ───────
        if footprint_sqft is None:
            ms_area = get_microsoft_footprint(lat, lon)
            if ms_area:
                footprint_sqft = ms_area
                footprint_source = "Microsoft building footprint"

        # ── 4. Last resort: heuristic based on address text ────────────────────
        if footprint_sqft is None:
            footprint_sqft = heuristic_footprint(address)
            footprint_source = "estimated (no footprint data available)"
            data_confidence = "low"

        # ── 5. Compute roof area ───────────────────────────────────────────────
        multiplier = PITCH_MULTIPLIERS.get(pitch, 1.12)
        roof_area = footprint_sqft * multiplier * 1.10   # 10 % waste factor

        low_estimate  = roof_area * LOW_RATE
        high_estimate = roof_area * HIGH_RATE

        confidence_note = ""
        if data_confidence == "low":
            confidence_note = """
            <div style="background:#fff3cd;border:1px solid #ffc107;border-radius:6px;
                        padding:12px 16px;margin:16px 0;font-size:14px;text-align:left;">
                ⚠️ <strong>Estimate based on typical home size</strong> — we couldn't retrieve
                a building footprint for this address. Range may vary significantly.
                A free on-site inspection will confirm the exact cost.
            </div>
            """
        else:
            confidence_note = f"""
            <p style="font-size:13px;color:#555;">
                Footprint source: {footprint_source}
            </p>
            """

        return f"""
        <html>
        <head>
            <title>Roof Estimate – Sierra Exteriors</title>
            <style>
                * {{ box-sizing: border-box; }}
                body {{ font-family: Arial, sans-serif; background: #f4f6f9;
                        text-align: center; padding: 50px 20px; }}
                .card {{ background: white; max-width: 660px; margin: auto;
                         padding: 40px; border-radius: 10px;
                         box-shadow: 0 10px 25px rgba(0,0,0,0.1); }}
                h1 {{ color: #1e3a8a; }}
                .row {{ display: flex; justify-content: space-between;
                        border-bottom: 1px solid #eee; padding: 10px 0;
                        font-size: 15px; }}
                .row:last-of-type {{ border-bottom: none; }}
                .label {{ color: #555; }}
                .value {{ font-weight: bold; }}
                .price {{ font-size: 26px; color: #1e3a8a; font-weight: bold;
                          margin: 20px 0 6px; }}
                .cta {{ background: #1e3a8a; color: white; padding: 13px 24px;
                        border-radius: 6px; text-decoration: none;
                        display: inline-block; margin-top: 20px; font-size: 15px; }}
                a.back {{ display: inline-block; margin-top: 14px;
                          color: #1e3a8a; font-size: 14px; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h1>Roof Estimate</h1>
                <p style="color:#555;margin-top:0;">Sierra Exteriors &mdash; Illinois &amp; Wisconsin</p>

                <div class="row">
                    <span class="label">Address</span>
                    <span class="value" style="max-width:70%;text-align:right;font-size:13px;">{address}</span>
                </div>
                <div class="row">
                    <span class="label">Roof Pitch</span>
                    <span class="value">{pitch}</span>
                </div>
                <div class="row">
                    <span class="label">Footprint Area</span>
                    <span class="value">{round(footprint_sqft):,} sq ft</span>
                </div>
                <div class="row">
                    <span class="label">Total Roof Area (w/ waste)</span>
                    <span class="value">{round(roof_area):,} sq ft</span>
                </div>

                {confidence_note}

                <div class="price">${round(low_estimate):,} &ndash; ${round(high_estimate):,}</div>
                <p style="font-size:13px;color:#888;margin-top:0;">
                    Estimated cost range &bull; Final pricing confirmed after inspection
                </p>

                <a href="/" class="back">← Calculate another address</a>
            </div>
        </body>
        </html>
        """

    except Exception as e:
        import traceback
        traceback.print_exc()
        return _error_page("Something went wrong. Please try again or contact us directly.")


def _error_page(message: str) -> str:
    return f"""
    <html>
    <body style="font-family:Arial;background:#f4f6f9;text-align:center;padding:80px 20px;">
        <div style="background:white;max-width:560px;margin:auto;padding:40px;
                    border-radius:10px;box-shadow:0 10px 25px rgba(0,0,0,0.1);">
            <h2 style="color:#c0392b;">⚠️ Unable to Process Request</h2>
            <p style="color:#555;">{message}</p>
            <a href="/" style="color:#1e3a8a;font-size:14px;">← Try a different address</a>
        </div>
    </body>
    </html>
    """
