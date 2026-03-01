from django.db import models


class FuelStation(models.Model):
    """A truck stop / fuel station with its location and retail fuel price."""

    opis_id = models.IntegerField(db_index=True, help_text="OPIS Truckstop ID")
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=512, blank=True)
    city = models.CharField(max_length=255)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.IntegerField(null=True, blank=True)
    retail_price = models.FloatField(help_text="Retail fuel price in USD per gallon")
    latitude = models.FloatField(null=True, blank=True, db_index=True)
    longitude = models.FloatField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["state", "city", "retail_price"]
        indexes = [
            models.Index(fields=["latitude", "longitude"], name="idx_lat_lon"),
        ]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) - ${self.retail_price:.3f}"
