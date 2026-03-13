from datetime import datetime, time
from time import perf_counter

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status, viewsets
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView

from common.auth_utils import is_admin_request, require_user_profile
from common.logs_file import logger
from common.models import UserProfile
from common.pagination import CustomPagination
from common.permissions import IsAdminOrAuthenticatedUserProfile

from ..querysets import USER_BOOKING_STATUS_BUCKETS, normalize_user_booking_status_bucket
from ..request_serializers import BookingCreateRequestSerializer, validate_serializer_or_raise
from ..serializers import BookingMutationSerializer, CurrentUserBookingListSerializer, DetailBookingSerializer
from ..services import (
    create_booking,
    find_existing_user_booking,
    get_booking_by_identifier_for_user,
    get_filtered_user_bookings_queryset,
    remove_booking_for_user,
)


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


def _parse_optional_booking_date(value):
    normalized_value = str(value or "").strip()
    if not normalized_value:
        return None

    parsed_datetime = parse_datetime(normalized_value)
    if parsed_datetime is not None:
        if timezone.is_naive(parsed_datetime):
            return timezone.make_aware(parsed_datetime, timezone.get_current_timezone())
        return parsed_datetime

    parsed_date_value = parse_date(normalized_value)
    if parsed_date_value is None:
        return None

    return timezone.make_aware(
        datetime.combine(parsed_date_value, time.min),
        timezone.get_current_timezone(),
    )


class BookingViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminOrAuthenticatedUserProfile]

    def list(self, request):
        _, user_profile = _payload_with_user_session(request, request.query_params)
        status_bucket = str(request.query_params.get("status_bucket") or "").strip().lower()
        if status_bucket and status_bucket not in USER_BOOKING_STATUS_BUCKETS:
            return Response(
                {
                    "message": "Invalid status_bucket. Must be one of: "
                    + ", ".join(sorted(USER_BOOKING_STATUS_BUCKETS))
                    + "."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = get_filtered_user_bookings_queryset(
            user_profile,
            status_bucket=normalize_user_booking_status_bucket(status_bucket),
        )
        paginator = CustomPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = CurrentUserBookingListSerializer(
            page,
            many=True,
            context={"request": request},
        )
        return paginator.get_paginated_response(serializer.data)

    def retrieve(self, request, pk=None):
        _, user_profile = _payload_with_user_session(request, request.query_params)
        booking = get_booking_by_identifier_for_user(
            user_profile,
            pk,
            must_be_future=False,
            include_detail_relations=True,
        )
        serializer = DetailBookingSerializer(booking, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request):
        payload, _ = _payload_with_user_session(request, request.data)
        input_serializer = BookingCreateRequestSerializer(data=payload)
        validated_data = validate_serializer_or_raise(input_serializer)
        request_started_at = perf_counter()
        booking, created = create_booking(validated_data)
        serializer_started_at = perf_counter()
        serializer = BookingMutationSerializer(booking, context={"request": request})
        response_payload = serializer.data
        serializer_duration_ms = (perf_counter() - serializer_started_at) * 1000
        logger.info(
            "booking.api event=create_booking_mutation duration_ms=%.2f serializer_duration_ms=%.2f booking_number=%s created=%s",
            (perf_counter() - request_started_at) * 1000,
            serializer_duration_ms,
            booking.booking_number,
            created,
        )
        return Response(
            response_payload,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def destroy(self, request, pk=None):
        payload, user_profile = _payload_with_user_session(request, request.data or request.query_params)
        result = remove_booking_for_user(
            payload.get("session_token") or user_profile.session_token,
            pk,
        )
        return Response(result, status=status.HTTP_200_OK)


class CurrentUserExistingBookingView(APIView):
    permission_classes = [IsAdminOrAuthenticatedUserProfile]

    def get(self, request):
        _, user_profile = _payload_with_user_session(request, request.query_params)
        huz_token = str(request.query_params.get("huz_token") or "").strip()
        if not huz_token:
            return Response(
                {"message": "Missing required data fields."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start_date = _parse_optional_booking_date(request.query_params.get("start_date"))
        end_date = _parse_optional_booking_date(request.query_params.get("end_date"))
        if request.query_params.get("start_date") and start_date is None:
            return Response(
                {"message": "Invalid start_date."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if request.query_params.get("end_date") and end_date is None:
            return Response(
                {"message": "Invalid end_date."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        booking = find_existing_user_booking(
            user_profile,
            huz_token=huz_token,
            start_date=start_date,
            end_date=end_date,
        )
        if not booking:
            return Response({"exists": False, "booking": None}, status=status.HTTP_200_OK)

        serializer = CurrentUserBookingListSerializer(booking, context={"request": request})
        return Response(
            {"exists": True, "booking": serializer.data},
            status=status.HTTP_200_OK,
        )
