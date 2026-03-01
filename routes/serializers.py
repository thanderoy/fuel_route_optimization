from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """Input: start and finish locations as free-text strings."""

    start = serializers.CharField(
        max_length=255,
        help_text="Starting location, e.g. 'New York, NY'",
    )
    finish = serializers.CharField(
        max_length=255,
        help_text="Ending location, e.g. 'Los Angeles, CA'",
    )


class FuelStopSerializer(serializers.Serializer):
    station_id = serializers.IntegerField()
    opis_id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    retail_price = serializers.FloatField()
    mile_marker = serializers.FloatField()
    gallons_needed = serializers.FloatField()
    cost = serializers.FloatField()


class LocationSerializer(serializers.Serializer):
    query = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()


class RouteInfoSerializer(serializers.Serializer):
    distance_miles = serializers.FloatField()
    duration_hours = serializers.FloatField()
    geometry_polyline = serializers.CharField()


class VehicleInfoSerializer(serializers.Serializer):
    max_range_miles = serializers.IntegerField()
    mpg = serializers.IntegerField()
    theoretical_gallons_needed = serializers.FloatField()


class SummarySerializer(serializers.Serializer):
    total_fuel_stops = serializers.IntegerField()
    total_gallons_purchased = serializers.FloatField()
    total_fuel_cost_usd = serializers.FloatField()


class RouteResponseSerializer(serializers.Serializer):
    start = LocationSerializer()
    finish = LocationSerializer()
    route = RouteInfoSerializer()
    vehicle = VehicleInfoSerializer()
    fuel_stops = FuelStopSerializer(many=True)
    summary = SummarySerializer()
