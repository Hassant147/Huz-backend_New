from datetime import timedelta
import random
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from django.db import transaction
from django.core.files.storage import default_storage
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException

from common.models import UserProfile
from common.utility import user_new_booking_email
from partners.models import HuzBasicDetail, PartnerProfile

from .manage_partner_booking import get_partner_bookings_queryset
from .models import Booking, DocumentsStatus, PassportValidity, Payment


class BookingServiceError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Unable to process booking request."

    def __init__(self, detail=None, *, status_code=None):
        if status_code is not None:
            self.status_code = status_code
        super().__init__({"message": detail or self.default_detail})


def _get_user_by_session_token(session_token):
    user = UserProfile.objects.filter(session_token=session_token).first()
    if not user:
        raise BookingServiceError("User not found.", status_code=status.HTTP_404_NOT_FOUND)
    return user


def _get_partner_by_session_token(partner_session_token):
    partner = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
    if not partner:
        raise BookingServiceError(
            "Package provider detail not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return partner


def _get_package_by_huz_token(huz_token):
    package = HuzBasicDetail.objects.filter(huz_token=huz_token).first()
    if not package:
        raise BookingServiceError(
            "Package detail not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return package


def _get_booking_for_user(session_token, booking_number, *, must_be_future=False):
    user = _get_user_by_session_token(session_token)
    filters = {
        "order_by": user,
        "booking_number": booking_number,
    }
    if must_be_future:
        filters["start_date__gte"] = timezone.now() + timedelta(days=10)

    booking = Booking.objects.filter(**filters).first()
    if not booking:
        message = "Booking detail not found or expire." if must_be_future else "Booking detail not found."
        raise BookingServiceError(message, status_code=status.HTTP_404_NOT_FOUND)

    return user, booking


def get_booking_by_identifier_for_user(user_profile, identifier, *, must_be_future=False):
    lookup = Q(booking_number=str(identifier))
    try:
        lookup |= Q(booking_id=UUID(str(identifier)))
    except (TypeError, ValueError):
        pass

    queryset = Booking.objects.filter(order_by=user_profile).filter(lookup)
    if must_be_future:
        queryset = queryset.filter(start_date__gte=timezone.now() + timedelta(days=10))

    booking = queryset.first()
    if not booking:
        message = "Booking detail not found or expire." if must_be_future else "Booking detail not found."
        raise BookingServiceError(message, status_code=status.HTTP_404_NOT_FOUND)

    return booking


def generate_unique_booking_number():
    while True:
        booking_number = random.randint(1000000000, 9999999999)
        if not Booking.objects.filter(booking_number=booking_number).exists():
            return booking_number


def get_user_bookings_queryset(user_profile):
    return get_partner_bookings_queryset(include_detail_relations=True).filter(order_by=user_profile)


def create_booking(validated_data):
    user = _get_user_by_session_token(validated_data["session_token"])
    partner = _get_partner_by_session_token(validated_data["partner_session_token"])
    package = _get_package_by_huz_token(validated_data["huz_token"])

    booking_fields = {
        "adults": validated_data["adults"],
        "child": validated_data.get("child", 0),
        "infants": validated_data.get("infants", 0),
        "sharing": validated_data["sharing"],
        "quad": validated_data["quad"],
        "triple": validated_data["triple"],
        "double": validated_data["double"],
        "single": validated_data["single"],
        "start_date": validated_data["start_date"],
        "end_date": validated_data["end_date"],
        "total_price": validated_data["total_price"],
        "special_request": validated_data.get("special_request"),
        "booking_status": "Initialize",
        "payment_type": validated_data["payment_type"],
        "order_by": user,
        "order_to": partner,
        "package_token": package,
        "booking_number": generate_unique_booking_number(),
    }

    with transaction.atomic():
        booking = Booking.objects.create(**booking_fields)
        DocumentsStatus.objects.create(status_for_booking=booking)
        return booking


def record_booking_payment(validated_data):
    user, booking = _get_booking_for_user(
        validated_data["session_token"],
        validated_data["booking_number"],
        must_be_future=True,
    )
    package = booking.package_token
    if not package:
        raise BookingServiceError("Package detail not found.", status_code=status.HTTP_404_NOT_FOUND)

    has_existing_payment = Payment.objects.filter(booking_token=booking).exists()

    with transaction.atomic():
        Payment.objects.create(
            transaction_number=validated_data["transaction_number"],
            transaction_type=validated_data["transaction_type"],
            transaction_amount=validated_data["transaction_amount"],
            booking_token=booking,
        )

        if not has_existing_payment:
            booking.booking_status = "Paid"
            booking.save(update_fields=["booking_status"])
            user_new_booking_email(
                user.email,
                user.name,
                package.package_type,
                package.package_name,
                booking.booking_number,
                booking.adults,
                booking.child,
                booking.infants,
                booking.start_date,
                booking.total_price,
                validated_data["transaction_amount"],
            )

        return booking


def update_booking_payment(validated_data):
    _, booking = _get_booking_for_user(
        validated_data["session_token"],
        validated_data["booking_number"],
        must_be_future=False,
    )
    payment = Payment.objects.filter(payment_id=validated_data["payment_id"]).first()
    if not payment:
        raise BookingServiceError("Record not found.", status_code=status.HTTP_404_NOT_FOUND)

    payment.transaction_number = validated_data["transaction_number"]
    payment.transaction_type = validated_data["transaction_type"]
    payment.transaction_amount = validated_data["transaction_amount"]
    payment.save(update_fields=["transaction_number", "transaction_type", "transaction_amount"])

    return booking


def record_booking_payment_photo_uploads(validated_data, files):
    user, booking = _get_booking_for_user(
        validated_data["session_token"],
        validated_data["booking_number"],
        must_be_future=True,
    )
    package = booking.package_token
    if not package:
        raise BookingServiceError("Package detail not found.", status_code=status.HTTP_404_NOT_FOUND)

    has_existing_payment = Payment.objects.filter(booking_token=booking).exists()

    with transaction.atomic():
        for uploaded_file in files:
            extension = Path(uploaded_file.name).suffix.lower()
            safe_name = f"payment_uploads/{uuid4().hex}{extension}"
            stored_path = default_storage.save(safe_name, uploaded_file)
            Payment.objects.create(
                transaction_photo=stored_path,
                transaction_type=validated_data["transaction_type"],
                transaction_amount=validated_data["transaction_amount"],
                booking_token=booking,
            )

        if not has_existing_payment:
            booking.booking_status = "Paid"
            booking.save(update_fields=["booking_status"])
            user_new_booking_email(
                user.email,
                user.name,
                package.package_type,
                package.package_name,
                booking.booking_number,
                booking.adults,
                booking.child,
                booking.infants,
                booking.start_date,
                booking.total_price,
                validated_data["transaction_amount"],
            )

        return booking


def validate_passport(validated_data):
    _, booking = _get_booking_for_user(
        validated_data["session_token"],
        validated_data["booking_number"],
        must_be_future=False,
    )
    existing_passport = PassportValidity.objects.filter(
        passport_for_booking_number=booking,
        passport_number=validated_data["passport_number"],
    ).first()
    if existing_passport:
        raise BookingServiceError(
            "Passport detail already exists.",
            status_code=status.HTTP_409_CONFLICT,
        )

    passport_fields = {
        "first_name": validated_data["first_name"],
        "middle_name": validated_data.get("middle_name"),
        "last_name": validated_data["last_name"],
        "date_of_birth": validated_data["date_of_birth"],
        "passport_number": validated_data["passport_number"],
        "passport_country": validated_data["passport_country"],
        "expiry_date": validated_data["expiry_date"],
        "passport_for_booking_number": booking,
    }

    with transaction.atomic():
        PassportValidity.objects.create(**passport_fields)
        booking.booking_status = "Passport_Validation"
        booking.save(update_fields=["booking_status"])
        return booking


def update_passport_validation(validated_data):
    _, booking = _get_booking_for_user(
        validated_data["session_token"],
        validated_data["booking_number"],
        must_be_future=False,
    )
    passport = PassportValidity.objects.filter(passport_id=validated_data["passport_id"]).first()
    if not passport:
        raise BookingServiceError(
            "Passport detail not exists.",
            status_code=status.HTTP_409_CONFLICT,
        )

    for field in (
        "first_name",
        "middle_name",
        "last_name",
        "date_of_birth",
        "passport_number",
        "passport_country",
        "expiry_date",
    ):
        setattr(passport, field, validated_data.get(field))

    with transaction.atomic():
        passport.save()

        traveller_status = PassportValidity.objects.filter(passport_for_booking_number=booking)
        is_completed = all(
            item.user_passport
            and item.user_photo
            and item.first_name
            and item.last_name
            and item.date_of_birth
            and item.passport_number
            and item.passport_country
            and item.expiry_date
            for item in traveller_status
        )
        if is_completed:
            booking.booking_status = "Pending"
            booking.save(update_fields=["booking_status"])

        return passport.passport_for_booking_number
