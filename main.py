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

# Roofing price range (per sq ft)
LOW_RATE = 5.50
HIGH_RATE = 7.50

def calculate_area_sqft(coords):
    polygon = Polygon(coords)

    transformer = Transformer.from_crs(
        "EPSG:4326", "EPSG:3857", always_xy=True
    )
    projected_polygon = transform(transformer.transform, polygon)

    area_m2 = projected_polygon.area
    return area_m2 * 10.7639


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
        <head>
            <title>Sierra Exteriors Roof Estimator</title>
        </head>
        <body style="font-family: Arial; text-align: center; margin-top: 100px;">
            <h1>Sierra Exteriors Roof Estimator</h1>
            <h3>Instant roof square footage & cost estimate</h3>

            <form action="/calculate" method="get">
                <input type="text" name="address" placeholder="Enter property address" size="50" required/>
                <br><br>
                
                <label>Select Roof Pitch:</label>
                <select name="pitch">
                    <option value="4/12">4/12</option>
                    <option value="6/12" selected>6/12</option>
                    <option value="8/12">8/12</option>
                    <option value="12/12">12/12</option>
                </select>
                
                <br><br>
                <button type="submit">Calculate Estimate</button>
            </form>
        </body>
    </html>
    """


@app.get("/calculate", response_class=HTMLResponse)
def calculate(address: str, pitch: str):

    # Step 1: Geocode
    geo_url = "https://nominatim.openstreetmap.org/search"
    geo_params = {
        "q": address,
        "format": "json",
        "limit": 1
    }
    headers = {"User-Agent": "sierra-roof-estimator"}

    geo_response = requests.get(geo_url, params=geo_params, headers=headers).json()

    if not geo_response:
        return "<h2>Address not found</h2>"

    lat = float(geo_response[0]["lat"])
    lon = float(geo_response[0]["lon"])

    # Step 2: Get building footprint
    overpass_query = f"""
    [out:json];
    way(around:20,{lat},{lon})["building"];
    out geom;
    """

    overpass_url = "https://overpass-api.de/api/interpreter"
    osm_response = requests.post(overpass_url, data=overpass_query).json()

    if not osm_response["elements"]:
        return "<h2>No building footprint found</h2>"

    point = Point(lon, lat)

    for element in osm_response["elements"]:
        coords = [(node["lon"], node["lat"]) for node in element["geometry"]]
        polygon = Polygon(coords)

        if polygon.contains(point) or polygon.distance(point) < 0.0001:
            footprint_sqft = calculate_area_sqft(coords)

            multiplier = PITCH_MULTIPLIERS.get(pitch, 1.12)
            roof_area = footprint_sqft * multiplier

            # Add 10% waste factor
            roof_area *= 1.10

            low_estimate = roof_area * LOW_RATE
            high_estimate = roof_area * HIGH_RATE

            return f"""
            <html>
                <body style="font-family: Arial; text-align: center; margin-top: 100px;">
                    <h1>Roof Estimate Result</h1>
                    <p><strong>Address:</strong> {address}</p>
                    <p><strong>Roof Pitch:</strong> {pitch}</p>
                    <p><strong>Roof Area:</strong> {round(roof_area, 0):,} sq ft (incl. 10% waste)</p>
                    <p><strong>Estimated Cost Range:</strong> 
                    ${round(low_estimate, 0):,} – ${round(high_estimate, 0):,}
                    </p>
                    <p><em>Final pricing confirmed after professional inspection.</em></p>
                    <br>
                    <a href="/">Calculate Another</a>
                </body>
            </html>
            """

    return "<h2>Building polygon not matched</h2>"
