import hashlib
from time import perf_counter
from urllib.parse import urlencode

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from rest_framework.permissions import IsAdminUser
from django.db import transaction
from django.db.models import Count, Max, Prefetch, Q, Sum
from django.core.cache import cache
from datetime import timedelta
from django.utils.dateparse import parse_date
from common.models import UserProfile
from common.pagination import CustomPagination
from common.serializers import UserProfileSerializer
from partners.models import (
    PartnerProfile,
    HuzBasicDetail,
    HuzHotelDetail,
    HuzHotelImage,
    Wallet,
    PartnerTransactionHistory,
    BusinessProfile,
    PartnerServices,
    PartnerMailingDetail,
)
from partners.serializers import PartnerProfileSerializer, HuzBasicSerializer, HuzHotelSerializer
from common.logs_file import logger
from common.utility import (
    save_notification,
    send_company_approval_email,
    send_payment_rejection_email,
    send_payment_verification_email,
    send_push_notification,
    preparation_email,
)
from booking.flow_utils import get_expected_traveller_count
from booking.models import Booking, PartnersBookingPayment, Payment, PassportValidity
from booking.querysets import annotate_booking_payment_statuses
from booking.serializers import LegacyDetailBookingSerializer, PartnersBookingPaymentSerializer, AdminPaidBookingSerializer
from booking.services import BookingServiceError, validate_booking_payment_amount
from booking.statuses import (
    BOOKING_STATUS_AWAITING_FINAL_PAYMENT,
    BOOKING_STATUS_COMPLETED,
    BOOKING_STATUS_HOLD,
    BOOKING_STATUS_READY_FOR_TRAVEL,
    BOOKING_STATUS_READY_FOR_OPERATOR,
    BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
    PAYMENT_STATUS_APPROVED,
    PAYMENT_STATUS_REJECTED,
    PAYMENT_STATUS_UNDER_REVIEW,
)
from booking.workflow import get_payment_stage_status, sync_booking_state
from django.utils import timezone


CACHE_KEY_PENDING_COMPANIES = "management:pending_companies:v1"
CACHE_KEY_APPROVED_COMPANIES = "management:approved_companies:v1"
CACHE_KEY_PAID_BOOKINGS = "management:paid_bookings:v1"
CACHE_KEY_PARTNER_RECEIVABLES = "management:partner_receivables:v1"
CACHE_KEY_MASTER_HOTELS = "management:master_hotels:v1"
MANAGEMENT_CACHE_TIMEOUT_SECONDS = 30
MANAGEMENT_CACHE_KEYS = [
    CACHE_KEY_PENDING_COMPANIES,
    CACHE_KEY_APPROVED_COMPANIES,
    CACHE_KEY_PAID_BOOKINGS,
    CACHE_KEY_PARTNER_RECEIVABLES,
    CACHE_KEY_MASTER_HOTELS,
]

MASTER_HOTEL_PROVIDER_SESSION_TOKEN = "__system_master_hotel_provider__"
MASTER_HOTEL_PROVIDER_USERNAME = "__system_master_hotel_provider__"
MASTER_HOTEL_PACKAGE_TOKEN = "__system_master_hotel_package__"
MASTER_HOTEL_PACKAGE_NAME = "System Master Hotel Catalog"
HOTEL_AMENITY_FIELDS = (
    "is_shuttle_services_included",
    "is_air_condition",
    "is_television",
    "is_wifi",
    "is_elevator",
    "is_attach_bathroom",
    "is_washroom_amenities",
    "is_english_toilet",
    "is_indian_toilet",
    "is_laundry",
)
MAX_MASTER_HOTEL_IMAGES = 6
MAX_MASTER_HOTEL_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
PAYMENT_REVIEWABLE_BOOKING_STATUSES = {
    BOOKING_STATUS_HOLD,
    BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
    BOOKING_STATUS_AWAITING_FINAL_PAYMENT,
    BOOKING_STATUS_READY_FOR_OPERATOR,
}
PAYMENT_REVIEW_QUEUE_MINIMUM_UNDER_REVIEW = "minimum_under_review"
PAYMENT_REVIEW_QUEUE_FULL_UNDER_REVIEW = "full_under_review"
PAYMENT_REVIEW_QUEUE_REJECTED_CORRECTIONS = "rejected_corrections"
PAYMENT_REVIEW_QUEUE_APPROVED_HISTORY = "approved_history"
PAYMENT_REVIEW_QUEUE_VALUES = {
    PAYMENT_REVIEW_QUEUE_MINIMUM_UNDER_REVIEW,
    PAYMENT_REVIEW_QUEUE_FULL_UNDER_REVIEW,
    PAYMENT_REVIEW_QUEUE_REJECTED_CORRECTIONS,
    PAYMENT_REVIEW_QUEUE_APPROVED_HISTORY,
}


def _build_management_cache_namespace_key(cache_key):
    return f"{cache_key}:namespace"


def _current_management_cache_namespace_value():
    return str(int(timezone.now().timestamp() * 1000000))


def _get_management_cache_namespace(cache_key):
    namespace_key = _build_management_cache_namespace_key(cache_key)
    namespace = cache.get(namespace_key)
    if namespace:
        return namespace

    namespace = "1"
    cache.set(namespace_key, namespace, None)
    return namespace


def _normalize_management_cache_params(raw_params):
    if hasattr(raw_params, "lists"):
        items = raw_params.lists()
    else:
        items = raw_params.items()

    normalized_params = []
    for key, values in sorted(items, key=lambda entry: entry[0]):
        iterable_values = values if isinstance(values, (list, tuple)) else [values]
        for value in iterable_values:
            normalized_params.append((str(key), str(value or "")))

    return normalized_params


def _build_management_scoped_cache_key(cache_key, raw_params):
    params_digest = hashlib.md5(
        urlencode(_normalize_management_cache_params(raw_params), doseq=True).encode("utf-8")
    ).hexdigest()
    return f"{cache_key}:{_get_management_cache_namespace(cache_key)}:{params_digest}"


def _format_management_performance_context(context):
    parts = []
    for key, value in context.items():
        if value in (None, ""):
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def _log_management_performance(event, started_at, **context):
    logger.info(
        "management.performance event=%s duration_ms=%.2f %s",
        event,
        (perf_counter() - started_at) * 1000,
        _format_management_performance_context(context),
    )


def _invalidate_management_cache():
    cache.delete_many(MANAGEMENT_CACHE_KEYS)
    cache.set(
        _build_management_cache_namespace_key(CACHE_KEY_PAID_BOOKINGS),
        _current_management_cache_namespace_value(),
        None,
    )
    cache.set(
        _build_management_cache_namespace_key(CACHE_KEY_PARTNER_RECEIVABLES),
        _current_management_cache_namespace_value(),
        None,
    )


def _normalize_payment_review_decision(value):
    normalized_value = str(value or "approve").strip().lower()
    if normalized_value in {"approve", "approved"}:
        return "approve"
    if normalized_value in {"reject", "rejected"}:
        return "reject"
    return ""


def _collect_user_notification_tokens(user):
    tokens = []
    for token in (
        getattr(user, "firebase_token", ""),
        getattr(user, "web_firebase_token", ""),
    ):
        normalized_token = str(token or "").strip()
        if normalized_token and normalized_token not in tokens:
            tokens.append(normalized_token)
    return tokens


def _notify_user_about_payment_update(user, booking_number, title, message):
    save_notification_started_at = perf_counter()
    try:
        save_notification(
            user,
            title,
            message,
            getattr(user, "firebase_token", "") or "",
            getattr(user, "web_firebase_token", "") or "",
            booking_number,
        )
    finally:
        _log_management_performance(
            "save_notification",
            save_notification_started_at,
            booking_number=booking_number,
            user_id=getattr(user, "pk", ""),
        )

    if not getattr(user, "is_notification_allowed", True):
        return

    registration_tokens = _collect_user_notification_tokens(user)
    if not registration_tokens:
        return

    push_started_at = perf_counter()
    try:
        send_push_notification(
            title,
            message,
            registration_tokens,
            {
                "booking_number": str(booking_number or ""),
            },
        )
    except Exception as exc:
        logger.error("Failed to send push notification for booking %s: %s", booking_number, str(exc))
    finally:
        _log_management_performance(
            "push_notification_dispatch",
            push_started_at,
            booking_number=booking_number,
            token_count=len(registration_tokens),
        )


def _parse_optional_management_date(raw_value, *, field_name):
    normalized_value = str(raw_value or "").strip()
    if not normalized_value:
        return None, None

    parsed_value = parse_date(normalized_value)
    if parsed_value is None:
        return None, Response(
            {"message": f"Invalid {field_name}. Expected YYYY-MM-DD."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return parsed_value, None


def _build_payment_review_queue_filters(*, now=None):
    now = now or timezone.now()
    minimum_under_review = Q(annotated_minimum_payment_status=PAYMENT_STATUS_UNDER_REVIEW)
    full_under_review_core = Q(annotated_full_payment_status=PAYMENT_STATUS_UNDER_REVIEW)
    rejected_corrections_core = (
        Q(payment_correction_expires_at__isnull=False)
        & Q(payment_correction_expires_at__gt=now)
        & (
            Q(annotated_minimum_payment_status=PAYMENT_STATUS_REJECTED)
            | Q(annotated_full_payment_status=PAYMENT_STATUS_REJECTED)
        )
    )
    approved_history_core = (
        Q(annotated_minimum_payment_status=PAYMENT_STATUS_APPROVED)
        | Q(annotated_full_payment_status=PAYMENT_STATUS_APPROVED)
    )

    return {
        PAYMENT_REVIEW_QUEUE_MINIMUM_UNDER_REVIEW: minimum_under_review,
        PAYMENT_REVIEW_QUEUE_FULL_UNDER_REVIEW: ~minimum_under_review & full_under_review_core,
        PAYMENT_REVIEW_QUEUE_REJECTED_CORRECTIONS: (
            ~minimum_under_review
            & ~full_under_review_core
            & rejected_corrections_core
        ),
        PAYMENT_REVIEW_QUEUE_APPROVED_HISTORY: (
            ~minimum_under_review
            & ~full_under_review_core
            & ~rejected_corrections_core
            & approved_history_core
        ),
    }


def _combine_queue_filters(queue_filters):
    combined_filter = None
    for queue_filter in queue_filters.values():
        combined_filter = queue_filter if combined_filter is None else combined_filter | queue_filter
    return combined_filter or Q(pk__in=[])


def _build_payment_review_summary(queryset, queue_filters):
    aggregate_kwargs = {
        "total_requests": Count("pk"),
        "total_amount": Sum("total_price"),
    }
    queue_aliases = {}
    queue_total_aliases = {}
    for index, (queue_key, queue_filter) in enumerate(queue_filters.items()):
        alias = f"queue_count_{index}"
        queue_aliases[alias] = queue_key
        aggregate_kwargs[alias] = Count("pk", filter=queue_filter)
        total_alias = f"queue_total_amount_{index}"
        queue_total_aliases[total_alias] = queue_key
        aggregate_kwargs[total_alias] = Sum("total_price", filter=queue_filter)

    summary = queryset.aggregate(**aggregate_kwargs)
    return {
        "total_requests": int(summary.get("total_requests") or 0),
        "total_amount": summary.get("total_amount") or 0,
        "queue_counts": {
            queue_key: int(summary.get(alias) or 0)
            for alias, queue_key in queue_aliases.items()
        },
        "queue_total_amounts": {
            queue_key: summary.get(alias) or 0
            for alias, queue_key in queue_total_aliases.items()
        },
    }


def _build_paginated_response(request, queryset, serializer_class, *, meta=None):
    paginator = CustomPagination()
    queryset_started_at = perf_counter()
    page = paginator.paginate_queryset(queryset, request)
    queryset_duration_ms = (perf_counter() - queryset_started_at) * 1000
    serializer_started_at = perf_counter()
    serializer = serializer_class(page, many=True, context={"request": request})
    response = paginator.get_paginated_response(serializer.data)
    serializer_duration_ms = (perf_counter() - serializer_started_at) * 1000
    if meta is not None:
        response.data["meta"] = meta
    response._timing_metrics = {
        "queryset_duration_ms": queryset_duration_ms,
        "serializer_duration_ms": serializer_duration_ms,
    }
    return response


def _has_approved_payment_for_stage(booking, transaction_type):
    return Payment.objects.filter(
        booking_token=booking,
        transaction_type__iexact=transaction_type,
        payment_status__iexact=PAYMENT_STATUS_APPROVED,
    ).exists()


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def _serialize_master_hotel(hotel):
    serialized = HuzHotelSerializer(hotel).data
    for field_name in HOTEL_AMENITY_FIELDS:
        serialized.pop(field_name, None)
    serialized.setdefault("hotel_images", [])
    serialized.setdefault("images", [])
    return serialized


def _contains_admin_managed_amenities(payload):
    for field_name in HOTEL_AMENITY_FIELDS:
        if field_name in payload:
            return True
    return False


def _extract_list_values(data, key):
    values = []
    if hasattr(data, "getlist"):
        values.extend(data.getlist(key))

    raw_value = data.get(key)
    if raw_value not in (None, "") and not isinstance(raw_value, (list, tuple)):
        values.append(raw_value)
    elif isinstance(raw_value, (list, tuple)):
        values.extend(raw_value)

    normalized = []
    for value in values:
        if value in (None, ""):
            continue
        if isinstance(value, str) and "," in value:
            normalized.extend([item.strip() for item in value.split(",") if item.strip()])
        else:
            normalized.append(str(value).strip())

    # Preserve order while removing duplicates.
    return list(dict.fromkeys([item for item in normalized if item]))


def _extract_uploaded_hotel_images(request):
    uploaded_files = []
    if hasattr(request, "FILES"):
        for key in ("images", "hotel_images", "new_images"):
            uploaded_files.extend(request.FILES.getlist(key))

        for key, uploaded_file in request.FILES.items():
            if key.startswith("image_"):
                uploaded_files.append(uploaded_file)

    deduplicated = []
    seen_file_ids = set()
    for uploaded_file in uploaded_files:
        object_id = id(uploaded_file)
        if object_id in seen_file_ids:
            continue
        seen_file_ids.add(object_id)
        deduplicated.append(uploaded_file)

    if len(deduplicated) > MAX_MASTER_HOTEL_IMAGES:
        return [], f"You can upload up to {MAX_MASTER_HOTEL_IMAGES} images at once."

    for uploaded_file in deduplicated:
        if not str(getattr(uploaded_file, "content_type", "")).startswith("image/"):
            return [], "Only image files are allowed."
        if getattr(uploaded_file, "size", 0) > MAX_MASTER_HOTEL_IMAGE_SIZE_BYTES:
            return [], "Each image must be 5 MB or smaller."

    return deduplicated, None


def _sync_master_hotel_images(hotel, add_files=None, delete_image_ids=None):
    add_files = add_files or []
    delete_image_ids = delete_image_ids or []

    if delete_image_ids:
        HuzHotelImage.objects.filter(
            image_for_hotel=hotel,
            image_id__in=delete_image_ids,
        ).delete()

    existing_count = HuzHotelImage.objects.filter(image_for_hotel=hotel).count()
    if existing_count + len(add_files) > MAX_MASTER_HOTEL_IMAGES:
        return f"Total images per hotel cannot exceed {MAX_MASTER_HOTEL_IMAGES}."

    if not add_files:
        return None

    max_sort_order = (
        HuzHotelImage.objects.filter(image_for_hotel=hotel).aggregate(max_order=Max("sort_order"))[
            "max_order"
        ]
        or 0
    )

    for index, uploaded_file in enumerate(add_files, start=1):
        HuzHotelImage.objects.create(
            image_for_hotel=hotel,
            hotel_image=uploaded_file,
            sort_order=max_sort_order + index,
        )

    return None


def _get_or_create_master_hotel_package():
    partner = PartnerProfile.objects.filter(
        partner_session_token=MASTER_HOTEL_PROVIDER_SESSION_TOKEN
    ).first()
    if not partner:
        partner = PartnerProfile.objects.filter(user_name=MASTER_HOTEL_PROVIDER_USERNAME).first()

    if not partner:
        partner = PartnerProfile.objects.create(
            partner_session_token=MASTER_HOTEL_PROVIDER_SESSION_TOKEN,
            user_name=MASTER_HOTEL_PROVIDER_USERNAME,
            name="System Hotel Catalog",
            partner_type="Company",
            account_status="Active",
            is_email_verified=True,
            is_address_exist=True,
        )
    else:
        update_fields = []
        if partner.partner_session_token != MASTER_HOTEL_PROVIDER_SESSION_TOKEN:
            partner.partner_session_token = MASTER_HOTEL_PROVIDER_SESSION_TOKEN
            update_fields.append("partner_session_token")
        if partner.user_name != MASTER_HOTEL_PROVIDER_USERNAME:
            partner.user_name = MASTER_HOTEL_PROVIDER_USERNAME
            update_fields.append("user_name")
        if partner.partner_type != "Company":
            partner.partner_type = "Company"
            update_fields.append("partner_type")
        if partner.account_status != "Active":
            partner.account_status = "Active"
            update_fields.append("account_status")
        if not partner.is_email_verified:
            partner.is_email_verified = True
            update_fields.append("is_email_verified")
        if update_fields:
            partner.save(update_fields=update_fields)

    package = HuzBasicDetail.objects.filter(huz_token=MASTER_HOTEL_PACKAGE_TOKEN).first()
    if package:
        if package.package_provider_id != partner.partner_id:
            package.package_provider = partner
            package.save(update_fields=["package_provider"])
        return package

    now = timezone.now()
    return HuzBasicDetail.objects.create(
        huz_token=MASTER_HOTEL_PACKAGE_TOKEN,
        package_type="Umrah",
        package_name=MASTER_HOTEL_PACKAGE_NAME,
        package_base_cost=0.0,
        cost_for_child=0.0,
        cost_for_infants=0.0,
        cost_for_sharing=0.0,
        cost_for_quad=0.0,
        cost_for_triple=0.0,
        cost_for_double=0.0,
        cost_for_single=0.0,
        mecca_nights=1,
        madinah_nights=1,
        start_date=now,
        end_date=now + timedelta(days=1),
        description="System package used for the hotel master catalog.",
        package_validity=now + timedelta(days=3650),
        package_status="Completed",
        package_stage=5,
        package_provider=partner,
    )


def _normalize_master_hotel_payload(payload, *, create=False):
    payload = payload or {}
    normalized = {}

    required_fields = ("hotel_city", "hotel_name", "hotel_rating", "room_sharing_type")
    for field_name in required_fields:
        raw_value = payload.get(field_name)
        if raw_value in (None, ""):
            if create:
                return None, f"{field_name} is required."
            continue
        normalized[field_name] = str(raw_value).strip()

    optional_text_fields = ("hotel_distance", "distance_type")
    for field_name in optional_text_fields:
        if field_name in payload and payload.get(field_name) not in (None, ""):
            normalized[field_name] = str(payload.get(field_name)).strip()

    return normalized, None


class ManageMasterHotelsCatalogView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Create, list, update, and delete master hotels for package templates.",
        manual_parameters=[
            openapi.Parameter(
                "city",
                openapi.IN_QUERY,
                description="Optional city filter",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "search",
                openapi.IN_QUERY,
                description="Optional keyword filter",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "hotel_id",
                openapi.IN_QUERY,
                description="Required for DELETE if omitted from payload",
                type=openapi.TYPE_STRING,
            ),
        ],
    )
    def get(self, request, *args, **kwargs):
        try:
            city = (request.GET.get("city") or "").strip()
            search = (request.GET.get("search") or "").strip()
            use_cache = not city and not search

            if use_cache:
                cached_results = cache.get(CACHE_KEY_MASTER_HOTELS)
                if cached_results is not None:
                    return Response(
                        {"count": len(cached_results), "results": cached_results},
                        status=status.HTTP_200_OK,
                    )

            package = _get_or_create_master_hotel_package()
            queryset = (
                HuzHotelDetail.objects.filter(hotel_for_package=package)
                .prefetch_related("hotel_images", "catalog_hotel__hotel_images")
                .order_by("hotel_city", "hotel_name")
            )

            if city:
                queryset = queryset.filter(hotel_city__iexact=city)

            if search:
                queryset = queryset.filter(
                    Q(hotel_city__icontains=search)
                    | Q(hotel_name__icontains=search)
                    | Q(hotel_rating__icontains=search)
                    | Q(room_sharing_type__icontains=search)
                )

            serialized_results = [_serialize_master_hotel(hotel) for hotel in queryset]
            if use_cache:
                cache.set(
                    CACHE_KEY_MASTER_HOTELS,
                    serialized_results,
                    MANAGEMENT_CACHE_TIMEOUT_SECONDS,
                )

            return Response(
                {"count": len(serialized_results), "results": serialized_results},
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            logger.error("ManageMasterHotelsCatalogView - Get: %s", exc, exc_info=True)
            return Response(
                {"message": "Failed to fetch master hotels. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def post(self, request, *args, **kwargs):
        try:
            if _contains_admin_managed_amenities(request.data):
                return Response(
                    {
                        "message": (
                            "Amenities are partner-managed and cannot be set from the super admin catalog."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package = _get_or_create_master_hotel_package()
            normalized_payload, error_message = _normalize_master_hotel_payload(
                request.data,
                create=True,
            )
            if error_message:
                return Response({"message": error_message}, status=status.HTTP_400_BAD_REQUEST)

            uploaded_images, image_error = _extract_uploaded_hotel_images(request)
            if image_error:
                return Response({"message": image_error}, status=status.HTTP_400_BAD_REQUEST)

            with transaction.atomic():
                duplicate = HuzHotelDetail.objects.filter(
                    hotel_for_package=package,
                    hotel_city__iexact=normalized_payload.get("hotel_city"),
                    hotel_name__iexact=normalized_payload.get("hotel_name"),
                ).first()
                if duplicate:
                    return Response(
                        {
                            "message": (
                                "Hotel already exists in the master catalog for this city."
                            )
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

                serializer = HuzHotelSerializer(data=normalized_payload)
                if not serializer.is_valid():
                    first_error_field = next(iter(serializer.errors))
                    first_error_message = serializer.errors[first_error_field][0]
                    return Response(
                        {"message": f"{first_error_field}: {first_error_message}"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                hotel = serializer.save(hotel_for_package=package)
                sync_error = _sync_master_hotel_images(hotel, add_files=uploaded_images)
                if sync_error:
                    transaction.set_rollback(True)
                    return Response({"message": sync_error}, status=status.HTTP_400_BAD_REQUEST)

            _invalidate_management_cache()
            return Response(
                {
                    "message": "Master hotel created successfully.",
                    "hotel": _serialize_master_hotel(hotel),
                },
                status=status.HTTP_201_CREATED,
            )
        except Exception as exc:
            logger.error("ManageMasterHotelsCatalogView - Post: %s", exc, exc_info=True)
            return Response(
                {"message": "Failed to create master hotel. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def put(self, request, *args, **kwargs):
        try:
            if _contains_admin_managed_amenities(request.data):
                return Response(
                    {
                        "message": (
                            "Amenities are partner-managed and cannot be set from the super admin catalog."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package = _get_or_create_master_hotel_package()
            hotel_id = request.data.get("hotel_id")
            if not hotel_id:
                return Response(
                    {"message": "hotel_id is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            hotel = HuzHotelDetail.objects.filter(
                hotel_for_package=package,
                hotel_id=hotel_id,
            ).first()
            if not hotel:
                return Response(
                    {"message": "Hotel not found in master catalog."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            normalized_payload, error_message = _normalize_master_hotel_payload(
                request.data,
                create=False,
            )
            if error_message:
                return Response({"message": error_message}, status=status.HTTP_400_BAD_REQUEST)
            uploaded_images, image_error = _extract_uploaded_hotel_images(request)
            if image_error:
                return Response({"message": image_error}, status=status.HTTP_400_BAD_REQUEST)

            delete_image_ids = _extract_list_values(request.data, "delete_image_ids")
            delete_image_ids.extend(_extract_list_values(request.data, "remove_image_ids"))
            delete_image_ids = list(dict.fromkeys(delete_image_ids))

            if not normalized_payload and not uploaded_images and not delete_image_ids:
                return Response(
                    {"message": "No hotel fields were provided for update."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            with transaction.atomic():
                if normalized_payload:
                    serializer = HuzHotelSerializer(hotel, data=normalized_payload, partial=True)
                    if not serializer.is_valid():
                        first_error_field = next(iter(serializer.errors))
                        first_error_message = serializer.errors[first_error_field][0]
                        return Response(
                            {"message": f"{first_error_field}: {first_error_message}"},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                    hotel = serializer.save()

                sync_error = _sync_master_hotel_images(
                    hotel,
                    add_files=uploaded_images,
                    delete_image_ids=delete_image_ids,
                )
                if sync_error:
                    transaction.set_rollback(True)
                    return Response({"message": sync_error}, status=status.HTTP_400_BAD_REQUEST)

            _invalidate_management_cache()
            return Response(
                {
                    "message": "Master hotel updated successfully.",
                    "hotel": _serialize_master_hotel(hotel),
                },
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            logger.error("ManageMasterHotelsCatalogView - Put: %s", exc, exc_info=True)
            return Response(
                {"message": "Failed to update master hotel. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def delete(self, request, *args, **kwargs):
        try:
            package = _get_or_create_master_hotel_package()
            hotel_id = request.data.get("hotel_id") or request.GET.get("hotel_id")
            if not hotel_id:
                return Response(
                    {"message": "hotel_id is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            hotel = HuzHotelDetail.objects.filter(
                hotel_for_package=package,
                hotel_id=hotel_id,
            ).first()
            if not hotel:
                return Response(
                    {"message": "Hotel not found in master catalog."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            hotel.delete()
            _invalidate_management_cache()
            return Response(
                {"message": "Master hotel deleted successfully."},
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            logger.error("ManageMasterHotelsCatalogView - Delete: %s", exc, exc_info=True)
            return Response(
                {"message": "Failed to delete master hotel. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ApprovedORRejectCompanyView(APIView):
    permission_classes = [IsAdminUser]
    ACCOUNT_STATUS_CHOICES = ['Active', 'Rejected']
    @swagger_auto_schema(
        operation_description="Update partner account approval status.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the sales director (optional, used for approval)'),
                'account_status': openapi.Schema(type=openapi.TYPE_STRING, description='Review decision for company profile', enum=['Active', 'Rejected']),
            },
            required=['partner_session_token', 'account_status'],
        ),
        responses={
            200: "Success: Company profile updated",
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User or sales director not found.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            # Extract data from request
            partner_session_token = (request.data.get('partner_session_token') or '').strip()
            session_token = (request.data.get('session_token') or '').strip()
            account_status = (request.data.get('account_status') or '').strip()

            # Check for required parameters
            if not partner_session_token or not account_status:
                return Response({"message": "Missing user or account status information."}, status=status.HTTP_400_BAD_REQUEST)

            if account_status not in self.ACCOUNT_STATUS_CHOICES:
                return Response(
                    {"message": f"Invalid review decision. Must be one of {', '.join(self.ACCOUNT_STATUS_CHOICES)}."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Retrieve partner profile based on session token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            if (user.account_status or "").strip().lower() == "underreview":
                user.account_status = "Pending"
                user.save(update_fields=['account_status'])

            if user.partner_type != "Company":
                return Response({"message": "Selected profile is not a company profile."}, status=status.HTTP_409_CONFLICT)

            if user.account_status != "Pending":
                return Response(
                    {"message": "Only pending company profiles can be reviewed from this screen."},
                    status=status.HTTP_409_CONFLICT
                )

            # Optionally link sales director to approved company profile
            if account_status == "Active" and session_token:
                sales_agent = UserProfile.objects.filter(user_type="sales_director", session_token=session_token).first()
                if not sales_agent:
                    return Response({"message": "Sales Director not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)
                user.sales_agenet_token = sales_agent

            if account_status == "Active":
                if user.account_status != "Active":
                    send_company_approval_email(user.email, user.name)

            # Update account status and save changes
            user.account_status = account_status
            user.save()
            _invalidate_management_cache()

            decision_label = "approved" if account_status == "Active" else "rejected"
            return Response(
                {
                    "message": f"Company profile {decision_label} successfully.",
                    "account_status": user.account_status
                },
                status=status.HTTP_200_OK
            )

        except Exception as e:
            # add in Logs file
            logger.error("Error updating company status: %s", str(e))
            return Response({"message": "Failed to update user status. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetAllPendingApprovalsView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Fetch all pending approval profiles.",
        responses={
            200: openapi.Response('Success: List of pending profiles fetched', PartnerProfileSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: No pending profiles found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            cached_payload = cache.get(CACHE_KEY_PENDING_COMPANIES)
            if cached_payload is not None:
                return Response(cached_payload, status=status.HTTP_200_OK)

            # Fetch only actionable pending company profiles, including legacy UnderReview records
            # without mutating status on read.
            pending_profiles_qs = PartnerProfile.objects.filter(
                account_status__in=["Pending", "UnderReview"],
                is_email_verified=True,
                partner_type="Company",
                is_address_exist=True,
                services_of_partner__isnull=False,
                company_of_partner__isnull=False,
                company_of_partner__company_name__isnull=False,
                company_of_partner__company_name__gt="",
                company_of_partner__contact_name__isnull=False,
                company_of_partner__contact_name__gt="",
                company_of_partner__contact_number__isnull=False,
                company_of_partner__contact_number__gt="",
                company_of_partner__total_experience__isnull=False,
                company_of_partner__total_experience__gt="",
                company_of_partner__company_bio__isnull=False,
                company_of_partner__company_bio__gt="",
                company_of_partner__license_type__isnull=False,
                company_of_partner__license_type__gt="",
                company_of_partner__license_number__isnull=False,
                company_of_partner__license_number__gt="",
                company_of_partner__license_certificate__isnull=False,
                company_of_partner__license_certificate__gt="",
                company_of_partner__company_logo__isnull=False,
                company_of_partner__company_logo__gt="",
            ).prefetch_related(
                Prefetch(
                    'company_of_partner',
                    queryset=BusinessProfile.objects.only(
                        'company_of_partner_id',
                        'company_id',
                        'company_name',
                        'contact_name',
                        'contact_number',
                        'company_website',
                        'total_experience',
                        'company_bio',
                        'license_type',
                        'license_number',
                        'license_certificate',
                        'company_logo',
                    ),
                ),
                Prefetch(
                    'services_of_partner',
                    queryset=PartnerServices.objects.only(
                        'services_of_partner_id',
                        'is_hajj_service_offer',
                        'is_umrah_service_offer',
                        'is_ziyarah_service_offer',
                        'is_transport_service_offer',
                        'is_visa_service_offer',
                    ),
                ),
                Prefetch(
                    'mailing_of_partner',
                    queryset=PartnerMailingDetail.objects.only(
                        'mailing_of_partner_id',
                        'address_id',
                        'street_address',
                        'address_line2',
                        'city',
                        'state',
                        'country',
                        'postal_code',
                        'lat',
                        'long',
                    ),
                ),
                Prefetch(
                    'wallet_session',
                    queryset=Wallet.objects.only('wallet_session_id', 'wallet_amount'),
                ),
            ).distinct()

            pending_profiles = list(pending_profiles_qs)
            if pending_profiles:
                serializer = PartnerProfileSerializer(pending_profiles, many=True)
                response_payload = serializer.data
                cache.set(CACHE_KEY_PENDING_COMPANIES, response_payload, MANAGEMENT_CACHE_TIMEOUT_SECONDS)
                return Response(response_payload, status=status.HTTP_200_OK)

            return Response({"message": "No pending profiles found."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"Error in GetAllPendingApprovalsView: {str(e)}")
            return Response({"message": "Failed to get pending profiles. Internal server error."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetAllApprovedCompaniesView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Fetch all approved partners profiles.",
        responses={
            200: openapi.Response('Success: List of approved profiles fetched', PartnerProfileSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: No Approved profiles found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            cached_payload = cache.get(CACHE_KEY_APPROVED_COMPANIES)
            if cached_payload is not None:
                return Response(cached_payload, status=status.HTTP_200_OK)

            approved_profiles_qs = PartnerProfile.objects.filter(
                account_status="Active",
                partner_type="Company",
            ).prefetch_related(
                Prefetch(
                    'company_of_partner',
                    queryset=BusinessProfile.objects.only(
                        'company_of_partner_id',
                        'company_id',
                        'company_name',
                        'contact_name',
                        'contact_number',
                        'company_website',
                        'total_experience',
                        'company_bio',
                        'license_type',
                        'license_number',
                        'license_certificate',
                        'company_logo',
                    ),
                ),
                Prefetch(
                    'services_of_partner',
                    queryset=PartnerServices.objects.only(
                        'services_of_partner_id',
                        'is_hajj_service_offer',
                        'is_umrah_service_offer',
                        'is_ziyarah_service_offer',
                        'is_transport_service_offer',
                        'is_visa_service_offer',
                    ),
                ),
                Prefetch(
                    'mailing_of_partner',
                    queryset=PartnerMailingDetail.objects.only(
                        'mailing_of_partner_id',
                        'address_id',
                        'street_address',
                        'address_line2',
                        'city',
                        'state',
                        'country',
                        'postal_code',
                        'lat',
                        'long',
                    ),
                ),
                Prefetch(
                    'wallet_session',
                    queryset=Wallet.objects.only('wallet_session_id', 'wallet_amount'),
                ),
            ).distinct()

            approved_profiles = list(approved_profiles_qs)
            if approved_profiles:
                serializer = PartnerProfileSerializer(approved_profiles, many=True)
                response_payload = serializer.data
                cache.set(CACHE_KEY_APPROVED_COMPANIES, response_payload, MANAGEMENT_CACHE_TIMEOUT_SECONDS)
                return Response(response_payload, status=status.HTTP_200_OK)

            return Response({"message": "No approved profiles found."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"Error in GetAllApprovedCompaniesView: {str(e)}")
            return Response({"message": "Failed to get approved profiles. Internal server error."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetAllSaleDirectorsView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Fetch all sale directors profiles.",
        responses={
            200: openapi.Response('Success: List of sale directors profiles fetched', UserProfileSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: No Approved profiles found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            # Fetch the all sales director profiles based on the status
            sales_profiles = UserProfile.objects.filter(account_status="Active", user_type="sales_director")

            if sales_profiles.exists():
                serializer = UserProfileSerializer(sales_profiles, many=True)
                return Response(serializer.data, status=status.HTTP_200_OK)

            return Response({"message": "No profiles found."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"Error in GetAllSaleDirectorsView: {str(e)}")
            return Response({"message": "Failed to get sale directors profiles. Internal server error."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ApproveBookingPaymentView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Approve or reject a booking payment submission",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='User session token'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'payment_id': openapi.Schema(type=openapi.TYPE_STRING, description='Optional payment Id'),
                'decision': openapi.Schema(type=openapi.TYPE_STRING, description='approve or reject', enum=['approve', 'reject']),
                'review_message': openapi.Schema(type=openapi.TYPE_STRING, description='Optional rejection reason shown to the user'),
            },
            required=['session_token', 'booking_number']
        ),
        responses={
            200: openapi.Response('Booking status updated successfully', LegacyDetailBookingSerializer(many=False)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: Booking detail or user detail not found.",
            409: "Conflict: Booking/payment state does not allow this review decision.",
            500: "Server Error: Internal server error."
        }
    )
    @transaction.atomic
    def put(self, request, *args, **kwargs):
        try:
            request_started_at = perf_counter()
            # Extract required data from the request
            session_token = (request.data.get("session_token") or "").strip()
            booking_number = (request.data.get("booking_number") or "").strip()
            payment_id = (request.data.get("payment_id") or "").strip()
            decision = _normalize_payment_review_decision(request.data.get("decision"))
            review_message = (request.data.get("review_message") or "").strip()

            # Check for missing required fields
            if not session_token or not booking_number:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)
            if not decision:
                return Response({"message": "decision must be either 'approve' or 'reject'."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve user profile based on session token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve booking detail based on user and booking number
            booking_detail = Booking.objects.select_for_update().select_related('package_token').filter(
                order_by=user,
                booking_number=booking_number,
            ).first()
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            sync_booking_state(booking_detail, save=False)
            current_status = (booking_detail.booking_status or "").strip()
            if current_status not in PAYMENT_REVIEWABLE_BOOKING_STATUSES:
                return Response(
                    {"message": "Only bookings with a submitted payment can be reviewed."},
                    status=status.HTTP_409_CONFLICT,
                )

            payment_queryset = Payment.objects.select_for_update().filter(booking_token=booking_detail)
            if payment_id:
                check_payment = payment_queryset.filter(payment_id=payment_id).first()
            else:
                check_payment = payment_queryset.exclude(payment_status=PAYMENT_STATUS_APPROVED).order_by('-transaction_time').first()
                if not check_payment:
                    check_payment = payment_queryset.order_by('-transaction_time').first()

            if not check_payment:
                return Response({"message": "Payment record not found."}, status=status.HTTP_404_NOT_FOUND)

            normalized_payment_status = str(check_payment.payment_status or "").strip().upper()

            if decision == "approve":
                if normalized_payment_status == PAYMENT_STATUS_REJECTED:
                    return Response(
                        {"message": "Rejected payment must be resubmitted before it can be approved."},
                        status=status.HTTP_409_CONFLICT,
                    )

                payment_already_approved = normalized_payment_status == PAYMENT_STATUS_APPROVED
                if not payment_already_approved:
                    try:
                        validate_booking_payment_amount(
                            booking_detail,
                            check_payment.transaction_type,
                            check_payment.transaction_amount,
                            exclude_payment_id=check_payment.payment_id,
                        )
                    except BookingServiceError as exc:
                        return Response(exc.detail, status=exc.status_code)
                    check_payment.payment_status = PAYMENT_STATUS_APPROVED
                    check_payment.review_message = None
                    check_payment.save(update_fields=['payment_status', 'review_message'])

                booking_detail.payment_correction_expires_at = None
                booking_detail.save(update_fields=["payment_correction_expires_at"])

                # Create only missing PassportValidity records to keep endpoint idempotent.
                required_passports = get_expected_traveller_count(booking_detail)
                existing_passports = PassportValidity.objects.filter(passport_for_booking_number=booking_detail).count()
                missing_passports = max(required_passports - existing_passports, 0)
                if missing_passports:
                    PassportValidity.objects.bulk_create(
                        [PassportValidity(passport_for_booking_number=booking_detail) for _ in range(missing_passports)]
                    )

                sync_booking_state(booking_detail, save=True)
                if not payment_already_approved:
                    verification_email_started_at = perf_counter()
                    try:
                        send_payment_verification_email(user.email, user.name, booking_number)
                    finally:
                        _log_management_performance(
                            "send_payment_verification_email",
                            verification_email_started_at,
                            booking_number=booking_number,
                        )
                    _notify_user_about_payment_update(
                        user,
                        booking_number,
                        "Payment approved",
                        f"Your payment for booking {booking_number} has been approved.",
                    )
                    if booking_detail.booking_status in {
                        BOOKING_STATUS_READY_FOR_TRAVEL,
                        BOOKING_STATUS_COMPLETED,
                    }:
                        preparation_email_started_at = perf_counter()
                        try:
                            preparation_email(
                                user.email,
                                user.name,
                                booking_detail.package_token.package_type,
                            )
                        finally:
                            _log_management_performance(
                                "preparation_email",
                                preparation_email_started_at,
                                booking_number=booking_number,
                            )
            else:
                rejection_note = review_message or "Please upload a clearer or corrected payment proof and submit it again."

                if normalized_payment_status == PAYMENT_STATUS_APPROVED:
                    return Response(
                        {"message": "Approved payments cannot be rejected."},
                        status=status.HTTP_409_CONFLICT,
                    )

                check_payment.payment_status = PAYMENT_STATUS_REJECTED
                check_payment.review_message = rejection_note
                check_payment.save(update_fields=['payment_status', 'review_message'])
                booking_detail.payment_correction_expires_at = timezone.now() + timedelta(hours=2)
                booking_detail.save(update_fields=["payment_correction_expires_at"])
                sync_booking_state(booking_detail, save=True)

                rejection_email_started_at = perf_counter()
                try:
                    send_payment_rejection_email(user.email, user.name, booking_number, rejection_note)
                finally:
                    _log_management_performance(
                        "send_payment_rejection_email",
                        rejection_email_started_at,
                        booking_number=booking_number,
                    )
                _notify_user_about_payment_update(
                    user,
                    booking_number,
                    "Payment rejected",
                    f"Your payment for booking {booking_number} was rejected. {rejection_note}",
                )

            # Serialize the updated booking detail and return response
            serializer_started_at = perf_counter()
            serialized_booking = LegacyDetailBookingSerializer(booking_detail)
            response_payload = serialized_booking.data
            serializer_duration_ms = (perf_counter() - serializer_started_at) * 1000
            _invalidate_management_cache()
            _log_management_performance(
                "approve_booking_payment",
                request_started_at,
                booking_number=booking_number,
                decision=decision,
                serializer_duration_ms=f"{serializer_duration_ms:.2f}",
            )
            return Response(response_payload, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Error in ConfirmPaymentView: {str(e)}")
            return Response({"message": "Failed to update payment status. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class FetchPaidBookingView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Fetch all bookings with a payment submission awaiting admin review",
        responses={
            200: openapi.Response('Successfully retrieved booking details', AdminPaidBookingSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: Booking detail not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            request_started_at = perf_counter()
            payment_queue = str(request.query_params.get("payment_queue") or "").strip().lower()
            if payment_queue and payment_queue not in PAYMENT_REVIEW_QUEUE_VALUES:
                return Response(
                    {
                        "message": "Invalid payment_queue. Must be one of: "
                        + ", ".join(sorted(PAYMENT_REVIEW_QUEUE_VALUES))
                        + "."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            booking_number = str(request.query_params.get("booking_number") or "").strip()
            order_date, order_date_error = _parse_optional_management_date(
                request.query_params.get("order_date"),
                field_name="order_date",
            )
            if order_date_error is not None:
                return order_date_error

            cache_key = _build_management_scoped_cache_key(
                CACHE_KEY_PAID_BOOKINGS,
                request.query_params,
            )
            cached_payload = cache.get(cache_key)
            if cached_payload is not None:
                _log_management_performance(
                    "fetch_paid_bookings",
                    request_started_at,
                    cache_hit=True,
                    payment_queue=payment_queue or "all",
                    booking_number=booking_number,
                    order_date=order_date.isoformat() if order_date is not None else "",
                    page=request.query_params.get("page") or 1,
                )
                return Response(cached_payload, status=status.HTTP_200_OK)

            booking_details_qs = Booking.objects.filter(
                booking_token__isnull=False,
            ).distinct().select_related(
                'order_to',
                'order_by',
                'package_token',
            ).prefetch_related(
                Prefetch(
                    'order_to__company_of_partner',
                    queryset=BusinessProfile.objects.only(
                        'company_of_partner_id',
                        'company_name',
                        'total_experience',
                        'company_bio',
                        'company_logo',
                        'contact_name',
                        'contact_number',
                    ),
                ),
                Prefetch(
                    'order_to__mailing_of_partner',
                    queryset=PartnerMailingDetail.objects.only(
                        'mailing_of_partner_id',
                        'address_id',
                        'street_address',
                        'address_line2',
                        'city',
                        'state',
                        'country',
                        'postal_code',
                        'lat',
                        'long',
                    ),
                ),
                Prefetch(
                    'passport_for_booking_number',
                    queryset=PassportValidity.objects.only(
                        'passport_for_booking_number_id',
                        'user_passport',
                        'user_photo',
                        'first_name',
                        'last_name',
                        'date_of_birth',
                        'passport_number',
                        'passport_country',
                        'expiry_date',
                    ),
                ),
                'booking_token',
            ).order_by('-order_time')

            if booking_number:
                booking_details_qs = booking_details_qs.filter(booking_number=booking_number)
            if order_date is not None:
                booking_details_qs = booking_details_qs.filter(order_time__date=order_date)

            annotated_queryset = annotate_booking_payment_statuses(booking_details_qs)
            queue_filters = _build_payment_review_queue_filters(now=timezone.now())
            reviewable_queryset = annotated_queryset.filter(_combine_queue_filters(queue_filters))
            summary_started_at = perf_counter()
            review_summary = _build_payment_review_summary(reviewable_queryset, queue_filters)
            summary_duration_ms = (perf_counter() - summary_started_at) * 1000

            filtered_queryset = (
                reviewable_queryset.filter(queue_filters[payment_queue])
                if payment_queue
                else reviewable_queryset
            )
            total_amount = (
                review_summary["queue_total_amounts"].get(payment_queue, 0)
                if payment_queue
                else review_summary["total_amount"]
            )

            response = _build_paginated_response(
                request,
                filtered_queryset,
                AdminPaidBookingSerializer,
                meta={
                    "payment_queue": payment_queue or None,
                    "order_date": order_date.isoformat() if order_date is not None else None,
                    "total_requests": review_summary["total_requests"],
                    "queue_counts": review_summary["queue_counts"],
                    "total_amount": float(total_amount),
                },
            )
            cache.set(cache_key, response.data, MANAGEMENT_CACHE_TIMEOUT_SECONDS)
            timing_metrics = getattr(response, "_timing_metrics", {})
            _log_management_performance(
                "fetch_paid_bookings",
                request_started_at,
                cache_hit=False,
                payment_queue=payment_queue or "all",
                booking_number=booking_number,
                order_date=order_date.isoformat() if order_date is not None else "",
                page=request.query_params.get("page") or 1,
                summary_duration_ms=f"{summary_duration_ms:.2f}",
                queryset_duration_ms=f"{timing_metrics.get('queryset_duration_ms', 0):.2f}",
                serializer_duration_ms=f"{timing_metrics.get('serializer_duration_ms', 0):.2f}",
                result_count=len(response.data.get("results") or []),
            )
            return response

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Error in FetchPaidBookingView: {str(e)}")
            return Response({"message": "Failed to fetch booking details. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManageFeaturedPackageView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Update an existing Huz Hajj or Umrah package.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Huz package token'),
                'is_featured': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='True or false'),
            },
            required=['partner_session_token', 'huz_token', 'is_featured']
        ),
        responses={
            200: openapi.Response("Successful update", HuzBasicSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or package not found.",
            409: "Conflict: Account status or type issue.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        partner_session_token = request.data.get('partner_session_token')
        huz_token = request.data.get('huz_token')
        is_featured = _coerce_bool(request.data.get('is_featured'))
        if not partner_session_token or not huz_token or is_featured is None:
            return Response({"message": "Missing user or package information."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # Check the account status and partner type
        if user.account_status != "Active":
            return Response({"message": "Account status does not allow you to perform this task."}, status=status.HTTP_409_CONFLICT)

        # Retrieve the package based on the huz token
        package = HuzBasicDetail.objects.filter(huz_token=huz_token).first()
        if not package:
            return Response({"message": "Package not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        try:
            package.is_featured = is_featured
            package.save()
            serialized_package = HuzBasicSerializer(package)
            return Response(serialized_package.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"ManageFeaturedPackageView - Put: {str(e)}")
            return Response({"message": "Failed to update package detail. Internal server error."},  status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetPartnerReceiveAblePaymentsView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Fetch all partner receivable booking payments that are not yet transferred.",
        responses={
            200: openapi.Response('Successfully retrieved partner receive able details', PartnersBookingPaymentSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: payment detail not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            request_started_at = perf_counter()
            cache_key = _build_management_scoped_cache_key(
                CACHE_KEY_PARTNER_RECEIVABLES,
                request.query_params,
            )
            cached_payload = cache.get(cache_key)
            if cached_payload is not None:
                _log_management_performance(
                    "fetch_partner_receivables",
                    request_started_at,
                    cache_hit=True,
                    page=request.query_params.get("page") or 1,
                )
                return Response(cached_payload, status=status.HTTP_200_OK)

            receive_able_qs = PartnersBookingPayment.objects.filter(payment_status="NotPaid").select_related(
                'payment_for_partner',
                'payment_for_booking',
                'payment_for_package',
            ).prefetch_related(
                Prefetch(
                    'payment_for_partner__company_of_partner',
                    queryset=BusinessProfile.objects.only(
                        'company_of_partner_id',
                        'company_name',
                        'total_experience',
                        'company_bio',
                        'company_logo',
                        'contact_name',
                        'contact_number',
                    ),
                )
            ).order_by("-create_date")
            summary_started_at = perf_counter()
            summary = receive_able_qs.aggregate(
                total_receivable=Sum("receivable_amount"),
                total_pending=Sum("pending_amount"),
                total_processed=Sum("processed_amount"),
            )
            summary_duration_ms = (perf_counter() - summary_started_at) * 1000

            response = _build_paginated_response(
                request,
                receive_able_qs,
                PartnersBookingPaymentSerializer,
                meta={
                    "total_receivable": float(summary.get("total_receivable") or 0),
                    "total_pending": float(summary.get("total_pending") or 0),
                    "total_processed": float(summary.get("total_processed") or 0),
                },
            )
            cache.set(cache_key, response.data, MANAGEMENT_CACHE_TIMEOUT_SECONDS)
            timing_metrics = getattr(response, "_timing_metrics", {})
            _log_management_performance(
                "fetch_partner_receivables",
                request_started_at,
                cache_hit=False,
                page=request.query_params.get("page") or 1,
                summary_duration_ms=f"{summary_duration_ms:.2f}",
                queryset_duration_ms=f"{timing_metrics.get('queryset_duration_ms', 0):.2f}",
                serializer_duration_ms=f"{timing_metrics.get('serializer_duration_ms', 0):.2f}",
                result_count=len(response.data.get("results") or []),
            )
            return response

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Error in GetPartnerReceiveAblePaymentsView: {str(e)}")
            return Response({"message": "Failed to fetch booking details. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManagePartnerReceiveAblePaymentView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_summary="Manage Partner Receivable Payment",
        operation_description="Updates the payment status for a partner based on the booking number and session token provided.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['partner_session_token', 'booking_number'],
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='The session token of the partner'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='The booking number associated with the payment'),
            },
        ),
        responses={
            200: openapi.Response(description="Successfully updated partner payment details.",
                                  schema=PartnersBookingPaymentSerializer),
            400: "Missing user or booking information.",
            404: "User or booking not found with the provided details.",
            409: "Account status does not allow you to perform this task.",
            500: "Failed to update partner payment detail. Internal server error."
        }
    )
    @transaction.atomic
    def put(self, request, *args, **kwargs):
        partner_session_token = request.data.get('partner_session_token')
        booking_number = request.data.get('booking_number')

        # Validate if both partner_session_token and booking_number are provided
        if not partner_session_token or not booking_number:
            return Response({"message": "Missing user or booking information."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided details."}, status=status.HTTP_404_NOT_FOUND)

        # Ensure the partner's account is active
        if user.account_status != "Active":
            return Response({"message": "Account status does not allow you to perform this task."},
                            status=status.HTTP_409_CONFLICT)

        # Retrieve the booking details based on the booking number
        booking_detail = Booking.objects.filter(booking_number=booking_number).first()
        if not booking_detail:
            return Response({"message": "Booking not found with the provided details."},
                            status=status.HTTP_404_NOT_FOUND)

        # Ensure partner payouts only process after fulfillment has reached the travel-ready or completed stage
        if booking_detail.booking_status not in [BOOKING_STATUS_READY_FOR_TRAVEL, BOOKING_STATUS_COMPLETED]:
            return Response({"message": "Only ready-for-travel or completed booking payments can be processed."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the receivable payment details for the partner and booking
        receive_able = PartnersBookingPayment.objects.select_for_update().filter(
            payment_for_partner=user,
            payment_for_booking=booking_detail
        ).first()
        if not receive_able:
            return Response({"message": "Payment detail not found with the provided details."},
                            status=status.HTTP_404_NOT_FOUND)

        # Retrieve the partner's wallet details
        wallet_detail = Wallet.objects.select_for_update().filter(wallet_session=user).first()
        if not wallet_detail:
            return Response({"message": "Partner wallet detail not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            # Process the payment based on the current payment status
            if receive_able.payment_status == "NotPaid":
                # Update payment status to "FirstPayment" and process the full amount
                receive_able.payment_status = "FirstPayment"
                receive_able.processed_amount = receive_able.receivable_amount
                receive_able.save()

                # Update the wallet amount with the receivable amount
                wallet_detail.wallet_amount += receive_able.receivable_amount
                wallet_detail.save()

                # Log the transaction in the partner's transaction history
                PartnerTransactionHistory.objects.create(
                    transaction_amount=receive_able.receivable_amount,
                    transaction_type="Credit",
                    transaction_for_partner=user,
                    transaction_wallet_token=wallet_detail,
                    transaction_for_package=booking_detail.package_token,
                    transaction_description=f"You have credited {receive_able.receivable_amount} for booking number {booking_detail.booking_number}."
                )

            elif receive_able.payment_status == "FirstPayment":
                # Update payment status to "FinalPayment" and process the pending amount
                receive_able.payment_status = "FinalPayment"
                receive_able.processed_amount += receive_able.pending_amount
                receive_able.processed_date = timezone.now()
                receive_able.save()

                # Update the wallet amount with the pending amount
                wallet_detail.wallet_amount += receive_able.pending_amount
                wallet_detail.save()

                # Log the transaction in the partner's transaction history
                PartnerTransactionHistory.objects.create(
                    transaction_amount=receive_able.pending_amount,
                    transaction_type="Credit",
                    transaction_for_partner=user,
                    transaction_wallet_token=wallet_detail,
                    transaction_for_package=booking_detail.package_token,
                    transaction_description=f"You have credited {receive_able.pending_amount} for booking number {booking_detail.booking_number}."
                )
            else:
                return Response({"message": "Payment has already been fully processed."},
                                status=status.HTTP_409_CONFLICT)

            # Serialize and return the updated payment details
            serialized_booking = PartnersBookingPaymentSerializer(receive_able)
            _invalidate_management_cache()
            return Response(serialized_booking.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return a 500 response
            logger.error(f"ManagePartnerReceiveAblePaymentView - Put: {str(e)}")
            return Response({"message": "Failed to update partner payment details. Internal server error."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
