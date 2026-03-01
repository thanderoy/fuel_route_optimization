from django.urls import path

from . import views

urlpatterns = [
    path("route/", views.RouteView.as_view(), name="route"),
]
