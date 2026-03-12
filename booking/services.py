from datetime import timedelta
import random
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.core.files.storage import default_storage
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException

from common.models import UserProfile
from common.utility import check_file_format_and_size, user_new_booking_email
from partners.models import HuzBasicDetail, HuzPackageDateRange, PartnerProfile

from .flow_utils import get_expected_traveller_count
from .manage_partner_booking import get_partner_bookings_queryset
from .models import Booking, DocumentsStatus, PassportValidity, Payment
from .querysets import (
    annotate_effective_booking_status,
    annotate_resume_priority,
    filter_active_capacity_queryset,
    filter_user_booking_status_bucket,
)
from .statuses import (
    BOOKING_STATUS_CANCELLED,
    BOOKING_STATUS_COMPLETED,
    BOOKING_STATUS_EXPIRED,
    BOOKING_STATUS_HOLD,
    PAYMENT_STATUS_APPROVED,
    PAYMENT_STATUS_NOT_SUBMITTED,
    PAYMENT_STATUS_UNDER_REVIEW,
)
from .workflow import (
    PAYMENT_REVIEWABLE_STATUSES,
    booking_allows_client_traveller_updates,
    booking_passports_are_complete,
    get_payment_stage_status,
    normalize_booking_status,
    sync_booking_state,
)

PAYMENT_STAGE_ALIASES = {
    "full": "Full",
    "minimum": "Minimum",
    "min": "Minimum",
}


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


def _validate_package_partner(package, partner):
    if getattr(package, "package_provider_id", None) != getattr(partner, "partner_id", None):
        raise BookingServiceError(
            "Selected package does not belong to the provided package provider.",
            status_code=status.HTTP_409_CONFLICT,
        )


def _validate_package_can_be_booked(package):
    if str(getattr(package, "package_status", "") or "").strip().lower() != "active":
        raise BookingServiceError(
            "This package is not currently open for booking.",
            status_code=status.HTTP_409_CONFLICT,
        )


def _get_booking_for_user(
    session_token,
    booking_number,
    *,
    must_be_future=False,
    lock_for_update=False,
    persist_state=True,
):
    user = _get_user_by_session_token(session_token)
    filters = {
        "order_by": user,
        "booking_number": booking_number,
    }
    if must_be_future:
        filters["start_date__gte"] = timezone.now() + timedelta(days=10)

    queryset = Booking.objects.filter(**filters)
    if lock_for_update:
        queryset = queryset.select_for_update()

    booking = queryset.first()
    if not booking:
        message = "Booking detail not found or expire." if must_be_future else "Booking detail not found."
        raise BookingServiceError(message, status_code=status.HTTP_404_NOT_FOUND)

    sync_booking_state(booking, save=persist_state)
    return user, booking


def _to_local_date(value):
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.date()


def _get_non_negative_traveller_count(validated_data, field_name):
    try:
        value = int(validated_data.get(field_name) or 0)
    except (TypeError, ValueError):
        raise BookingServiceError(
            f"{field_name} must be a valid whole number.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if value < 0:
        raise BookingServiceError(
            f"{field_name} cannot be negative.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return value


def _get_requested_traveller_count(validated_data):
    total = sum(
        _get_non_negative_traveller_count(validated_data, field_name)
        for field_name in ("adults", "child", "infants")
    )
    if total <= 0:
        raise BookingServiceError(
            "At least one traveller is required to create a booking.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return total


def _build_booking_window_filters(start_date, end_date):
    return {
        "start_date__date": _to_local_date(start_date),
        "end_date__date": _to_local_date(end_date),
    }


def _resolve_package_date_range(
    package,
    *,
    start_date,
    end_date,
    package_date_range_id=None,
):
    range_queryset = HuzPackageDateRange.objects.filter(
        date_range_for_package=package
    ).order_by("start_date", "end_date")

    if package_date_range_id:
        package_date_range = range_queryset.filter(range_id=package_date_range_id).first()
        if not package_date_range:
            raise BookingServiceError(
                "Selected departure window was not found for this package.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return package_date_range

    available_ranges = list(range_queryset)
    if not available_ranges:
        return None

    for package_date_range in available_ranges:
        if (
            _to_local_date(package_date_range.start_date) == _to_local_date(start_date)
            and _to_local_date(package_date_range.end_date) == _to_local_date(end_date)
        ):
            return package_date_range

    raise BookingServiceError(
        "Selected departure window is no longer available for this package.",
        status_code=status.HTTP_409_CONFLICT,
    )


def _resolve_booking_window_validity(package, package_date_range, start_date):
    if package_date_range is not None:
        package_validity = package_date_range.package_validity
        if package_validity:
            return package_validity
        return package_date_range.start_date - timedelta(days=2)

    package_validity = getattr(package, "package_validity", None)
    if package_validity:
        return package_validity

    return start_date - timedelta(days=2)


def _validate_booking_window_is_open(
    *,
    package,
    package_date_range,
    start_date,
):
    validity_date = _to_local_date(
        _resolve_booking_window_validity(package, package_date_range, start_date)
    )
    if validity_date is None:
        return

    if timezone.localdate() > validity_date:
        raise BookingServiceError(
            "Selected departure window is no longer open for booking.",
            status_code=status.HTTP_409_CONFLICT,
        )


def _count_travellers_for_queryset(queryset):
    return sum(
        (int(adults or 0) + int(child or 0) + int(infants or 0))
        for adults, child, infants in queryset.values_list("adults", "child", "infants")
    )


def _get_active_booking_window_queryset(
    *,
    package,
    start_date,
    end_date,
    lock_for_update=False,
):
    queryset = Booking.objects.filter(
        package_token=package,
        **_build_booking_window_filters(start_date, end_date),
    )
    if lock_for_update:
        queryset = queryset.select_for_update()

    queryset = annotate_effective_booking_status(queryset)
    return filter_active_capacity_queryset(queryset)


def _validate_package_range_capacity(
    *,
    package,
    package_date_range,
    start_date,
    end_date,
    requested_traveller_count,
    exclude_booking_id=None,
):
    if package_date_range is None:
        return

    capacity_limit = int(package_date_range.group_capacity or 0)
    if capacity_limit <= 0:
        raise BookingServiceError(
            "Selected departure window is fully booked.",
            status_code=status.HTTP_409_CONFLICT,
        )

    if requested_traveller_count > capacity_limit:
        raise BookingServiceError(
            f"Selected departure window only allows {capacity_limit} travellers.",
            status_code=status.HTTP_409_CONFLICT,
        )

    active_bookings = _get_active_booking_window_queryset(
        package=package,
        start_date=start_date,
        end_date=end_date,
        lock_for_update=True,
    )
    if exclude_booking_id:
        active_bookings = active_bookings.exclude(booking_id=exclude_booking_id)

    occupied_capacity = _count_travellers_for_queryset(active_bookings)
    remaining_capacity = capacity_limit - occupied_capacity

    if requested_traveller_count > remaining_capacity:
        if remaining_capacity <= 0:
            raise BookingServiceError(
                "Selected departure window is fully booked.",
                status_code=status.HTTP_409_CONFLICT,
            )

        raise BookingServiceError(
            f"Only {remaining_capacity} travellers can still be booked for this departure window.",
            status_code=status.HTTP_409_CONFLICT,
        )


def _normalize_payment_stage(value):
    normalized_value = str(value or "").strip().lower()
    payment_stage = PAYMENT_STAGE_ALIASES.get(normalized_value)
    if not payment_stage:
        raise BookingServiceError(
            "transaction_type must be 'Full' or 'Minimum'.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return payment_stage


def _is_payment_approved(payment):
    return str(getattr(payment, "payment_status", "") or "").strip().upper() == PAYMENT_STATUS_APPROVED


def _is_payment_under_review(payment):
    return str(getattr(payment, "payment_status", "") or "").strip().upper() == PAYMENT_STATUS_UNDER_REVIEW


def _normalize_transaction_number(transaction_number):
    normalized_value = str(transaction_number or "").strip()
    return normalized_value or ""


def _ensure_transaction_number_is_available(transaction_number, *, payment_id=None):
    normalized_value = _normalize_transaction_number(transaction_number)
    if not normalized_value:
        return normalized_value

    duplicate_queryset = Payment.objects.filter(transaction_number=normalized_value)
    if payment_id:
        duplicate_queryset = duplicate_queryset.exclude(payment_id=payment_id)

    if duplicate_queryset.exists():
        raise BookingServiceError(
            "This transaction number has already been used.",
            status_code=status.HTTP_409_CONFLICT,
        )

    return normalized_value


def _get_payment_stage_queryset(booking, payment_stage, *, lock_for_update=False):
    queryset = Payment.objects.filter(
        booking_token=booking,
        transaction_type__iexact=payment_stage,
    )
    if lock_for_update:
        queryset = queryset.select_for_update()
    return queryset


def _update_booking_status_for_passport_progress(booking):
    return sync_booking_state(booking, save=True)


def _booking_passports_are_complete(booking):
    return booking_passports_are_complete(booking)


def _attach_passport_files(passport, validated_data):
    update_fields = []

    for field_name in ("user_passport", "user_photo"):
        uploaded_file = validated_data.get(field_name)
        if uploaded_file is None:
            continue

        if not check_file_format_and_size(uploaded_file):
            raise BookingServiceError(
                "Invalid file format or size.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        extension = Path(uploaded_file.name).suffix.lower()
        safe_name = f"passport_uploads/{uuid4().hex}{extension}"
        stored_path = default_storage.save(safe_name, uploaded_file)
        existing_file = getattr(passport, field_name, None)
        if passport.pk and existing_file:
            existing_file.delete(save=False)
        setattr(passport, field_name, stored_path)
        update_fields.append(field_name)

    return update_fields


def _upsert_payment_record(
    *,
    booking,
    payment_stage,
    transaction_amount,
    transaction_number=None,
    uploaded_file=None,
    payment_id=None,
):
    payments_queryset = Payment.objects.select_for_update().filter(booking_token=booking)
    has_existing_payment = payments_queryset.exists()
    stage_queryset = _get_payment_stage_queryset(
        booking,
        payment_stage,
        lock_for_update=True,
    ).order_by("-transaction_time")

    if payment_id:
        payment = payments_queryset.filter(payment_id=payment_id).first()
        if not payment:
            raise BookingServiceError("Record not found.", status_code=status.HTTP_404_NOT_FOUND)
        existing_stage = _normalize_payment_stage(payment.transaction_type)
        if existing_stage != payment_stage:
            raise BookingServiceError(
                f"This payment belongs to the {existing_stage} stage and cannot be reused for {payment_stage}.",
                status_code=status.HTTP_409_CONFLICT,
            )
        if _is_payment_approved(payment):
            raise BookingServiceError(
                "This payment has already been approved and cannot be updated.",
                status_code=status.HTTP_409_CONFLICT,
            )
        if _is_payment_under_review(payment):
            raise BookingServiceError(
                f"{payment_stage} payment is already under review for this booking.",
                status_code=status.HTTP_409_CONFLICT,
            )
        conflicting_stage_payment = stage_queryset.exclude(payment_id=payment.payment_id).first()
        if conflicting_stage_payment:
            if _is_payment_approved(conflicting_stage_payment):
                raise BookingServiceError(
                    f"{payment_stage} payment has already been approved for this booking.",
                    status_code=status.HTTP_409_CONFLICT,
                )
            raise BookingServiceError(
                f"{payment_stage} payment already exists for this booking.",
                status_code=status.HTTP_409_CONFLICT,
            )
    else:
        approved_stage_payment = stage_queryset.filter(payment_status__iexact=PAYMENT_STATUS_APPROVED).first()
        if approved_stage_payment:
            raise BookingServiceError(
                f"{payment_stage} payment has already been approved for this booking.",
                status_code=status.HTTP_409_CONFLICT,
            )
        payment = stage_queryset.exclude(payment_status__iexact=PAYMENT_STATUS_APPROVED).first()
        if payment and _is_payment_under_review(payment):
            raise BookingServiceError(
                f"{payment_stage} payment is already under review for this booking.",
                status_code=status.HTTP_409_CONFLICT,
            )

    normalized_transaction_number = _ensure_transaction_number_is_available(
        transaction_number,
        payment_id=getattr(payment, "payment_id", None),
    )

    update_fields = [
        "transaction_number",
        "transaction_type",
        "transaction_amount",
        "payment_status",
        "review_message",
        "transaction_time",
    ]
    if payment:
        payment.transaction_number = normalized_transaction_number or payment.transaction_number
        payment.transaction_type = payment_stage
        payment.transaction_amount = transaction_amount
        payment.payment_status = PAYMENT_STATUS_UNDER_REVIEW
        payment.review_message = None
        payment.transaction_time = timezone.now()
    else:
        payment = Payment(
            transaction_number=normalized_transaction_number or None,
            transaction_type=payment_stage,
            transaction_amount=transaction_amount,
            payment_status=PAYMENT_STATUS_UNDER_REVIEW,
            review_message=None,
            booking_token=booking,
        )

    if uploaded_file is not None:
        extension = Path(uploaded_file.name).suffix.lower()
        safe_name = f"payment_uploads/{uuid4().hex}{extension}"
        stored_path = default_storage.save(safe_name, uploaded_file)
        if payment.pk and payment.transaction_photo:
            payment.transaction_photo.delete(save=False)
        payment.transaction_photo = stored_path
        update_fields.append("transaction_photo")

    try:
        if payment.pk:
            payment.save(update_fields=list(dict.fromkeys(update_fields)))
        else:
            payment.save()
    except IntegrityError as exc:
        if normalized_transaction_number:
            raise BookingServiceError(
                "This transaction number has already been used.",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        raise

    return payment, has_existing_payment


def get_booking_by_identifier_for_user(
    user_profile,
    identifier,
    *,
    must_be_future=False,
    lock_for_update=False,
    persist_state=False,
):
    lookup = Q(booking_number=str(identifier))
    try:
        lookup |= Q(booking_id=UUID(str(identifier)))
    except (TypeError, ValueError):
        pass

    queryset = Booking.objects.filter(order_by=user_profile).filter(lookup)
    if lock_for_update:
        queryset = queryset.select_for_update()
    if must_be_future:
        queryset = queryset.filter(start_date__gte=timezone.now() + timedelta(days=10))

    booking = queryset.first()
    if not booking:
        message = "Booking detail not found or expire." if must_be_future else "Booking detail not found."
        raise BookingServiceError(message, status_code=status.HTTP_404_NOT_FOUND)

    sync_booking_state(booking, save=persist_state)
    return booking


def remove_booking_for_user(session_token, booking_identifier):
    with transaction.atomic():
        user = _get_user_by_session_token(session_token)
        booking = get_booking_by_identifier_for_user(
            user,
            booking_identifier,
            must_be_future=False,
            lock_for_update=True,
            persist_state=True,
        )

        sync_booking_state(booking, save=True)
        booking_status = normalize_booking_status(booking.booking_status)
        minimum_payment_status = get_payment_stage_status(booking, "Minimum")
        full_payment_status = get_payment_stage_status(booking, "Full")
        has_submitted_payment = (
            minimum_payment_status != PAYMENT_STATUS_NOT_SUBMITTED
            or full_payment_status != PAYMENT_STATUS_NOT_SUBMITTED
        )

        if booking_status == BOOKING_STATUS_HOLD and not has_submitted_payment:
            booking_number = booking.booking_number
            booking.delete()
            return {
                "message": "Selected booking request has been removed.",
                "booking_number": booking_number,
                "deleted": True,
            }

        if booking_status == BOOKING_STATUS_HOLD:
            raise BookingServiceError(
                "Bookings with submitted payments cannot be removed or cancelled from self-service.",
                status_code=status.HTTP_409_CONFLICT,
            )

        raise BookingServiceError(
            "Only bookings that are still on hold can be removed or cancelled.",
            status_code=status.HTTP_409_CONFLICT,
        )


def generate_unique_booking_number():
    while True:
        booking_number = random.randint(1000000000, 9999999999)
        if not Booking.objects.filter(booking_number=booking_number).exists():
            return booking_number


def get_user_bookings_queryset(user_profile):
    return (
        get_partner_bookings_queryset(include_detail_relations=False)
        .filter(order_by=user_profile)
        .order_by("-order_time")
    )


def get_filtered_user_bookings_queryset(user_profile, *, status_bucket="all"):
    queryset = annotate_effective_booking_status(get_user_bookings_queryset(user_profile))
    return filter_user_booking_status_bucket(queryset, status_bucket)


def find_existing_user_booking(
    user_profile,
    *,
    huz_token,
    start_date=None,
    end_date=None,
):
    queryset = annotate_effective_booking_status(
        get_partner_bookings_queryset(include_detail_relations=False).filter(
            order_by=user_profile,
            package_token__huz_token=huz_token,
        )
    )
    queryset = queryset.exclude(
        effective_booking_status__in=(
            BOOKING_STATUS_COMPLETED,
            BOOKING_STATUS_CANCELLED,
            BOOKING_STATUS_EXPIRED,
        )
    )

    if start_date is not None:
        queryset = queryset.filter(start_date__date=_to_local_date(start_date))
    if end_date is not None:
        queryset = queryset.filter(end_date__date=_to_local_date(end_date))

    queryset = annotate_resume_priority(queryset)
    return queryset.order_by("resume_priority", "-order_time").first()


def create_booking(validated_data):
    user = _get_user_by_session_token(validated_data["session_token"])
    partner = _get_partner_by_session_token(validated_data["partner_session_token"])
    package = _get_package_by_huz_token(validated_data["huz_token"])
    _validate_package_partner(package, partner)

    requested_traveller_count = _get_requested_traveller_count(validated_data)
    package_date_range = _resolve_package_date_range(
        package,
        start_date=validated_data["start_date"],
        end_date=validated_data["end_date"],
        package_date_range_id=validated_data.get("package_date_range_id"),
    )
    canonical_start_date = (
        package_date_range.start_date if package_date_range else validated_data["start_date"]
    )
    canonical_end_date = (
        package_date_range.end_date if package_date_range else validated_data["end_date"]
    )
    _validate_package_can_be_booked(package)
    _validate_booking_window_is_open(
        package=package,
        package_date_range=package_date_range,
        start_date=canonical_start_date,
    )

    booking_fields = {
        "adults": validated_data["adults"],
        "child": validated_data.get("child", 0),
        "infants": validated_data.get("infants", 0),
        "sharing": validated_data["sharing"],
        "quad": validated_data["quad"],
        "triple": validated_data["triple"],
        "double": validated_data["double"],
        "single": validated_data["single"],
        "start_date": canonical_start_date,
        "end_date": canonical_end_date,
        "total_price": validated_data["total_price"],
        "special_request": validated_data.get("special_request"),
        "booking_status": BOOKING_STATUS_HOLD,
        "hold_expires_at": timezone.now() + timedelta(minutes=15),
        "payment_type": validated_data["payment_type"],
        "order_by": user,
        "order_to": partner,
        "package_token": package,
    }

    with transaction.atomic():
        locked_user = UserProfile.objects.select_for_update().filter(pk=user.pk).first()
        resumable_booking = (
            _get_active_booking_window_queryset(
                package=package,
                start_date=canonical_start_date,
                end_date=canonical_end_date,
                lock_for_update=True,
            )
            .filter(order_by=locked_user)
            .order_by("-order_time")
            .first()
        )
        if resumable_booking:
            if normalize_booking_status(resumable_booking.booking_status) == BOOKING_STATUS_HOLD:
                _validate_package_range_capacity(
                    package=package,
                    package_date_range=package_date_range,
                    start_date=canonical_start_date,
                    end_date=canonical_end_date,
                    requested_traveller_count=requested_traveller_count,
                    exclude_booking_id=resumable_booking.booking_id,
                )

                updated_fields = []
                for field_name, field_value in booking_fields.items():
                    if getattr(resumable_booking, field_name) != field_value:
                        setattr(resumable_booking, field_name, field_value)
                        updated_fields.append(field_name)

                if updated_fields:
                    resumable_booking.save(update_fields=updated_fields)

            DocumentsStatus.objects.get_or_create(status_for_booking=resumable_booking)
            return sync_booking_state(resumable_booking, save=True), False

        _validate_package_range_capacity(
            package=package,
            package_date_range=package_date_range,
            start_date=canonical_start_date,
            end_date=canonical_end_date,
            requested_traveller_count=requested_traveller_count,
        )

        booking = Booking.objects.create(
            booking_number=generate_unique_booking_number(),
            **booking_fields,
        )
        DocumentsStatus.objects.get_or_create(status_for_booking=booking)
        return sync_booking_state(booking, save=True), True


def record_booking_payment(validated_data):
    with transaction.atomic():
        user, booking = _get_booking_for_user(
            validated_data["session_token"],
            validated_data["booking_number"],
            must_be_future=False,
            lock_for_update=True,
        )
        package = booking.package_token
        if not package:
            raise BookingServiceError("Package detail not found.", status_code=status.HTTP_404_NOT_FOUND)

        payment_stage = _normalize_payment_stage(validated_data["transaction_type"])
        _, has_existing_payment = _upsert_payment_record(
            booking=booking,
            payment_stage=payment_stage,
            transaction_amount=validated_data["transaction_amount"],
            transaction_number=validated_data.get("transaction_number"),
            uploaded_file=validated_data.get("transaction_photo"),
            payment_id=validated_data.get("payment_id"),
        )

        booking.hold_expires_at = None
        booking.payment_correction_expires_at = None
        booking.save(update_fields=["hold_expires_at", "payment_correction_expires_at"])
        sync_booking_state(booking, save=True)

        if not has_existing_payment:
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

        return sync_booking_state(booking, save=True)


def update_booking_payment(validated_data):
    with transaction.atomic():
        _, booking = _get_booking_for_user(
            validated_data["session_token"],
            validated_data["booking_number"],
            must_be_future=False,
            lock_for_update=True,
        )
        payment_stage = _normalize_payment_stage(validated_data["transaction_type"])
        _upsert_payment_record(
            booking=booking,
            payment_stage=payment_stage,
            transaction_amount=validated_data["transaction_amount"],
            transaction_number=validated_data.get("transaction_number"),
            uploaded_file=validated_data.get("transaction_photo"),
            payment_id=validated_data["payment_id"],
        )
        booking.hold_expires_at = None
        booking.payment_correction_expires_at = None
        booking.save(update_fields=["hold_expires_at", "payment_correction_expires_at"])
        return sync_booking_state(booking, save=True)


def record_booking_payment_photo_uploads(validated_data, files):
    with transaction.atomic():
        user, booking = _get_booking_for_user(
            validated_data["session_token"],
            validated_data["booking_number"],
            must_be_future=False,
            lock_for_update=True,
        )
        package = booking.package_token
        if not package:
            raise BookingServiceError("Package detail not found.", status_code=status.HTTP_404_NOT_FOUND)

        payment_stage = _normalize_payment_stage(validated_data["transaction_type"])
        _, has_existing_payment = _upsert_payment_record(
            booking=booking,
            payment_stage=payment_stage,
            transaction_amount=validated_data["transaction_amount"],
            transaction_number=validated_data.get("transaction_number"),
            uploaded_file=files[0],
            payment_id=validated_data.get("payment_id"),
        )

        booking.hold_expires_at = None
        booking.payment_correction_expires_at = None
        booking.save(update_fields=["hold_expires_at", "payment_correction_expires_at"])
        sync_booking_state(booking, save=True)

        if not has_existing_payment:
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

        return sync_booking_state(booking, save=True)


def validate_passport(validated_data):
    with transaction.atomic():
        _, booking = _get_booking_for_user(
            validated_data["session_token"],
            validated_data["booking_number"],
            must_be_future=False,
            lock_for_update=True,
        )
        if not booking_allows_client_traveller_updates(booking):
            raise BookingServiceError(
                "Traveler details cannot be updated at the current booking stage.",
                status_code=status.HTTP_409_CONFLICT,
            )
        traveller_status = PassportValidity.objects.select_for_update().filter(
            passport_for_booking_number=booking
        ).order_by("passport_id")
        existing_passport = traveller_status.filter(
            passport_number__iexact=validated_data["passport_number"]
        ).first()
        if existing_passport:
            raise BookingServiceError(
                "Passport detail already exists.",
                status_code=status.HTTP_409_CONFLICT,
            )

        blank_passport = traveller_status.filter(
            Q(passport_number__isnull=True) | Q(passport_number="")
        ).first()
        if blank_passport:
            passport = blank_passport
        else:
            expected_traveller_count = get_expected_traveller_count(booking)
            if expected_traveller_count and traveller_status.count() >= expected_traveller_count:
                raise BookingServiceError(
                    "Traveller details already exist for all allocated travelers.",
                    status_code=status.HTTP_409_CONFLICT,
                )
            passport = PassportValidity(passport_for_booking_number=booking)

        passport.first_name = validated_data["first_name"]
        passport.middle_name = validated_data.get("middle_name")
        passport.last_name = validated_data["last_name"]
        passport.date_of_birth = validated_data["date_of_birth"]
        passport.passport_number = validated_data["passport_number"]
        passport.passport_country = validated_data["passport_country"]
        passport.expiry_date = validated_data["expiry_date"]
        upload_fields = _attach_passport_files(passport, validated_data)
        update_fields = [
            "first_name",
            "middle_name",
            "last_name",
            "date_of_birth",
            "passport_number",
            "passport_country",
            "expiry_date",
            *upload_fields,
        ]
        if passport.pk:
            passport.save(update_fields=list(dict.fromkeys(update_fields)))
        else:
            passport.save()

        return _update_booking_status_for_passport_progress(booking)


def update_passport_validation(validated_data):
    with transaction.atomic():
        _, booking = _get_booking_for_user(
            validated_data["session_token"],
            validated_data["booking_number"],
            must_be_future=False,
            lock_for_update=True,
        )
        if not booking_allows_client_traveller_updates(booking):
            raise BookingServiceError(
                "Traveler details cannot be updated at the current booking stage.",
                status_code=status.HTTP_409_CONFLICT,
            )
        passport_queryset = PassportValidity.objects.select_for_update().filter(
            passport_for_booking_number=booking
        )
        passport = passport_queryset.filter(passport_id=validated_data["passport_id"]).first()
        if not passport:
            raise BookingServiceError(
                "Passport detail not exists.",
                status_code=status.HTTP_409_CONFLICT,
            )

        duplicate_passport = passport_queryset.exclude(passport_id=passport.passport_id).filter(
            passport_number__iexact=validated_data["passport_number"]
        ).first()
        if duplicate_passport:
            raise BookingServiceError(
                "Passport detail already exists.",
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

        upload_fields = _attach_passport_files(passport, validated_data)
        passport.save(
            update_fields=[
                "first_name",
                "middle_name",
                "last_name",
                "date_of_birth",
                "passport_number",
                "passport_country",
                "expiry_date",
                *upload_fields,
            ]
        )
        return _update_booking_status_for_passport_progress(booking)
