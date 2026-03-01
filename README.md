# Fuel Route Optimization API

A Django REST API that plans fuel-efficient truck routes across the USA, using real fuel price data and the OSRM routing engine.

## Features

- **Single-call routing** via OSRM (free, no API key required)
- **Optimal fuel stop selection** using a greedy cheapest-reachable-stop algorithm
- **Fast spatial lookup** via an in-memory SciPy KDTree index
- **6,600+ fuel stations** with real retail prices from the provided CSV.
Geo-codes are polpulated via management command `load_fuel_data` for reduced external API calls.
- **Swagger/OpenAPI docs** at `/api/docs/`

## Quick Start

```bash
# 1. Create and activate a virtual environment (using uv)
uv venv .venv
source .venv/bin/activate

# 2. Install dependencies
uv pip install -r requirements.txt

# 3. Run migrations
python manage.py migrate

# 4. Load fuel station data (geocodes automatically)
python manage.py load_fuel_data

# 5. Start the development server
python manage.py runserver
```

## API Usage

### `POST /api/route/`

Plan a fuel-efficient route between two US locations.

**Request:**

```json
{
    "start": "New York, NY",
    "finish": "Los Angeles, CA"
}
```

**Response:**

```json
{
    "start": { "query": "New York, NY", "latitude": 40.712728, "longitude": -74.006015 },
    "finish": { "query": "Los Angeles, CA", "latitude": 34.053691, "longitude": -118.242766 },
    "route": {
        "distance_miles": 2776.4,
        "duration_hours": 40.54,
        "geometry_polyline": "..."
    },
    "vehicle": {
        "max_range_miles": 500,
        "mpg": 10,
        "theoretical_gallons_needed": 277.64
    },
    "fuel_stops": [
        {
            "name": "SHEETZ #639",
            "city": "Youngstown",
            "state": "OH",
            "retail_price": 3.059,
            "mile_marker": 391.6,
            "gallons_needed": 39.16,
            "cost": 119.79
        }
    ],
    "summary": {
        "total_fuel_stops": 6,
        "total_gallons_purchased": 249.85,
        "total_fuel_cost_usd": 764.17
    }
}
```

### Interactive API Docs

- **Swagger UI**: [http://localhost:8000/api/docs/](http://localhost:8000/api/docs/)
- **OpenAPI Schema (YAML)**: [http://localhost:8000/api/schema/](http://localhost:8000/api/schema/)

## Running Tests

```bash
python manage.py test routes -v2
```

## Project Structure

```text
├── fuel_route_api/          # Django project settings
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── routes/                  # Main application
│   ├── management/commands/ # Data loading commands
│   │   └── load_fuel_data.py
│   ├── models.py            # FuelStation model
│   ├── serializers.py       # DRF serializers
│   ├── services.py          # Routing, spatial index, optimization
│   ├── views.py             # API endpoint
│   └── tests.py             # Test suite
├── fuel-prices-for-be-assessment.csv
├── pyproject.toml
├── requirements.txt
└── manage.py
```

## Assumptions/Constraints

- Vehicle maximum range: **500 miles**
- Fuel consumption: **10 miles per gallon**
- Vehicle starts with a full tank
- At each fuel stop, the tank is filled to capacity
