from datetime import date, datetime

from django.conf import settings
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from .models import Booking


def _flatten_validation_detail(detail):
    if isinstance(detail, dict):
        for field, errors in detail.items():
            if isinstance(errors, (list, tuple)) and errors:
                return f"{field}: {errors[0]}"
            return f"{field}: {errors}"

    if isinstance(detail, (list, tuple)) and detail:
        return str(detail[0])

    return "Invalid input."


def validate_serializer_or_raise(serializer):
    try:
        serializer.is_valid(raise_exception=True)
    except ValidationError as exc:
        detail = exc.detail
        payload = {"message": _flatten_validation_detail(detail)}
        if isinstance(detail, dict):
            payload.update(detail)
        raise ValidationError(payload)

    return serializer.validated_data


def _maybe_make_aware(value):
    if settings.USE_TZ and isinstance(value, datetime) and timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


class DateOrDateTimeField(serializers.Field):
    default_error_messages = {
        "invalid": "Datetime has wrong format. Use ISO-8601 datetime or YYYY-MM-DD."
    }

    def to_internal_value(self, value):
        if isinstance(value, datetime):
            return _maybe_make_aware(value)

        if isinstance(value, date):
            return _maybe_make_aware(datetime.combine(value, datetime.min.time()))

        if not isinstance(value, str):
            self.fail("invalid")

        parsed_datetime = parse_datetime(value)
        if parsed_datetime is not None:
            return _maybe_make_aware(parsed_datetime)

        parsed_date = parse_date(value)
        if parsed_date is not None:
            return _maybe_make_aware(datetime.combine(parsed_date, datetime.min.time()))

        self.fail("invalid")

    def to_representation(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class BookingCreateRequestSerializer(serializers.Serializer):
    session_token = serializers.CharField()
    partner_session_token = serializers.CharField()
    huz_token = serializers.CharField()
    adults = serializers.IntegerField()
    child = serializers.IntegerField(required=False, default=0)
    infants = serializers.IntegerField(required=False, default=0)
    sharing = serializers.CharField()
    quad = serializers.CharField()
    triple = serializers.CharField()
    double = serializers.CharField()
    single = serializers.CharField()
    start_date = DateOrDateTimeField()
    end_date = DateOrDateTimeField()
    total_price = serializers.FloatField()
    special_request = serializers.CharField(allow_blank=True, allow_null=True)
    payment_type = serializers.ChoiceField(choices=[choice[0] for choice in Booking.PAYMENT_TYPE])


class BookingPaymentCreateRequestSerializer(serializers.Serializer):
    session_token = serializers.CharField()
    booking_number = serializers.CharField()
    transaction_number = serializers.CharField()
    transaction_type = serializers.CharField()
    transaction_amount = serializers.FloatField()


class BookingPaymentUpdateRequestSerializer(BookingPaymentCreateRequestSerializer):
    payment_id = serializers.UUIDField()


class BookingPaymentPhotoUploadRequestSerializer(serializers.Serializer):
    session_token = serializers.CharField()
    booking_number = serializers.CharField()
    transaction_amount = serializers.FloatField()
    transaction_type = serializers.CharField()


class PassportValidityCreateRequestSerializer(serializers.Serializer):
    session_token = serializers.CharField()
    booking_number = serializers.CharField()
    first_name = serializers.CharField()
    middle_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    last_name = serializers.CharField()
    date_of_birth = DateOrDateTimeField()
    passport_number = serializers.CharField()
    passport_country = serializers.CharField()
    expiry_date = DateOrDateTimeField()


class PassportValidityUpdateRequestSerializer(PassportValidityCreateRequestSerializer):
    passport_id = serializers.UUIDField()
