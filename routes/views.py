import logging

from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer, RouteResponseSerializer
from .services import plan_route

logger = logging.getLogger(__name__)


class RouteView(APIView):
    """Plan a fuel-efficient route between two US locations."""

    @extend_schema(
        request=RouteRequestSerializer,
        responses={200: RouteResponseSerializer},
        summary="Plan a fuel-efficient route",
        description=(
            "Given a start and finish location (free-text, e.g. 'New York, NY'), "
            "returns the driving route geometry, optimal fuel stops along the route, "
            "and the total fuel cost. The vehicle is assumed to have a 500-mile range "
            "and achieves 10 miles per gallon."
        ),
        examples=[
            OpenApiExample(
                "Cross-country route",
                value={"start": "New York, NY", "finish": "Los Angeles, CA"},
                request_only=True,
            ),
            OpenApiExample(
                "Short route",
                value={"start": "Houston, TX", "finish": "Dallas, TX"},
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        start = serializer.validated_data["start"]
        finish = serializer.validated_data["finish"]

        try:
            result = plan_route(start, finish)
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception("Unexpected error planning route")
            return Response(
                {"error": "An unexpected error occurred while planning the route."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response_serializer = RouteResponseSerializer(result)
        return Response(response_serializer.data)
