from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from .bookings import _payload_with_user_session
from ..request_serializers import (
    BookingPaymentCreateRequestSerializer,
    BookingPaymentUpdateRequestSerializer,
    PassportValidityCreateRequestSerializer,
    PassportValidityUpdateRequestSerializer,
    validate_serializer_or_raise,
)
from ..serializers import DetailBookingSerializer
from ..services import (
    get_booking_by_identifier_for_user,
    record_booking_payment,
    update_booking_payment,
    update_passport_validation,
    validate_passport,
)
from .bookings import BookingViewSet as BaseBookingViewSet


class BookingViewSet(BaseBookingViewSet):
    @action(detail=True, methods=["post", "put"], url_path="payments")
    def payments(self, request, pk=None):
        payload, user_profile = _payload_with_user_session(request, request.data)
        booking = get_booking_by_identifier_for_user(
            user_profile,
            pk,
            must_be_future=request.method.lower() == "post",
        )

        payload["booking_number"] = booking.booking_number
        if request.method.lower() == "post":
            input_serializer = BookingPaymentCreateRequestSerializer(data=payload)
            validated_data = validate_serializer_or_raise(input_serializer)
            updated_booking = record_booking_payment(validated_data)
            response_status = status.HTTP_201_CREATED
        else:
            input_serializer = BookingPaymentUpdateRequestSerializer(data=payload)
            validated_data = validate_serializer_or_raise(input_serializer)
            updated_booking = update_booking_payment(validated_data)
            response_status = status.HTTP_200_OK

        serializer = DetailBookingSerializer(updated_booking, context={"request": request})
        return Response(serializer.data, status=response_status)

    @action(detail=True, methods=["post", "put"], url_path="passports")
    def passports(self, request, pk=None):
        payload, user_profile = _payload_with_user_session(request, request.data)
        booking = get_booking_by_identifier_for_user(
            user_profile,
            pk,
            must_be_future=False,
        )

        payload["booking_number"] = booking.booking_number
        if request.method.lower() == "post":
            input_serializer = PassportValidityCreateRequestSerializer(data=payload)
            validated_data = validate_serializer_or_raise(input_serializer)
            updated_booking = validate_passport(validated_data)
            response_status = status.HTTP_201_CREATED
        else:
            input_serializer = PassportValidityUpdateRequestSerializer(data=payload)
            validated_data = validate_serializer_or_raise(input_serializer)
            updated_booking = update_passport_validation(validated_data)
            response_status = status.HTTP_200_OK

        serializer = DetailBookingSerializer(updated_booking, context={"request": request})
        return Response(serializer.data, status=response_status)
