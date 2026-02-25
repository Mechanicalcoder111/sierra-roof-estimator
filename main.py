from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import requests
from shapely.geometry import Polygon, Point
from shapely.ops import transform
from pyproj import Transformer

app = FastAPI()

# Roof pitch multipliers
PITCH_MULTIPLIERS = {
    "4/12": 1.05,
    "6/12": 1.12,
    "8/12": 1.20,
    "12/12": 1.41
}

# Roofing price range
LOW_RATE = 5.50
HIGH_RATE = 7.50


def calculate_area_sqft(coords):
    polygon = Polygon(coords)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    projected_polygon = transform(transformer.transform, polygon)
    area_m2 = projected_polygon.area
    return area_m2 * 10.7639


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
        <head>
            <title>Sierra Exteriors Roof Estimator</title>
            <style>
                body { font-family: Arial; background:#f4f6f9; text-align:center; padding:50px; }
                .container { background:white; max-width:600px; margin:auto; padding:40px;
                             border-radius:10px; box-shadow:0 10px 25px rgba(0,0,0,0.1); }
                h1 { color:#1e3a8a; }
                input, select { width:100%; padding:12px; margin:10px 0;
                                border-radius:6px; border:1px solid #ccc; }
                button { background:#1e3a8a; color:white; padding:12px;
                         border:none; border-radius:6px; width:100%; cursor:pointer; }
                button:hover { background:#162d6b; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Sierra Exteriors Roof Estimator</h1>
                <p>Supports Illinois & Wisconsin properties</p>
                <form action="/calculate" method="get">
                    <input type="text" name="address"
                        placeholder="Enter property address" required/>
                    <select name="pitch">
                        <option value="4/12">4/12</option>
                        <option value="6/12" selected>6/12</option>
                        <option value="8/12">8/12</option>
                        <option value="12/12">12/12</option>
                    </select>
                    <button type="submit">Calculate Estimate</button>
                </form>
            </div>
        </body>
    </html>
    """


@app.get("/calculate", response_class=HTMLResponse)
def calculate(address: str, pitch: str):
    try:

        # Step 1: Geocode
        geo_url = "https://nominatim.openstreetmap.org/search"
        geo_params = {"q": address, "format": "json", "limit": 1}
        headers = {"User-Agent": "sierra-roof-estimator"}

        geo_response = requests.get(
            geo_url, params=geo_params, headers=headers, timeout=10
        ).json()

        if not geo_response:
            return "<h2 style='font-family:Arial;'>Address not found.</h2>"

        lat = float(geo_response[0]["lat"])
        lon = float(geo_response[0]["lon"])

        display_name = geo_response[0].get("display_name", "")

        # Restrict to IL and WI
        if not ("Illinois" in display_name or "Wisconsin" in display_name):
            return """
            <h2 style="font-family:Arial; text-align:center; margin-top:100px;">
            This estimator currently supports Illinois and Wisconsin only.
            </h2>
            """

        # Step 2: Query building footprints
        overpass_query = f"""
        [out:json];
        way(around:100,{lat},{lon})["building"];
        out geom;
        """

        overpass_url = "https://overpass-api.de/api/interpreter"
        osm_response = requests.post(
            overpass_url, data=overpass_query, timeout=20
        ).json()

        elements = osm_response.get("elements", [])
        if not elements:
            return """
            <h2 style="font-family:Arial; text-align:center; margin-top:100px;">
            We could not automatically detect this building.
            Please request a manual inspection.
            </h2>
            """

        point = Point(lon, lat)

        closest_polygon = None
        min_distance = float("inf")

        for element in elements:
            coords = [(node["lon"], node["lat"]) for node in element["geometry"]]
            polygon = Polygon(coords)
            distance = polygon.distance(point)

            if distance < min_distance:
                min_distance = distance
                closest_polygon = polygon

        if closest_polygon is None:
            return """
            <h2 style="font-family:Arial; text-align:center; margin-top:100px;">
            Could not match building footprint.
            </h2>
            """

        footprint_sqft = calculate_area_sqft(
            list(closest_polygon.exterior.coords)
        )

        multiplier = PITCH_MULTIPLIERS.get(pitch, 1.12)
        roof_area = footprint_sqft * multiplier
        roof_area *= 1.10  # waste factor

        low_estimate = roof_area * LOW_RATE
        high_estimate = roof_area * HIGH_RATE

        return f"""
        <html>
        <body style="font-family:Arial; background:#f4f6f9;
                     text-align:center; padding:50px;">
            <div style="background:white; max-width:650px;
                        margin:auto; padding:40px;
                        border-radius:10px;
                        box-shadow:0 10px 25px rgba(0,0,0,0.1);">
                <h1 style="color:#1e3a8a;">Roof Estimate Result</h1>
                <p><strong>Address:</strong> {address}</p>
                <p><strong>Roof Pitch:</strong> {pitch}</p>
                <p><strong>Roof Area:</strong>
                   {round(roof_area,0):,} sq ft (incl. 10% waste)</p>
                <p><strong>Estimated Cost Range:</strong>
                   ${round(low_estimate,0):,}
                   – ${round(high_estimate,0):,}</p>
                <p><em>Final pricing confirmed after inspection.</em></p>
                <br>
                <a href="/">Calculate Another</a>
            </div>
        </body>
        </html>
        """

    except Exception as e:
        print("ERROR:", e)
        return """
        <h2 style="font-family:Arial; text-align:center; margin-top:100px;">
        Something went wrong. Please try again.
        </h2>
        """
