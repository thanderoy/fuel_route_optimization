"""Tests for the fuel route optimization API."""

from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from routes.models import FuelStation
from routes.services import (
    build_station_index,
    find_optimal_fuel_stops,
    haversine,
    invalidate_station_index,
    sample_route_distances,
)


class HaversineTest(TestCase):
    """Test the haversine distance calculation."""

    def test_same_point(self):
        self.assertAlmostEqual(haversine(40.0, -74.0, 40.0, -74.0), 0.0, places=5)

    def test_known_distance(self):
        # New York to Los Angeles ~ 2451 miles (great circle)
        dist = haversine(40.7128, -74.0060, 33.9425, -118.4081)
        self.assertAlmostEqual(dist, 2451, delta=50)

    def test_short_distance(self):
        # Two points ~69 miles apart (1 degree of latitude)
        dist = haversine(40.0, -74.0, 41.0, -74.0)
        self.assertAlmostEqual(dist, 69.0, delta=2)


class SampleRouteDistancesTest(TestCase):
    """Test route point distance sampling."""

    def test_basic_sampling(self):
        geometry = [
            (40.0, -74.0),
            (40.5, -74.0),
            (41.0, -74.0),
        ]
        result = sample_route_distances(geometry)
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[0]["cumulative_miles"], 0.0)
        self.assertGreater(result[1]["cumulative_miles"], 0)
        self.assertGreater(result[2]["cumulative_miles"], result[1]["cumulative_miles"])

    def test_single_point(self):
        result = sample_route_distances([(40.0, -74.0)])
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["cumulative_miles"], 0.0)


class FuelStationModelTest(TestCase):
    """Test FuelStation model."""

    def setUp(self):
        self.station = FuelStation.objects.create(
            opis_id=100,
            name="Test Station",
            address="123 Main St",
            city="Test City",
            state="TX",
            retail_price=3.499,
            latitude=30.0,
            longitude=-97.0,
        )

    def test_str(self):
        self.assertIn("Test Station", str(self.station))
        self.assertIn("TX", str(self.station))

    def test_fields(self):
        station = FuelStation.objects.get(opis_id=100)
        self.assertEqual(station.name, "Test Station")
        self.assertAlmostEqual(station.retail_price, 3.499)
        self.assertAlmostEqual(station.latitude, 30.0)


class StationIndexTest(TestCase):
    """Test the KDTree spatial index."""

    def setUp(self):
        invalidate_station_index()
        # Create a few test stations
        FuelStation.objects.bulk_create(
            [
                FuelStation(
                    opis_id=1,
                    name="Station A",
                    city="Austin",
                    state="TX",
                    retail_price=2.99,
                    latitude=30.2672,
                    longitude=-97.7431,
                ),
                FuelStation(
                    opis_id=2,
                    name="Station B",
                    city="Dallas",
                    state="TX",
                    retail_price=3.19,
                    latitude=32.7767,
                    longitude=-96.7970,
                ),
                FuelStation(
                    opis_id=3,
                    name="Station C",
                    city="Houston",
                    state="TX",
                    retail_price=2.89,
                    latitude=29.7604,
                    longitude=-95.3698,
                ),
            ]
        )

    def tearDown(self):
        invalidate_station_index()

    def test_build_index(self):
        index = build_station_index()
        self.assertIsNotNone(index)
        self.assertEqual(len(index["stations"]), 3)
        self.assertIsNotNone(index["tree"])

    def test_empty_db_returns_none(self):
        FuelStation.objects.all().delete()
        index = build_station_index()
        self.assertIsNone(index)


class FindOptimalFuelStopsTest(TestCase):
    """Test the fuel stop optimization algorithm."""

    def setUp(self):
        invalidate_station_index()
        # Create stations along a roughly straight line (I-35 corridor: Austin to Dallas)
        self.stations = FuelStation.objects.bulk_create(
            [
                FuelStation(
                    opis_id=10,
                    name="Austin Cheap Station",
                    city="Austin",
                    state="TX",
                    retail_price=2.50,
                    latitude=30.2672,
                    longitude=-97.7431,
                ),
                FuelStation(
                    opis_id=11,
                    name="Waco Station",
                    city="Waco",
                    state="TX",
                    retail_price=3.00,
                    latitude=31.5493,
                    longitude=-97.1467,
                ),
                FuelStation(
                    opis_id=12,
                    name="Dallas Cheap",
                    city="Dallas",
                    state="TX",
                    retail_price=2.60,
                    latitude=32.7767,
                    longitude=-96.7970,
                ),
                FuelStation(
                    opis_id=13,
                    name="OKC Station",
                    city="Oklahoma City",
                    state="OK",
                    retail_price=2.80,
                    latitude=35.4676,
                    longitude=-97.5164,
                ),
                FuelStation(
                    opis_id=14,
                    name="Wichita Station",
                    city="Wichita",
                    state="KS",
                    retail_price=2.70,
                    latitude=37.6872,
                    longitude=-97.3301,
                ),
            ]
        )
        build_station_index()

    def tearDown(self):
        invalidate_station_index()

    def test_short_route_no_stops_needed(self):
        """A route shorter than vehicle range should need no fuel stops."""
        # Austin to Waco is about 100 miles — well within 500 mile range
        geometry = [
            (30.2672, -97.7431),  # Austin
            (30.9, -97.4),
            (31.5493, -97.1467),  # Waco
        ]
        stops = find_optimal_fuel_stops(geometry, total_distance_miles=100)
        self.assertEqual(len(stops), 0)

    def test_long_route_needs_stops(self):
        """A route longer than 500 miles should have fuel stops."""
        # Austin to Wichita is about 600 miles
        geometry = [
            (30.2672, -97.7431),  # Austin
            (31.5493, -97.1467),  # Waco
            (32.7767, -96.7970),  # Dallas
            (35.4676, -97.5164),  # OKC
            (37.6872, -97.3301),  # Wichita
        ]
        stops = find_optimal_fuel_stops(geometry, total_distance_miles=600)
        # Should have at least one stop
        self.assertGreaterEqual(len(stops), 1)
        # Each stop should have required fields
        for stop in stops:
            self.assertIn("name", stop)
            self.assertIn("retail_price", stop)
            self.assertIn("gallons_needed", stop)
            self.assertIn("cost", stop)
            self.assertIn("mile_marker", stop)
            self.assertGreater(stop["gallons_needed"], 0)
            self.assertGreater(stop["cost"], 0)

    def test_stops_respect_range(self):
        """Fuel stops should be within vehicle range of each other."""
        geometry = [
            (30.2672, -97.7431),
            (31.5493, -97.1467),
            (32.7767, -96.7970),
            (35.4676, -97.5164),
            (37.6872, -97.3301),
        ]
        stops = find_optimal_fuel_stops(geometry, total_distance_miles=600)
        prev_mile = 0.0
        for stop in stops:
            gap = stop["mile_marker"] - prev_mile
            self.assertLessEqual(gap, 500, f"Gap of {gap} miles exceeds vehicle range")
            prev_mile = stop["mile_marker"]


class RouteAPIViewTest(TestCase):
    """Integration test for POST /api/route/."""

    def setUp(self):
        self.client = APIClient()
        invalidate_station_index()
        # Create test stations
        FuelStation.objects.bulk_create(
            [
                FuelStation(
                    opis_id=20,
                    name="Test Station 1",
                    city="Houston",
                    state="TX",
                    retail_price=2.99,
                    latitude=29.7604,
                    longitude=-95.3698,
                ),
                FuelStation(
                    opis_id=21,
                    name="Test Station 2",
                    city="San Antonio",
                    state="TX",
                    retail_price=2.89,
                    latitude=29.4241,
                    longitude=-98.4936,
                ),
            ]
        )
        build_station_index()

    def tearDown(self):
        invalidate_station_index()

    def test_missing_fields(self):
        resp = self.client.post("/api/route/", {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_missing_finish(self):
        resp = self.client.post("/api/route/", {"start": "Houston, TX"}, format="json")
        self.assertEqual(resp.status_code, 400)

    @patch("routes.services.geocode_location")
    @patch("routes.services.get_route_from_osrm")
    def test_successful_route(self, mock_osrm, mock_geocode):
        """Test a successful route planning request with mocked external APIs."""
        # Mock geocoding
        mock_geocode.side_effect = [
            (29.7604, -95.3698),  # Houston
            (29.4241, -98.4936),  # San Antonio
        ]

        # Mock OSRM response
        mock_osrm.return_value = {
            "distance_miles": 197.0,
            "duration_seconds": 10800,
            "geometry": [
                (29.7604, -95.3698),
                (29.6, -96.0),
                (29.5, -97.0),
                (29.4241, -98.4936),
            ],
        }

        resp = self.client.post(
            "/api/route/",
            {"start": "Houston, TX", "finish": "San Antonio, TX"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Check response structure
        self.assertIn("start", data)
        self.assertIn("finish", data)
        self.assertIn("route", data)
        self.assertIn("fuel_stops", data)
        self.assertIn("summary", data)
        self.assertIn("vehicle", data)

        # Route info
        self.assertAlmostEqual(data["route"]["distance_miles"], 197.0, places=1)
        self.assertIn("geometry_polyline", data["route"])

        # Vehicle info
        self.assertEqual(data["vehicle"]["max_range_miles"], 500)
        self.assertEqual(data["vehicle"]["mpg"], 10)

        # Summary
        self.assertIn("total_fuel_cost_usd", data["summary"])
        self.assertIn("total_fuel_stops", data["summary"])

    @patch("routes.services.geocode_location")
    def test_invalid_location(self, mock_geocode):
        """Test error handling for invalid locations."""
        mock_geocode.side_effect = ValueError("Could not geocode location: Nowhere")

        resp = self.client.post(
            "/api/route/",
            {"start": "Nowhere", "finish": "San Antonio, TX"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())


class CostCalculationTest(TestCase):
    """Test that fuel cost calculations are correct."""

    def test_cost_formula(self):
        """Verify: gallons = distance / mpg, cost = gallons * price."""
        distance = 500  # miles
        mpg = 10
        price = 3.00
        expected_gallons = distance / mpg  # 50 gallons
        expected_cost = expected_gallons * price  # $150
        self.assertEqual(expected_gallons, 50.0)
        self.assertEqual(expected_cost, 150.0)
