from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.utils import timezone

from .flow_utils import get_expected_traveller_count
from .statuses import (
    BOOKING_STATUS_AWAITING_FINAL_PAYMENT,
    BOOKING_STATUS_CANCELLED,
    BOOKING_STATUS_CAPACITY_SET,
    BOOKING_STATUS_COMPLETED,
    BOOKING_STATUS_EXPIRED,
    BOOKING_STATUS_HOLD,
    BOOKING_STATUS_IN_FULFILLMENT,
    BOOKING_STATUS_OPERATOR_VISIBLE_SET,
    BOOKING_STATUS_READY_FOR_OPERATOR,
    BOOKING_STATUS_READY_FOR_TRAVEL,
    BOOKING_STATUS_SET,
    BOOKING_STATUS_TERMINAL_SET,
    BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
    ISSUE_STATUS_NONE,
    ISSUE_STATUS_OPERATOR_OBJECTION,
    ISSUE_STATUS_REPORTED,
    PAYMENT_STATUS_APPROVED,
    PAYMENT_STATUS_NOT_SUBMITTED,
    PAYMENT_STATUS_REJECTED,
    PAYMENT_STATUS_UNDER_REVIEW,
    WORKFLOW_BUCKET_COMPLETED,
    WORKFLOW_BUCKET_FULFILLMENT,
    WORKFLOW_BUCKET_HISTORY,
    WORKFLOW_BUCKET_ISSUES,
    WORKFLOW_BUCKET_READY,
    WORKFLOW_BUCKET_READY_FOR_TRAVEL,
    WORKFLOW_BUCKET_VIEW_ONLY,
)

PAYMENT_REVIEWABLE_STATUSES = {
    BOOKING_STATUS_HOLD,
    BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
    BOOKING_STATUS_AWAITING_FINAL_PAYMENT,
    BOOKING_STATUS_READY_FOR_OPERATOR,
}


def normalize_booking_status(value):
    normalized = str(value or "").strip().upper()
    if normalized in BOOKING_STATUS_SET:
        return normalized
    return ""


def normalize_payment_status(value):
    normalized = str(value or "").strip().upper()
    if normalized in {
        PAYMENT_STATUS_UNDER_REVIEW,
        PAYMENT_STATUS_APPROVED,
        PAYMENT_STATUS_REJECTED,
    }:
        return normalized
    return PAYMENT_STATUS_NOT_SUBMITTED


def _payment_sort_key(payment):
    transaction_time = getattr(payment, "transaction_time", None)
    if transaction_time is None:
        return 0
    if timezone.is_naive(transaction_time):
        transaction_time = timezone.make_aware(transaction_time, timezone.get_current_timezone())
    return transaction_time.timestamp()


def _as_local_date(value):
    if value is None:
        return None
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.date()


def get_booking_payments(booking):
    cached_payments = getattr(booking, "_cached_booking_payments", None)
    if cached_payments is not None:
        return cached_payments

    prefetched_cache = getattr(booking, "_prefetched_objects_cache", None) or {}
    prefetched_payments = prefetched_cache.get("booking_token")
    if prefetched_payments is not None:
        payments = list(prefetched_payments)
    else:
        relation = getattr(booking, "booking_token", None)
        payments = list(relation.all()) if relation is not None else []

    sorted_payments = sorted(payments, key=_payment_sort_key, reverse=True)
    setattr(booking, "_cached_booking_payments", sorted_payments)
    return sorted_payments


def _normalize_payment_stage(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"minimum", "min"}:
        return "Minimum"
    if normalized in {"full"}:
        return "Full"
    return ""


def get_latest_payment_for_stage(booking, stage):
    normalized_stage = _normalize_payment_stage(stage)
    if not normalized_stage:
        return None
    for payment in get_booking_payments(booking):
        if _normalize_payment_stage(getattr(payment, "transaction_type", "")) == normalized_stage:
            return payment
    return None


def get_payment_stage_status(booking, stage):
    normalized_stage = _normalize_payment_stage(stage)
    if normalized_stage == "Minimum":
        annotated_status = getattr(booking, "annotated_minimum_payment_status", None)
        if annotated_status:
            return normalize_payment_status(annotated_status)
    if normalized_stage == "Full":
        annotated_status = getattr(booking, "annotated_full_payment_status", None)
        if annotated_status:
            return normalize_payment_status(annotated_status)

    payment = get_latest_payment_for_stage(booking, stage)
    if payment is None:
        return PAYMENT_STATUS_NOT_SUBMITTED
    return normalize_payment_status(getattr(payment, "payment_status", ""))


def booking_has_any_submitted_payment(booking):
    return (
        get_payment_stage_status(booking, "Minimum") != PAYMENT_STATUS_NOT_SUBMITTED
        or get_payment_stage_status(booking, "Full") != PAYMENT_STATUS_NOT_SUBMITTED
    )


def booking_has_under_review_payment(booking):
    return (
        get_payment_stage_status(booking, "Minimum") == PAYMENT_STATUS_UNDER_REVIEW
        or get_payment_stage_status(booking, "Full") == PAYMENT_STATUS_UNDER_REVIEW
    )


def get_initial_payment_status(booking):
    minimum_payment_status = get_payment_stage_status(booking, "Minimum")
    full_payment_status = get_payment_stage_status(booking, "Full")

    if (
        minimum_payment_status == PAYMENT_STATUS_APPROVED
        or full_payment_status == PAYMENT_STATUS_APPROVED
    ):
        return PAYMENT_STATUS_APPROVED

    if (
        minimum_payment_status == PAYMENT_STATUS_UNDER_REVIEW
        or full_payment_status == PAYMENT_STATUS_UNDER_REVIEW
    ):
        return PAYMENT_STATUS_UNDER_REVIEW

    if (
        minimum_payment_status == PAYMENT_STATUS_REJECTED
        or full_payment_status == PAYMENT_STATUS_REJECTED
    ):
        return PAYMENT_STATUS_REJECTED

    return PAYMENT_STATUS_NOT_SUBMITTED


def booking_has_minimum_approval(booking):
    return (
        get_payment_stage_status(booking, "Minimum") == PAYMENT_STATUS_APPROVED
        or get_payment_stage_status(booking, "Full") == PAYMENT_STATUS_APPROVED
    )


def booking_has_full_approval(booking):
    return get_payment_stage_status(booking, "Full") == PAYMENT_STATUS_APPROVED


def _to_decimal_amount(value):
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def get_total_approved_payment_amount(booking):
    annotated_total = getattr(booking, "annotated_total_approved_payment_amount", None)
    if annotated_total is not None:
        return float(_to_decimal_amount(annotated_total))

    total = Decimal("0.00")
    for payment in get_booking_payments(booking):
        if normalize_payment_status(getattr(payment, "payment_status", "")) == PAYMENT_STATUS_APPROVED:
            total += _to_decimal_amount(getattr(payment, "transaction_amount", 0))
    return float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def get_remaining_amount_due(booking):
    remaining_amount = _to_decimal_amount(getattr(booking, "total_price", 0)) - Decimal(
        str(get_total_approved_payment_amount(booking))
    )
    return float(max(remaining_amount, Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def normalize_airline_direction(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"return", "inbound", "back"}:
        return "return"
    return "outbound"


def _get_airline_sort_timestamp(detail):
    flight_date = getattr(detail, "flight_date", None)
    if flight_date is None:
        return 0
    if timezone.is_naive(flight_date):
        flight_date = timezone.make_aware(flight_date, timezone.get_current_timezone())
    return flight_date.timestamp()


def _get_related_items(booking, relation_name):
    related_items_cache = getattr(booking, "_cached_related_items", None)
    if related_items_cache is None:
        related_items_cache = {}
        setattr(booking, "_cached_related_items", related_items_cache)

    if relation_name in related_items_cache:
        return related_items_cache[relation_name]

    prefetched_cache = getattr(booking, "_prefetched_objects_cache", None) or {}
    prefetched_items = prefetched_cache.get(relation_name)
    if prefetched_items is not None:
        related_items_cache[relation_name] = list(prefetched_items)
        return related_items_cache[relation_name]

    relation = getattr(booking, relation_name, None)
    if relation is None:
        related_items_cache[relation_name] = []
        return related_items_cache[relation_name]

    try:
        related_items_cache[relation_name] = list(relation.all())
    except Exception:
        related_items_cache[relation_name] = []

    return related_items_cache[relation_name]


def _get_single_related_item(instance, relation_name):
    try:
        return getattr(instance, relation_name, None)
    except Exception:
        return None


def booking_requires_return_airline_detail(booking):
    package = getattr(booking, "package_token", None)
    if package is None:
        return False

    package_airlines = _get_related_items(package, "airline_for_package")
    if not package_airlines:
        return False

    return bool(getattr(package_airlines[0], "is_return_flight_included", False))


def get_booking_airline_details(booking):
    direction_order = {"outbound": 0, "return": 1}
    return sorted(
        _get_related_items(booking, "airline_for_booking"),
        key=lambda detail: (
            direction_order.get(
                normalize_airline_direction(getattr(detail, "flight_direction", "")),
                len(direction_order),
            ),
            _get_airline_sort_timestamp(detail),
        ),
    )


def booking_airline_details_are_complete(booking):
    required_directions = {"outbound"}
    if booking_requires_return_airline_detail(booking):
        required_directions.add("return")

    captured_directions = {
        normalize_airline_direction(getattr(detail, "flight_direction", ""))
        for detail in get_booking_airline_details(booking)
    }
    return required_directions.issubset(captured_directions)


def booking_passports_are_complete(booking):
    annotated_passports_complete = getattr(booking, "annotated_passports_complete", None)
    if annotated_passports_complete is not None:
        return bool(annotated_passports_complete)

    traveller_status = _get_related_items(booking, "passport_for_booking_number")
    expected_traveller_count = get_expected_traveller_count(booking)
    if len(traveller_status) < expected_traveller_count:
        return False

    return all(
        item.user_passport
        and item.user_photo
        and item.first_name
        and item.last_name
        and item.date_of_birth
        and item.passport_number
        and item.passport_country
        and item.expiry_date
        for item in traveller_status[:expected_traveller_count]
    )


def _normalize_city_key(value):
    normalized = str(value or "").strip().lower()
    if normalized == "mecca":
        return "makkah"
    return normalized


def _get_required_hotel_city_keys(booking):
    package = getattr(booking, "package_token", None)
    if package is None:
        return []

    hotel_items = _get_related_items(package, "hotel_for_package")
    if hotel_items:
        seen = []
        for hotel in hotel_items:
            city_key = _normalize_city_key(getattr(hotel, "hotel_city", ""))
            if city_key and city_key not in seen:
                seen.append(city_key)
        if seen:
            return seen

    city_night_pairs = (
        ("jeddah", getattr(package, "jeddah_nights", 0)),
        ("makkah", getattr(package, "mecca_nights", 0)),
        ("madinah", getattr(package, "madinah_nights", 0)),
        ("taif", getattr(package, "taif_nights", 0)),
        ("riyadh", getattr(package, "riyadah_nights", 0)),
    )
    return [city_key for city_key, nights in city_night_pairs if int(nights or 0) > 0]


def _hotel_fulfillment_has_shared_details(fulfillment):
    return any(
        [
            getattr(fulfillment, "hotel_name", None),
            getattr(fulfillment, "contact_name", None),
            getattr(fulfillment, "contact_phone", None),
            getattr(fulfillment, "note", None),
        ]
    )


def booking_hotel_fulfillments_are_complete(booking):
    hotel_documents = _get_booking_documents_by_category(booking, "hotel")
    if hotel_documents:
        return True

    required_city_keys = _get_required_hotel_city_keys(booking)
    if not required_city_keys:
        return True

    hotel_fulfillments = _get_related_items(booking, "hotel_fulfillments")
    if not hotel_fulfillments:
        return False

    fulfilled_city_keys = set()
    for fulfillment in hotel_fulfillments:
        if not _hotel_fulfillment_has_shared_details(fulfillment):
            continue

        city_key = _normalize_city_key(getattr(fulfillment, "city", ""))
        if city_key:
            fulfilled_city_keys.add(city_key)

    return set(required_city_keys).issubset(fulfilled_city_keys)


def _get_booking_documents_by_category(booking, category):
    normalized_category = str(category or "").strip().lower()
    documents = _get_related_items(booking, "document_for_booking_token")
    filtered_documents = []
    for document in documents:
        document_category = str(
            getattr(document, "document_category", None)
            or getattr(document, "document_for", "")
            or ""
        ).strip().lower()
        if document_category == normalized_category:
            filtered_documents.append(document)
    return filtered_documents


def booking_visa_documents_are_complete(booking):
    return bool(_get_booking_documents_by_category(booking, "evisa"))


def booking_airline_documents_are_complete(booking):
    return bool(_get_booking_documents_by_category(booking, "airline"))


def _package_has_transport_default(booking):
    package = getattr(booking, "package_token", None)
    if package is None:
        return False

    return bool(_get_related_items(package, "transport_for_package"))


def _transport_fulfillment_has_ticket(transport_fulfillment):
    return bool(getattr(transport_fulfillment, "ticket_reference", None))


def _transport_fulfillment_has_details(transport_fulfillment):
    return any(
        [
            getattr(transport_fulfillment, "transport_name", None),
            getattr(transport_fulfillment, "transport_type", None),
            getattr(transport_fulfillment, "route_summary", None),
            getattr(transport_fulfillment, "contact_name", None),
            getattr(transport_fulfillment, "contact_phone", None),
        ]
    )


def booking_transport_fulfillment_is_complete(booking):
    transport_fulfillment = _get_single_related_item(booking, "transport_fulfillment")
    transport_documents = _get_booking_documents_by_category(booking, "transport")
    package_has_transport = _package_has_transport_default(booking)

    if not transport_fulfillment:
        if transport_documents:
            return True
        return not package_has_transport

    mode = str(getattr(transport_fulfillment, "transport_mode", "") or "").strip().lower()
    has_ticket = _transport_fulfillment_has_ticket(transport_fulfillment) or bool(transport_documents)
    has_details = _transport_fulfillment_has_details(transport_fulfillment)

    if has_ticket or has_details:
        return True

    if not mode or mode == "none":
        return not package_has_transport

    return False


def get_open_traveler_issues(booking):
    traveler_issues = _get_related_items(booking, "traveler_issues")
    return [
        issue
        for issue in traveler_issues
        if str(getattr(issue, "status", "") or "").strip().lower() == "open"
    ]


def booking_has_open_traveler_issues(booking):
    annotated_has_open_issues = getattr(booking, "annotated_has_open_traveler_issues", None)
    if annotated_has_open_issues is not None:
        return bool(annotated_has_open_issues)

    return bool(get_open_traveler_issues(booking))


def booking_fulfillment_summary(booking):
    return {
        "visa_completed": booking_visa_documents_are_complete(booking),
        "airline_documents_completed": booking_airline_documents_are_complete(booking),
        "airline_details_completed": booking_airline_details_are_complete(booking),
        "hotel_completed": booking_hotel_fulfillments_are_complete(booking),
        "transport_completed": booking_transport_fulfillment_is_complete(booking),
    }


def booking_fulfillment_is_complete(booking):
    summary = booking_fulfillment_summary(booking)
    return all(summary.values())


def booking_operator_documents_are_complete(booking):
    return (
        booking_visa_documents_are_complete(booking)
        and booking_airline_documents_are_complete(booking)
        and booking_airline_details_are_complete(booking)
        and booking_hotel_fulfillments_are_complete(booking)
        and booking_transport_fulfillment_is_complete(booking)
    )


def booking_has_operator_visibility(booking):
    if booking_has_minimum_approval(booking):
        return True
    return normalize_booking_status(getattr(booking, "booking_status", "")) in BOOKING_STATUS_OPERATOR_VISIBLE_SET


def booking_allows_operator_action(booking):
    return normalize_booking_status(getattr(booking, "booking_status", "")) == BOOKING_STATUS_READY_FOR_OPERATOR


def booking_allows_client_traveller_updates(booking):
    return normalize_booking_status(getattr(booking, "booking_status", "")) == BOOKING_STATUS_TRAVELER_DETAILS_PENDING


def booking_allows_minimum_payment_submission(booking):
    if normalize_booking_status(getattr(booking, "booking_status", "")) in BOOKING_STATUS_TERMINAL_SET:
        return False

    minimum_status = get_payment_stage_status(booking, "Minimum")
    full_status = get_payment_stage_status(booking, "Full")
    if minimum_status == PAYMENT_STATUS_UNDER_REVIEW or full_status == PAYMENT_STATUS_UNDER_REVIEW:
        return False
    if minimum_status == PAYMENT_STATUS_APPROVED or full_status == PAYMENT_STATUS_APPROVED:
        return False
    return True


def booking_allows_full_payment_submission(booking):
    if normalize_booking_status(getattr(booking, "booking_status", "")) in BOOKING_STATUS_TERMINAL_SET:
        return False

    full_status = get_payment_stage_status(booking, "Full")
    if full_status in {PAYMENT_STATUS_UNDER_REVIEW, PAYMENT_STATUS_APPROVED}:
        return False
    if not booking_has_minimum_approval(booking):
        return False
    return booking_passports_are_complete(booking)


def booking_can_take_decision(booking):
    return normalize_booking_status(getattr(booking, "booking_status", "")) == BOOKING_STATUS_READY_FOR_OPERATOR


def booking_can_edit_fulfillment(booking):
    return normalize_booking_status(getattr(booking, "booking_status", "")) in {
        BOOKING_STATUS_IN_FULFILLMENT,
        BOOKING_STATUS_READY_FOR_TRAVEL,
    }


def booking_can_manage_traveler_issues(booking):
    return normalize_booking_status(getattr(booking, "booking_status", "")) in {
        BOOKING_STATUS_IN_FULFILLMENT,
        BOOKING_STATUS_READY_FOR_TRAVEL,
        BOOKING_STATUS_COMPLETED,
    }


def booking_can_complete(booking):
    return (
        normalize_booking_status(getattr(booking, "booking_status", "")) == BOOKING_STATUS_READY_FOR_TRAVEL
        and not booking_has_open_traveler_issues(booking)
    )


def resolve_client_workflow_stage(booking):
    booking_status = normalize_booking_status(getattr(booking, "booking_status", ""))
    minimum_status = get_payment_stage_status(booking, "Minimum")
    full_status = get_payment_stage_status(booking, "Full")

    if booking_status in BOOKING_STATUS_TERMINAL_SET:
        return "booking_status"

    has_any_approved_payment = booking_has_minimum_approval(booking)
    if not has_any_approved_payment:
        if (
            minimum_status == PAYMENT_STATUS_UNDER_REVIEW
            or full_status == PAYMENT_STATUS_UNDER_REVIEW
        ):
            return "initial_payment_review"
        return "initial_payment"

    if not booking_passports_are_complete(booking):
        return "traveler_details"

    if full_status == PAYMENT_STATUS_UNDER_REVIEW:
        return "full_payment_review"

    if not booking_has_full_approval(booking):
        return "remaining_payment"

    return "booking_status"


def resolve_client_workflow_step(booking):
    stage = resolve_client_workflow_stage(booking)
    return {
        "initial_payment": 2,
        "initial_payment_review": 2,
        "traveler_details": 3,
        "remaining_payment": 4,
        "full_payment_review": 4,
        "booking_status": 5,
    }.get(stage, 1)


def booking_counts_against_capacity(booking):
    return normalize_booking_status(getattr(booking, "booking_status", "")) in BOOKING_STATUS_CAPACITY_SET


def resolve_operator_workflow_bucket(booking):
    if not booking_has_operator_visibility(booking):
        return ""

    booking_status = normalize_booking_status(getattr(booking, "booking_status", ""))
    issue_status = str(getattr(booking, "issue_status", ISSUE_STATUS_NONE) or ISSUE_STATUS_NONE).strip().upper()
    if issue_status == ISSUE_STATUS_OPERATOR_OBJECTION:
        return WORKFLOW_BUCKET_ISSUES
    if issue_status == ISSUE_STATUS_REPORTED or booking_has_open_traveler_issues(booking):
        return WORKFLOW_BUCKET_ISSUES

    if booking_status in {BOOKING_STATUS_TRAVELER_DETAILS_PENDING, BOOKING_STATUS_AWAITING_FINAL_PAYMENT}:
        return WORKFLOW_BUCKET_VIEW_ONLY
    if booking_status == BOOKING_STATUS_READY_FOR_OPERATOR:
        return WORKFLOW_BUCKET_READY
    if booking_status == BOOKING_STATUS_IN_FULFILLMENT:
        return WORKFLOW_BUCKET_FULFILLMENT
    if booking_status == BOOKING_STATUS_READY_FOR_TRAVEL:
        return WORKFLOW_BUCKET_READY_FOR_TRAVEL
    if booking_status == BOOKING_STATUS_COMPLETED:
        return WORKFLOW_BUCKET_COMPLETED
    if booking_status in {BOOKING_STATUS_CANCELLED, BOOKING_STATUS_EXPIRED}:
        return WORKFLOW_BUCKET_HISTORY
    return ""


def ensure_hold_expiry(booking, *, now=None):
    now = now or timezone.now()
    if getattr(booking, "hold_expires_at", None) or normalize_booking_status(getattr(booking, "booking_status", "")) == BOOKING_STATUS_HOLD:
        if not booking_has_any_submitted_payment(booking) and not getattr(booking, "hold_expires_at", None):
            booking.hold_expires_at = now + timedelta(minutes=15)
            return True
    return False


def clear_booking_runtime_caches(booking):
    for attribute_name in (
        "_cached_booking_payments",
        "_cached_related_items",
        "_workflow_read_resolved",
    ):
        if hasattr(booking, attribute_name):
            delattr(booking, attribute_name)

    if hasattr(booking, "_prefetched_objects_cache"):
        booking._prefetched_objects_cache = {}


def sync_booking_state(booking, *, now=None, save=True):
    now = now or timezone.now()
    if save:
        clear_booking_runtime_caches(booking)
    update_fields = []

    current_status = normalize_booking_status(getattr(booking, "booking_status", ""))
    if current_status in BOOKING_STATUS_TERMINAL_SET:
        target_status = current_status
    else:
        minimum_status = get_payment_stage_status(booking, "Minimum")
        full_status = get_payment_stage_status(booking, "Full")
        hold_expires_at = getattr(booking, "hold_expires_at", None)
        correction_expires_at = getattr(booking, "payment_correction_expires_at", None)
        booking_end_date = getattr(booking, "end_date", None)
        has_submitted_payment = booking_has_any_submitted_payment(booking)
        has_minimum_approval = booking_has_minimum_approval(booking)
        has_full_approval = booking_has_full_approval(booking)
        has_trip_ended = booking_end_date is not None and timezone.localdate(now) > _as_local_date(booking_end_date)

        if hold_expires_at and not has_submitted_payment and now > hold_expires_at:
            target_status = BOOKING_STATUS_EXPIRED
        elif correction_expires_at and (
            minimum_status == PAYMENT_STATUS_REJECTED or full_status == PAYMENT_STATUS_REJECTED
        ) and now > correction_expires_at:
            target_status = BOOKING_STATUS_EXPIRED
        elif current_status == BOOKING_STATUS_READY_FOR_TRAVEL:
            if has_trip_ended and booking_can_complete(booking):
                target_status = BOOKING_STATUS_COMPLETED
            else:
                target_status = BOOKING_STATUS_READY_FOR_TRAVEL
        elif current_status == BOOKING_STATUS_IN_FULFILLMENT:
            has_operator_documents = booking_operator_documents_are_complete(booking)
            if has_operator_documents:
                target_status = BOOKING_STATUS_READY_FOR_TRAVEL
            else:
                target_status = BOOKING_STATUS_IN_FULFILLMENT
        elif not has_minimum_approval:
            target_status = BOOKING_STATUS_HOLD
        elif not booking_passports_are_complete(booking):
            target_status = BOOKING_STATUS_TRAVELER_DETAILS_PENDING
        elif not has_full_approval:
            target_status = BOOKING_STATUS_AWAITING_FINAL_PAYMENT
        else:
            target_status = BOOKING_STATUS_READY_FOR_OPERATOR

    if target_status != current_status:
        booking.booking_status = target_status
        booking.status_changed_at = now
        update_fields.extend(["booking_status", "status_changed_at"])

    current_issue_status = str(
        getattr(booking, "issue_status", ISSUE_STATUS_NONE) or ISSUE_STATUS_NONE
    ).strip().upper()
    if current_issue_status == ISSUE_STATUS_OPERATOR_OBJECTION:
        normalized_issue_status = ISSUE_STATUS_OPERATOR_OBJECTION
    elif booking_has_open_traveler_issues(booking):
        normalized_issue_status = ISSUE_STATUS_REPORTED
    else:
        normalized_issue_status = ISSUE_STATUS_NONE
    if normalized_issue_status != getattr(booking, "issue_status", ISSUE_STATUS_NONE):
        booking.issue_status = normalized_issue_status
        update_fields.append("issue_status")

    has_any_approved_payment = (
        get_payment_stage_status(booking, "Minimum") == PAYMENT_STATUS_APPROVED
        or get_payment_stage_status(booking, "Full") == PAYMENT_STATUS_APPROVED
    )
    if bool(getattr(booking, "is_payment_received", False)) != has_any_approved_payment:
        booking.is_payment_received = has_any_approved_payment
        update_fields.append("is_payment_received")

    should_clear_hold = booking_has_any_submitted_payment(booking)
    if should_clear_hold and getattr(booking, "hold_expires_at", None) is not None:
        booking.hold_expires_at = None
        update_fields.append("hold_expires_at")
    if not should_clear_hold and target_status == BOOKING_STATUS_HOLD and getattr(booking, "hold_expires_at", None) is None:
        booking.hold_expires_at = now + timedelta(minutes=15)
        update_fields.append("hold_expires_at")

    has_rejected_payment = (
        get_payment_stage_status(booking, "Minimum") == PAYMENT_STATUS_REJECTED
        or get_payment_stage_status(booking, "Full") == PAYMENT_STATUS_REJECTED
    )
    if not has_rejected_payment and getattr(booking, "payment_correction_expires_at", None) is not None:
        booking.payment_correction_expires_at = None
        update_fields.append("payment_correction_expires_at")

    if save and update_fields:
        booking.save(update_fields=list(dict.fromkeys(update_fields)))

    return booking
