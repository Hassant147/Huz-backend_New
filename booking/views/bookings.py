from rest_framework import status, viewsets
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response

from common.auth_utils import is_admin_request, require_user_profile
from common.models import UserProfile
from common.permissions import IsAdminOrAuthenticatedUserProfile

from .. import manage_bookings as legacy_manage_bookings
from ..request_serializers import BookingCreateRequestSerializer, validate_serializer_or_raise
from ..serializers import DetailBookingSerializer
from ..services import create_booking, get_user_bookings_queryset


ManageBookingsView = legacy_manage_bookings.ManageBookingsView
GetAllBookingsByUserView = legacy_manage_bookings.GetAllBookingsByUserView


def _resolve_request_user_profile(request, payload=None):
    payload = payload or {}
    try:
        return require_user_profile(request)
    except AuthenticationFailed:
        if not is_admin_request(request):
            raise

    session_token = payload.get("session_token") or request.query_params.get("session_token")
    if not session_token:
        raise AuthenticationFailed("Authenticated user profile is required.")

    user_profile = UserProfile.objects.filter(session_token=session_token).first()
    if not user_profile:
        raise AuthenticationFailed("Authenticated user profile is required.")

    return user_profile


def _payload_with_user_session(request, payload=None):
    base_payload = {}
    if payload is not None:
        try:
            base_payload = payload.copy()
        except Exception:
            base_payload = dict(payload)

    user_profile = _resolve_request_user_profile(request, base_payload)
    if not base_payload.get("session_token"):
        base_payload["session_token"] = user_profile.session_token

    return base_payload, user_profile


class BookingViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminOrAuthenticatedUserProfile]

    def list(self, request):
        _, user_profile = _payload_with_user_session(request, request.query_params)
        queryset = get_user_bookings_queryset(user_profile)
        serializer = DetailBookingSerializer(
            queryset,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request):
        payload, _ = _payload_with_user_session(request, request.data)
        input_serializer = BookingCreateRequestSerializer(data=payload)
        validated_data = validate_serializer_or_raise(input_serializer)
        booking = create_booking(validated_data)
        serializer = DetailBookingSerializer(booking, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)
