import json
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum, Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from common.authentication import (
    get_authenticated_partner_profile,
    is_authenticated_staff_user,
    resolve_authenticated_partner_profile,
)
from common.pagination import CustomPagination
from common.permissions import IsAdminOrAuthenticatedPartnerProfile
from common.utility import validate_required_fields, check_file_format_and_size, save_file_in_directory, delete_file_from_directory, send_objection_email, send_booking_documents_email
from common.logs_file import logger
from partners.models import PartnerProfile, HuzBasicDetail, HuzHotelDetail
from .models import (
    Booking,
    BookingAirlineDetail,
    BookingComplaints,
    BookingDocuments,
    BookingGroup,
    BookingHotelFulfillment,
    BookingObjections,
    BookingRatingAndReview,
    BookingTransportFulfillment,
    DocumentsStatus,
    PartnersBookingPayment,
    PassportValidity,
    TravelerIssue,
)
from .querysets import (
    annotate_effective_booking_status,
    build_partner_workflow_bucket_q,
    filter_partner_booking_queryset,
)
from .serializers import (
    DetailBookingSerializer,
    PartnerBookingListSerializer,
    PartnersBookingPaymentSerializer,
    BookingComplaintsSerializer,
    PartnerRatingSerializer,
)
from .statuses import (
    BOOKING_STATUS_AWAITING_FINAL_PAYMENT,
    BOOKING_STATUS_CANCELLED,
    BOOKING_STATUS_CHOICES,
    BOOKING_STATUS_COMPLETED,
    BOOKING_STATUS_EXPIRED,
    BOOKING_STATUS_HOLD,
    BOOKING_STATUS_IN_FULFILLMENT,
    BOOKING_STATUS_READY_FOR_OPERATOR,
    BOOKING_STATUS_READY_FOR_TRAVEL,
    BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
    ISSUE_STATUS_NONE,
    ISSUE_STATUS_OPERATOR_OBJECTION,
    ISSUE_STATUS_REPORTED,
    WORKFLOW_BUCKET_CHOICES,
)
from .workflow import (
    booking_allows_operator_action,
    booking_airline_details_are_complete,
    booking_hotel_fulfillments_are_complete,
    booking_requires_return_airline_detail,
    booking_transport_fulfillment_is_complete,
    clear_booking_runtime_caches,
    normalize_booking_status,
    normalize_airline_direction,
    resolve_operator_workflow_bucket,
    sync_booking_state,
)
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi


def extract_partner_session_token(request):
    authenticated_partner = get_authenticated_partner_profile(request)
    if authenticated_partner is not None:
        return str(authenticated_partner.partner_session_token or "").strip()

    token = request.query_params.get("partner_session_token")
    if token:
        return str(token).strip()

    try:
        payload = request.data
    except Exception:
        payload = None

    if hasattr(payload, "get"):
        token = payload.get("partner_session_token")
        if token:
            return str(token).strip()

    return ""


class IsAdminOrPartnerSessionToken(IsAdminOrAuthenticatedPartnerProfile):
    """
    Backward-compatible alias while partner booking endpoints move from
    raw token presence checks to authenticated partner principals.
    """


VALID_BOOKING_STATUSES = tuple(status_name for status_name, _ in BOOKING_STATUS_CHOICES)
BOOKING_STATUS_NORMALIZER = {status_name.lower(): status_name for status_name in VALID_BOOKING_STATUSES}
VALID_BOOKING_UPDATE_STATUSES = (BOOKING_STATUS_IN_FULFILLMENT, BOOKING_STATUS_READY_FOR_TRAVEL)
VALID_BOOKING_DOCUMENT_TYPES = ("eVisa", "airline", "hotel", "transport")
VALID_BOOKING_DOCUMENT_SCOPES = (
    BookingDocuments.DOCUMENT_SCOPE_BOOKING,
    BookingDocuments.DOCUMENT_SCOPE_GROUP,
    BookingDocuments.DOCUMENT_SCOPE_TRAVELER,
)
VALID_ARRANGEMENT_DETAIL_TYPES = ("Hotel", "Transport")
VALID_COMPLAINT_STATUSES = ("Open", "InProgress", "Solved", "Close")
REVIEW_SORT_ORDERING = {
    "newest": ("-rating_time",),
    "oldest": ("rating_time",),
    "highest": ("-partner_total_stars", "-rating_time"),
    "lowest": ("partner_total_stars", "-rating_time"),
}
VALID_TRAVELER_ISSUE_MUTABLE_STATUSES = (
    BOOKING_STATUS_IN_FULFILLMENT,
    BOOKING_STATUS_READY_FOR_TRAVEL,
    BOOKING_STATUS_COMPLETED,
)

COMPLAINT_STATUS_NORMALIZER = {
    "open": "Open",
    "inprogress": "InProgress",
    "in_progress": "InProgress",
    "in progress": "InProgress",
    "solved": "Solved",
    "close": "Close",
    "closed": "Close",
}

COMPLAINT_STATUS_NEXT_TRANSITIONS = {
    "Open": "InProgress",
    "InProgress": "Solved",
    "Solved": "Close",
    "Close": None,
}

BOOKING_DOCUMENT_TYPE_NORMALIZER = {
    "evisa": "eVisa",
    "visa": "eVisa",
    "airline": "airline",
    "flight": "airline",
    "hotel": "hotel",
    "transport": "transport",
}

ARRANGEMENT_DETAIL_TYPE_NORMALIZER = {
    "hotel": "Hotel",
    "transport": "Transport",
}

DOCUMENT_SCOPE_NORMALIZER = {
    "booking": BookingDocuments.DOCUMENT_SCOPE_BOOKING,
    "booking_wide": BookingDocuments.DOCUMENT_SCOPE_BOOKING,
    "group": BookingDocuments.DOCUMENT_SCOPE_GROUP,
    "group_wide": BookingDocuments.DOCUMENT_SCOPE_GROUP,
    "traveler": BookingDocuments.DOCUMENT_SCOPE_TRAVELER,
    "traveller": BookingDocuments.DOCUMENT_SCOPE_TRAVELER,
    "traveler_specific": BookingDocuments.DOCUMENT_SCOPE_TRAVELER,
    "traveller_specific": BookingDocuments.DOCUMENT_SCOPE_TRAVELER,
}

TRANSPORT_MODE_NORMALIZER = {
    BookingTransportFulfillment.MODE_NONE: BookingTransportFulfillment.MODE_NONE,
    "no_transport": BookingTransportFulfillment.MODE_NONE,
    "none": BookingTransportFulfillment.MODE_NONE,
    BookingTransportFulfillment.MODE_TICKET_ONLY: BookingTransportFulfillment.MODE_TICKET_ONLY,
    "ticket": BookingTransportFulfillment.MODE_TICKET_ONLY,
    "file_only": BookingTransportFulfillment.MODE_TICKET_ONLY,
    BookingTransportFulfillment.MODE_DETAILS_ONLY: BookingTransportFulfillment.MODE_DETAILS_ONLY,
    "details": BookingTransportFulfillment.MODE_DETAILS_ONLY,
    "contact_only": BookingTransportFulfillment.MODE_DETAILS_ONLY,
    BookingTransportFulfillment.MODE_DETAILS_AND_TICKET: BookingTransportFulfillment.MODE_DETAILS_AND_TICKET,
    "both": BookingTransportFulfillment.MODE_DETAILS_AND_TICKET,
}

TRAVELER_ISSUE_ACTION_NORMALIZER = {
    "report": "report",
    "reported": "report",
    "mark": "report",
    "create": "report",
    "reopen": "reopen",
    "resolve": "resolve",
    "resolved": "resolve",
    "unreport": "resolve",
    "close": "resolve",
}

TRAVELER_ISSUE_TYPE_NORMALIZER = {
    TravelerIssue.ISSUE_TYPE_REPORTED.lower(): TravelerIssue.ISSUE_TYPE_REPORTED,
    "reported": TravelerIssue.ISSUE_TYPE_REPORTED,
    "rabbit": TravelerIssue.ISSUE_TYPE_RABBIT,
    TravelerIssue.ISSUE_TYPE_RABBIT.lower(): TravelerIssue.ISSUE_TYPE_RABBIT,
}

COMPLETE_BOOKING_STATUS_FLAGS = (
    "is_visa_completed",
    "is_airline_completed",
    "is_airline_detail_completed",
    "is_hotel_completed",
    "is_transport_completed",
)

BOOKING_LIST_SELECT_RELATED = ("order_by", "order_to", "package_token")
BOOKING_LIST_PREFETCH_RELATED = (
    "order_by__mailing_session",
    "order_to__company_of_partner",
    "passport_for_booking_number__booking_group",
    "passport_for_booking_number__traveler_issues",
    "passport_for_booking_number",
    "booking_token",
    "booking_groups",
    "booking_groups__documents",
    "booking_groups__travelers",
    "booking_groups__travelers__traveler_issues",
    "document_for_booking_token",
    "package_token__airline_for_package",
    "package_token__hotel_for_package",
    "package_token__transport_for_package",
    "hotel_fulfillments",
    "traveler_issues",
)
BOOKING_STATS_PREFETCH_RELATED = (
    "passport_for_booking_number",
    "booking_token",
    "status_for_booking",
)

BOOKING_DETAIL_SELECT_RELATED = (
    "order_by",
    "order_to",
    "package_token",
    "package_token__package_provider",
    "transport_fulfillment",
)
BOOKING_DETAIL_PREFETCH_RELATED = (
    "order_by__mailing_session",
    "order_to__company_of_partner",
    "order_to__mailing_of_partner",
    "package_token__airline_for_package",
    "package_token__transport_for_package",
    "package_token__hotel_for_package",
    "package_token__hotel_for_package__hotel_images",
    "package_token__hotel_for_package__catalog_hotel__hotel_images",
    "objection_for_booking",
    "passport_for_booking_number__booking_group",
    "passport_for_booking_number__traveler_issues",
    "passport_for_booking_number",
    "booking_token",
    "booking_groups",
    "booking_groups__documents",
    "booking_groups__travelers",
    "booking_groups__travelers__traveler_issues",
    "status_for_booking",
    "document_for_booking_token",
    "user_document_for_booking_token",
    "airline_for_booking",
    "hotel_fulfillments",
    "rating_for_booking",
    "traveler_issues",
)


def get_partner_bookings_queryset(include_detail_relations=False):
    if include_detail_relations:
        return Booking.objects.select_related(*BOOKING_DETAIL_SELECT_RELATED).prefetch_related(
            *BOOKING_DETAIL_PREFETCH_RELATED
        )
    return Booking.objects.select_related(*BOOKING_LIST_SELECT_RELATED).prefetch_related(
        *BOOKING_LIST_PREFETCH_RELATED
    )


def request_has_partner_visibility_override(request):
    return is_authenticated_staff_user(request)


def partner_booking_is_accessible(booking, *, allow_hidden=False):
    if not booking:
        return False

    sync_booking_state(booking, save=False)
    if allow_hidden:
        return True

    return bool(resolve_operator_workflow_bucket(booking))


def get_partner_booking_detail(partner, booking_number, *, allow_hidden=False):
    booking = (
        get_partner_bookings_queryset(include_detail_relations=True)
        .filter(order_to=partner, booking_number=booking_number)
        .first()
    )
    if not partner_booking_is_accessible(booking, allow_hidden=allow_hidden):
        return None
    return booking


def get_request_partner_booking_detail(request, partner, booking_number):
    return get_partner_booking_detail(
        partner,
        booking_number,
        allow_hidden=request_has_partner_visibility_override(request),
    )


def normalize_document_type(document_for):
    normalized = str(document_for or "").strip().lower()
    return BOOKING_DOCUMENT_TYPE_NORMALIZER.get(normalized, "")


def normalize_arrangement_detail_type(detail_for):
    normalized = str(detail_for or "").strip().lower()
    return ARRANGEMENT_DETAIL_TYPE_NORMALIZER.get(normalized, "")


def normalize_document_scope(value):
    normalized = str(value or "").strip().lower()
    return DOCUMENT_SCOPE_NORMALIZER.get(normalized, "")


def normalize_transport_mode(value):
    normalized = str(value or "").strip().lower()
    return TRANSPORT_MODE_NORMALIZER.get(normalized, "")


def normalize_traveler_issue_action(value):
    normalized = str(value or "").strip().lower()
    return TRAVELER_ISSUE_ACTION_NORMALIZER.get(normalized, "")


def normalize_traveler_issue_type(value):
    normalized = str(value or "").strip().lower()
    return TRAVELER_ISSUE_TYPE_NORMALIZER.get(normalized, "")


def normalize_city_key(value):
    normalized = str(value or "").strip().lower()
    if normalized == "mecca":
        return "makkah"
    return normalized


def _parse_jsonish(value):
    if isinstance(value, (list, dict)):
        return value
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _has_meaningful_value(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _build_partner_booking_response(request, booking_detail):
    sync_booking_state(booking_detail, save=True)
    booking_detail = get_booking_detail_for_request_context(
        request,
        booking_detail.booking_number,
        partner_session_token=getattr(getattr(booking_detail, "order_to", None), "partner_session_token", ""),
    ) or booking_detail
    serialized_booking = DetailBookingSerializer(
        booking_detail,
        context={"request": request, "hide_payment_detail": True},
    )
    return serialized_booking.data


def resolve_partner_for_request(request, partner_session_token=""):
    partner = resolve_authenticated_partner_profile(request)
    if partner:
        return partner

    normalized_token = str(partner_session_token or extract_partner_session_token(request) or "").strip()
    if not normalized_token:
        return None
    return PartnerProfile.objects.filter(partner_session_token=normalized_token).first()


def get_booking_detail_for_request_context(request, booking_number, *, partner_session_token=""):
    allow_hidden = request_has_partner_visibility_override(request)
    queryset = get_partner_bookings_queryset(include_detail_relations=True)
    partner = resolve_partner_for_request(request, partner_session_token=partner_session_token)

    if partner is not None:
        booking = queryset.filter(order_to=partner, booking_number=booking_number).first()
    elif allow_hidden:
        booking = queryset.filter(booking_number=booking_number).first()
    else:
        return None

    if not partner_booking_is_accessible(booking, allow_hidden=allow_hidden):
        return None
    return booking


def get_airline_direction_label(direction):
    return "Return" if normalize_airline_direction(direction) == "return" else "Outbound"


def sync_airline_detail_completion(booking_detail, doc=None):
    clear_booking_runtime_caches(booking_detail)
    doc = doc or DocumentsStatus.objects.get_or_create(status_for_booking=booking_detail)[0]
    is_complete = booking_airline_details_are_complete(booking_detail)
    if bool(doc.is_airline_detail_completed) != is_complete:
        doc.is_airline_detail_completed = is_complete
        doc.save(update_fields=["is_airline_detail_completed"])
    return doc, is_complete


def can_update_booking_documents(booking_detail):
    sync_booking_state(booking_detail, save=False)
    return booking_detail.booking_status in VALID_BOOKING_UPDATE_STATUSES


def get_booking_documents_for_category(booking_detail, category):
    normalized_category = str(category or "").strip().lower()
    documents = BookingDocuments.objects.filter(document_for_booking_token=booking_detail)
    matched_documents = []
    for document in documents:
        document_category = str(
            getattr(document, "document_category", None)
            or getattr(document, "document_for", "")
            or ""
        ).strip().lower()
        if document_category == normalized_category:
            matched_documents.append(document)
    return matched_documents


def sync_booking_document_status(booking_detail):
    clear_booking_runtime_caches(booking_detail)
    document_status, _ = DocumentsStatus.objects.get_or_create(status_for_booking=booking_detail)
    updated_values = {
        "is_visa_completed": bool(get_booking_documents_for_category(booking_detail, "evisa")),
        "is_airline_completed": bool(get_booking_documents_for_category(booking_detail, "airline")),
        "is_airline_detail_completed": booking_airline_details_are_complete(booking_detail),
        "is_hotel_completed": booking_hotel_fulfillments_are_complete(booking_detail),
        "is_transport_completed": booking_transport_fulfillment_is_complete(booking_detail),
    }
    dirty_fields = []
    for field_name, field_value in updated_values.items():
        if bool(getattr(document_status, field_name, False)) != bool(field_value):
            setattr(document_status, field_name, bool(field_value))
            dirty_fields.append(field_name)
    if dirty_fields:
        document_status.save(update_fields=dirty_fields)
    clear_booking_runtime_caches(booking_detail)
    return document_status


def infer_document_scope(data):
    explicit_scope = normalize_document_scope(data.get("document_scope"))
    if explicit_scope:
        return explicit_scope
    if data.get("traveler_id"):
        return BookingDocuments.DOCUMENT_SCOPE_TRAVELER
    if data.get("booking_group_id"):
        return BookingDocuments.DOCUMENT_SCOPE_GROUP
    return BookingDocuments.DOCUMENT_SCOPE_BOOKING


def resolve_booking_group_and_traveler(booking_detail, data, *, allow_empty=False):
    booking_group = None
    traveler = None
    booking_group_id = data.get("booking_group_id")
    traveler_id = data.get("traveler_id")

    if booking_group_id:
        booking_group = BookingGroup.objects.filter(
            booking=booking_detail,
            group_id=booking_group_id,
        ).first()
        if booking_group is None:
            raise ValueError("Booking group not found for the provided booking.")

    if traveler_id:
        traveler = PassportValidity.objects.filter(
            passport_for_booking_number=booking_detail,
            passport_id=traveler_id,
        ).first()
        if traveler is None:
            raise ValueError("Traveler not found for the provided booking.")
        if booking_group is not None and traveler.booking_group_id != booking_group.group_id:
            raise ValueError("Traveler does not belong to the provided booking group.")
        if booking_group is None and traveler.booking_group_id:
            booking_group = traveler.booking_group

    if not allow_empty and booking_group is None and traveler is None:
        return None, None
    return booking_group, traveler


def infer_document_category(data):
    normalized_document_for = normalize_document_type(
        data.get("document_category") or data.get("document_for")
    )
    return normalized_document_for or ""


def build_hotel_fulfillment_payloads(data):
    parsed_items = _parse_jsonish(data.get("hotel_items"))
    if isinstance(parsed_items, dict):
        parsed_items = [parsed_items]

    hotel_items = []
    if isinstance(parsed_items, list):
        for item in parsed_items:
            if not isinstance(item, dict):
                continue
            city_key = normalize_city_key(item.get("city"))
            if not city_key:
                continue
            hotel_items.append(
                {
                    "city": city_key,
                    "package_hotel_id": item.get("package_hotel_id"),
                    "hotel_name": item.get("hotel_name"),
                    "contact_name": item.get("contact_name"),
                    "contact_phone": item.get("contact_phone"),
                    "note": item.get("note"),
                }
            )
    if hotel_items:
        return hotel_items
    return hotel_items


def resolve_package_hotel(booking_detail, payload):
    package_hotel_id = payload.get("package_hotel_id")
    base_queryset = HuzHotelDetail.objects.filter(hotel_for_package=booking_detail.package_token)
    if package_hotel_id:
        return base_queryset.filter(hotel_id=package_hotel_id).first()

    city_key = normalize_city_key(payload.get("city"))
    hotel_name = str(payload.get("hotel_name") or "").strip()
    if city_key and hotel_name:
        return base_queryset.filter(
            hotel_city__iexact=city_key,
            hotel_name__iexact=hotel_name,
        ).first()
    if city_key:
        return base_queryset.filter(hotel_city__iexact=city_key).first()
    return None


def replace_hotel_fulfillments(booking_detail, hotel_items):
    BookingHotelFulfillment.objects.filter(hotel_for_booking=booking_detail).delete()
    for hotel_item in hotel_items:
        BookingHotelFulfillment.objects.create(
            city=normalize_city_key(hotel_item.get("city")),
            hotel_name=(hotel_item.get("hotel_name") or None),
            contact_name=(hotel_item.get("contact_name") or None),
            contact_phone=(hotel_item.get("contact_phone") or None),
            note=(hotel_item.get("note") or None),
            package_hotel=resolve_package_hotel(booking_detail, hotel_item),
            hotel_for_booking=booking_detail,
        )


def build_transport_payload(data):
    parsed_transport = _parse_jsonish(data.get("transport"))
    if isinstance(parsed_transport, dict):
        normalized_payload = dict(parsed_transport)
    else:
        normalized_payload = {
            "transport_mode": data.get("transport_mode"),
            "transport_name": data.get("transport_name"),
            "transport_type": data.get("transport_type"),
            "route_summary": data.get("route_summary"),
            "contact_name": data.get("contact_name"),
            "contact_phone": data.get("contact_phone"),
            "ticket_reference": data.get("ticket_reference"),
            "note": data.get("note"),
        }
    has_ticket = _has_meaningful_value(normalized_payload.get("ticket_reference"))
    has_details = any(
        _has_meaningful_value(normalized_payload.get(field_name))
        for field_name in (
            "transport_name",
            "transport_type",
            "route_summary",
            "contact_name",
            "contact_phone",
        )
    )
    transport_mode = normalize_transport_mode(normalized_payload.get("transport_mode"))
    if not transport_mode:
        if has_ticket and has_details:
            transport_mode = BookingTransportFulfillment.MODE_DETAILS_AND_TICKET
        elif has_ticket:
            transport_mode = BookingTransportFulfillment.MODE_TICKET_ONLY
        elif has_details:
            transport_mode = BookingTransportFulfillment.MODE_DETAILS_ONLY
        else:
            transport_mode = BookingTransportFulfillment.MODE_NONE
    normalized_payload["transport_mode"] = transport_mode
    return normalized_payload


def replace_transport_fulfillment(booking_detail, transport_payload):
    transport_mode = transport_payload["transport_mode"]
    if transport_mode == BookingTransportFulfillment.MODE_NONE and not any(
        _has_meaningful_value(transport_payload.get(field_name))
        for field_name in (
            "transport_name",
            "transport_type",
            "route_summary",
            "contact_name",
            "contact_phone",
            "ticket_reference",
            "note",
        )
    ):
        BookingTransportFulfillment.objects.filter(transport_for_booking=booking_detail).delete()
        return None

    fulfillment, _ = BookingTransportFulfillment.objects.update_or_create(
        transport_for_booking=booking_detail,
        defaults={
            "transport_mode": transport_mode,
            "transport_name": transport_payload.get("transport_name") or None,
            "transport_type": transport_payload.get("transport_type") or None,
            "route_summary": transport_payload.get("route_summary") or None,
            "contact_name": transport_payload.get("contact_name") or None,
            "contact_phone": transport_payload.get("contact_phone") or None,
            "ticket_reference": transport_payload.get("ticket_reference") or None,
            "note": transport_payload.get("note") or None,
        },
    )
    return fulfillment

def mutate_traveler_issue(*, booking_detail, traveler, partner, issue_type, action, notes):
    open_issue = TravelerIssue.objects.filter(
        booking=booking_detail,
        traveler=traveler,
        issue_type=issue_type,
        status=TravelerIssue.STATUS_OPEN,
    ).order_by("-created_at").first()

    if action in {"report", "reopen"}:
        if open_issue:
            dirty_fields = []
            if notes is not None and open_issue.notes != notes:
                open_issue.notes = notes
                dirty_fields.append("notes")
            if action == "reopen" and open_issue.status != TravelerIssue.STATUS_OPEN:
                open_issue.status = TravelerIssue.STATUS_OPEN
                open_issue.resolved_at = None
                open_issue.resolved_by = None
                dirty_fields.extend(["status", "resolved_at", "resolved_by"])
            if dirty_fields:
                open_issue.save(update_fields=list(dict.fromkeys(dirty_fields)))
            return open_issue

        issue = TravelerIssue.objects.create(
            booking=booking_detail,
            traveler=traveler,
            issue_type=issue_type,
            status=TravelerIssue.STATUS_OPEN,
            notes=notes,
            created_by=partner,
        )
        return issue

    if open_issue is None:
        raise ValueError("No open traveler issue exists for the selected traveler.")

    open_issue.status = TravelerIssue.STATUS_RESOLVED
    open_issue.resolved_at = timezone.now()
    open_issue.resolved_by = partner
    if notes is not None:
        open_issue.notes = notes
        open_issue.save(update_fields=["status", "resolved_at", "resolved_by", "notes"])
    else:
        open_issue.save(update_fields=["status", "resolved_at", "resolved_by"])
    return open_issue


def normalize_complaint_status(value):
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    return COMPLAINT_STATUS_NORMALIZER.get(normalized, "")


def build_star_distribution(queryset, key_prefix):
    aggregate_keys = {}
    aggregate_filters = (
        (5, Q(partner_total_stars__gte=Decimal("4.5"), partner_total_stars__lte=Decimal("5.0"))),
        (4, Q(partner_total_stars__gte=Decimal("3.5"), partner_total_stars__lt=Decimal("4.5"))),
        (3, Q(partner_total_stars__gte=Decimal("2.5"), partner_total_stars__lt=Decimal("3.5"))),
        (2, Q(partner_total_stars__gte=Decimal("1.5"), partner_total_stars__lt=Decimal("2.5"))),
        (1, Q(partner_total_stars__gte=Decimal("1.0"), partner_total_stars__lt=Decimal("1.5"))),
    )
    for star, rating_filter in aggregate_filters:
        aggregate_keys[f"star_{star}"] = Count("pk", filter=rating_filter)

    aggregated_counts = queryset.aggregate(**aggregate_keys)
    return {
        f"{key_prefix}_{star}": int(aggregated_counts.get(f"star_{star}") or 0)
        for star in range(5, 0, -1)
    }


def finalize_booking_if_all_documents_completed(booking_detail, doc, package_detail, partner):
    is_complete = all(getattr(doc, flag, False) for flag in COMPLETE_BOOKING_STATUS_FLAGS)
    if not is_complete:
        return False

    sync_booking_state(booking_detail, save=True)

    check_payments = PartnersBookingPayment.objects.filter(
        payment_for_booking=booking_detail
    ).first()
    if not check_payments:
        process_partner_payments(booking_detail, package_detail, partner)

    return True


class GetBookingShortDetailForPartnersView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('partner_session_token', openapi.IN_QUERY, description="Session token of the partner", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('booking_status', openapi.IN_QUERY, description="Booking status", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('workflow_bucket', openapi.IN_QUERY, description="Workflow queue bucket", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('booking_number', openapi.IN_QUERY, description="Booking number search filter", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Page size", type=openapi.TYPE_INTEGER)
        ],
        responses={
            200: openapi.Response('Success', PartnerBookingListSerializer(many=True)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or booking not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            partner_session_token = extract_partner_session_token(request)
            booking_status = request.GET.get('booking_status')
            workflow_bucket = str(request.GET.get("workflow_bucket") or "").strip().upper()
            booking_number = str(request.GET.get('booking_number') or "").strip()
            if not partner_session_token or (not booking_status and not workflow_bucket):
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)
            normalized_booking_status = ""
            if booking_status:
                normalized_booking_status = BOOKING_STATUS_NORMALIZER.get(str(booking_status).strip().lower())
                if not normalized_booking_status:
                    return Response(
                        {"message": f"Invalid booking_status. Must be one of: {', '.join(VALID_BOOKING_STATUSES)}."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            if workflow_bucket and workflow_bucket not in WORKFLOW_BUCKET_CHOICES:
                return Response(
                    {"message": f"Invalid workflow_bucket. Must be one of: {', '.join(sorted(WORKFLOW_BUCKET_CHOICES))}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = resolve_authenticated_partner_profile(request)
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)
            allow_hidden = request_has_partner_visibility_override(request)

            bookings_queryset = (
                get_partner_bookings_queryset(include_detail_relations=False)
                .filter(order_to=user)
                .order_by('-order_time')
            )
            bookings_queryset = filter_partner_booking_queryset(
                bookings_queryset,
                booking_status=normalized_booking_status,
                workflow_bucket=workflow_bucket,
                booking_number=booking_number,
                allow_hidden=allow_hidden,
            )

            paginator = CustomPagination()
            paginated_packages = paginator.paginate_queryset(bookings_queryset, request)
            serialized_package = PartnerBookingListSerializer(
                paginated_packages,
                many=True,
                context={"request": request},
            )
            return paginator.get_paginated_response(serialized_package.data)
        except Exception as e:
            logger.error(f"GetBookingShortDetailForPartnersView: {str(e)}")
            return Response({"message": "Failed to fetch booking list. Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetBookingDetailByBookingNumberForPartnerView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        operation_description="Retrieve booking details by user session token and booking number.",
        manual_parameters=[
            openapi.Parameter('partner_session_token', openapi.IN_QUERY, description="Session token of the partner", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('booking_number', openapi.IN_QUERY, description="Booking number", type=openapi.TYPE_STRING, required=True)
        ],
        responses={
            200: DetailBookingSerializer(many=False),
            400: "Bad Request: Missing or invalid input data",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, Partner, or Package not found.",
            500: "Server error: Internal server error."
        }
    )
    def get(self, request):
        try:
            partner_session_token = extract_partner_session_token(request)
            booking_number = request.GET.get('booking_number')

            # Check for required parameters
            if not booking_number or (not partner_session_token and not request_has_partner_visibility_override(request)):
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            booking = get_booking_detail_for_request_context(
                request,
                booking_number,
                partner_session_token=partner_session_token,
            )
            if not booking:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)
            sync_booking_state(booking, save=False)

            # Serialize and return booking data
            serialized_package = DetailBookingSerializer(booking, context={"request": request, "hide_payment_detail": True})
            return Response(serialized_package.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return a generic error response
            logger.error(f"GetBookingDetailByBookingNumberForPartnerView: {str(e)}")
            return Response({"message": "Failed to get booking detail. Internal server error.."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TakeActionView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['partner_session_token', 'booking_number', 'partner_remarks', 'booking_status'],
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'partner_remarks': openapi.Schema(type=openapi.TYPE_STRING, description='Remarks from the partner'),
                'booking_status': openapi.Schema(type=openapi.TYPE_STRING, description='New booking status')
            }
        ),
        responses={
            201: openapi.Response('Created: Booking status updated successfully.', DetailBookingSerializer(many=False)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or booking detail not found.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            # Extract data from request
            data = request.data
            required_fields = ['partner_session_token', 'booking_number', 'partner_remarks', 'booking_status']

            # Check for missing required fields
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Find the partner user with the provided session token
            partner = PartnerProfile.objects.filter(partner_session_token=data.get('partner_session_token')).first()
            if not partner:
                return Response({"message": "Partner profile not found."}, status=status.HTTP_404_NOT_FOUND)

            # Find the booking detail associated with the user and booking number
            booking_detail = get_request_partner_booking_detail(request, partner, data.get('booking_number'))
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the provided booking status is valid
            sync_booking_state(booking_detail, save=True)
            requested_status = str(data.get('booking_status', '')).strip().lower()
            normalized_status = {
                'active': BOOKING_STATUS_IN_FULFILLMENT,
                'in_fulfillment': BOOKING_STATUS_IN_FULFILLMENT,
                'objection': ISSUE_STATUS_OPERATOR_OBJECTION,
                'operator_objection': ISSUE_STATUS_OPERATOR_OBJECTION,
            }.get(requested_status)
            if not normalized_status:
                return Response({"message": "Invalid booking status. Booking status should be 'IN_FULFILLMENT' or 'OPERATOR_OBJECTION'."}, status=status.HTTP_400_BAD_REQUEST)

            if booking_allows_operator_action(booking_detail):
                if normalized_status == ISSUE_STATUS_OPERATOR_OBJECTION:
                    BookingObjections.objects.create(
                        remarks_or_reason=request.data.get('partner_remarks'),
                        objection_for_booking=booking_detail
                    )
                    user = booking_detail.order_by
                    if user:
                        send_objection_email(user.email, user.name, booking_detail.booking_number, request.data.get('partner_remarks'))
                    booking_detail.issue_status = ISSUE_STATUS_OPERATOR_OBJECTION
                    update_fields = ['issue_status', 'partner_remarks']
                else:
                    booking_detail.booking_status = BOOKING_STATUS_IN_FULFILLMENT
                    booking_detail.issue_status = ISSUE_STATUS_NONE
                    update_fields = ['booking_status', 'issue_status', 'partner_remarks']
                booking_detail.partner_remarks = request.data.get('partner_remarks')
                booking_detail.save(update_fields=update_fields)
                sync_booking_state(booking_detail, save=True)

                serialized_package = DetailBookingSerializer(booking_detail, context={"request": request, "hide_payment_detail": True})
                return Response(serialized_package.data, status=status.HTTP_201_CREATED)

            return Response({"message": "Only ready-for-operator bookings can be updated."}, status=status.HTTP_409_CONFLICT)
        except Exception as e:
            logger.error(f"Error in TakeActionView: {str(e)}")
            return Response({"message": "Failed to update booking status. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManageBookingDocumentsView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('document_link', in_=openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="The document file(s) to upload"),
            openapi.Parameter('document_for', in_=openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Type of document (e.g., 'eVisa', 'airline')"),
            openapi.Parameter('booking_number', in_=openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Booking number related to the document"),
            openapi.Parameter('partner_session_token', in_=openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Partner's session token for authentication"),
        ],
        responses={

            201: openapi.Response('Created:', DetailBookingSerializer(many=False)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: Partner agency detail not found, Booking detail not found, Package detail not found, User not found.",
            409: "Conflict: Only bookings that are in fulfillment or ready for travel can perform this task.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            files = request.FILES.getlist('document_link')
            booking_number = request.data.get('booking_number')
            partner_session_token = request.data.get('partner_session_token')
            normalized_document_for = infer_document_category(request.data)
            if not files or not booking_number or (
                not partner_session_token and not request_has_partner_visibility_override(request)
            ):
                return Response({"message": "Missing file or required information."},
                                status=status.HTTP_400_BAD_REQUEST)

            if normalized_document_for not in VALID_BOOKING_DOCUMENT_TYPES:
                return Response(
                    {"message": f"Invalid document_for. Must be one of: {', '.join(VALID_BOOKING_DOCUMENT_TYPES)}."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            for file in files:
                if not check_file_format_and_size(file):
                    return Response({"message": "Invalid file format or size."}, status=status.HTTP_400_BAD_REQUEST)

            partner = resolve_partner_for_request(request, partner_session_token=partner_session_token)
            if partner is None and not request_has_partner_visibility_override(request):
                return Response({"message": "Partner agency detail not found."}, status=status.HTTP_404_NOT_FOUND)

            booking_detail = get_booking_detail_for_request_context(
                request,
                booking_number,
                partner_session_token=partner_session_token,
            )
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)
            partner = partner or booking_detail.order_to

            if not can_update_booking_documents(booking_detail):
                return Response({"message": "Only bookings that are in fulfillment or ready for travel can perform this task."}, status=status.HTTP_409_CONFLICT)

            package_detail = booking_detail.package_token
            if not package_detail:
                return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

            user = booking_detail.order_by
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            document_scope = infer_document_scope(request.data)
            if document_scope not in VALID_BOOKING_DOCUMENT_SCOPES:
                return Response(
                    {"message": f"Invalid document_scope. Must be one of: {', '.join(VALID_BOOKING_DOCUMENT_SCOPES)}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                booking_group, traveler = resolve_booking_group_and_traveler(booking_detail, request.data)
            except ValueError as exc:
                return Response({"message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            if document_scope == BookingDocuments.DOCUMENT_SCOPE_GROUP and booking_group is None:
                return Response({"message": "booking_group_id is required for group-scoped documents."}, status=status.HTTP_400_BAD_REQUEST)
            if document_scope == BookingDocuments.DOCUMENT_SCOPE_TRAVELER and traveler is None:
                return Response({"message": "traveler_id is required for traveler-scoped documents."}, status=status.HTTP_400_BAD_REQUEST)

            with transaction.atomic():
                document_title = str(request.data.get("document_title") or "").strip()
                for file in files:
                    file_path = save_file_in_directory(file)
                    BookingDocuments.objects.create(
                        document_link=file_path,
                        document_for_booking_token=booking_detail,
                        document_for=normalized_document_for,
                        document_category=normalized_document_for,
                        document_scope=document_scope,
                        document_title=document_title or getattr(file, "name", normalized_document_for),
                        booking_group=booking_group,
                        traveler=traveler,
                    )

                doc = sync_booking_document_status(booking_detail)

            notification_type = {
                "eVisa": "Visa",
                "airline": "Airline Tickets",
                "hotel": "Hotel Details",
                "transport": "Transport Details",
            }.get(normalized_document_for, normalized_document_for)
            send_booking_documents_email(user.email, user.name, booking_number, notification_type)

            finalize_booking_if_all_documents_completed(
                booking_detail=booking_detail,
                doc=doc,
                package_detail=package_detail,
                partner=partner,
            )

            return Response(_build_partner_booking_response(request, booking_detail), status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"ManageBookingDocumentsView -Post: {str(e)}")
            return Response({"message": "Failed to submit data. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DeleteBookingDocumentsView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('booking_number', in_=openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True, description="Booking number related to the document"),
            openapi.Parameter('document_id', in_=openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True, description="ID of the document to delete"),
            openapi.Parameter('partner_session_token', in_=openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True, description="Partner's session token for authentication"),
        ],
        responses={
            200: "OK: Record deleted successfully.",
            400: "Bad Request: Missing required information, Document record not found, Failed to delete record. Internal server error.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: Partner agency not found, Booking detail not found.",
        }
    )
    def delete(self, request, *args, **kwargs):

        # Extract parameters from the request
        booking_number = request.data.get('booking_number') or request.query_params.get('booking_number')
        document_id = request.data.get('document_id') or request.query_params.get('document_id')
        partner_session_token = request.data.get('partner_session_token') or request.query_params.get('partner_session_token')

        # Validate the presence of required parameters
        if not booking_number or not document_id or (
            not partner_session_token and not request_has_partner_visibility_override(request)
        ):
            return Response({"message": "Missing required information."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve partner profile
        partner = resolve_partner_for_request(request, partner_session_token=partner_session_token)
        if not partner and not request_has_partner_visibility_override(request):
            return Response({"message": "Partner agency not found."}, status=status.HTTP_404_NOT_FOUND)

        # Retrieve booking detail
        booking_detail = get_booking_detail_for_request_context(
            request,
            booking_number,
            partner_session_token=partner_session_token,
        )
        if not booking_detail:
            return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

        if not can_update_booking_documents(booking_detail):
            return Response(
                {"message": "Only bookings that are in fulfillment or ready for travel can perform this task."},
                status=status.HTTP_409_CONFLICT,
            )

        # Retrieve and check the document to delete
        check_document = BookingDocuments.objects.filter(document_id=document_id,
                                                         document_for_booking_token=booking_detail).first()
        if not check_document:
            return Response({"message": "Document record not found."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Delete associated file from directory if exists
            if check_document.document_link:
                delete_file_from_directory(check_document.document_link.name)

            # Delete the document record from the database
            check_document.delete()
            sync_booking_document_status(booking_detail)
            sync_booking_state(booking_detail, save=True)

            return Response({"message": "Record deleted successfully."}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"DeleteBookingDocumentsView: {str(e)}")
            return Response({"message": "Failed to delete record. Try again."}, status=status.HTTP_400_BAD_REQUEST)


class BookingAirlineDetailsView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        operation_description="Create airline details for a booking.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['partner_session_token', 'booking_number', 'flight_date', 'flight_time', 'flight_from', 'flight_to'],
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Partner session token'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'flight_direction': openapi.Schema(type=openapi.TYPE_STRING, description='Flight leg direction: outbound or return'),
                'flight_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE, description='Flight date'),
                'flight_time': openapi.Schema(type=openapi.TYPE_STRING, description='Flight time'),
                'flight_from': openapi.Schema(type=openapi.TYPE_STRING, description='Flight origin'),
                'flight_to': openapi.Schema(type=openapi.TYPE_STRING, description='Flight destination'),
            },
        ),
        responses={
            201: openapi.Response('Airline details created successfully', DetailBookingSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: Partner agency detail not found, Booking detail not found, Package detail not found, client not found.",
            409: "Conflict: Airline details already exist or the booking is not yet actionable.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.data
            required_fields = ['partner_session_token', 'booking_number', 'flight_date', 'flight_time', 'flight_from', 'flight_to']

            # Check for missing required fields
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Retrieve partner profile using session token
            partner = resolve_partner_for_request(
                request,
                partner_session_token=request.data.get('partner_session_token'),
            )
            if not partner and not request_has_partner_visibility_override(request):
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve booking details using partner and booking number
            booking_detail = get_booking_detail_for_request_context(
                request,
                request.data.get('booking_number'),
                partner_session_token=request.data.get('partner_session_token'),
            )
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)
            partner = partner or booking_detail.order_to

            # # Retrieve client details from booking details
            # client_detail = booking_detail.order_by
            # if not client_detail:
            #     return Response({"message": "Client detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve package details using package token from booking details
            package_detail = booking_detail.package_token
            if not package_detail:
                return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

            flight_direction = normalize_airline_direction(data.get('flight_direction'))
            if flight_direction == "return" and not booking_requires_return_airline_detail(booking_detail):
                return Response({"message": "Return airline details are not enabled for this booking."}, status=status.HTTP_400_BAD_REQUEST)

            check_exist = BookingAirlineDetail.objects.filter(
                airline_for_booking=booking_detail,
                flight_direction=flight_direction,
            ).first()
            if check_exist:
                return Response(
                    {"message": f"{get_airline_direction_label(flight_direction)} airline details already exist."},
                    status=status.HTTP_409_CONFLICT,
                )

            # Check if the booking status allows for adding airline details
            if can_update_booking_documents(booking_detail):
                # Retrieve document status for the booking
                doc, _ = DocumentsStatus.objects.get_or_create(status_for_booking=booking_detail)

                # Create new airline detail entry
                BookingAirlineDetail.objects.create(
                    flight_direction=flight_direction,
                    flight_date=request.data.get('flight_date'),
                    flight_time=request.data.get('flight_time'),
                    flight_from=request.data.get('flight_from'),
                    flight_to=request.data.get('flight_to'),
                    airline_for_booking=booking_detail
                )

                doc = sync_booking_document_status(booking_detail)

                finalize_booking_if_all_documents_completed(
                    booking_detail=booking_detail,
                    doc=doc,
                    package_detail=package_detail,
                    partner=partner,
                )

                # Serialize and return the updated booking details
                return Response(_build_partner_booking_response(request, booking_detail), status=status.HTTP_201_CREATED)

            return Response({"message": "Only bookings that are in fulfillment or ready for travel can perform this task."}, status=status.HTTP_409_CONFLICT)

        except Exception as e:
            # Log the error and return a generic error response
            logger.error(f"BookingAirlineDetailsView: {str(e)}")
            return Response({"message": "Failed to create record. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Update airline details for a booking.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['partner_session_token', 'booking_airline_id', 'booking_number', 'flight_date', 'flight_time', 'flight_from', 'flight_to'],
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Partner session token'),
                'booking_airline_id': openapi.Schema(type=openapi.TYPE_STRING, description='Booking airline ID'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'flight_direction': openapi.Schema(type=openapi.TYPE_STRING, description='Flight leg direction: outbound or return'),
                'flight_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE, description='Flight date'),
                'flight_time': openapi.Schema(type=openapi.TYPE_STRING, description='Flight time'),
                'flight_from': openapi.Schema(type=openapi.TYPE_STRING, description='Flight origin'),
                'flight_to': openapi.Schema(type=openapi.TYPE_STRING, description='Flight destination'),
            },
        ),
        responses={
            200: openapi.Response('Airline details updated successfully', DetailBookingSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: 'User, Booking detail, Client detail, or Airline details not found'",
            409: "Conflict: Only bookings that are in fulfillment or ready for travel can perform this task.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            data = request.data
            required_fields = ['partner_session_token', 'booking_airline_id', 'booking_number', 'flight_date', 'flight_time', 'flight_from', 'flight_to']
            # Check for missing required fields
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            partner = resolve_partner_for_request(
                request,
                partner_session_token=data.get('partner_session_token'),
            )
            if not partner and not request_has_partner_visibility_override(request):
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            booking_detail = get_booking_detail_for_request_context(
                request,
                data.get('booking_number'),
                partner_session_token=data.get('partner_session_token'),
            )
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)
            partner = partner or booking_detail.order_to

            # Retrieve the existing airline details for the booking
            airline_detail = BookingAirlineDetail.objects.filter(
                airline_for_booking=booking_detail,
                booking_airline_id=data.get('booking_airline_id')
            ).first()
            if not airline_detail:
                return Response({"message": "Airline details not found."}, status=status.HTTP_404_NOT_FOUND)

            if can_update_booking_documents(booking_detail):
                flight_direction = normalize_airline_direction(
                    data.get('flight_direction') or getattr(airline_detail, 'flight_direction', '')
                )
                if flight_direction == "return" and not booking_requires_return_airline_detail(booking_detail):
                    return Response({"message": "Return airline details are not enabled for this booking."}, status=status.HTTP_400_BAD_REQUEST)

                duplicate_direction = BookingAirlineDetail.objects.filter(
                    airline_for_booking=booking_detail,
                    flight_direction=flight_direction,
                ).exclude(booking_airline_id=airline_detail.booking_airline_id).exists()
                if duplicate_direction:
                    return Response(
                        {"message": f"{get_airline_direction_label(flight_direction)} airline details already exist."},
                        status=status.HTTP_409_CONFLICT,
                    )

                # Update the airline detail fields with the new data
                airline_detail.flight_direction = flight_direction
                airline_detail.flight_date = data.get('flight_date')
                airline_detail.flight_time = data.get('flight_time')
                airline_detail.flight_from = data.get('flight_from')
                airline_detail.flight_to = data.get('flight_to')
                airline_detail.save()

                doc = sync_booking_document_status(booking_detail)
                finalize_booking_if_all_documents_completed(
                    booking_detail=booking_detail,
                    doc=doc,
                    package_detail=booking_detail.package_token,
                    partner=partner,
                )

                return Response(_build_partner_booking_response(request, booking_detail), status=status.HTTP_200_OK)

            return Response({"message": "Only bookings that are in fulfillment or ready for travel can be managed."}, status=status.HTTP_409_CONFLICT)

        except Exception as e:
            logger.error(f"BookingAirlineDetailsView - Put: {str(e)}")
            return Response({"message": "Failed to update record. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BookingHotelAndTransportDetailsView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        operation_description="Add hotel and transport details for a booking.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['partner_session_token', 'booking_number', 'detail_for'],
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Partner session token'),
                'detail_for': openapi.Schema(type=openapi.TYPE_STRING, description='Detail type (Hotel or Transport)'),
                'hotel_items': openapi.Schema(type=openapi.TYPE_STRING, description='JSON array of hotel fulfillment items when detail_for=Hotel'),
                'transport_mode': openapi.Schema(type=openapi.TYPE_STRING, description='Transport mode when detail_for=Transport'),
                'transport_name': openapi.Schema(type=openapi.TYPE_STRING, description='Transport name when detail_for=Transport'),
                'transport_type': openapi.Schema(type=openapi.TYPE_STRING, description='Transport type when detail_for=Transport'),
                'route_summary': openapi.Schema(type=openapi.TYPE_STRING, description='Route summary when detail_for=Transport'),
                'contact_name': openapi.Schema(type=openapi.TYPE_STRING, description='Optional transport contact name'),
                'contact_phone': openapi.Schema(type=openapi.TYPE_STRING, description='Optional transport contact phone'),
                'ticket_reference': openapi.Schema(type=openapi.TYPE_STRING, description='Optional transport ticket reference'),
                'note': openapi.Schema(type=openapi.TYPE_STRING, description='Traveler-facing hotel or transport note'),
            },
        ),
        responses={
            201: openapi.Response('Hotel or transport details created successfully', DetailBookingSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User, Booking detail, Client detail, Package detail, or Record already exists",
            409: "Conflict: Only bookings that are in fulfillment or ready for travel can perform this task.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.data
            if not data.get("booking_number") or not data.get("detail_for") or (
                not data.get("partner_session_token") and not request_has_partner_visibility_override(request)
            ):
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            normalized_detail_for = normalize_arrangement_detail_type(data.get('detail_for'))
            if normalized_detail_for not in VALID_ARRANGEMENT_DETAIL_TYPES:
                return Response(
                    {"message": f"Invalid detail_for. Must be one of: {', '.join(VALID_ARRANGEMENT_DETAIL_TYPES)}."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Retrieve the user based on the provided session token
            partner = resolve_partner_for_request(
                request,
                partner_session_token=data.get('partner_session_token'),
            )
            if not partner and not request_has_partner_visibility_override(request):
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            booking_detail = get_booking_detail_for_request_context(
                request,
                data.get('booking_number'),
                partner_session_token=data.get('partner_session_token'),
            )
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)
            partner = partner or booking_detail.order_to

            # Retrieve client details using the session token from booking details
            client_detail = booking_detail.order_by
            if not client_detail:
                return Response({"message": "Client detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the record already exists for the given detail type
            if normalized_detail_for == "Hotel" and BookingHotelFulfillment.objects.filter(hotel_for_booking=booking_detail).exists():
                return Response({"message": "Record already exists."}, status=status.HTTP_409_CONFLICT)
            if normalized_detail_for == "Transport" and BookingTransportFulfillment.objects.filter(transport_for_booking=booking_detail).exists():
                return Response({"message": "Record already exists."}, status=status.HTTP_409_CONFLICT)

            # Retrieve package details using the package token from booking details
            package_detail = booking_detail.package_token
            if not package_detail:
                return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the booking status allows for adding hotel or transport details
            if can_update_booking_documents(booking_detail):
                with transaction.atomic():
                    if normalized_detail_for == "Hotel":
                        hotel_items = build_hotel_fulfillment_payloads(data)
                        if not hotel_items:
                            return Response(
                                {"message": "At least one hotel fulfillment item is required."},
                                status=status.HTTP_400_BAD_REQUEST,
                            )
                        replace_hotel_fulfillments(booking_detail, hotel_items)
                    else:
                        replace_transport_fulfillment(booking_detail, build_transport_payload(data))

                    doc = sync_booking_document_status(booking_detail)

                send_booking_documents_email(
                    client_detail.email,
                    client_detail.name,
                    booking_detail.booking_number,
                    normalized_detail_for,
                )

                finalize_booking_if_all_documents_completed(
                    booking_detail=booking_detail,
                    doc=doc,
                    package_detail=package_detail,
                    partner=partner,
                )

                # Serialize and return the updated booking details
                return Response(_build_partner_booking_response(request, booking_detail), status=status.HTTP_201_CREATED)

            return Response({"message": "Only bookings that are in fulfillment or ready for travel can be managed."}, status=status.HTTP_409_CONFLICT)

        except Exception as e:
            # Log the error and return a generic error response
            logger.error(f"BookingHotelAndTransportDetailsView: {str(e)}")
            return Response({"message": "Failed to add record. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Update hotel or transport details for a booking",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Partner session token'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'detail_for': openapi.Schema(type=openapi.TYPE_STRING, description='Detail type (Hotel/Transport)'),
                'hotel_items': openapi.Schema(type=openapi.TYPE_STRING, description='JSON array of hotel fulfillment items when detail_for=Hotel'),
                'transport_mode': openapi.Schema(type=openapi.TYPE_STRING, description='Transport mode when detail_for=Transport'),
                'transport_name': openapi.Schema(type=openapi.TYPE_STRING, description='Transport name when detail_for=Transport'),
                'transport_type': openapi.Schema(type=openapi.TYPE_STRING, description='Transport type when detail_for=Transport'),
                'route_summary': openapi.Schema(type=openapi.TYPE_STRING, description='Route summary when detail_for=Transport'),
                'contact_name': openapi.Schema(type=openapi.TYPE_STRING, description='Optional transport contact name'),
                'contact_phone': openapi.Schema(type=openapi.TYPE_STRING, description='Optional transport contact phone'),
                'ticket_reference': openapi.Schema(type=openapi.TYPE_STRING, description='Optional transport ticket reference'),
                'note': openapi.Schema(type=openapi.TYPE_STRING, description='Traveler-facing hotel or transport note'),
            },
            required=['partner_session_token', 'booking_number', 'detail_for']
        ),
        responses={
            200: openapi.Response('Hotel or transport details updated successfully', DetailBookingSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User, Booking detail, Client detail, Package detail, or Record not exists",
            409: "Conflict: Only bookings that are in fulfillment or ready for travel can perform this task.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            data = request.data
            if not data.get("booking_number") or not data.get("detail_for") or (
                not data.get("partner_session_token") and not request_has_partner_visibility_override(request)
            ):
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            normalized_detail_for = normalize_arrangement_detail_type(data.get('detail_for'))
            if normalized_detail_for not in VALID_ARRANGEMENT_DETAIL_TYPES:
                return Response(
                    {"message": f"Invalid detail_for. Must be one of: {', '.join(VALID_ARRANGEMENT_DETAIL_TYPES)}."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Retrieve partner profile using session token
            partner = resolve_partner_for_request(
                request,
                partner_session_token=data.get('partner_session_token'),
            )
            if not partner and not request_has_partner_visibility_override(request):
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve booking details using partner and booking number
            booking_detail = get_booking_detail_for_request_context(
                request,
                data.get('booking_number'),
                partner_session_token=data.get('partner_session_token'),
            )
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)
            partner = partner or booking_detail.order_to

            # Check if hotel or transport details exist for the booking
            detail_exists = (
                BookingHotelFulfillment.objects.filter(hotel_for_booking=booking_detail).exists()
                if normalized_detail_for == "Hotel"
                else BookingTransportFulfillment.objects.filter(transport_for_booking=booking_detail).exists()
            )
            if not detail_exists:
                return Response({"message": "Details not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the booking status allows for managing details
            if can_update_booking_documents(booking_detail):
                with transaction.atomic():
                    if normalized_detail_for == "Hotel":
                        hotel_items = build_hotel_fulfillment_payloads(data)
                        if not hotel_items:
                            return Response(
                                {"message": "At least one hotel fulfillment item is required."},
                                status=status.HTTP_400_BAD_REQUEST,
                            )
                        replace_hotel_fulfillments(booking_detail, hotel_items)
                    else:
                        replace_transport_fulfillment(booking_detail, build_transport_payload(data))
                    doc = sync_booking_document_status(booking_detail)

                finalize_booking_if_all_documents_completed(
                    booking_detail=booking_detail,
                    doc=doc,
                    package_detail=booking_detail.package_token,
                    partner=partner,
                )
                return Response(_build_partner_booking_response(request, booking_detail), status=status.HTTP_200_OK)

            return Response({"message": "Only bookings that are in fulfillment or ready for travel can be managed."}, status=status.HTTP_409_CONFLICT)

        except Exception as e:
            logger.error(f"BookingHotelAndTransportDetailsView - Put: {str(e)}")
            return Response({"message": "Failed to update record. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def process_partner_payments(booking_detail, package_detail, partner):
    huz_cut = booking_detail.total_price * 0.04
    remaining_amount = booking_detail.total_price - huz_cut
    partner_first_payment = remaining_amount * 0.9
    partner_final_payment = remaining_amount * 0.1
    payment_status = "NotPaid"
    receive_able_payment(payment_status, partner_first_payment, partner_final_payment, 0, partner, package_detail, booking_detail)


def receive_able_payment(payment_status, receivable_amount, pending_amount, processed_amount, payment_for_partner, payment_for_package, payment_for_booking):
    PartnersBookingPayment.objects.create(
        receivable_amount=receivable_amount,
        pending_amount=pending_amount,
        processed_amount=processed_amount,
        payment_for_partner=payment_for_partner,
        payment_for_package=payment_for_package,
        payment_for_booking=payment_for_booking,
        payment_status=payment_status
    )
    return "Success"


def send_email_notification(user, booking_number, document_type):
    # url = f"https://hajjumrah.co/booking_details/{booking_number}"
    # title = f"Your {document_type.capitalize()} for kingdom of Saudi Arabia is Ready"
    # first_message = f"We are pleased to inform you that your {document_type} for your booking have been successfully processed."
    # second_message = f"You can check your {document_type} by clicking the following link:"
    # button_title = f"Check Your {document_type.capitalize()}"
    # if document_type.lower() in ["hotel", "transport"]:
    #     title = f"Your {document_type.capitalize()} Reservation is Confirmed"
    #     first_message = f"We are pleased to inform you that {document_type} Reservation for your booking have been successfully confirmed."
    #     second_message = f"You can check your {document_type} Reservation by clicking the following link:"
    #     button_title = f"Check Your {document_type.capitalize()} Reservation"
    document_type.capitalize()
    send_booking_documents_email(user.email, user.name, booking_number, document_type)


class GetOverallRatingView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('partner_session_token', openapi.IN_QUERY, description="Session token of the partner",
                              type=openapi.TYPE_STRING, required=True)
            ],
        responses={
            200: "Success: Overall rating",
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            partner_session_token = request.GET.get('partner_session_token')
            if not partner_session_token:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve partner profile using session token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "Partner not found for the given session token."}, status=status.HTTP_404_NOT_FOUND)

            partner_ratings = BookingRatingAndReview.objects.filter(rating_for_partner=user)
            total_star_counts = build_star_distribution(partner_ratings, "total_star")

            return Response(total_star_counts, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error in GetOverallRatingView: {str(e)}", exc_info=True)
            return Response({"message": "An unexpected error occurred while fetching the ratings."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetRatingPackageWiseView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        operation_description="Retrieve ratings for a specific package",
        manual_parameters=[
            openapi.Parameter('partner_session_token', openapi.IN_QUERY, description="Session token of the partner requesting the ratings", type=openapi.TYPE_STRING, required=True ),
            openapi.Parameter('huz_token', openapi.IN_QUERY, description="Token of the package for which ratings are to be fetched", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('search', openapi.IN_QUERY, description="Search by traveler name, email, or review comment", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('sort', openapi.IN_QUERY, description="Sort order: newest, oldest, highest, lowest", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('from_date', openapi.IN_QUERY, description="Filter rating date from YYYY-MM-DD", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('to_date', openapi.IN_QUERY, description="Filter rating date to YYYY-MM-DD", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER, required=False),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Page size", type=openapi.TYPE_INTEGER, required=False),
        ],
        responses={
            200: openapi.Response('Success', PartnerRatingSerializer(many=True)),
            400: "Missing required query parameters.",
            401: "Unauthorized: Admin permissions required.",
            404: "User or package detail not found.",
            500: "An unexpected error occurred while fetching the ratings."
        }
    )
    def get(self, request):
        try:
            partner_session_token = extract_partner_session_token(request)
            huz_token = request.GET.get('huz_token')
            search = str(request.GET.get('search') or "").strip()[:100]
            requested_sort = str(request.GET.get('sort') or 'newest').strip().lower()
            from_date_raw = str(request.GET.get('from_date') or '').strip()
            to_date_raw = str(request.GET.get('to_date') or '').strip()

            # Check for missing required parameters
            if not partner_session_token or not huz_token:
                return Response({"message": "Missing user or package info."},status=status.HTTP_400_BAD_REQUEST)

            if requested_sort and requested_sort not in REVIEW_SORT_ORDERING:
                return Response(
                    {"message": f"Invalid sort. Must be one of: {', '.join(REVIEW_SORT_ORDERING)}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            from_date = None
            if from_date_raw:
                from_date = parse_date(from_date_raw)
                if not from_date:
                    return Response(
                        {"message": "Invalid from_date. Expected YYYY-MM-DD."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            to_date = None
            if to_date_raw:
                to_date = parse_date(to_date_raw)
                if not to_date:
                    return Response(
                        {"message": "Invalid to_date. Expected YYYY-MM-DD."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            user = resolve_authenticated_partner_profile(request)
            if not user:
                user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve package detail using package token and partner profile
            package_detail = HuzBasicDetail.objects.filter(huz_token=huz_token, package_provider=user).first()
            if not package_detail:
                return Response({"message": "Package detail not found for the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve ratings for the package
            ratings_queryset = BookingRatingAndReview.objects.select_related(
                "rating_by_user"
            ).prefetch_related(
                "rating_by_user__mailing_session"
            ).filter(
                rating_for_partner=user,
                rating_for_package=package_detail,
            )

            if search:
                ratings_queryset = ratings_queryset.filter(
                    Q(partner_comment__icontains=search)
                    | Q(rating_by_user__name__icontains=search)
                    | Q(rating_by_user__email__icontains=search)
                )

            if from_date:
                ratings_queryset = ratings_queryset.filter(rating_time__date__gte=from_date)

            if to_date:
                ratings_queryset = ratings_queryset.filter(rating_time__date__lte=to_date)

            ordering = REVIEW_SORT_ORDERING.get(requested_sort or "newest", REVIEW_SORT_ORDERING["newest"])
            ratings_queryset = ratings_queryset.order_by(*ordering)

            paginator = CustomPagination()
            paginated_ratings = paginator.paginate_queryset(ratings_queryset, request)
            serialized_ratings = PartnerRatingSerializer(paginated_ratings, many=True)
            return paginator.get_paginated_response(serialized_ratings.data)

        except Exception as e:
            logger.error(f"Error in GetRatingPackageWiseView: {str(e)}", exc_info=True)
            return Response({"message": "An unexpected error occurred while fetching the ratings."},status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetPackageOverallRatingView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('partner_session_token', openapi.IN_QUERY, description="Session token of the partner",type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('huz_token', openapi.IN_QUERY, description="Token of the Huz package", type=openapi.TYPE_STRING, required=True)
        ],
        responses={
            200: "Success: Total star counts for the package",
            400: "Missing required query parameters.",
            401: "Unauthorized: Admin permissions required.",
            404: "User or package detail not found. No ratings found for this package.",
            500: "An unexpected error occurred while fetching the ratings."
        }
    )
    def get(self, request):
        try:
            partner_session_token = request.GET.get('partner_session_token')
            huz_token = request.GET.get('huz_token')

            # Check if required fields are provided
            if not partner_session_token or not huz_token:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve the partner user using the session token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve the package detail using the huz_token and partner user
            package_detail = HuzBasicDetail.objects.filter(huz_token=huz_token, package_provider=user).first()
            if not package_detail:
                return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

            package_ratings = BookingRatingAndReview.objects.filter(
                rating_for_partner=user,
                rating_for_package=package_detail,
            )
            total_star_counts = build_star_distribution(package_ratings, "total_package_star")

            return Response(total_star_counts, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error with exception information
            logger.error(f"GetPackageOverallRatingView: {str(e)}", exc_info=True)
            return Response({"message": "An unexpected error occurred while fetching the ratings."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetOverallPartnerComplaintsView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('partner_session_token', openapi.IN_QUERY, description="Session token of the partner", type=openapi.TYPE_STRING, required=True)
        ],
        responses={
            200: "Success: Dictionary of complaint statuses and their counts",
            400: "Missing required query parameters.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User detail not found.",
            500: "Internal Error: An unexpected error occurred while fetching the ratings."
        }
    )
    def get(self, request):
        try:
            partner_session_token = request.GET.get('partner_session_token')

            # Check if the partner session token is provided
            if not partner_session_token:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve the partner user using the session token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Define the possible complaint statuses
            complaint_statuses = list(VALID_COMPLAINT_STATUSES)

            # Query the complaint counts grouped by status for the partner
            complaint_counts = BookingComplaints.objects.filter(
                complaint_for_partner=user
            ).values('complaint_status').annotate(total_count=Count('complaint_id')).order_by('complaint_status')

            # Initialize a dictionary with zero counts for each status
            complaint_status_counts = {statuses: 0 for statuses in complaint_statuses}

            # Populate the dictionary with actual counts from the query results
            for item in complaint_counts:
                raw_status = normalize_complaint_status(item.get('complaint_status'))
                if raw_status:
                    complaint_status_counts[raw_status] += item['total_count']

            return Response(complaint_status_counts, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"GetOverallPartnerComplaintsView: {str(e)}", exc_info=True)
            return Response({"message": "An unexpected error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetPartnerComplaintsView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('partner_session_token', openapi.IN_QUERY, description="Session token of the partner",type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('complaint_status', openapi.IN_QUERY, description="Status of the complaint",type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('complaint_id', openapi.IN_QUERY, description="Complaint ID (exact match)", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('search', openapi.IN_QUERY, description="Search by ticket/title/message/booking/user name", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('from_date', openapi.IN_QUERY, description="Filter complaint date from YYYY-MM-DD", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('to_date', openapi.IN_QUERY, description="Filter complaint date to YYYY-MM-DD", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Page size", type=openapi.TYPE_INTEGER)
        ],
        responses={
            200: "Paginated list of complaints for the partner",
            400: "Missing required data fields",
            401: "Unauthorized: Admin permissions required.",
            404: "User not found",
            500: "An unexpected error occurred"
        }
    )
    def get(self, request):
        try:
            partner_session_token = extract_partner_session_token(request)
            complaint_status = request.GET.get('complaint_status')
            complaint_id = str(request.GET.get('complaint_id') or "").strip()
            search = str(request.GET.get('search') or "").strip()
            from_date_raw = str(request.GET.get('from_date') or "").strip()
            to_date_raw = str(request.GET.get('to_date') or "").strip()

            # Check if the required parameters are provided
            if not partner_session_token:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            normalized_complaint_status = ""
            if complaint_status:
                normalized_complaint_status = normalize_complaint_status(complaint_status)
                if not normalized_complaint_status:
                    return Response(
                        {"message": f"Invalid complaint_status. Must be one of: {', '.join(VALID_COMPLAINT_STATUSES)}."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            from_date = None
            if from_date_raw:
                from_date = parse_date(from_date_raw)
                if not from_date:
                    return Response(
                        {"message": "Invalid from_date. Expected YYYY-MM-DD."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            to_date = None
            if to_date_raw:
                to_date = parse_date(to_date_raw)
                if not to_date:
                    return Response(
                        {"message": "Invalid to_date. Expected YYYY-MM-DD."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            user = resolve_authenticated_partner_profile(request)
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            complaints = (
                BookingComplaints.objects.select_related(
                    'complaint_by_user',
                    'complaint_for_booking',
                    'complaint_for_package',
                    'complaint_for_partner',
                )
                .prefetch_related(
                    'complaint_by_user__mailing_session',
                    'complaint_for_partner__company_of_partner',
                )
                .filter(complaint_for_partner=user)
                .order_by('-complaint_time')
            )

            if normalized_complaint_status:
                complaints = complaints.filter(complaint_status=normalized_complaint_status)

            if complaint_id:
                complaints = complaints.filter(complaint_id=complaint_id)

            if search:
                complaints = complaints.filter(
                    Q(complaint_ticket__icontains=search)
                    | Q(complaint_title__icontains=search)
                    | Q(complaint_message__icontains=search)
                    | Q(complaint_for_booking__booking_number__icontains=search)
                    | Q(complaint_by_user__name__icontains=search)
                )

            if from_date:
                complaints = complaints.filter(complaint_time__date__gte=from_date)

            if to_date:
                complaints = complaints.filter(complaint_time__date__lte=to_date)

            paginator = CustomPagination()
            paginated_packages = paginator.paginate_queryset(complaints, request)
            serialized_package = BookingComplaintsSerializer(
                paginated_packages,
                many=True,
                context={"request": request},
            )
            return paginator.get_paginated_response(serialized_package.data)

        except Exception as e:
            # Log the error with exception information
            logger.error(f"Error in GetPartnerComplaintsView: {str(e)}", exc_info=True)
            return Response({"message": "An unexpected error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GiveUpdateOnComplaintsView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['partner_session_token', 'complaint_id', 'complaint_status'],
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'complaint_id': openapi.Schema(type=openapi.TYPE_STRING, description='ID of the complaint'),
                'complaint_status': openapi.Schema(type=openapi.TYPE_STRING, description='New status of the complaint'),
            },
        ),
        responses={
            201: openapi.Response(description="Complaint status updated successfully", examples={"application/json": {"complaint_status": "InProgress"}}),
            400: "Missing required data fields",
            401: "Unauthorized: Admin permissions required.",
            404: "User or complaints not found",
            409: "Invalid complaint status",
            500: "An unexpected error occurred"
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.data
            required_fields = ['partner_session_token', 'complaint_id', 'complaint_status']

            # Check for missing fields in the request data
            missing_fields = [field for field in required_fields if field not in data]
            if missing_fields:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            # Validate complaint status
            complaint_status = normalize_complaint_status(data.get("complaint_status"))
            if not complaint_status:
                return Response(
                    {"message": f"Invalid complaint status. Status should be one of: {', '.join(VALID_COMPLAINT_STATUSES)}."},
                    status=status.HTTP_409_CONFLICT)

            # Retrieve the partner user using the session token
            user = PartnerProfile.objects.filter(partner_session_token=data.get('partner_session_token')).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve the complaint for the partner using the complaint ID
            complaint = (
                BookingComplaints.objects.select_related('complaint_for_partner')
                .filter(complaint_for_partner=user, complaint_id=data.get('complaint_id'))
                .first()
            )
            if not complaint:
                return Response({"message": "Complaint not found."}, status=status.HTTP_404_NOT_FOUND)

            current_status = normalize_complaint_status(complaint.complaint_status)
            if not current_status:
                current_status = "Open"

            if complaint_status != current_status:
                allowed_next_status = COMPLAINT_STATUS_NEXT_TRANSITIONS.get(current_status)
                if allowed_next_status != complaint_status:
                    return Response(
                        {
                            "message": (
                                f"Invalid complaint status transition from {current_status} to {complaint_status}. "
                                f"Allowed next status: {allowed_next_status or 'None'}."
                            )
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

            # Update the complaint status
            complaint.complaint_status = complaint_status
            response_message = request.data.get('response_message', None)
            complaint.response_message = response_message if response_message else None
            complaint.save(update_fields=['complaint_status', 'response_message'])

            # Serialize the updated complaint
            serialized_complaint = BookingComplaintsSerializer(complaint)
            return Response(serialized_complaint.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            # Log the error with exception information
            logger.error(f"Error in GiveUpdateOnComplaintsView: {str(e)}", exc_info=True)
            return Response({"message": "An unexpected error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetPartnersOverallBookingStatisticsView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('partner_session_token', openapi.IN_QUERY, description="Partner's session token", type=openapi.TYPE_STRING),
        ],
        responses={
            status.HTTP_200_OK: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    **{
                        status_name: openapi.Schema(type=openapi.TYPE_INTEGER)
                        for status_name, _ in BOOKING_STATUS_CHOICES
                    },
                    **{
                        workflow_bucket: openapi.Schema(type=openapi.TYPE_INTEGER)
                        for workflow_bucket in sorted(WORKFLOW_BUCKET_CHOICES)
                    },
                },
            ),
            400: "Missing required data fields.",
            404: "User not found.",
            401: "Unauthorized: Admin permissions required.",
            500: "An unexpected error occurred."
        }
    )
    def get(self, request):
        try:
            partner_session_token = extract_partner_session_token(request)
            if not partner_session_token:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            user = resolve_authenticated_partner_profile(request)
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            bookings_queryset = annotate_effective_booking_status(
                Booking.objects.filter(order_to=user)
            )
            status_aliases = {
                f"status_{index}": status_name
                for index, (status_name, _) in enumerate(BOOKING_STATUS_CHOICES)
            }
            workflow_bucket_aliases = {
                f"workflow_bucket_{index}": workflow_bucket
                for index, workflow_bucket in enumerate(sorted(WORKFLOW_BUCKET_CHOICES))
            }
            aggregate_kwargs = {
                alias: Count("pk", filter=Q(effective_booking_status=status_name))
                for alias, status_name in status_aliases.items()
            }
            aggregate_kwargs.update(
                {
                    alias: Count("pk", filter=build_partner_workflow_bucket_q(workflow_bucket))
                    for alias, workflow_bucket in workflow_bucket_aliases.items()
                }
            )
            aggregate_counts = bookings_queryset.aggregate(**aggregate_kwargs)
            booking_status_counts = {
                status_name: int(aggregate_counts.get(alias) or 0)
                for alias, status_name in status_aliases.items()
            }
            workflow_bucket_counts = {
                workflow_bucket: int(aggregate_counts.get(alias) or 0)
                for alias, workflow_bucket in workflow_bucket_aliases.items()
            }

            return Response(
                {
                    **booking_status_counts,
                    **workflow_bucket_counts,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error in GetPartnersOverallBookingStatisticsView: {str(e)}", exc_info=True)
            return Response({"message": "An unexpected error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetYearlyBookingStatisticsView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('partner_session_token', openapi.IN_QUERY, description="Partner's session token", type=openapi.TYPE_STRING),
            openapi.Parameter('year', openapi.IN_QUERY, description="Year to filter bookings", type=openapi.TYPE_INTEGER),
        ],
        responses={
            status.HTTP_200_OK: openapi.Schema(
                type=openapi.TYPE_NUMBER,
                description="Total earnings for the year.",
            ),
            400: "Missing required data fields.",
            401: "Unauthorized: Admin permissions required.",
            404: "User not found.",
            500: "An unexpected error occurred."
        }
    )
    def get(self, request):
        try:
            partner_session_token = extract_partner_session_token(request)
            year_raw = str(request.GET.get('year') or '').strip()
            if not partner_session_token or not year_raw:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            try:
                year = int(year_raw)
            except (TypeError, ValueError):
                return Response(
                    {"message": "Invalid year. Expected a numeric year like 2026."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = resolve_authenticated_partner_profile(request)
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            booking_status = [BOOKING_STATUS_READY_FOR_TRAVEL, BOOKING_STATUS_COMPLETED]
            yearly_earning = Booking.objects.filter(order_to=user, booking_status__in=booking_status, order_time__year=year).aggregate(total_price=Sum('total_price'))
            total_earnings = yearly_earning['total_price'] or 0
            return Response(total_earnings, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error in GetYearlyBookingStatisticsView: {str(e)}", exc_info=True)
            return Response({"message": "An unexpected error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PartnersBookingPaymentView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('partner_session_token', openapi.IN_QUERY, description="Partner's session token", type=openapi.TYPE_STRING),
        ],
        responses={
            status.HTTP_200_OK: openapi.Schema(
                type=openapi.TYPE_ARRAY,
                items=openapi.Schema(type=openapi.TYPE_OBJECT, properties={
                    'id': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'payment_amount': openapi.Schema(type=openapi.TYPE_NUMBER),
                    'payment_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE),
                    # Add more properties as needed
                }),
                description="List of partner payments.",
            ),
            400: "Missing required data fields.",
            401: "Unauthorized: Admin permissions required.",
            404: "User not found for the provided session token.",
            500: "An unexpected error occurred."
        }
    )
    def get(self, request):

        try:
            partner_session_token = extract_partner_session_token(request)
            if not partner_session_token:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            user = resolve_authenticated_partner_profile(request)
            if not user:
                return Response({"message": "User not found for the provided session token."},
                                status=status.HTTP_404_NOT_FOUND)

            partner_payments = (
                PartnersBookingPayment.objects.select_related(
                    "payment_for_booking",
                    "payment_for_package",
                    "payment_for_partner",
                )
                .prefetch_related("payment_for_partner__company_of_partner")
                .filter(payment_for_partner=user)
                .order_by("-create_date")
            )

            paginator = CustomPagination()
            paginated_payments = paginator.paginate_queryset(partner_payments, request)
            serialized_payments = PartnersBookingPaymentSerializer(
                paginated_payments,
                many=True,
                context={"request": request},
            )
            return paginator.get_paginated_response(serialized_payments.data)

        except Exception as e:
            logger.error(f"Error in PartnersBookingPaymentView: {str(e)}", exc_info=True)
            return Response({"message": "An unexpected error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CloseBookingView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]
    @swagger_auto_schema(
        operation_description="Mark a ready-for-travel booking as completed for a given booking number.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['booking_number', 'partner_session_token'],
            properties={
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number to complete'),
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Partner session token'),
            },
        ),
        responses={
            200: openapi.Response(description="Booking status successfully updated to 'COMPLETED'", schema=DetailBookingSerializer),
            400: openapi.Response(description="Bad Request - Missing or invalid fields"),
            401: 'Unauthorized: Partner permissions required',
            404: openapi.Response(description="Not Found - Partner or booking detail not found"),
            409: openapi.Response(description="Conflict - Booking status is not 'READY_FOR_TRAVEL'"),
            500: openapi.Response(description="Internal Server Error"),
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            data = request.data
            required_fields = ['booking_number', 'partner_session_token']

            # Validate that all required fields are present in the request data
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Retrieve partner associated with the provided partner session token
            partner_detail = PartnerProfile.objects.filter(partner_session_token=data.get('partner_session_token')).first()
            if not partner_detail:
                return Response({"message": "Package provider detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve booking details associated with the provided booking number
            booking_detail = get_request_partner_booking_detail(request, partner_detail, data.get('booking_number'))
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            sync_booking_state(booking_detail, save=True)
            if booking_detail.booking_status != BOOKING_STATUS_READY_FOR_TRAVEL:
                return Response(
                    {"message": "Booking can only be completed if its status is 'READY_FOR_TRAVEL'."},
                    status=status.HTTP_409_CONFLICT
                )

            booking_detail.booking_status = BOOKING_STATUS_COMPLETED
            booking_detail.save(update_fields=["booking_status"])

            # Serialize updated booking details
            sync_booking_state(booking_detail, save=True)
            serialized_booking = DetailBookingSerializer(booking_detail, context={"request": request, "hide_payment_detail": True})
            return Response(serialized_booking.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return an internal server error response
            logger.error(f"PUT - CloseBooking: {str(e)}")
            return Response(
                {"message": "Failed to update booking status. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ReportBookingView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        operation_description="Mark the associated booking with a reported traveler issue.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['passport_id', 'partner_session_token', 'booking_number'],
            properties={
                'passport_id': openapi.Schema(type=openapi.TYPE_STRING, description='ID of the passport to update'),
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Partner session token'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING,
                                                 description='Booking number of the booking to update'),
            },
        ),
        responses={
            200: openapi.Response(
                description="Traveler issue updated successfully."),
            400: openapi.Response(description="Bad Request - Missing required fields."),
            401: "Unauthorized: Admin permissions required.",
            404: openapi.Response(description="Not Found - Booking, partner, or passport not found."),
            409: openapi.Response(description="Conflict - Booking status must be 'READY_FOR_TRAVEL' or 'COMPLETED'."),
            500: openapi.Response(description="Internal Server Error."),
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            passport_id = request.data.get('passport_id')
            traveler_issue_id = request.data.get("traveler_issue_id")
            partner_session_token = request.data.get('partner_session_token')
            booking_number = request.data.get('booking_number')
            if not booking_number or (
                not partner_session_token and not request_has_partner_visibility_override(request)
            ) or (not passport_id and not traveler_issue_id):
                return Response(
                    {"message": "Missing required data fields."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            partner = resolve_partner_for_request(request, partner_session_token=partner_session_token)
            if not partner and not request_has_partner_visibility_override(request):
                return Response({"message": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)

            booking = get_booking_detail_for_request_context(
                request,
                booking_number,
                partner_session_token=partner_session_token,
            )
            if not booking:
                return Response({"message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
            partner = partner or booking.order_to

            sync_booking_state(booking, save=True)
            if booking.booking_status not in VALID_TRAVELER_ISSUE_MUTABLE_STATUSES:
                return Response(
                    {"message": "Booking status must be IN_FULFILLMENT, READY_FOR_TRAVEL, or COMPLETED to manage traveler issues."},
                    status=status.HTTP_409_CONFLICT
                )

            action = normalize_traveler_issue_action(request.data.get("action") or "report")
            if not action:
                return Response({"message": "Invalid action."}, status=status.HTTP_400_BAD_REQUEST)

            issue_type = normalize_traveler_issue_type(
                request.data.get("issue_type") or TravelerIssue.ISSUE_TYPE_REPORTED
            )
            if not issue_type:
                return Response({"message": "Invalid issue_type."}, status=status.HTTP_400_BAD_REQUEST)

            issue_record = None
            traveler = None
            if traveler_issue_id:
                issue_record = TravelerIssue.objects.filter(
                    traveler_issue_id=traveler_issue_id,
                    booking=booking,
                ).first()
                if issue_record is None:
                    return Response({"message": "Traveler issue not found for the provided booking."}, status=status.HTTP_404_NOT_FOUND)
                traveler = issue_record.traveler
            else:
                traveler = PassportValidity.objects.filter(
                    passport_id=passport_id,
                    passport_for_booking_number=booking
                ).first()
                if traveler is None:
                    return Response({"message": "Passport not found for the provided booking."},
                                    status=status.HTTP_404_NOT_FOUND)

            try:
                with transaction.atomic():
                    mutate_traveler_issue(
                        booking_detail=booking,
                        traveler=traveler,
                        partner=partner,
                        issue_type=issue_type if issue_record is None else issue_record.issue_type,
                        action=action,
                        notes=request.data.get("notes"),
                    )
                    sync_booking_state(booking, save=True)
            except ValueError as exc:
                return Response({"message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            return Response(_build_partner_booking_response(request, booking), status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return an internal server error response
            logger.error(f"Error in ReportBooking: {str(e)}")
            return Response(
                {"message": "Failed to update booking status and passport. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
