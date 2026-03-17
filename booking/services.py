from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import random
from pathlib import Path
from time import perf_counter
from uuid import UUID
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.core.files.storage import default_storage
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError

from common.logs_file import logger
from common.models import UserProfile
from common.utility import check_file_format_and_size, user_new_booking_email
from partners.models import HuzBasicDetail, HuzPackageDateRange, PartnerProfile

from .flow_utils import get_expected_traveller_count
from .manage_partner_booking import get_partner_bookings_queryset
from .models import Booking, BookingGroup, DocumentsStatus, PassportValidity, Payment
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
    get_booking_payments,
    get_payment_stage_status,
    normalize_booking_status,
    sync_booking_state,
)

PAYMENT_STAGE_ALIASES = {
    "full": "Full",
    "minimum": "Minimum",
    "min": "Minimum",
}
BOOKING_NUMBER_RETRY_ATTEMPTS = 5
CURRENCY_QUANTUM = Decimal("0.01")
MINIMUM_PAYMENT_RATE = Decimal("0.10")
MINIMUM_PAYMENT_FLOOR = Decimal("1.00")
TRAVELER_TYPE_ADULT = "Adult"
TRAVELER_TYPE_CHILD_WITH_BED = "Child (5-11)"
TRAVELER_TYPE_CHILD_NO_BED = "Child (2-5)"
TRAVELER_TYPE_INFANT = "Infant"
TRAVELER_TYPE_ALIASES = {
    "adult": TRAVELER_TYPE_ADULT,
    "child (5-11)": TRAVELER_TYPE_CHILD_WITH_BED,
    "child 5-11": TRAVELER_TYPE_CHILD_WITH_BED,
    "child_5_11": TRAVELER_TYPE_CHILD_WITH_BED,
    "child_with_bed": TRAVELER_TYPE_CHILD_WITH_BED,
    "child (2-5)": TRAVELER_TYPE_CHILD_NO_BED,
    "child 2-5": TRAVELER_TYPE_CHILD_NO_BED,
    "child_2_5": TRAVELER_TYPE_CHILD_NO_BED,
    "child_no_bed": TRAVELER_TYPE_CHILD_NO_BED,
    "infant": TRAVELER_TYPE_INFANT,
}
ROOM_TYPE_SINGLE = "Single(1 bed)"
ROOM_TYPE_DOUBLE = "Double(2 bed)"
ROOM_TYPE_TRIPLE = "Triple(3 bed)"
ROOM_TYPE_QUAD = "Quad(4 bed)"
ROOM_TYPE_SHARING = "Sharing"
ROOM_TYPE_ALIASES = {
    "single": ROOM_TYPE_SINGLE,
    "single(1 bed)": ROOM_TYPE_SINGLE,
    "double": ROOM_TYPE_DOUBLE,
    "double(2 bed)": ROOM_TYPE_DOUBLE,
    "triple": ROOM_TYPE_TRIPLE,
    "triple(3 bed)": ROOM_TYPE_TRIPLE,
    "quad": ROOM_TYPE_QUAD,
    "quad(4 bed)": ROOM_TYPE_QUAD,
    "sharing": ROOM_TYPE_SHARING,
}
ROOM_PRICE_FIELDS = {
    ROOM_TYPE_SINGLE: "cost_for_single",
    ROOM_TYPE_DOUBLE: "cost_for_double",
    ROOM_TYPE_TRIPLE: "cost_for_triple",
    ROOM_TYPE_QUAD: "cost_for_quad",
    ROOM_TYPE_SHARING: "cost_for_sharing",
}
ROOM_COUNT_FIELDS = {
    ROOM_TYPE_SINGLE: "single",
    ROOM_TYPE_DOUBLE: "double",
    ROOM_TYPE_TRIPLE: "triple",
    ROOM_TYPE_QUAD: "quad",
    ROOM_TYPE_SHARING: "sharing",
}
TRAVELER_TYPE_CHILD_GENERIC = "Child"
PASSPORT_EXPIRY_AFTER_RETURN_DATE_MESSAGE = (
    "Passport expiry must be later than the package return date. Please renew the passport before continuing with this booking."
)


def _derive_default_traveler_type(booking, traveler_sequence):
    adults = int(getattr(booking, "adults", 0) or 0)
    children = int(getattr(booking, "child", 0) or 0)
    if traveler_sequence <= adults:
        return TRAVELER_TYPE_ADULT
    if traveler_sequence <= adults + children:
        return TRAVELER_TYPE_CHILD_GENERIC
    return TRAVELER_TYPE_INFANT


def _normalize_booking_calendar_date(value):
    if value is None:
        return None

    if isinstance(value, datetime):
        localized_value = timezone.localtime(value) if timezone.is_aware(value) else value
        return localized_value.date()

    if isinstance(value, date):
        return value

    return None


def _validate_passport_expiry_after_return_date(booking, expiry_date):
    booking_return_date = _normalize_booking_calendar_date(getattr(booking, "end_date", None))
    passport_expiry_date = _normalize_booking_calendar_date(expiry_date)

    if booking_return_date is None or passport_expiry_date is None:
        return

    if passport_expiry_date <= booking_return_date:
        raise ValidationError(
            {
                "message": PASSPORT_EXPIRY_AFTER_RETURN_DATE_MESSAGE,
                "expiry_date": [PASSPORT_EXPIRY_AFTER_RETURN_DATE_MESSAGE],
            }
        )


def ensure_booking_group_assignments(booking):
    booking_groups = list(
        BookingGroup.objects.filter(booking=booking).order_by("sequence", "label", "group_id")
    )
    if booking_groups:
        default_group = booking_groups[0]
    else:
        default_group = BookingGroup.objects.create(
            booking=booking,
            label="Group 1",
            sequence=1,
        )

    passports = list(
        PassportValidity.objects.filter(passport_for_booking_number=booking).order_by(
            "traveler_sequence",
            "passport_id",
        )
    )
    for traveler_sequence, passport in enumerate(passports, start=1):
        update_fields = []
        if not getattr(passport, "booking_group_id", None):
            passport.booking_group = default_group
            update_fields.append("booking_group")
        if int(getattr(passport, "traveler_sequence", 0) or 0) != traveler_sequence:
            passport.traveler_sequence = traveler_sequence
            update_fields.append("traveler_sequence")
        if not getattr(passport, "traveler_type", None):
            passport.traveler_type = _derive_default_traveler_type(booking, traveler_sequence)
            update_fields.append("traveler_type")
        if update_fields:
            passport.save(update_fields=update_fields)

    return default_group


class BookingServiceError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Unable to process booking request."

    def __init__(self, detail=None, *, status_code=None):
        if status_code is not None:
            self.status_code = status_code
        super().__init__({"message": detail or self.default_detail})


def _format_performance_context(context):
    parts = []
    for key, value in context.items():
        if value in (None, ""):
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def _log_booking_performance(event, started_at, **context):
    logger.info(
        "booking.performance event=%s duration_ms=%.2f %s",
        event,
        (perf_counter() - started_at) * 1000,
        _format_performance_context(context),
    )


def _quantize_amount(value):
    return Decimal(value).quantize(CURRENCY_QUANTUM, rounding=ROUND_HALF_UP)


def _to_decimal_amount(value, *, field_name="amount"):
    try:
        parsed_value = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        raise BookingServiceError(
            f"{field_name} must be a valid amount.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return _quantize_amount(parsed_value)


def _format_amount_for_message(value):
    normalized_amount = _quantize_amount(value)
    if normalized_amount == normalized_amount.to_integral():
        return str(normalized_amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return f"{normalized_amount:.2f}"


def _amounts_match(left, right):
    return _quantize_amount(left) == _quantize_amount(right)


def _normalize_traveler_type(value):
    return TRAVELER_TYPE_ALIASES.get(str(value or "").strip().lower(), "")


def _normalize_room_type(value):
    normalized_value = str(value or "").strip().lower()
    if not normalized_value:
        return ""
    return ROOM_TYPE_ALIASES.get(normalized_value, "")


def _normalize_room_count(value, *, field_name):
    try:
        normalized_value = int(str(value or 0).strip())
    except (TypeError, ValueError):
        raise BookingServiceError(
            f"{field_name} must be a valid whole number.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if normalized_value < 0:
        raise BookingServiceError(
            f"{field_name} cannot be negative.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return normalized_value


def _derive_booking_commercials(package, traveler_breakdown):
    if not isinstance(traveler_breakdown, list) or not traveler_breakdown:
        raise BookingServiceError(
            "traveler_breakdown must include at least one traveler.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    room_prices = {
        room_type: _to_decimal_amount(getattr(package, field_name, 0), field_name=field_name)
        for room_type, field_name in ROOM_PRICE_FIELDS.items()
    }
    child_no_bed_price = _to_decimal_amount(
        getattr(package, "cost_for_child", 0),
        field_name="cost_for_child",
    )
    infant_price = _to_decimal_amount(
        getattr(package, "cost_for_infants", 0),
        field_name="cost_for_infants",
    )
    child_with_bed_discount = _to_decimal_amount(
        getattr(package, "discount_if_child_with_bed", 0),
        field_name="discount_if_child_with_bed",
    )

    summary = {
        "adults": 0,
        "child": 0,
        "infants": 0,
        "sharing": 0,
        "quad": 0,
        "triple": 0,
        "double": 0,
        "single": 0,
        "traveller_count": 0,
        "total_price": Decimal("0.00"),
    }

    for index, traveler in enumerate(traveler_breakdown, start=1):
        traveler_type = _normalize_traveler_type(traveler.get("traveler_type"))
        raw_room_type = traveler.get("room_type")
        room_type = _normalize_room_type(raw_room_type)

        if not traveler_type:
            raise BookingServiceError(
                f"traveler_breakdown[{index}] has an unsupported traveler_type.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if str(raw_room_type or "").strip() and not room_type:
            raise BookingServiceError(
                f"traveler_breakdown[{index}] has an unsupported room_type.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        summary["traveller_count"] += 1

        if traveler_type == TRAVELER_TYPE_ADULT:
            if not room_type:
                raise BookingServiceError(
                    f"traveler_breakdown[{index}] requires a room_type for adults.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            summary["adults"] += 1
            summary[ROOM_COUNT_FIELDS[room_type]] += 1
            summary["total_price"] += room_prices[room_type]
            continue

        if traveler_type == TRAVELER_TYPE_CHILD_WITH_BED:
            if not room_type:
                raise BookingServiceError(
                    f"traveler_breakdown[{index}] requires a room_type for children with bed.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            summary["child"] += 1
            summary[ROOM_COUNT_FIELDS[room_type]] += 1
            summary["total_price"] += max(
                room_prices[room_type] - child_with_bed_discount,
                Decimal("0.00"),
            )
            continue

        if room_type:
            raise BookingServiceError(
                f"traveler_breakdown[{index}] must not set room_type for no-bed travelers.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if traveler_type == TRAVELER_TYPE_CHILD_NO_BED:
            summary["child"] += 1
            summary["total_price"] += child_no_bed_price
            continue

        summary["infants"] += 1
        summary["total_price"] += infant_price

    if summary["traveller_count"] <= 0:
        raise BookingServiceError(
            "At least one traveller is required to create a booking.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    summary["total_price"] = _quantize_amount(summary["total_price"])
    return summary


def _validate_booking_breakdown_matches_request(validated_data, derived_summary):
    for field_name in ("adults", "child", "infants"):
        submitted_value = _get_non_negative_traveller_count(validated_data, field_name)
        if submitted_value != derived_summary[field_name]:
            raise BookingServiceError(
                "Booking traveller summary does not match the submitted traveler breakdown.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    for field_name in ("sharing", "quad", "triple", "double", "single"):
        submitted_value = _normalize_room_count(validated_data.get(field_name), field_name=field_name)
        if submitted_value != derived_summary[field_name]:
            raise BookingServiceError(
                "Booking room allocation does not match the submitted traveler breakdown.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    submitted_total_price = _to_decimal_amount(validated_data.get("total_price"), field_name="total_price")
    if not _amounts_match(submitted_total_price, derived_summary["total_price"]):
        raise BookingServiceError(
            "Submitted total_price does not match the server-calculated booking total.",
            status_code=status.HTTP_409_CONFLICT,
        )


def _build_booking_group_payloads(validated_data):
    group_items = validated_data.get("groups") or []
    if not group_items:
        group_items = [
            {
                "label": "Group 1",
                "notes": "",
                "travelers": validated_data.get("traveler_breakdown") or [],
            }
        ]

    normalized_groups = []
    for group_index, group in enumerate(group_items, start=1):
        travelers = []
        for traveler in group.get("travelers") or []:
            travelers.append(
                {
                    "traveler_type": _normalize_traveler_type(traveler.get("traveler_type"))
                    or str(traveler.get("traveler_type") or "").strip(),
                    "room_type": _normalize_room_type(traveler.get("room_type"))
                    or str(traveler.get("room_type") or "").strip(),
                }
            )

        normalized_groups.append(
            {
                "label": str(group.get("label") or f"Group {group_index}").strip()
                or f"Group {group_index}",
                "notes": str(group.get("notes") or "").strip(),
                "sequence": group_index,
                "travelers": travelers,
            }
        )

    return normalized_groups


def _validate_booking_groups(group_payloads, *, expected_traveller_count):
    if not group_payloads:
        raise BookingServiceError(
            "At least one booking group is required.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    calculated_total = 0
    for group_index, group in enumerate(group_payloads, start=1):
        traveler_count = len(group.get("travelers") or [])
        if traveler_count <= 0:
            raise BookingServiceError(
                f"groups[{group_index}] must include at least one traveler.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        calculated_total += traveler_count

    if calculated_total != expected_traveller_count:
        raise BookingServiceError(
            "Booking groups do not match the submitted traveler totals.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )


def _replace_booking_group_structure(booking, group_payloads):
    PassportValidity.objects.filter(passport_for_booking_number=booking).delete()
    BookingGroup.objects.filter(booking=booking).delete()

    for group in group_payloads:
        booking_group = BookingGroup.objects.create(
            booking=booking,
            label=group["label"],
            notes=group["notes"],
            sequence=group["sequence"],
        )

        for traveler_index, traveler in enumerate(group.get("travelers") or [], start=1):
            PassportValidity.objects.create(
                passport_for_booking_number=booking,
                booking_group=booking_group,
                traveler_sequence=traveler_index,
                traveler_type=traveler.get("traveler_type") or None,
                room_type=traveler.get("room_type") or None,
            )


def get_total_approved_payment_amount_decimal(booking, *, exclude_payment_id=None):
    total_amount = Decimal("0.00")
    excluded_payment_id = str(exclude_payment_id or "").strip()

    for payment in get_booking_payments(booking):
        if excluded_payment_id and str(getattr(payment, "payment_id", "")) == excluded_payment_id:
            continue
        if _is_payment_approved(payment):
            total_amount += _to_decimal_amount(
                getattr(payment, "transaction_amount", 0),
                field_name="transaction_amount",
            )

    return _quantize_amount(total_amount)


def get_expected_payment_amount(booking, payment_stage, *, exclude_payment_id=None):
    normalized_stage = _normalize_payment_stage(payment_stage)
    total_price = _to_decimal_amount(getattr(booking, "total_price", 0), field_name="total_price")

    if normalized_stage == "Minimum":
        return max(_quantize_amount(total_price * MINIMUM_PAYMENT_RATE), MINIMUM_PAYMENT_FLOOR)

    approved_amount = get_total_approved_payment_amount_decimal(
        booking,
        exclude_payment_id=exclude_payment_id,
    )
    remaining_amount = total_price - approved_amount
    if remaining_amount < Decimal("0.00"):
        remaining_amount = Decimal("0.00")
    return _quantize_amount(remaining_amount)


def validate_booking_payment_amount(booking, payment_stage, transaction_amount, *, exclude_payment_id=None):
    normalized_stage = _normalize_payment_stage(payment_stage)
    submitted_amount = _to_decimal_amount(transaction_amount, field_name="transaction_amount")
    expected_amount = get_expected_payment_amount(
        booking,
        normalized_stage,
        exclude_payment_id=exclude_payment_id,
    )

    if not _amounts_match(submitted_amount, expected_amount):
        stage_label = "minimum" if normalized_stage == "Minimum" else "full"
        raise BookingServiceError(
            f"{stage_label.capitalize()} payment amount must be {_format_amount_for_message(expected_amount)} for this booking.",
            status_code=status.HTTP_409_CONFLICT,
        )

    return float(submitted_amount)


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
    totals = queryset.aggregate(
        adults_total=Coalesce(Sum("adults"), 0),
        child_total=Coalesce(Sum("child"), 0),
        infants_total=Coalesce(Sum("infants"), 0),
    )
    return (
        int(totals.get("adults_total") or 0)
        + int(totals.get("child_total") or 0)
        + int(totals.get("infants_total") or 0)
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


def _update_booking_status_for_passport_progress(user_profile, booking):
    return _reload_booking_detail_for_user(user_profile, sync_booking_state(booking, save=True))


def _booking_passports_are_complete(booking):
    return booking_passports_are_complete(booking)


def _attach_passport_files(passport, validated_data):
    update_fields = []
    booking_number = getattr(
        getattr(passport, "passport_for_booking_number", None),
        "booking_number",
        "",
    )

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
        file_save_started_at = perf_counter()
        stored_path = default_storage.save(safe_name, uploaded_file)
        _log_booking_performance(
            "passport_file_save",
            file_save_started_at,
            booking_number=booking_number,
            field_name=field_name,
            size_bytes=getattr(uploaded_file, "size", 0),
        )
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
    validated_transaction_amount = validate_booking_payment_amount(
        booking,
        payment_stage,
        transaction_amount,
        exclude_payment_id=getattr(payment, "payment_id", None),
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
        payment.transaction_amount = validated_transaction_amount
        payment.payment_status = PAYMENT_STATUS_UNDER_REVIEW
        payment.review_message = None
        payment.transaction_time = timezone.now()
    else:
        payment = Payment(
            transaction_number=normalized_transaction_number or None,
            transaction_type=payment_stage,
            transaction_amount=validated_transaction_amount,
            payment_status=PAYMENT_STATUS_UNDER_REVIEW,
            review_message=None,
            booking_token=booking,
        )

    if uploaded_file is not None:
        extension = Path(uploaded_file.name).suffix.lower()
        safe_name = f"payment_uploads/{uuid4().hex}{extension}"
        file_save_started_at = perf_counter()
        stored_path = default_storage.save(safe_name, uploaded_file)
        _log_booking_performance(
            "payment_file_save",
            file_save_started_at,
            booking_number=getattr(booking, "booking_number", ""),
            payment_stage=payment_stage,
            size_bytes=getattr(uploaded_file, "size", 0),
        )
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
    include_detail_relations=False,
):
    lookup = Q(booking_number=str(identifier))
    try:
        lookup |= Q(booking_id=UUID(str(identifier)))
    except (TypeError, ValueError):
        pass

    if include_detail_relations:
        queryset = _get_user_booking_detail_queryset(user_profile).filter(lookup)
    else:
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


def _is_booking_number_collision(exc):
    normalized_message = str(exc).lower()
    return "booking_number" in normalized_message and (
        "duplicate" in normalized_message or "unique" in normalized_message
    )


def _get_user_booking_detail_queryset(user_profile):
    return get_partner_bookings_queryset(include_detail_relations=True).filter(order_by=user_profile)


def _get_user_booking_mutation_queryset(user_profile):
    return (
        Booking.objects.select_related("order_by", "order_to", "package_token")
        .prefetch_related(
            "order_to__company_of_partner",
            "status_for_booking",
            "passport_for_booking_number",
            "booking_token",
        )
        .filter(order_by=user_profile)
    )


def _reload_booking_detail_for_user(user_profile, booking):
    detailed_booking = _get_user_booking_detail_queryset(user_profile).filter(pk=booking.pk).first()
    return detailed_booking or booking


def _reload_booking_mutation_for_user(user_profile, booking):
    summarized_booking = _get_user_booking_mutation_queryset(user_profile).filter(pk=booking.pk).first()
    return summarized_booking or booking


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
    derived_booking_summary = _derive_booking_commercials(
        package,
        validated_data.get("traveler_breakdown") or [],
    )
    _validate_booking_breakdown_matches_request(validated_data, derived_booking_summary)

    requested_traveller_count = derived_booking_summary["traveller_count"]
    booking_group_payloads = _build_booking_group_payloads(validated_data)
    _validate_booking_groups(
        booking_group_payloads,
        expected_traveller_count=requested_traveller_count,
    )
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
        "adults": derived_booking_summary["adults"],
        "child": derived_booking_summary["child"],
        "infants": derived_booking_summary["infants"],
        "sharing": str(derived_booking_summary["sharing"]),
        "quad": str(derived_booking_summary["quad"]),
        "triple": str(derived_booking_summary["triple"]),
        "double": str(derived_booking_summary["double"]),
        "single": str(derived_booking_summary["single"]),
        "start_date": canonical_start_date,
        "end_date": canonical_end_date,
        "total_price": float(derived_booking_summary["total_price"]),
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

                _replace_booking_group_structure(resumable_booking, booking_group_payloads)

            DocumentsStatus.objects.get_or_create(status_for_booking=resumable_booking)
            return _reload_booking_mutation_for_user(
                user,
                sync_booking_state(resumable_booking, save=True),
            ), False

        _validate_package_range_capacity(
            package=package,
            package_date_range=package_date_range,
            start_date=canonical_start_date,
            end_date=canonical_end_date,
            requested_traveller_count=requested_traveller_count,
        )

        booking = None
        last_collision_error = None
        for _ in range(BOOKING_NUMBER_RETRY_ATTEMPTS):
            try:
                with transaction.atomic():
                    booking = Booking.objects.create(
                        booking_number=generate_unique_booking_number(),
                        **booking_fields,
                    )
                break
            except IntegrityError as exc:
                if not _is_booking_number_collision(exc):
                    raise
                last_collision_error = exc

        if booking is None:
            raise BookingServiceError(
                "Unable to generate a unique booking number. Please try again.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from last_collision_error

        _replace_booking_group_structure(booking, booking_group_payloads)
        DocumentsStatus.objects.get_or_create(status_for_booking=booking)
        return _reload_booking_mutation_for_user(user, sync_booking_state(booking, save=True)), True


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
            email_started_at = perf_counter()
            try:
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
            finally:
                _log_booking_performance(
                    "user_new_booking_email",
                    email_started_at,
                    booking_number=booking.booking_number,
                    payment_stage=payment_stage,
                )

        return _reload_booking_mutation_for_user(user, sync_booking_state(booking, save=True))


def update_booking_payment(validated_data):
    with transaction.atomic():
        user, booking = _get_booking_for_user(
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
        return _reload_booking_mutation_for_user(user, sync_booking_state(booking, save=True))


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
            email_started_at = perf_counter()
            try:
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
            finally:
                _log_booking_performance(
                    "user_new_booking_email",
                    email_started_at,
                    booking_number=booking.booking_number,
                    payment_stage=payment_stage,
                )

        return _reload_booking_mutation_for_user(user, sync_booking_state(booking, save=True))


def validate_passport(validated_data):
    with transaction.atomic():
        user, booking = _get_booking_for_user(
            validated_data["session_token"],
            validated_data["booking_number"],
            must_be_future=False,
            lock_for_update=True,
        )
        default_group = ensure_booking_group_assignments(booking)
        if not booking_allows_client_traveller_updates(booking):
            raise BookingServiceError(
                "Traveler details cannot be updated at the current booking stage.",
                status_code=status.HTTP_409_CONFLICT,
            )
        _validate_passport_expiry_after_return_date(booking, validated_data.get("expiry_date"))
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
            next_traveler_sequence = traveller_status.count() + 1
            passport = PassportValidity(
                passport_for_booking_number=booking,
                booking_group=default_group,
                traveler_sequence=next_traveler_sequence,
                traveler_type=_derive_default_traveler_type(booking, next_traveler_sequence),
            )

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

        return _update_booking_status_for_passport_progress(user, booking)


def update_passport_validation(validated_data):
    with transaction.atomic():
        user, booking = _get_booking_for_user(
            validated_data["session_token"],
            validated_data["booking_number"],
            must_be_future=False,
            lock_for_update=True,
        )
        ensure_booking_group_assignments(booking)
        if not booking_allows_client_traveller_updates(booking):
            raise BookingServiceError(
                "Traveler details cannot be updated at the current booking stage.",
                status_code=status.HTTP_409_CONFLICT,
            )
        _validate_passport_expiry_after_return_date(booking, validated_data.get("expiry_date"))
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
        return _update_booking_status_for_passport_progress(user, booking)
