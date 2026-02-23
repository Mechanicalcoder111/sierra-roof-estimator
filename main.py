from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import requests
from shapely.geometry import Polygon, Point
from shapely.ops import transform
from pyproj import Transformer

app = FastAPI()

PITCH_MULTIPLIERS = {
    "4/12": 1.05,
    "6/12": 1.12,
    "8/12": 1.20,
    "12/12": 1.41
}

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
        <body style="font-family: Arial; text-align: center; margin-top: 80px;">
            
            <h1 style="font-size: 36px;">
                Sierra Exteriors Roof Estimator
            </h1>
            
            <p style="color: gray;">
                Instant roof square footage & cost estimate
            </p>

            <br>

            <form action="/calculate" method="get">
                <input 
                    type="text" 
                    name="address" 
                    placeholder="Enter property address" 
                    size="50"
                    required
                    style="padding: 8px;"
                />
                
                <br><br>
                
                <label><strong>Select Roof Pitch:</strong></label>
                <select name="pitch" style="padding: 5px;">
                    <option value="4/12">4/12</option>
                    <option value="6/12" selected>6/12</option>
                    <option value="8/12">8/12</option>
                    <option value="12/12">12/12</option>
                </select>

                <br><br>

                <label><strong>Cost per Sq Ft ($):</strong></label>
                <input 
                    type="number" 
                    name="cost" 
                    step="0.01"
                    placeholder="e.g. 5.50"
                    required
                    style="padding: 5px;"
                />

                <br><br>
                <button style="padding: 10px 25px; font-size: 16px;">
                    Calculate Estimate
                </button>
            </form>

        </body>
    </html>
    """


@app.get("/calculate", response_class=HTMLResponse)
def calculate(address: str, pitch: str, cost: float):

    geo_url = "https://nominatim.openstreetmap.org/search"
    geo_params = {
        "q": address,
        "format": "json",
        "limit": 1
    }
    headers = {"User-Agent": "roof-calculator-app"}

    geo_response = requests.get(geo_url, params=geo_params, headers=headers).json()

    if not geo_response:
        return "<h2>Address not found</h2>"

    lat = float(geo_response[0]["lat"])
    lon = float(geo_response[0]["lon"])

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
            roof_area_with_waste = roof_area * 1.10

            total_cost = roof_area_with_waste * cost

            return f"""
            <html>
                <body style="font-family: Arial; text-align: center; margin-top: 80px;">
                    
                    <h1>Roof Estimate Result</h1>
                    
                    <p><strong>Address:</strong> {address}</p>
                    <p><strong>Roof Pitch:</strong> {pitch}</p>
                    <p><strong>Cost per Sq Ft:</strong> ${cost:.2f}</p>

                    <hr style="width: 300px;">

                    <p><strong>Roof Area:</strong> {format(roof_area_with_waste, ',.0f')} sq ft (incl. 10% waste)</p>
                    
                    <h2 style="color: green;">
                        Estimated Project Cost: ${format(total_cost, ',.0f')}
                    </h2>

                    <br>
                    <a href="/">Calculate Another</a>

                </body>
            </html>
            """

    return "<h2>Building polygon not matched</h2>"