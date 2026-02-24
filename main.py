from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import requests
from shapely.geometry import Polygon, Point
from shapely.ops import transform
from pyproj import Transformer
import os
from datetime import datetime

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
                body {
                    font-family: Arial, sans-serif;
                    background-color: #f4f6f9;
                    text-align: center;
                    padding: 50px;
                }
                .container {
                    background: white;
                    max-width: 650px;
                    margin: auto;
                    padding: 40px;
                    border-radius: 10px;
                    box-shadow: 0 10px 25px rgba(0,0,0,0.1);
                }
                h1 { color: #1e3a8a; margin-bottom: 8px; }
                .subtext { color: #555; margin-top: 0; margin-bottom: 30px; }
                label { display: block; text-align: left; margin-top: 10px; font-weight: bold; }
                input, select {
                    width: 100%;
                    padding: 12px;
                    margin-top: 8px;
                    margin-bottom: 18px;
                    border-radius: 6px;
                    border: 1px solid #ccc;
                    font-size: 15px;
                }
                button {
                    background-color: #1e3a8a;
                    color: white;
                    padding: 12px 20px;
                    border: none;
                    border-radius: 6px;
                    cursor: pointer;
                    font-size: 16px;
                    width: 100%;
                }
                button:hover { background-color: #162d6b; }
                .note { font-size: 12px; color: #666; margin-top: 12px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Sierra Exteriors Roof Estimator</h1>
                <p class="subtext">Get an instant roof size and cost estimate in seconds.</p>

                <form action="/calculate" method="get">
                    <label>Property Address</label>
                    <input type="text" name="address" placeholder="Enter property address" required/>

                    <label>Roof Pitch</label>
                    <select name="pitch">
                        <option value="4/12">4/12</option>
                        <option value="6/12" selected>6/12</option>
                        <option value="8/12">8/12</option>
                        <option value="12/12">12/12</option>
                    </select>

                    <button type="submit">Calculate Estimate</button>
                </form>

                <p class="note">
                    Estimates are based on public building footprint data and typical roofing multipliers.
                </p>
            </div>
        </body>
    </html>
    """


@app.get("/calculate", response_class=HTMLResponse)
def calculate(address: str, pitch: str):
    # Step 1: Geocode via Nominatim (OpenStreetMap)
    geo_url = "https://nominatim.openstreetmap.org/search"
    geo_params = {"q": address, "format": "json", "limit": 1}
    headers = {"User-Agent": "sierra-roof-estimator"}

    geo_response = requests.get(geo_url, params=geo_params, headers=headers).json()
    if not geo_response:
        return "<h2 style='font-family: Arial;'>Address not found</h2>"

    lat = float(geo_response[0]["lat"])
    lon = float(geo_response[0]["lon"])

    # Step 2: Pull nearby building footprint from Overpass API
    overpass_query = f"""
    [out:json];
    way(around:25,{lat},{lon})["building"];
    out geom;
    """
    overpass_url = "https://overpass-api.de/api/interpreter"
    osm_response = requests.post(overpass_url, data=overpass_query).json()

    if not osm_response.get("elements"):
        return "<h2 style='font-family: Arial;'>No building footprint found</h2>"

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
            <head>
                <title>Roof Estimate Result</title>
                <style>
                    body {{
                        font-family: Arial, sans-serif;
                        background-color: #f4f6f9;
                        text-align: center;
                        padding: 50px;
                    }}
                    .container {{
                        background: white;
                        max-width: 750px;
                        margin: auto;
                        padding: 40px;
                        border-radius: 10px;
                        box-shadow: 0 10px 25px rgba(0,0,0,0.1);
                    }}
                    h1 {{ color: #1e3a8a; }}
                    .result p {{ font-size: 16px; }}
                    .cta {{
                        margin-top: 30px;
                        padding: 20px;
                        background-color: #eef2ff;
                        border-radius: 8px;
                        text-align: left;
                    }}
                    .cta h3 {{ margin-top: 0; color: #1e3a8a; text-align: center; }}
                    .cta input {{
                        width: 100%;
                        padding: 10px;
                        margin: 8px 0;
                        border-radius: 6px;
                        border: 1px solid #ccc;
                    }}
                    .cta button {{
                        background-color: #1e3a8a;
                        color: white;
                        padding: 10px 20px;
                        border: none;
                        border-radius: 6px;
                        cursor: pointer;
                        width: 100%;
                        font-size: 16px;
                    }}
                    .cta button:hover {{ background-color: #162d6b; }}
                    a {{ display: inline-block; margin-top: 18px; text-decoration: none; color: #1e3a8a; }}
                    .fineprint {{ color: #666; font-size: 12px; margin-top: 10px; text-align: center; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>Roof Estimate Result</h1>

                    <div class="result">
                        <p><strong>Address:</strong> {address}</p>
                        <p><strong>Roof Pitch:</strong> {pitch}</p>
                        <p><strong>Roof Area:</strong> {round(roof_area, 0):,} sq ft (incl. 10% waste)</p>
                        <p><strong>Estimated Cost Range:</strong>
                            ${round(low_estimate, 0):,} – ${round(high_estimate, 0):,}
                        </p>
                        <p><em>Final pricing confirmed after professional inspection.</em></p>
                    </div>

                    <div class="cta">
                        <h3>Request a Free Professional Inspection</h3>
                        <form action="/lead" method="post">
                            <input type="text" name="name" placeholder="Full Name" required/>
                            <input type="email" name="email" placeholder="Email Address" required/>
                            <input type="tel" name="phone" placeholder="Phone Number" required/>
                            <input type="hidden" name="address" value="{address}"/>
                            <input type="hidden" name="pitch" value="{pitch}"/>
                            <input type="hidden" name="roof_area" value="{round(roof_area, 0)}"/>
                            <input type="hidden" name="low_estimate" value="{round(low_estimate, 0)}"/>
                            <input type="hidden" name="high_estimate" value="{round(high_estimate, 0)}"/>
                            <button type="submit">Schedule Inspection</button>
                        </form>
                        <div class="fineprint">
                            Submitting this form notifies Sierra Exteriors to contact you.
                        </div>
                    </div>

                    <a href="/">Calculate Another</a>
                </div>
            </body>
            </html>
            """

    return "<h2 style='font-family: Arial;'>Building polygon not matched</h2>"


@app.post("/lead", response_class=HTMLResponse)
def lead(
    name: str = "",
    email: str = "",
    phone: str = "",
    address: str = "",
    pitch: str = "",
    roof_area: str = "",
    low_estimate: str = "",
    high_estimate: str = "",
):
    # Minimal "storage": print to logs for now (viewable in Railway logs)
    timestamp = datetime.utcnow().isoformat()
    print(
        f"[{timestamp}] NEW LEAD | name={name} | email={email} | phone={phone} | "
        f"address={address} | pitch={pitch} | roof_area={roof_area} | "
        f"range=${low_estimate}-{high_estimate}"
    )

    return f"""
    <html>
    <head>
        <title>Lead Submitted</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background-color: #f4f6f9;
                text-align: center;
                padding: 50px;
            }}
            .container {{
                background: white;
                max-width: 650px;
                margin: auto;
                padding: 40px;
                border-radius: 10px;
                box-shadow: 0 10px 25px rgba(0,0,0,0.1);
            }}
            h1 {{ color: #1e3a8a; }}
            a {{ color: #1e3a8a; text-decoration: none; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Thanks, {name}!</h1>
            <p>We received your request. Sierra Exteriors will contact you soon.</p>
            <p><strong>Address:</strong> {address}</p>
            <p><strong>Estimated Roof Area:</strong> {roof_area} sq ft</p>
            <p><strong>Estimated Range:</strong> ${low_estimate} – ${high_estimate}</p>
            <br>
            <a href="/">Back to Estimator</a>
        </div>
    </body>
    </html>
    """
