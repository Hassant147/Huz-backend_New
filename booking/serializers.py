from datetime import datetime

from django.utils import timezone
from rest_framework import serializers
from .models import (
    Booking,
    BookingAirlineDetail,
    BookingComplaints,
    BookingDocuments,
    BookingGroup,
    BookingHotelFulfillment,
    BookingObjections,
    BookingRatingAndReview,
    BookingRequest,
    BookingTransportFulfillment,
    DocumentsStatus,
    PartnersBookingPayment,
    PassportValidity,
    Payment,
    TravelerIssue,
    UserRequiredDocuments,
)
from common.models import UserProfile, MailingDetail
from common.serializers import MailingDetailSerializer
from partners.models import PartnerProfile, HuzBasicDetail, BusinessProfile, PartnerMailingDetail, HuzAirlineDetail
from partners.serializers import (
    ShortBusinessSerializer,
    PartnerMailingDetailSerializer,
    HuzAirlineSerializer,
    HuzHotelSerializer,
    HuzTransportSerializer,
)
from .statuses import ISSUE_STATUS_NONE
from .workflow import (
    booking_allows_client_traveller_updates,
    booking_allows_full_payment_submission,
    booking_allows_minimum_payment_submission,
    booking_allows_operator_action,
    get_booking_airline_details,
    get_open_traveler_issues,
    booking_hotel_fulfillments_are_complete,
    booking_has_operator_visibility,
    booking_transport_fulfillment_is_complete,
    get_payment_stage_status,
    get_remaining_amount_due,
    resolve_client_workflow_stage,
    resolve_client_workflow_step,
    resolve_operator_workflow_bucket,
    sync_booking_state,
)


def _resolve_workflow_read_state(obj):
    if getattr(obj, "_workflow_read_resolved", False):
        return obj

    sync_booking_state(obj, save=False)
    setattr(obj, "_workflow_read_resolved", True)
    return obj


def _get_prefetched_items(instance, relation_name):
    prefetched_cache = getattr(instance, '_prefetched_objects_cache', None) or {}
    if relation_name not in prefetched_cache:
        return None
    return list(prefetched_cache.get(relation_name) or [])


def _list_related_items(instance, relation_name):
    prefetched_items = _get_prefetched_items(instance, relation_name)
    if prefetched_items is not None:
        return prefetched_items

    relation = getattr(instance, relation_name, None)
    if relation is None:
        return []

    try:
        return list(relation.all())
    except Exception:
        return []


def _get_first_related_item(instance, relation_name):
    related_items = _list_related_items(instance, relation_name)
    return related_items[0] if related_items else None


def _serialize_user_mailing_detail(user):
    if not user:
        return None

    mailing_detail = _get_first_related_item(user, 'mailing_session')
    if mailing_detail is None:
        mailing_detail = MailingDetail.objects.filter(mailing_session=user).first()

    if not mailing_detail:
        return None

    return MailingDetailSerializer(mailing_detail).data


def _serialize_partner_company_detail(partner):
    if not partner:
        return None

    company_detail = _get_first_related_item(partner, 'company_of_partner')
    if company_detail is None:
        company_detail = BusinessProfile.objects.filter(company_of_partner=partner).first()

    if not company_detail:
        return None

    return ShortBusinessSerializer(company_detail).data


def _get_datetime_sort_value(value):
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        return value.timestamp()

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def get_company_detail(obj):
    if not obj.order_to or obj.order_to.partner_type != "Company":
        return None

    prefetched_company = _get_first_related_item(obj.order_to, 'company_of_partner')
    if prefetched_company:
        return ShortBusinessSerializer(prefetched_company).data

    return None


def get_user_address_detail(obj):
    if not obj.order_by:
        return None

    prefetched_address = _get_first_related_item(obj.order_by, 'mailing_session')
    if prefetched_address:
        return MailingDetailSerializer(prefetched_address).data

    return None


def get_partner_address_detail(obj):
    if not obj.order_to:
        return None

    prefetched_address = _get_first_related_item(obj.order_to, 'mailing_of_partner')
    if prefetched_address:
        return PartnerMailingDetailSerializer(prefetched_address).data

    return None


def get_booking_objections(obj):
    objections_detail = _list_related_items(obj, 'objection_for_booking')
    return BookingObjectionsSerializer(objections_detail, many=True).data


def get_passport_validity(obj):
    passport_validity = sorted(
        _list_related_items(obj, 'passport_for_booking_number'),
        key=lambda passport: (
            getattr(getattr(passport, "booking_group", None), "sequence", 10_000),
            getattr(passport, "traveler_sequence", 10_000),
            str(getattr(passport, "passport_id", "")),
        ),
    )
    return PassportValiditySerializer(passport_validity, many=True).data


def get_traveler_issues(obj):
    traveler_issues = sorted(
        _list_related_items(obj, "traveler_issues"),
        key=lambda issue: (
            0 if str(getattr(issue, "status", "") or "").strip().lower() == "open" else 1,
            -_get_datetime_sort_value(getattr(issue, "created_at", None)),
        ),
    )
    return TravelerIssueSerializer(traveler_issues, many=True).data


def get_traveler_groups(obj):
    booking_groups = _list_related_items(obj, "booking_groups")
    return BookingGroupSerializer(booking_groups, many=True).data


def get_package_defaults(obj):
    if not obj.package_token:
        return None

    package = obj.package_token
    airline_items = _list_related_items(package, "airline_for_package")
    hotel_items = _list_related_items(package, "hotel_for_package")
    transport_items = _list_related_items(package, "transport_for_package")

    return {
        "airlines": HuzAirlineSerializer(airline_items, many=True).data,
        "hotels": HuzHotelSerializer(hotel_items, many=True, context={}).data,
        "transport": HuzTransportSerializer(transport_items[0], context={}).data if transport_items else None,
        "inclusions": {
            "visa": bool(getattr(package, "is_visa_included", False)),
            "airport_reception": bool(getattr(package, "is_airport_reception_included", False)),
            "tour_guide": bool(getattr(package, "is_tour_guide_included", False)),
            "insurance": bool(getattr(package, "is_insurance_included", False)),
            "breakfast": bool(getattr(package, "is_breakfast_included", False)),
            "lunch": bool(getattr(package, "is_lunch_included", False)),
            "dinner": bool(getattr(package, "is_dinner_included", False)),
        },
    }


def get_booking_fulfillment(obj):
    document_status = _get_first_related_item(obj, "status_for_booking")
    booking_documents = _list_related_items(obj, "document_for_booking_token")
    hotel_fulfillments = _list_related_items(obj, "hotel_fulfillments")
    try:
        transport_fulfillment = obj.transport_fulfillment
    except Exception:
        transport_fulfillment = None

    return {
        "summary": {
            "visa_completed": bool(getattr(document_status, "is_visa_completed", False)),
            "airline_documents_completed": bool(getattr(document_status, "is_airline_completed", False)),
            "airline_details_completed": bool(getattr(document_status, "is_airline_detail_completed", False)),
            "hotel_completed": booking_hotel_fulfillments_are_complete(obj),
            "transport_completed": booking_transport_fulfillment_is_complete(obj),
        },
        "documents": BookingDocumentsSerializer(booking_documents, many=True).data,
        "airlines": BookingAirlineSerializer(get_booking_airline_details(obj), many=True).data,
        "hotels": BookingHotelFulfillmentSerializer(hotel_fulfillments, many=True).data,
        "transport": BookingTransportFulfillmentSerializer(transport_fulfillment).data
        if transport_fulfillment
        else None,
    }


def get_payment_detail(obj):
    _resolve_workflow_read_state(obj)
    payment_paid = sorted(
        _list_related_items(obj, 'booking_token'),
        key=lambda payment: _get_datetime_sort_value(getattr(payment, 'transaction_time', None)),
        reverse=True,
    )
    return PaymentSerializer(payment_paid, many=True).data


def should_hide_payment_detail(serializer):
    return bool(getattr(serializer, "context", {}).get("hide_payment_detail"))


class BookingWorkflowFieldsMixin(serializers.Serializer):
    issue_status = serializers.CharField(read_only=True)
    minimum_payment_status = serializers.SerializerMethodField()
    full_payment_status = serializers.SerializerMethodField()
    client_workflow_stage = serializers.SerializerMethodField()
    client_workflow_step = serializers.SerializerMethodField()
    operator_visible = serializers.SerializerMethodField()
    operator_can_act = serializers.SerializerMethodField()
    client_can_edit_travellers = serializers.SerializerMethodField()
    client_can_submit_minimum_payment = serializers.SerializerMethodField()
    client_can_submit_full_payment = serializers.SerializerMethodField()
    remaining_amount_due = serializers.SerializerMethodField()
    workflow_bucket = serializers.SerializerMethodField()

    def to_representation(self, instance):
        _resolve_workflow_read_state(instance)
        return super().to_representation(instance)

    def get_minimum_payment_status(self, obj):
        _resolve_workflow_read_state(obj)
        return get_payment_stage_status(obj, "Minimum")

    def get_full_payment_status(self, obj):
        _resolve_workflow_read_state(obj)
        return get_payment_stage_status(obj, "Full")

    def get_client_workflow_stage(self, obj):
        _resolve_workflow_read_state(obj)
        return resolve_client_workflow_stage(obj)

    def get_client_workflow_step(self, obj):
        _resolve_workflow_read_state(obj)
        return resolve_client_workflow_step(obj)

    def get_operator_visible(self, obj):
        _resolve_workflow_read_state(obj)
        return booking_has_operator_visibility(obj)

    def get_operator_can_act(self, obj):
        _resolve_workflow_read_state(obj)
        return booking_allows_operator_action(obj)

    def get_client_can_edit_travellers(self, obj):
        _resolve_workflow_read_state(obj)
        return booking_allows_client_traveller_updates(obj)

    def get_client_can_submit_minimum_payment(self, obj):
        _resolve_workflow_read_state(obj)
        return booking_allows_minimum_payment_submission(obj)

    def get_client_can_submit_full_payment(self, obj):
        _resolve_workflow_read_state(obj)
        return booking_allows_full_payment_submission(obj)

    def get_remaining_amount_due(self, obj):
        _resolve_workflow_read_state(obj)
        return get_remaining_amount_due(obj)

    def get_workflow_bucket(self, obj):
        _resolve_workflow_read_state(obj)
        return resolve_operator_workflow_bucket(obj)


class CurrentUserBookingListSerializer(BookingWorkflowFieldsMixin, serializers.ModelSerializer):
    user_session_token = serializers.CharField(source="order_by.session_token", read_only=True)
    package_name = serializers.CharField(source="package_token.package_name", read_only=True)
    package_type = serializers.CharField(source="package_token.package_type", read_only=True)
    package_cost = serializers.CharField(source="package_token.package_base_cost", read_only=True)
    mecca_nights = serializers.CharField(source="package_token.mecca_nights", read_only=True)
    madinah_nights = serializers.CharField(source="package_token.madinah_nights", read_only=True)
    company_detail = serializers.SerializerMethodField()
    is_insurance_included = serializers.BooleanField(
        source="package_token.is_insurance_included",
        read_only=True,
    )
    is_breakfast_included = serializers.BooleanField(
        source="package_token.is_breakfast_included",
        read_only=True,
    )
    is_lunch_included = serializers.BooleanField(
        source="package_token.is_lunch_included",
        read_only=True,
    )
    is_dinner_included = serializers.BooleanField(
        source="package_token.is_dinner_included",
        read_only=True,
    )
    has_airline_detail = serializers.SerializerMethodField()
    has_transport_detail = serializers.SerializerMethodField()
    partner_name = serializers.CharField(source="order_to.name", read_only=True)
    partner_session_token = serializers.CharField(source="order_to.partner_session_token", read_only=True)

    class Meta:
        model = Booking
        fields = (
            "booking_id",
            "booking_number",
            "user_session_token",
            "adults",
            "child",
            "infants",
            "start_date",
            "end_date",
            "total_price",
            "booking_status",
            "issue_status",
            "order_time",
            "payment_type",
            "hold_expires_at",
            "payment_correction_expires_at",
            "minimum_payment_status",
            "full_payment_status",
            "client_workflow_stage",
            "client_workflow_step",
            "operator_visible",
            "operator_can_act",
            "client_can_edit_travellers",
            "client_can_submit_minimum_payment",
            "client_can_submit_full_payment",
            "remaining_amount_due",
            "workflow_bucket",
            "package_name",
            "package_type",
            "package_cost",
            "mecca_nights",
            "madinah_nights",
            "company_detail",
            "is_insurance_included",
            "is_breakfast_included",
            "is_lunch_included",
            "is_dinner_included",
            "has_airline_detail",
            "has_transport_detail",
            "partner_name",
            "partner_session_token",
        )

    def get_company_detail(self, obj):
        return get_company_detail(obj)

    def get_has_airline_detail(self, obj):
        if not obj.package_token:
            return False
        return bool(_list_related_items(obj.package_token, "airline_for_package"))

    def get_has_transport_detail(self, obj):
        if not obj.package_token:
            return False
        return bool(_list_related_items(obj.package_token, "transport_for_package"))


class BookingMutationSerializer(CurrentUserBookingListSerializer):
    partner_session_token = serializers.CharField(source="order_to.partner_session_token", read_only=True)
    partner_name = serializers.CharField(source="order_to.name", read_only=True)
    huz_token = serializers.CharField(source="package_token.huz_token", read_only=True)
    sharing = serializers.CharField(read_only=True)
    quad = serializers.CharField(read_only=True)
    triple = serializers.CharField(read_only=True)
    double = serializers.CharField(read_only=True)
    single = serializers.CharField(read_only=True)
    special_request = serializers.CharField(read_only=True)
    payment_detail = serializers.SerializerMethodField()
    response_mode = serializers.SerializerMethodField()

    class Meta(CurrentUserBookingListSerializer.Meta):
        fields = (
            "booking_id",
            "booking_number",
            "user_session_token",
            "partner_session_token",
            "partner_name",
            "adults",
            "child",
            "infants",
            "sharing",
            "quad",
            "triple",
            "double",
            "single",
            "start_date",
            "end_date",
            "total_price",
            "special_request",
            "booking_status",
            "issue_status",
            "order_time",
            "payment_type",
            "hold_expires_at",
            "payment_correction_expires_at",
            "minimum_payment_status",
            "full_payment_status",
            "client_workflow_stage",
            "client_workflow_step",
            "operator_visible",
            "operator_can_act",
            "client_can_edit_travellers",
            "client_can_submit_minimum_payment",
            "client_can_submit_full_payment",
            "remaining_amount_due",
            "workflow_bucket",
            "huz_token",
            "package_name",
            "package_type",
            "package_cost",
            "mecca_nights",
            "madinah_nights",
            "company_detail",
            "is_insurance_included",
            "is_breakfast_included",
            "is_lunch_included",
            "is_dinner_included",
            "payment_detail",
            "response_mode",
        )

    def get_payment_detail(self, obj):
        return get_payment_detail(obj)

    def get_response_mode(self, _obj):
        return "mutation_summary"


class PartnerBookingListSerializer(BookingWorkflowFieldsMixin, serializers.ModelSerializer):
    user_session_token = serializers.CharField(source="order_by.session_token", read_only=True)
    user_fullName = serializers.CharField(source="order_by.name", read_only=True)
    user_fullname = serializers.CharField(source="order_by.name", read_only=True)
    user_country_code = serializers.CharField(source="order_by.country_code", read_only=True)
    user_phone_number = serializers.CharField(source="order_by.phone_number", read_only=True)
    user_email = serializers.CharField(source="order_by.email", read_only=True)
    user_photo = serializers.CharField(source="order_by.user_photo", read_only=True)
    user_address_detail = serializers.SerializerMethodField()
    package_name = serializers.CharField(source="package_token.package_name", read_only=True)
    package_type = serializers.CharField(source="package_token.package_type", read_only=True)
    package_cost = serializers.CharField(source="package_token.package_base_cost", read_only=True)

    class Meta:
        model = Booking
        fields = (
            "booking_id",
            "booking_number",
            "adults",
            "child",
            "infants",
            "start_date",
            "end_date",
            "total_price",
            "booking_status",
            "issue_status",
            "order_time",
            "payment_type",
            "hold_expires_at",
            "payment_correction_expires_at",
            "minimum_payment_status",
            "full_payment_status",
            "client_workflow_stage",
            "client_workflow_step",
            "operator_visible",
            "operator_can_act",
            "client_can_edit_travellers",
            "client_can_submit_minimum_payment",
            "client_can_submit_full_payment",
            "remaining_amount_due",
            "workflow_bucket",
            "user_session_token",
            "user_fullName",
            "user_fullname",
            "user_country_code",
            "user_phone_number",
            "user_email",
            "user_photo",
            "user_address_detail",
            "package_name",
            "package_type",
            "package_cost",
        )

    def get_user_address_detail(self, obj):
        return get_user_address_detail(obj)


class ShortBookingSerializer(BookingWorkflowFieldsMixin, serializers.ModelSerializer):
    # Partner Section
    partner_session_token = serializers.CharField(source='order_to.partner_session_token', read_only=True)
    # User Section
    user_session_token = serializers.CharField(source='order_by.session_token', read_only=True)
    user_fullname = serializers.CharField(source='order_by.name', read_only=True)
    user_country_code = serializers.CharField(source='order_by.country_code', read_only=True)
    user_phone_number = serializers.CharField(source='order_by.phone_number', read_only=True)
    user_email = serializers.CharField(source='order_by.email', read_only=True)
    user_photo = serializers.CharField(source='order_by.user_photo', read_only=True)
    user_address_detail = serializers.SerializerMethodField()
    # Package Section
    huz_token = serializers.CharField(source='package_token.huz_token', read_only=True)
    package_type = serializers.CharField(source='package_token.package_type', read_only=True)
    package_name = serializers.CharField(source='package_token.package_name', read_only=True)
    package_cost = serializers.CharField(source='package_token.package_base_cost', read_only=True)
    mecca_nights = serializers.CharField(source='package_token.mecca_nights', read_only=True)
    madinah_nights = serializers.CharField(source='package_token.madinah_nights', read_only=True)
    jeddah_nights = serializers.CharField(source='package_token.jeddah_nights', read_only=True)
    taif_nights = serializers.CharField(source='package_token.taif_nights', read_only=True)
    riyadah_nights = serializers.CharField(source='package_token.riyadah_nights', read_only=True)
    is_visa_included = serializers.CharField(source='package_token.is_visa_included', read_only=True)
    is_airport_reception_included = serializers.CharField(source='package_token.is_airport_reception_included', read_only=True)
    is_tour_guide_included = serializers.CharField(source='package_token.is_tour_guide_included', read_only=True)
    is_insurance_included = serializers.CharField(source='package_token.is_insurance_included', read_only=True)
    is_breakfast_included = serializers.CharField(source='package_token.is_breakfast_included', read_only=True)
    is_lunch_included = serializers.CharField(source='package_token.is_lunch_included', read_only=True)
    is_dinner_included = serializers.CharField(source='package_token.is_dinner_included', read_only=True)
    # Payment Verified or not
    payment_detail = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = (
            'booking_number', 'adults', 'child', 'infants', 'start_date', 'end_date', 'sharing', 'quad', 'triple',
            'double', 'single', 'total_price', 'special_request', 'booking_status', 'issue_status',
            'order_time', 'payment_type', 'is_payment_received', 'hold_expires_at',
            'payment_correction_expires_at', 'minimum_payment_status', 'full_payment_status',
            'client_workflow_stage', 'client_workflow_step',
            'operator_visible', 'operator_can_act', 'client_can_edit_travellers',
            'client_can_submit_minimum_payment', 'client_can_submit_full_payment',
            'remaining_amount_due', 'workflow_bucket',

            'partner_session_token',

            'user_session_token', 'user_fullname', 'user_country_code', 'user_phone_number', 'user_email',
            'user_photo', 'user_address_detail',

            'huz_token', 'package_type', 'package_name', 'package_cost', 'mecca_nights', 'madinah_nights',
            'jeddah_nights', 'taif_nights', 'riyadah_nights',
            'is_visa_included', 'is_airport_reception_included', 'is_tour_guide_included', 'is_insurance_included',
            'is_breakfast_included', 'is_lunch_included', 'is_dinner_included',

            'payment_detail',
            'order_by', 'order_to', 'package_token'
                  )

    def get_company_detail(self, obj):
        return get_company_detail(obj)

    def get_user_address_detail(self, obj):
        return get_user_address_detail(obj)

    def get_payment_detail(self, obj):
        if should_hide_payment_detail(self):
            return []
        return get_payment_detail(obj)

class DetailBookingSerializer(BookingWorkflowFieldsMixin, serializers.ModelSerializer):
    # Partner Section
    partner_session_token = serializers.CharField(source='order_to.partner_session_token', read_only=True)
    partner_email = serializers.CharField(source='order_to.email', read_only=True)
    partner_name = serializers.CharField(source='order_to.name', read_only=True)
    partner_username = serializers.CharField(source='order_to.user_name', read_only=True)
    company_detail = serializers.SerializerMethodField()
    partner_address_detail = serializers.SerializerMethodField()
    # User Section
    user_session_token = serializers.CharField(source='order_by.session_token', read_only=True)
    user_fullName = serializers.CharField(source='order_by.name', read_only=True)
    user_email = serializers.CharField(source='order_by.email', read_only=True)
    user_country_code = serializers.CharField(source='order_by.country_code', read_only=True)
    user_phone_number = serializers.CharField(source='order_by.phone_number', read_only=True)
    user_photo = serializers.CharField(source='order_by.user_photo', read_only=True)
    user_address_detail = serializers.SerializerMethodField()
    # Package Detail
    huz_token = serializers.CharField(source='package_token.huz_token', read_only=True)
    package_type = serializers.CharField(source='package_token.package_type', read_only=True)
    package_name = serializers.CharField(source='package_token.package_name', read_only=True)
    package_cost = serializers.CharField(source='package_token.package_base_cost', read_only=True)
    mecca_nights = serializers.CharField(source='package_token.mecca_nights', read_only=True)
    madinah_nights = serializers.CharField(source='package_token.madinah_nights', read_only=True)
    jeddah_nights = serializers.CharField(source='package_token.jeddah_nights', read_only=True)
    taif_nights = serializers.CharField(source='package_token.taif_nights', read_only=True)
    riyadah_nights = serializers.CharField(source='package_token.riyadah_nights', read_only=True)
    is_visa_included = serializers.CharField(source='package_token.is_visa_included', read_only=True)
    is_airport_reception_included = serializers.CharField(source='package_token.is_airport_reception_included', read_only=True)
    is_tour_guide_included = serializers.CharField(source='package_token.is_tour_guide_included', read_only=True)
    is_insurance_included = serializers.CharField(source='package_token.is_insurance_included', read_only=True)
    is_breakfast_included = serializers.CharField(source='package_token.is_breakfast_included', read_only=True)
    is_lunch_included = serializers.CharField(source='package_token.is_lunch_included', read_only=True)
    is_dinner_included = serializers.CharField(source='package_token.is_dinner_included', read_only=True)

    cost_for_sharing = serializers.CharField(source='package_token.cost_for_sharing', read_only=True)
    cost_for_quad = serializers.CharField(source='package_token.cost_for_quad', read_only=True)
    cost_for_triple = serializers.CharField(source='package_token.cost_for_triple', read_only=True)
    cost_for_double = serializers.CharField(source='package_token.cost_for_double', read_only=True)
    cost_for_single = serializers.CharField(source='package_token.cost_for_single', read_only=True)

    booking_documents_status = serializers.SerializerMethodField()
    booking_documents = serializers.SerializerMethodField()
    user_documents = serializers.SerializerMethodField()
    booking_airline_details = serializers.SerializerMethodField()
    booking_rating = serializers.SerializerMethodField()
    payment_detail = serializers.SerializerMethodField()
    booking_objections = serializers.SerializerMethodField()
    package_defaults = serializers.SerializerMethodField()
    booking_fulfillment = serializers.SerializerMethodField()
    traveler_groups = serializers.SerializerMethodField()
    traveler_issues = serializers.SerializerMethodField()
    class Meta:
        model = Booking
        fields = (
            'booking_id', 'booking_number', 'adults', 'child', 'infants', 'start_date', 'end_date', 'sharing', 'quad',
            'triple', 'double', 'single', 'total_price',
            'special_request', 'booking_status', 'issue_status', 'order_time', 'payment_type',
            'is_payment_received', 'partner_remarks', 'hold_expires_at',
            'payment_correction_expires_at', 'minimum_payment_status', 'full_payment_status',
            'client_workflow_stage', 'client_workflow_step',
            'operator_visible', 'operator_can_act', 'client_can_edit_travellers',
            'client_can_submit_minimum_payment', 'client_can_submit_full_payment',
            'remaining_amount_due', 'workflow_bucket',

            'partner_session_token', 'partner_email', 'partner_name', 'partner_username', 'company_detail',
            'partner_address_detail',

            'user_session_token', 'user_fullName', 'user_country_code', 'user_phone_number', 'user_email',
            'user_photo', 'user_address_detail',

            'huz_token', 'package_type', 'package_name', 'package_cost', 'mecca_nights', 'madinah_nights',
            'jeddah_nights', 'taif_nights', 'riyadah_nights',
            'is_visa_included', 'is_airport_reception_included', 'is_tour_guide_included', 'is_insurance_included',
            'is_breakfast_included', 'is_lunch_included', 'is_dinner_included',
            'cost_for_sharing', 'cost_for_quad', 'cost_for_triple', 'cost_for_double', 'cost_for_single',
            'payment_detail', 'booking_objections',

            'booking_documents_status',  'user_documents',
            'booking_documents', 'booking_airline_details', 'booking_rating',
            'package_defaults', 'booking_fulfillment', 'traveler_groups', 'traveler_issues',
                  )

    def get_company_detail(self, obj):
        return get_company_detail(obj)

    def get_package_defaults(self, obj):
        return get_package_defaults(obj)

    def get_booking_fulfillment(self, obj):
        return get_booking_fulfillment(obj)

    def get_traveler_groups(self, obj):
        return get_traveler_groups(obj)

    def get_traveler_issues(self, obj):
        return get_traveler_issues(obj)

    def get_partner_address_detail(self, obj):
        return get_partner_address_detail(obj)

    def get_user_address_detail(self, obj):
        return get_user_address_detail(obj)

    def get_booking_objections(self, obj):
        return get_booking_objections(obj)

    def get_booking_documents_status(self, obj):
        documents = _list_related_items(obj, 'status_for_booking')
        return DocumentsStatusSerializer(documents, many=True).data

    def get_user_documents(self, obj):
        documents = _list_related_items(obj, 'user_document_for_booking_token')
        return UserRequiredBookingDocumentsSerializer(documents, many=True).data

    def get_booking_documents(self, obj):
        documents = _list_related_items(obj, 'document_for_booking_token')
        return BookingDocumentsSerializer(documents, many=True).data

    def get_booking_airline_details(self, obj):
        airline = get_booking_airline_details(obj)
        return BookingAirlineSerializer(airline, many=True).data

    def get_booking_rating(self, obj):
        airline = _list_related_items(obj, 'rating_for_booking')
        return BookingRatingAndReviewSerializer(airline, many=True).data

    def get_payment_detail(self, obj):
        if should_hide_payment_detail(self):
            return []
        return get_payment_detail(obj)

class AdminPaidBookingSerializer(BookingWorkflowFieldsMixin, serializers.ModelSerializer):
    # Partner Section
    partner_session_token = serializers.CharField(source='order_to.partner_session_token', read_only=True)
    partner_email = serializers.CharField(source='order_to.email', read_only=True)
    partner_name = serializers.CharField(source='order_to.name', read_only=True)
    partner_username = serializers.CharField(source='order_to.user_name', read_only=True)
    company_detail = serializers.SerializerMethodField()
    partner_address_detail = serializers.SerializerMethodField()

    # User Section
    user_session_token = serializers.CharField(source='order_by.session_token', read_only=True)
    user_fullName = serializers.CharField(source='order_by.name', read_only=True)
    user_email = serializers.CharField(source='order_by.email', read_only=True)
    user_country_code = serializers.CharField(source='order_by.country_code', read_only=True)
    user_phone_number = serializers.CharField(source='order_by.phone_number', read_only=True)
    user_photo = serializers.CharField(source='order_by.user_photo', read_only=True)

    # Package Detail
    huz_token = serializers.CharField(source='package_token.huz_token', read_only=True)
    package_type = serializers.CharField(source='package_token.package_type', read_only=True)
    package_name = serializers.CharField(source='package_token.package_name', read_only=True)
    package_cost = serializers.CharField(source='package_token.package_base_cost', read_only=True)
    mecca_nights = serializers.CharField(source='package_token.mecca_nights', read_only=True)
    madinah_nights = serializers.CharField(source='package_token.madinah_nights', read_only=True)
    is_visa_included = serializers.CharField(source='package_token.is_visa_included', read_only=True)
    is_airport_reception_included = serializers.CharField(source='package_token.is_airport_reception_included', read_only=True)
    is_tour_guide_included = serializers.CharField(source='package_token.is_tour_guide_included', read_only=True)
    is_insurance_included = serializers.CharField(source='package_token.is_insurance_included', read_only=True)
    is_breakfast_included = serializers.CharField(source='package_token.is_breakfast_included', read_only=True)
    is_lunch_included = serializers.CharField(source='package_token.is_lunch_included', read_only=True)
    is_dinner_included = serializers.CharField(source='package_token.is_dinner_included', read_only=True)

    payment_detail = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = (
            'booking_number', 'adults', 'child', 'infants', 'start_date', 'end_date', 'sharing', 'quad', 'triple',
            'double', 'single', 'total_price', 'special_request', 'booking_status', 'issue_status',
            'order_time', 'payment_type', 'is_payment_received', 'hold_expires_at',
            'payment_correction_expires_at', 'minimum_payment_status', 'full_payment_status',
            'client_workflow_stage', 'client_workflow_step',
            'operator_visible', 'operator_can_act', 'client_can_edit_travellers',
            'client_can_submit_minimum_payment', 'client_can_submit_full_payment',
            'remaining_amount_due', 'workflow_bucket',

            'partner_session_token', 'partner_email', 'partner_name', 'partner_username',
            'company_detail', 'partner_address_detail',

            'user_session_token', 'user_fullName', 'user_email', 'user_country_code', 'user_phone_number',
            'user_photo',

            'huz_token', 'package_type', 'package_name', 'package_cost', 'mecca_nights', 'madinah_nights',
            'is_visa_included', 'is_airport_reception_included', 'is_tour_guide_included', 'is_insurance_included',
            'is_breakfast_included', 'is_lunch_included', 'is_dinner_included',

            'payment_detail',
        )

    def get_company_detail(self, obj):
        return get_company_detail(obj)

    def get_partner_address_detail(self, obj):
        return get_partner_address_detail(obj)

    def get_payment_detail(self, obj):
        if should_hide_payment_detail(self):
            return []
        return get_payment_detail(obj)


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            'payment_id',
            'transaction_number',
            'transaction_photo',
            'transaction_amount',
            'transaction_time',
            'transaction_type',
            'payment_status',
            'review_message',
        ]


class BookingObjectionsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingObjections
        fields = ['objection_id', 'remarks_or_reason', 'client_remarks', 'required_document_for_objection', 'create_time']


class UserRequiredBookingDocumentsSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserRequiredDocuments
        fields = ['user_document_id', 'comment', 'user_document', 'document_type',
                  'user_document_for_booking_token']


class BookingDocumentsSerializer(serializers.ModelSerializer):
    booking_group_id = serializers.UUIDField(read_only=True)
    traveler_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = BookingDocuments
        fields = [
            'document_id',
            'document_for',
            'document_category',
            'document_scope',
            'document_title',
            'document_link',
            'booking_group_id',
            'traveler_id',
        ]


class DocumentsStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentsStatus
        fields = ['is_user_passport_completed', 'is_visa_completed', 'is_airline_detail_completed',
                  'is_airline_completed', 'is_hotel_completed', 'is_transport_completed']


class BookingAirlineSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingAirlineDetail
        fields = ['booking_airline_id', 'flight_direction', 'flight_date', 'flight_time', 'flight_from', 'flight_to']


class BookingHotelFulfillmentSerializer(serializers.ModelSerializer):
    package_hotel_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = BookingHotelFulfillment
        fields = [
            'fulfillment_id',
            'city',
            'hotel_name',
            'contact_name',
            'contact_phone',
            'note',
            'shared_time',
            'package_hotel_id',
        ]


class BookingTransportFulfillmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingTransportFulfillment
        fields = [
            'transport_fulfillment_id',
            'transport_mode',
            'transport_name',
            'transport_type',
            'route_summary',
            'contact_name',
            'contact_phone',
            'ticket_reference',
            'note',
            'shared_time',
        ]


class BookingRatingAndReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingRatingAndReview
        fields = ['huz_concierge', 'huz_support', 'huz_platform', 'huz_service_quality', 'huz_response_time',
                  'huz_comment', 'partner_total_stars', 'partner_comment', 'rating_time']


class PassportValiditySerializer(serializers.ModelSerializer):
    booking_group_id = serializers.UUIDField(read_only=True)
    booking_group_label = serializers.CharField(source="booking_group.label", read_only=True)
    traveler_issues = serializers.SerializerMethodField()

    class Meta:
        model = PassportValidity
        fields = [
            'passport_id',
            'traveler_sequence',
            'traveler_type',
            'room_type',
            'booking_group_id',
            'booking_group_label',
            'first_name',
            'middle_name',
            'last_name',
            'date_of_birth',
            'passport_number',
            'passport_country',
            'expiry_date',
            'user_passport',
            'user_photo',
            'traveler_issues',
        ]

    def get_traveler_issues(self, obj):
        issues = _list_related_items(obj, "traveler_issues")
        return TravelerIssueSerializer(issues, many=True).data


class TravelerIssueSerializer(serializers.ModelSerializer):
    traveler_id = serializers.UUIDField(read_only=True)
    booking_group_id = serializers.UUIDField(source="traveler.booking_group_id", read_only=True)
    booking_group_label = serializers.CharField(source="traveler.booking_group.label", read_only=True)
    traveler_name = serializers.SerializerMethodField()

    class Meta:
        model = TravelerIssue
        fields = [
            "traveler_issue_id",
            "traveler_id",
            "booking_group_id",
            "booking_group_label",
            "traveler_name",
            "issue_type",
            "status",
            "notes",
            "created_at",
            "resolved_at",
        ]

    def get_traveler_name(self, obj):
        traveler = getattr(obj, "traveler", None)
        if not traveler:
            return ""
        return " ".join(
            [
                str(getattr(traveler, "first_name", "") or "").strip(),
                str(getattr(traveler, "middle_name", "") or "").strip(),
                str(getattr(traveler, "last_name", "") or "").strip(),
            ]
        ).strip()


class BookingGroupSerializer(serializers.ModelSerializer):
    travelers = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()

    class Meta:
        model = BookingGroup
        fields = ["group_id", "label", "sequence", "notes", "travelers", "documents"]

    def get_travelers(self, obj):
        travelers = _list_related_items(obj, "travelers")
        return PassportValiditySerializer(travelers, many=True).data

    def get_documents(self, obj):
        documents = _list_related_items(obj, "documents")
        return BookingDocumentsSerializer(documents, many=True).data


class PartnerRatingSerializer(serializers.ModelSerializer):
    user_photo = serializers.CharField(source='rating_by_user.user_photo', read_only=True)
    user_fullName = serializers.CharField(source='rating_by_user.name', read_only=True)
    user_address_detail = serializers.SerializerMethodField()

    class Meta:
        model = BookingRatingAndReview
        fields = ['partner_total_stars', 'partner_comment', 'rating_time', 'user_fullName', 'user_photo', 'user_address_detail']

    def get_user_address_detail(self, obj):
        return _serialize_user_mailing_detail(obj.rating_by_user)


class BookingComplaintsSerializer(serializers.ModelSerializer):
    user_photo = serializers.CharField(source='complaint_by_user.user_photo', read_only=True)
    user_fullName = serializers.CharField(source='complaint_by_user.name', read_only=True)
    user_address_detail = serializers.SerializerMethodField()
    partner_contact_detail = serializers.SerializerMethodField()
    package_type = serializers.CharField(source='complaint_for_package.package_type', read_only=True)
    package_name = serializers.CharField(source='complaint_for_package.package_name', read_only=True)
    package_cost = serializers.CharField(source='complaint_for_package.package_base_cost', read_only=True)
    booking_number = serializers.CharField(source='complaint_for_booking.booking_number', read_only=True)

    class Meta:
        model = BookingComplaints
        fields = ['complaint_id', 'complaint_ticket', 'complaint_title', 'complaint_message', 'audio_message',
                  'complaint_attachment', 'complaint_status',
                  'complaint_time', 'response_message', 'user_fullName', 'user_photo', 'user_address_detail',
                  'package_type', 'package_name', 'package_cost', 'booking_number', 'partner_contact_detail']

    def get_user_address_detail(self, obj):
        return _serialize_user_mailing_detail(obj.complaint_by_user)

    def get_partner_contact_detail(self, obj):
        return _serialize_partner_company_detail(obj.complaint_for_partner)


class PartnersBookingPaymentSerializer(serializers.ModelSerializer):
    booking_number = serializers.CharField(source='payment_for_booking.booking_number', read_only=True)
    package_type = serializers.CharField(source='payment_for_package.package_type', read_only=True)
    package_name = serializers.CharField(source='payment_for_package.package_name', read_only=True)
    partner_name = serializers.CharField(source='payment_for_partner.name', read_only=True)
    partner_session_token = serializers.CharField(source='payment_for_partner.partner_session_token', read_only=True)

    partner_contact_detail = serializers.SerializerMethodField()

    class Meta:
        model = PartnersBookingPayment
        fields = ['package_type', 'package_name', 'booking_number', 'payment_status', 'receivable_amount', 'pending_amount', 'processed_amount', 'processed_date', 'create_date', 'partner_contact_detail', 'partner_name', 'partner_session_token']

    def get_partner_contact_detail(self, obj):
        return _serialize_partner_company_detail(obj.payment_for_partner)


class BookingRequestSerializer(serializers.ModelSerializer):
    user_photo = serializers.CharField(source='request_by_user.user_photo', read_only=True)
    user_fullName = serializers.CharField(source='request_by_user.name', read_only=True)
    user_address_detail = serializers.SerializerMethodField()
    partner_contact_detail = serializers.SerializerMethodField()
    package_type = serializers.CharField(source='request_for_package.package_type', read_only=True)
    package_name = serializers.CharField(source='request_for_package.package_name', read_only=True)
    package_cost = serializers.CharField(source='request_for_package.package_base_cost', read_only=True)
    booking_number = serializers.CharField(source='request_for_booking.booking_number', read_only=True)

    class Meta:
        model = BookingRequest
        fields = ['request_id', 'request_ticket', 'request_title', 'request_message', 'request_attachment',
                  'request_status', 'inProgress_message', 'final_response_message', 'created_at', 'updated_at',
                  'user_fullName', 'user_photo', 'user_address_detail',
                  'package_type', 'package_name', 'package_cost', 'booking_number', 'partner_contact_detail']

    def get_user_address_detail(self, obj):
        return _serialize_user_mailing_detail(obj.request_by_user)

    def get_partner_contact_detail(self, obj):
        return _serialize_partner_company_detail(obj.request_for_partner)
