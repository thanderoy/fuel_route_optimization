"""
Management command to load fuel station data from the CSV file.

Geocoding strategy:
1. Download the free US Cities Database (GitHub) for instant bulk geocoding
2. Fall back to Nominatim for any remaining unmatched US cities
3. Store lat/lon directly in FuelStation model rows

Usage:
    python manage.py load_fuel_data
    python manage.py load_fuel_data --csv /path/to/file.csv
    python manage.py load_fuel_data --skip-nominatim   # skip fallback geocoding
"""

import csv
import io
import logging
import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from routes.models import FuelStation
from routes.services import invalidate_station_index

logger = logging.getLogger(__name__)

# US state abbreviations (to filter out non-US entries like AB = Alberta)
US_STATES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "MA",
    "MD",
    "ME",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}

US_CITIES_URL = (
    "https://raw.githubusercontent.com/kelvins/US-Cities-Database/main/csv/us_cities.csv"
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


class Command(BaseCommand):
    help = "Load fuel station data from the CSV file into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            type=str,
            default=str(settings.FUEL_PRICES_CSV),
            help="Path to the fuel prices CSV file",
        )
        parser.add_argument(
            "--skip-nominatim",
            action="store_true",
            help="Skip Nominatim fallback geocoding for unmatched cities",
        )

    def handle(self, *args, **options):
        csv_path = options["csv"]
        skip_nominatim = options["skip_nominatim"]

        self.stdout.write(f"Loading fuel data from: {csv_path}")

        # 1. Parse CSV and deduplicate (keep lowest price per opis_id)
        raw_rows = self._parse_csv(csv_path)
        self.stdout.write(f"Parsed {len(raw_rows)} US rows from CSV")

        deduped = self._deduplicate(raw_rows)
        self.stdout.write(f"After dedup: {len(deduped)} unique stations")

        # 2. Build geocode lookup from free US Cities Database
        self.stdout.write("Downloading US Cities Database for geocoding...")
        city_coords = self._download_us_cities_lookup()
        self.stdout.write(f"Built lookup with {len(city_coords)} US city coordinates")

        # 3. Geocode stations — first pass (instant via lookup)
        geocode_results = {}
        unmatched_cities = set()
        for row in deduped.values():
            key = (row["city"].upper().strip(), row["state"])
            if key not in geocode_results:
                lookup_key = f"{key[0]}|{key[1]}"
                if lookup_key in city_coords:
                    geocode_results[key] = city_coords[lookup_key]
                else:
                    unmatched_cities.add(key)

        matched = len(geocode_results)
        self.stdout.write(f"Instant geocode: {matched} cities matched")

        # 4. Nominatim fallback for remaining unmatched US cities
        if unmatched_cities and not skip_nominatim:
            self.stdout.write(
                f"Geocoding {len(unmatched_cities)} remaining cities via Nominatim..."
            )
            nominatim_success = 0
            for i, (city, state) in enumerate(sorted(unmatched_cities)):
                coords = self._nominatim_geocode(city, state)
                if coords:
                    geocode_results[(city, state)] = coords
                    nominatim_success += 1

                if (i + 1) % 10 == 0:
                    self.stdout.write(f"  Nominatim: {i + 1}/{len(unmatched_cities)}...")

                # Nominatim rate limit: max 1 request/second
                time.sleep(1.1)

            self.stdout.write(f"Nominatim geocoded: {nominatim_success}/{len(unmatched_cities)}")
        elif unmatched_cities:
            self.stdout.write(f"Skipping Nominatim for {len(unmatched_cities)} unmatched cities")

        # 5. Clear existing data and bulk insert
        FuelStation.objects.all().delete()

        stations_to_create = []
        for row in deduped.values():
            key = (row["city"].upper().strip(), row["state"])
            coords = geocode_results.get(key)
            lat = coords[0] if coords else None
            lon = coords[1] if coords else None

            stations_to_create.append(
                FuelStation(
                    opis_id=row["opis_id"],
                    name=row["name"],
                    address=row["address"],
                    city=row["city"],
                    state=row["state"],
                    rack_id=row["rack_id"],
                    retail_price=row["retail_price"],
                    latitude=lat,
                    longitude=lon,
                )
            )

        FuelStation.objects.bulk_create(stations_to_create, batch_size=500)

        # 6. Invalidate the spatial index so it gets rebuilt on next request
        invalidate_station_index()

        total = FuelStation.objects.count()
        geocoded = FuelStation.objects.filter(latitude__isnull=False).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {total} stations ({geocoded} geocoded, "
                f"{total - geocoded} without coordinates)"
            )
        )

    def _parse_csv(self, csv_path: str) -> list[dict]:
        """Parse the CSV file, filtering to US-only stations."""
        rows = []
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                state = row.get("State", "").strip()
                if state not in US_STATES:
                    continue
                try:
                    opis_id = int(row["OPIS Truckstop ID"])
                    retail_price = float(row["Retail Price"])
                    rack_id_str = row.get("Rack ID", "").strip()
                    rack_id = int(rack_id_str) if rack_id_str else None
                except (ValueError, KeyError):
                    continue
                rows.append(
                    {
                        "opis_id": opis_id,
                        "name": row.get("Truckstop Name", "").strip(),
                        "address": row.get("Address", "").strip(),
                        "city": row.get("City", "").strip(),
                        "state": state,
                        "rack_id": rack_id,
                        "retail_price": retail_price,
                    }
                )
        return rows

    def _deduplicate(self, rows: list[dict]) -> dict[int, dict]:
        """Keep the row with the lowest retail_price per opis_id."""
        deduped: dict[int, dict] = {}
        for row in rows:
            opis_id = row["opis_id"]
            if opis_id not in deduped or row["retail_price"] < deduped[opis_id]["retail_price"]:
                deduped[opis_id] = row
        return deduped

    def _download_us_cities_lookup(self) -> dict[str, list[float]]:
        """Download US cities lat/lon from GitHub and build a lookup dict."""
        try:
            resp = requests.get(US_CITIES_URL, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            self.stderr.write(f"Failed to download US cities data: {e}")
            return {}

        lookup = {}
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            city = row["CITY"].strip().upper()
            state = row["STATE_CODE"].strip().upper()
            try:
                lat = float(row["LATITUDE"])
                lon = float(row["LONGITUDE"])
            except (ValueError, KeyError):
                continue
            key = f"{city}|{state}"
            lookup[key] = [lat, lon]

        return lookup

    def _nominatim_geocode(self, city: str, state: str) -> list[float] | None:
        """Geocode a city/state pair using Nominatim."""
        query = f"{city}, {state}, USA"
        try:
            resp = requests.get(
                NOMINATIM_URL,
                params={
                    "q": query,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "us",
                },
                headers={"User-Agent": "FuelRouteAPI/1.0 (data-load)"},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json()
            if results:
                return [float(results[0]["lat"]), float(results[0]["lon"])]
        except Exception as e:
            self.stderr.write(f"Nominatim failed for '{query}': {e}")
        return None
