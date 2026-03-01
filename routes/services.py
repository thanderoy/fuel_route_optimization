"""
Core route optimization services.

Architecture:
- get_route_from_osrm():  Single OSRM API call → route geometry + distance
- build_station_index():  Builds a KDTree of fuel stations on server startup
- find_optimal_fuel_stops(): Greedy cheapest-reachable-stop algorithm
- geocode_location():     Nominatim geocoding for start/finish inputs
"""

import logging
import math
import threading
from functools import lru_cache

import numpy as np
import polyline
import requests
from django.conf import settings
from scipy.spatial import KDTree

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (sourced from Django settings for configurability)
# ---------------------------------------------------------------------------
VEHICLE_RANGE_MILES = settings.VEHICLE_RANGE_MILES
MPG = settings.VEHICLE_MPG
EARTH_RADIUS_MILES = 3958.8

STATION_SEARCH_RADIUS_MILES = settings.STATION_SEARCH_RADIUS_MILES
REFUEL_THRESHOLD_MILES = settings.REFUEL_THRESHOLD_MILES

OSRM_BASE_URL = settings.OSRM_BASE_URL
NOMINATIM_URL = settings.NOMINATIM_URL

# ---------------------------------------------------------------------------
# Global station index (thread-safe lazy init)
# ---------------------------------------------------------------------------
_index_lock = threading.Lock()
_station_index: dict | None = None  # { "tree": KDTree, "stations": list[dict] }


def build_station_index() -> dict | None:
    """Build a KDTree spatial index from all fuel stations in the database."""
    global _station_index

    # Don't rebuild if already built
    if _station_index is not None:
        return _station_index

    with _index_lock:
        # Double-check after acquiring lock
        if _station_index is not None:
            return _station_index

        try:
            from .models import FuelStation

            stations = list(
                FuelStation.objects.filter(
                    latitude__isnull=False,
                    longitude__isnull=False,
                ).values(
                    "id",
                    "opis_id",
                    "name",
                    "address",
                    "city",
                    "state",
                    "retail_price",
                    "latitude",
                    "longitude",
                )
            )

            if not stations:
                logger.warning("No geocoded stations found. Index not built.")
                return None

            # Convert lat/lon to radians for haversine-based KDTree
            coords = np.array(
                [[math.radians(s["latitude"]), math.radians(s["longitude"])] for s in stations]
            )

            tree = KDTree(coords)
            _station_index = {"tree": tree, "stations": stations, "coords": coords}
            logger.info(f"Station spatial index built with {len(stations)} stations.")
            return _station_index

        except Exception:
            logger.exception("Failed to build station index")
            return None


def get_station_index():
    """Get (or build) the station index."""
    global _station_index
    if _station_index is None:
        build_station_index()
    return _station_index


def invalidate_station_index():
    """Force rebuild of station index (e.g. after data reload)."""
    global _station_index
    with _index_lock:
        _station_index = None


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------
@lru_cache(maxsize=256)
def geocode_location(query: str) -> tuple[float, float]:
    """
    Geocode a location string into (latitude, longitude).
    Uses Nominatim (free, no API key). Results are cached.
    """
    resp = requests.get(
        NOMINATIM_URL,
        params={
            "q": query,
            "format": "json",
            "limit": 1,
            "countrycodes": "us",
        },
        headers={"User-Agent": "FuelRouteAPI/1.0"},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()

    if not results:
        raise ValueError(f"Could not geocode location: {query}")

    return float(results[0]["lat"]), float(results[0]["lon"])


# ---------------------------------------------------------------------------
# OSRM routing
# ---------------------------------------------------------------------------
def get_route_from_osrm(
    start: tuple[float, float],
    finish: tuple[float, float],
) -> dict:
    """
    Get a driving route from OSRM. Single API call.

    Args:
        start:  (lat, lon)
        finish: (lat, lon)

    Returns:
        {
            "distance_miles": float,
            "duration_seconds": float,
            "geometry": [(lat, lon), ...],  # decoded polyline
        }
    """
    # OSRM expects lon,lat order
    coords = f"{start[1]},{start[0]};{finish[1]},{finish[0]}"

    resp = requests.get(
        f"{OSRM_BASE_URL}/route/v1/driving/{coords}",
        params={
            "overview": "full",
            "geometries": "polyline",
            "steps": "false",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != "Ok":
        raise ValueError(f"OSRM routing failed: {data.get('message', 'Unknown error')}")

    route = data["routes"][0]
    geometry = polyline.decode(route["geometry"])  # list of (lat, lon)

    return {
        "distance_miles": route["distance"] * 0.000621371,  # meters → miles
        "duration_seconds": route["duration"],
        "geometry": geometry,
    }


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------
def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in miles between two points using the haversine formula."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Route point sampling (cumulative distances)
# ---------------------------------------------------------------------------
def sample_route_distances(geometry: list[tuple[float, float]]) -> list[dict]:
    """
    Walk along the route polyline and compute cumulative distance at each point.
    Returns a list of {"lat": ..., "lon": ..., "cumulative_miles": ...} entries.
    """
    result = [{"lat": geometry[0][0], "lon": geometry[0][1], "cumulative_miles": 0.0}]
    cumulative = 0.0

    for i in range(1, len(geometry)):
        d = haversine(
            geometry[i - 1][0],
            geometry[i - 1][1],
            geometry[i][0],
            geometry[i][1],
        )
        cumulative += d
        result.append(
            {
                "lat": geometry[i][0],
                "lon": geometry[i][1],
                "cumulative_miles": cumulative,
            }
        )

    return result


# ---------------------------------------------------------------------------
# Optimal fuel stop selection
# ---------------------------------------------------------------------------
def find_optimal_fuel_stops(
    route_geometry: list[tuple[float, float]],
    total_distance_miles: float,
) -> list[dict]:
    """
    Find optimal (cheapest) fuel stops along the route.

    Algorithm:
    1) Sample route points with cumulative distances
    2) For each sample point, find nearby stations within STATION_SEARCH_RADIUS_MILES
    3) Use a greedy forward algorithm:
       - Track remaining fuel range (start full at 500 miles)
       - When range drops below REFUEL_THRESHOLD_MILES, look ahead for
         the cheapest station within the remaining range
       - If no station found within threshold, force refuel at the cheapest
         available station before running out

    Returns a list of stop dicts:
        {
            "station_id", "name", "address", "city", "state",
            "latitude", "longitude", "retail_price",
            "mile_marker", "gallons_needed", "cost"
        }
    """
    index = get_station_index()
    if index is None:
        return []

    tree = index["tree"]
    stations = index["stations"]

    # Sample route distances
    route_points = sample_route_distances(route_geometry)

    # For each route point, find nearby stations
    # Convert search radius to radians for KDTree
    search_radius_rad = STATION_SEARCH_RADIUS_MILES / EARTH_RADIUS_MILES

    # Subsample route points every ~5 miles to avoid redundant queries
    subsample_indices = [0]
    last_mile = 0.0
    for i, p in enumerate(route_points):
        if p["cumulative_miles"] - last_mile >= 5.0:
            subsample_indices.append(i)
            last_mile = p["cumulative_miles"]
    if subsample_indices[-1] != len(route_points) - 1:
        subsample_indices.append(len(route_points) - 1)

    # Find all candidate stations along the entire route
    # station_id → { station_info + best_mile_marker }
    candidate_stations: dict[int, dict] = {}

    for idx in subsample_indices:
        p = route_points[idx]
        query_point_rad = [math.radians(p["lat"]), math.radians(p["lon"])]

        nearby_indices = tree.query_ball_point(query_point_rad, search_radius_rad)

        for si in nearby_indices:
            station = stations[si]
            sid = station["id"]

            if sid not in candidate_stations:
                # Calculate the station's approximate mile marker along the route
                station_lat, station_lon = station["latitude"], station["longitude"]
                dist_to_route = haversine(p["lat"], p["lon"], station_lat, station_lon)

                candidate_stations[sid] = {
                    **station,
                    "mile_marker": p["cumulative_miles"],
                    "distance_from_route": dist_to_route,
                }
            else:
                # Update mile marker if this route point is closer
                station_lat, station_lon = station["latitude"], station["longitude"]
                dist_to_route = haversine(p["lat"], p["lon"], station_lat, station_lon)
                if dist_to_route < candidate_stations[sid]["distance_from_route"]:
                    candidate_stations[sid]["mile_marker"] = p["cumulative_miles"]
                    candidate_stations[sid]["distance_from_route"] = dist_to_route

    # Sort candidates by mile marker
    sorted_candidates = sorted(candidate_stations.values(), key=lambda s: s["mile_marker"])

    if not sorted_candidates:
        return []

    # Greedy forward-looking fuel stop selection
    stops = []
    remaining_range = VEHICLE_RANGE_MILES  # start with full tank
    current_mile = 0.0

    while current_mile < total_distance_miles:
        # Find stations that are reachable (within remaining range from current position)
        reachable = [
            s
            for s in sorted_candidates
            if current_mile < s["mile_marker"] <= current_mile + remaining_range
        ]

        if not reachable:
            # No more stations reachable — we should be close enough to finish
            break

        # Check if we need to refuel
        miles_to_end = total_distance_miles - current_mile
        if remaining_range >= miles_to_end:
            # We can make it to the end without refueling
            break

        # Need to refuel — find optimal station
        # Strategy: look at stations in the "decision window"
        # Pick the cheapest one within the reachable range, but prefer stations
        # that are further along (to minimize total stops)
        # We want the cheapest station in the forward 50% of our remaining range
        # If none found, fall back to cheapest anywhere in range

        # The decision window: refuel when remaining_range <= REFUEL_THRESHOLD_MILES
        # or when we must (remaining_range < distance_to_cheapest_station)
        if remaining_range > REFUEL_THRESHOLD_MILES:
            # Not urgent yet — advance along the route
            # Find the next point where remaining_range drops below threshold
            advance_miles = remaining_range - REFUEL_THRESHOLD_MILES
            current_mile += advance_miles
            remaining_range = REFUEL_THRESHOLD_MILES
            continue

        # Select the cheapest station from reachable stations
        # Prefer stations that let us travel further (within reason)
        best_station = min(reachable, key=lambda s: s["retail_price"])

        # Calculate fuel needed: fill up to full tank
        miles_traveled = best_station["mile_marker"] - current_mile
        remaining_range -= miles_traveled
        gallons_needed = (VEHICLE_RANGE_MILES - remaining_range) / MPG
        cost = gallons_needed * best_station["retail_price"]

        stops.append(
            {
                "station_id": best_station["id"],
                "opis_id": best_station["opis_id"],
                "name": best_station["name"],
                "address": best_station["address"],
                "city": best_station["city"],
                "state": best_station["state"],
                "latitude": best_station["latitude"],
                "longitude": best_station["longitude"],
                "retail_price": round(best_station["retail_price"], 4),
                "mile_marker": round(best_station["mile_marker"], 1),
                "gallons_needed": round(gallons_needed, 2),
                "cost": round(cost, 2),
            }
        )

        # After refueling, we have a full tank
        current_mile = best_station["mile_marker"]
        remaining_range = VEHICLE_RANGE_MILES

        # Remove stations we've passed
        sorted_candidates = [s for s in sorted_candidates if s["mile_marker"] > current_mile]

    return stops


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def plan_route(start_location: str, finish_location: str) -> dict:
    """
    Plan a fuel-efficient route from start to finish.

    Args:
        start_location:  Free-text location (e.g. "New York, NY")
        finish_location: Free-text location (e.g. "Los Angeles, CA")

    Returns:
        {
            "start": {"query": str, "lat": float, "lon": float},
            "finish": {"query": str, "lat": float, "lon": float},
            "route": {
                "distance_miles": float,
                "duration_hours": float,
                "geometry_polyline": str,  # encoded polyline for map display
            },
            "fuel_stops": [...],
            "total_fuel_cost": float,
            "total_gallons": float,
        }
    """
    # 1. Geocode start and finish
    start_lat, start_lon = geocode_location(start_location)
    finish_lat, finish_lon = geocode_location(finish_location)

    # 2. Get route from OSRM (single API call)
    route_data = get_route_from_osrm(
        (start_lat, start_lon),
        (finish_lat, finish_lon),
    )

    # 3. Find optimal fuel stops
    fuel_stops = find_optimal_fuel_stops(
        route_data["geometry"],
        route_data["distance_miles"],
    )

    # 4. Calculate totals
    total_gallons = sum(s["gallons_needed"] for s in fuel_stops)
    total_fuel_cost = sum(s["cost"] for s in fuel_stops)

    # Calculate theoretical fuel needed for the whole trip
    theoretical_gallons = route_data["distance_miles"] / MPG

    # Re-encode geometry as polyline for frontend consumption
    geometry_polyline = polyline.encode(route_data["geometry"])

    return {
        "start": {
            "query": start_location,
            "latitude": round(start_lat, 6),
            "longitude": round(start_lon, 6),
        },
        "finish": {
            "query": finish_location,
            "latitude": round(finish_lat, 6),
            "longitude": round(finish_lon, 6),
        },
        "route": {
            "distance_miles": round(route_data["distance_miles"], 1),
            "duration_hours": round(route_data["duration_seconds"] / 3600, 2),
            "geometry_polyline": geometry_polyline,
        },
        "vehicle": {
            "max_range_miles": VEHICLE_RANGE_MILES,
            "mpg": MPG,
            "theoretical_gallons_needed": round(theoretical_gallons, 2),
        },
        "fuel_stops": fuel_stops,
        "summary": {
            "total_fuel_stops": len(fuel_stops),
            "total_gallons_purchased": round(total_gallons, 2),
            "total_fuel_cost_usd": round(total_fuel_cost, 2),
        },
    }
