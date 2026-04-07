from datetime import datetime, timedelta
from uuid import UUID
import json

from django.db import transaction
from django.db.models import (
    Count,
    FloatField,
    IntegerField,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, Lower, Trim
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import serializers, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from booking.models import BookingRatingAndReview
from common.authentication import (
    SessionTokenHeaderAuthentication,
    get_authenticated_partner_profile,
    is_authenticated_staff_user,
)
from common.auth_utils import is_admin_request, require_partner_profile
from common.logs_file import logger
from common.pagination import CustomPagination
from common.permissions import IsAdminOrAuthenticatedPartnerProfile
from common.utility import generate_token, random_six_digits
from management.authentication import ManagementSessionAuthentication
from .models import (
    BusinessProfile,
    HuzAirlineDetail,
    HuzBasicDetail,
    HuzHotelDetail,
    HuzPackageDateRange,
    HuzTransportDetail,
    HuzZiyarahDetail,
    PartnerProfile,
)
from .serializers import (
    HuzAlignedPackageSerializer,
    HuzAirlineSerializer,
    HuzBasicSerializer,
    HuzHotelSerializer,
    HuzPackageDateRangeSerializer,
    HuzTransportSerializer,
    HuzZiyarahSerializer,
    ShortBusinessSerializer,
)


PACKAGE_PREFETCH_RELATED = (
    "airline_for_package",
    "transport_for_package",
    "hotel_for_package",
    "hotel_for_package__hotel_images",
    "hotel_for_package__catalog_hotel",
    "hotel_for_package__catalog_hotel__hotel_images",
    "ziyarah_for_package",
    "package_date_ranges",
    "rating_for_package",
    "package_provider__company_of_partner",
)

PACKAGE_PREFETCH_RELATED_WITHOUT_RATINGS = tuple(
    relation for relation in PACKAGE_PREFETCH_RELATED if relation != "rating_for_package"
)

BASIC_FLOAT_FIELDS = (
    "package_base_cost",
    "cost_for_child",
    "cost_for_infants",
    "cost_for_sharing",
    "cost_for_quad",
    "cost_for_triple",
    "cost_for_double",
    "cost_for_single",
    "discount_if_child_with_bed",
)

BASIC_INT_FIELDS = (
    "mecca_nights",
    "madinah_nights",
    "jeddah_nights",
    "taif_nights",
    "riyadah_nights",
)

BASIC_BOOL_FIELDS = (
    "is_visa_included",
    "is_airport_reception_included",
    "is_tour_guide_included",
    "is_insurance_included",
    "is_breakfast_included",
    "is_lunch_included",
    "is_dinner_included",
    "is_package_open_for_other_date",
)

HOTEL_BOOL_FIELDS = (
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

HOTEL_CITY_NIGHT_FIELD_MAP = {
    "makkah": "mecca_nights",
    "madinah": "madinah_nights",
    "jeddah": "jeddah_nights",
    "taif": "taif_nights",
    "riyadh": "riyadah_nights",
    "riyadah": "riyadah_nights",
}

STATUS_NORMALIZER = {
    "initialize": "Initialize",
    "completed": "Completed",
    "active": "Active",
    "deactivated": "Deactivated",
    "deactivate": "Deactivated",
    "pending": "Pending",
}
SUPPORTED_PACKAGE_TYPES = ("Hajj", "Umrah")
PACKAGE_BOOKING_VALIDITY_LEAD_DAYS = 2
MASTER_HOTEL_PACKAGE_TOKEN = "__system_master_hotel_package__"


class MasterHotelCatalogPagination(CustomPagination):
    page_size = 25
    max_page_size = 100


def _to_mutable_dict(data):
    if data is None:
        return {}
    try:
        copied = data.copy()
    except Exception:
        copied = dict(data)
    if hasattr(copied, "items"):
        return {key: value for key, value in copied.items()}
    return dict(copied)


def _first_error_message(serializer):
    errors = serializer.errors
    if not errors:
        return "Invalid payload."

    first_field = next(iter(errors))
    first_error = errors[first_field]

    if isinstance(first_error, (list, tuple)) and first_error:
        first_error = first_error[0]
    elif isinstance(first_error, dict) and first_error:
        first_error = next(iter(first_error.values()))

    return f"{first_field}: {first_error}"


def _to_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value

    value_str = str(value).strip().lower()
    if value_str in {"true", "1", "yes", "on"}:
        return True
    if value_str in {"false", "0", "no", "off"}:
        return False
    return default


def _to_int(value, default=0):
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value, default=0.0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_datetime_value(value):
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value

    parsed_datetime = parse_datetime(str(value))
    if parsed_datetime:
        if timezone.is_naive(parsed_datetime):
            return timezone.make_aware(parsed_datetime, timezone.get_current_timezone())
        return parsed_datetime

    parsed_date = parse_date(str(value))
    if parsed_date:
        parsed_datetime = datetime.combine(parsed_date, datetime.min.time())
        return timezone.make_aware(parsed_datetime, timezone.get_current_timezone())

    return None


def _derive_package_validity(start_date):
    if not start_date:
        return None

    return start_date - timedelta(days=PACKAGE_BOOKING_VALIDITY_LEAD_DAYS)


def _build_package_token_query(raw_token):
    token_query = Q(huz_token=raw_token)
    try:
        token_query |= Q(huz_id=UUID(str(raw_token)))
    except (TypeError, ValueError):
        pass
    return token_query


def _normalize_package_type(raw_value):
    if raw_value in (None, ""):
        return None
    value = str(raw_value).strip()
    lower = value.lower()
    if lower == "hajj":
        return "Hajj"
    if lower == "umrah":
        return "Umrah"
    return None


def _serialize_catalog_hotel(hotel):
    payload = HuzHotelSerializer(hotel).data
    payload.setdefault("images", [])
    return payload


def _apply_master_hotel_db_dedupe(queryset):
    dedupe_queryset = queryset.annotate(
        dedupe_city=Lower(Trim(Coalesce("hotel_city", Value("")))),
        dedupe_name=Lower(Trim(Coalesce("hotel_name", Value("")))),
        dedupe_rating=Lower(Trim(Coalesce("hotel_rating", Value("")))),
    )
    representative_hotel_ids = (
        dedupe_queryset.values("dedupe_city", "dedupe_name", "dedupe_rating")
        .annotate(representative_hotel_id=Min("hotel_id"))
        .values("representative_hotel_id")
    )
    return dedupe_queryset.filter(hotel_id__in=Subquery(representative_hotel_ids))


def _apply_expired_package_filter(queryset, reference_date=None):
    today = reference_date or timezone.localdate()
    fallback_start_date_min = today + timedelta(days=2)

    annotated_queryset = queryset.annotate(
        package_date_range_count=Count("package_date_ranges", distinct=True),
        valid_package_date_range_count=Count(
            "package_date_ranges",
            filter=(
                Q(package_date_ranges__package_validity__date__gte=today)
                | (
                    Q(package_date_ranges__package_validity__isnull=True)
                    & Q(package_date_ranges__start_date__date__gte=fallback_start_date_min)
                )
            ),
            distinct=True,
        ),
    )

    ranges_fully_expired_filter = Q(
        package_date_range_count__gt=0,
        valid_package_date_range_count=0,
    )
    single_window_fully_expired_filter = Q(
        package_date_range_count=0,
    ) & (
        Q(package_validity__date__lt=today)
        | (
            Q(package_validity__isnull=True)
            & Q(start_date__date__lt=fallback_start_date_min)
        )
    )

    return annotated_queryset.filter(
        ranges_fully_expired_filter | single_window_fully_expired_filter
    )


def _annotate_package_rating_summary(queryset):
    rating_base_queryset = BookingRatingAndReview.objects.filter(
        rating_for_package=OuterRef("pk")
    ).values("rating_for_package")
    package_rating_total_stars_subquery = (
        rating_base_queryset.annotate(total_stars=Sum("partner_total_stars"))
        .values("total_stars")[:1]
    )
    package_rating_total_count_subquery = (
        rating_base_queryset.annotate(rating_count=Count("rating_id"))
        .values("rating_count")[:1]
    )

    return queryset.annotate(
        package_rating_total_stars=Coalesce(
            Subquery(
                package_rating_total_stars_subquery,
                output_field=FloatField(),
            ),
            Value(0.0),
            output_field=FloatField(),
        ),
        package_rating_total_count=Coalesce(
            Subquery(
                package_rating_total_count_subquery,
                output_field=IntegerField(),
            ),
            Value(0),
            output_field=IntegerField(),
        ),
    )


class OperatorHuzPackageSerializer(HuzAlignedPackageSerializer):
    pass


class OperatorPackageBaseView(APIView):
    permission_classes = [IsAdminOrAuthenticatedPartnerProfile]

    def _get_partner(self, request, require_active=False):
        partner_token = request.data.get("partner_session_token") or request.GET.get(
            "partner_session_token"
        )
        partner = None
        if not is_admin_request(request):
            try:
                partner = require_partner_profile(request)
            except AuthenticationFailed as exc:
                return None, Response(
                    {"detail": str(exc)},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
        elif not partner_token:
            return None, Response(
                {"message": "Missing user information."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if partner is None:
            partner = PartnerProfile.objects.filter(partner_session_token=partner_token).first()
        if not partner:
            return None, Response(
                {"message": "User not found with the provided detail."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if require_active and partner.account_status != "Active":
            return None, Response(
                {
                    "message": (
                        "Your account status does not allow you to perform this task. "
                        "Please contact our support team for assistance."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        return partner, None

    @staticmethod
    def _extract_huz_token(payload=None, request=None):
        payload = payload or {}
        token = payload.get("huz_token") or payload.get("huz_id")
        if token:
            return str(token).strip()

        if request is None:
            return None

        query_token = request.GET.get("huz_token") or request.GET.get("huz_id")
        return str(query_token).strip() if query_token else None

    @staticmethod
    def _package_queryset():
        return (
            HuzBasicDetail.objects.filter(package_type__in=SUPPORTED_PACKAGE_TYPES)
            .select_related("package_provider")
            .prefetch_related(*PACKAGE_PREFETCH_RELATED)
        )

    @staticmethod
    def _package_list_queryset():
        return (
            HuzBasicDetail.objects.filter(package_type__in=SUPPORTED_PACKAGE_TYPES)
            .select_related("package_provider")
            .prefetch_related(*PACKAGE_PREFETCH_RELATED_WITHOUT_RATINGS)
        )

    @staticmethod
    def _compute_total_nights(basic_payload):
        source = basic_payload or {}
        return max(sum(_to_int(source.get(field), 0) for field in BASIC_INT_FIELDS), 1)


class AuthenticatedPartnerPackageMutationAPIView(OperatorPackageBaseView):
    authentication_classes = [
        SessionTokenHeaderAuthentication,
        ManagementSessionAuthentication,
    ]
    permission_classes = [IsAdminOrAuthenticatedPartnerProfile]

    @staticmethod
    def _extract_partner_session_token(request):
        token = str(request.query_params.get("partner_session_token") or "").strip()
        if token:
            return token

        try:
            payload = request.data
        except Exception:
            payload = None

        if hasattr(payload, "get"):
            token = str(payload.get("partner_session_token") or "").strip()
            if token:
                return token

        return ""

    def _get_partner(self, request, require_active=False):
        partner = get_authenticated_partner_profile(request)
        if partner is None:
            if not is_authenticated_staff_user(request):
                return None, Response(
                    {"detail": "Authentication credentials were not provided."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            # Staff/admin compatibility can still scope these mutation views to
            # a selected partner token; authenticated operators resolve from
            # the Authorization header instead.
            partner_token = self._extract_partner_session_token(request)
            if not partner_token:
                return None, Response(
                    {"message": "Missing user information."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            partner = PartnerProfile.objects.filter(
                partner_session_token=partner_token
            ).first()
            if partner is None:
                return None, Response(
                    {"message": "User not found with the provided detail."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        if require_active and partner.account_status != "Active":
            return None, Response(
                {
                    "message": (
                        "Your account status does not allow you to perform this task. "
                        "Please contact our support team for assistance."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        return partner, None

    def _get_partner_package(self, partner, huz_token, with_prefetch=False):
        queryset = (
            self._package_queryset()
            if with_prefetch
            else HuzBasicDetail.objects.filter(package_type__in=SUPPORTED_PACKAGE_TYPES)
        )
        queryset = queryset.filter(package_provider=partner).filter(_build_package_token_query(huz_token))
        return queryset.first()

    def _serialize_package(self, package):
        package_obj = (
            self._package_queryset().filter(huz_id=package.huz_id).first() if package else None
        )
        package_obj = package_obj or package
        return OperatorHuzPackageSerializer(
            package_obj,
            context={"package_rating_cache": {}, "request": self.request},
        ).data

    @staticmethod
    def _normalize_hotel_city_key(raw_city):
        city_key = str(raw_city or "").strip().lower()
        if city_key == "makkah":
            return "makkah"
        if city_key == "madinah":
            return "madinah"
        if city_key == "jeddah":
            return "jeddah"
        if city_key == "taif":
            return "taif"
        if city_key in {"riyadh", "riyadah"}:
            return "riyadh"
        return None

    def _validate_hotel_city_nights(self, package, hotel_city):
        city_key = self._normalize_hotel_city_key(hotel_city)
        if not city_key:
            return None

        night_field = HOTEL_CITY_NIGHT_FIELD_MAP.get(city_key)
        if not night_field:
            return None

        if _to_int(getattr(package, night_field, 0), 0) <= 0:
            return (
                f"Cannot add hotel for {hotel_city}. "
                f"{city_key.title()} nights are 0 in package basic details."
            )
        return None

    @staticmethod
    def _normalize_basic_payload(payload, create=False):
        normalized = {}

        package_type = payload.get("package_type")
        if package_type not in (None, ""):
            resolved_type = _normalize_package_type(package_type)
            if not resolved_type:
                return None, "package_type: Invalid package type. Use Hajj or Umrah."
            normalized["package_type"] = resolved_type
        elif create:
            return None, "package_type is required."

        package_name = payload.get("package_name")
        if package_name not in (None, ""):
            normalized["package_name"] = str(package_name).strip()
        elif create:
            return None, "package_name is required."

        description = payload.get("description")
        if description is not None:
            normalized["description"] = description
        elif create:
            normalized["description"] = ""

        for field in BASIC_FLOAT_FIELDS:
            if payload.get(field) is not None:
                normalized[field] = _to_float(payload.get(field), 0.0)
            elif create:
                normalized[field] = 0.0

        for field in BASIC_INT_FIELDS:
            if payload.get(field) is not None:
                normalized[field] = _to_int(payload.get(field), 0)
            elif create:
                normalized[field] = 0

        for field in BASIC_BOOL_FIELDS:
            if payload.get(field) is not None:
                normalized[field] = _to_bool(payload.get(field), default=False)
            elif create:
                normalized[field] = False

        for date_field in ("start_date", "end_date"):
            if payload.get(date_field) not in (None, ""):
                parsed_datetime = _parse_datetime_value(payload.get(date_field))
                if not parsed_datetime:
                    return None, f"{date_field}: Invalid datetime value."
                normalized[date_field] = parsed_datetime

        if create:
            if "start_date" not in normalized:
                normalized["start_date"] = timezone.now() + timedelta(days=10)

            nights_total = max(
                sum(_to_int(normalized.get(field), 0) for field in BASIC_INT_FIELDS),
                1,
            )
            if "end_date" not in normalized:
                normalized["end_date"] = normalized["start_date"] + timedelta(days=nights_total)

            normalized["package_validity"] = _derive_package_validity(
                normalized["start_date"]
            )

            if payload.get("package_base_cost") in (None, ""):
                normalized["package_base_cost"] = _to_float(
                    payload.get("cost_for_sharing"),
                    0.0,
                )
        elif "start_date" in normalized:
            normalized["package_validity"] = _derive_package_validity(
                normalized["start_date"]
            )

        return normalized, None

    @staticmethod
    def _normalize_package_date_range_payload(payload, basic_payload=None):
        raw_ranges = payload.get("package_date_range")
        if raw_ranges is None:
            raw_ranges = payload.get("package_date_ranges")
        if raw_ranges is None:
            raw_ranges = payload.get("date_ranges")

        if raw_ranges is None:
            return None, None

        if isinstance(raw_ranges, str):
            raw_text = raw_ranges.strip()
            if raw_text == "":
                return None, "At least one date range is required."
            try:
                raw_ranges = json.loads(raw_text)
            except json.JSONDecodeError:
                return None, "package_date_range: Invalid JSON list."

        if isinstance(raw_ranges, dict):
            raw_ranges = [raw_ranges]

        if not isinstance(raw_ranges, (list, tuple)):
            return None, "package_date_range must be an array."
        if len(raw_ranges) == 0:
            return None, "At least one date range is required."

        total_nights = OperatorPackageBaseView._compute_total_nights(basic_payload)
        normalized_ranges = []
        seen_start_dates = set()

        for index, raw_item in enumerate(raw_ranges):
            item = _to_mutable_dict(raw_item)

            range_id = item.get("range_id") or item.get("rangeId")
            if range_id in ("", None):
                range_id = None

            if range_id is not None:
                try:
                    range_id = str(UUID(str(range_id)))
                except (TypeError, ValueError):
                    return None, f"package_date_range[{index}].range_id: Invalid UUID."

            start_raw = item.get("start_date") or item.get("startDate")
            if start_raw in (None, ""):
                return None, f"package_date_range[{index}].start_date is required."
            start_date = _parse_datetime_value(start_raw)
            if not start_date:
                return None, f"package_date_range[{index}].start_date: Invalid datetime value."

            end_raw = item.get("end_date") or item.get("endDate")
            end_date = _parse_datetime_value(end_raw) if end_raw not in (None, "") else None
            if end_date is None:
                end_date = start_date + timedelta(days=total_nights)
            if end_date < start_date:
                return None, f"package_date_range[{index}].end_date must be after start_date."

            package_validity = _derive_package_validity(start_date)

            group_raw = item.get("group_capacity")
            if group_raw is None:
                group_raw = item.get("groupCapacity")
            if group_raw in (None, ""):
                group_capacity = None
            else:
                group_capacity = _to_int(group_raw, 0)
                if group_capacity <= 0:
                    return None, f"package_date_range[{index}].group_capacity must be greater than 0."

            start_key = start_date.date().isoformat()
            if start_key in seen_start_dates:
                return None, "Each date range must have a unique start_date."
            seen_start_dates.add(start_key)

            normalized_ranges.append(
                {
                    "range_id": range_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "group_capacity": group_capacity,
                    "package_validity": package_validity,
                }
            )

        return normalized_ranges, None

    @staticmethod
    def _sync_package_date_ranges(package, normalized_ranges):
        if normalized_ranges is None:
            return

        existing = {
            str(item.range_id): item
            for item in HuzPackageDateRange.objects.filter(date_range_for_package=package)
        }
        retained_ids = []

        for item in normalized_ranges:
            existing_item = None
            item_range_id = item.get("range_id")
            if item_range_id:
                existing_item = existing.get(str(item_range_id))

            if existing_item:
                existing_item.start_date = item["start_date"]
                existing_item.end_date = item["end_date"]
                existing_item.group_capacity = item["group_capacity"]
                existing_item.package_validity = item["package_validity"]
                existing_item.save(
                    update_fields=[
                        "start_date",
                        "end_date",
                        "group_capacity",
                        "package_validity",
                    ]
                )
                retained_ids.append(existing_item.range_id)
                continue

            created = HuzPackageDateRange.objects.create(
                date_range_for_package=package,
                start_date=item["start_date"],
                end_date=item["end_date"],
                group_capacity=item["group_capacity"],
                package_validity=item["package_validity"],
            )
            retained_ids.append(created.range_id)

        if retained_ids:
            HuzPackageDateRange.objects.filter(date_range_for_package=package).exclude(
                range_id__in=retained_ids
            ).delete()
        else:
            HuzPackageDateRange.objects.filter(date_range_for_package=package).delete()

        ranges = list(
            HuzPackageDateRange.objects.filter(date_range_for_package=package).order_by(
                "start_date",
                "end_date",
            )
        )
        if not ranges:
            return

        package.start_date = ranges[0].start_date
        package.end_date = max(item.end_date for item in ranges)
        package.package_validity = max(
            (
                item.package_validity or _derive_package_validity(item.start_date)
                for item in ranges
            ),
            default=package.end_date,
        )
        package.save(update_fields=["start_date", "end_date", "package_validity"])

    @staticmethod
    def _normalize_airline_payload(payload, create=False):
        normalized = {}
        required_fields = (
            "airline_name",
            "ticket_type",
            "flight_from",
            "flight_to",
            "return_flight_from",
            "return_flight_to",
        )

        for field in required_fields:
            field_value = payload.get(field)
            if field_value in (None, ""):
                if create:
                    return None, f"{field} is required."
                continue
            normalized[field] = field_value

        if payload.get("is_return_flight_included") is not None:
            normalized["is_return_flight_included"] = _to_bool(
                payload.get("is_return_flight_included"),
                default=True,
            )
        elif create:
            normalized["is_return_flight_included"] = True

        return normalized, None

    @staticmethod
    def _normalize_transport_payload(payload, create=False):
        normalized = {}
        required_fields = ("transport_name", "transport_type", "routes")

        for field in required_fields:
            field_value = payload.get(field)
            if field_value in (None, ""):
                if create:
                    return None, f"{field} is required."
                continue
            if field == "routes" and isinstance(field_value, (list, tuple)):
                field_value = ",".join(str(route).strip() for route in field_value if str(route).strip())
            normalized[field] = field_value

        return normalized, None

    @staticmethod
    def _normalize_ziyarah_payload(payload):
        ziyarah_list = payload.get("ziyarah_list", "")
        if isinstance(ziyarah_list, (list, tuple)):
            ziyarah_list = ",".join(
                str(item).strip() for item in ziyarah_list if str(item).strip()
            )
        return {"ziyarah_list": ziyarah_list}

    @staticmethod
    def _resolve_hotel_template(hotel_id):
        if not hotel_id:
            return None
        hotel_template = (
            HuzHotelDetail.objects.filter(hotel_id=hotel_id)
            .select_related("hotel_for_package")
            .prefetch_related("hotel_images")
            .first()
        )
        if not hotel_template:
            return None
        package = getattr(hotel_template, "hotel_for_package", None)
        if not package or package.huz_token != MASTER_HOTEL_PACKAGE_TOKEN:
            return None
        return hotel_template

    @staticmethod
    def _normalize_hotel_payload(payload, create=False, template_hotel=None):
        normalized = {}

        hotel_city = payload.get("hotel_city") or getattr(template_hotel, "hotel_city", None)
        hotel_name = payload.get("hotel_name") or getattr(template_hotel, "hotel_name", None)
        hotel_rating = payload.get("hotel_rating") or getattr(template_hotel, "hotel_rating", None)
        room_sharing_type = payload.get("room_sharing_type") or getattr(
            template_hotel, "room_sharing_type", None
        )
        hotel_distance = payload.get("hotel_distance")
        if hotel_distance in (None, ""):
            hotel_distance = getattr(template_hotel, "hotel_distance", None)
        distance_type = payload.get("distance_type")
        if distance_type in (None, ""):
            distance_type = getattr(template_hotel, "distance_type", None)

        required_text_fields = {
            "hotel_city": hotel_city,
            "hotel_name": hotel_name,
            "hotel_rating": hotel_rating,
            "room_sharing_type": room_sharing_type,
        }

        for field_name, field_value in required_text_fields.items():
            if field_value in (None, ""):
                if create:
                    return None, f"{field_name} is required."
                continue
            normalized[field_name] = field_value

        if hotel_distance not in (None, ""):
            normalized["hotel_distance"] = hotel_distance
        if distance_type not in (None, ""):
            normalized["distance_type"] = distance_type

        for field in HOTEL_BOOL_FIELDS:
            if payload.get(field) is not None:
                normalized[field] = _to_bool(payload.get(field), default=False)
            elif create:
                normalized[field] = False

        return normalized, None


class CreateHuzPackageView(AuthenticatedPartnerPackageMutationAPIView):
    @swagger_auto_schema(operation_description="Create a new package for the current partner.")
    def post(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=True)
            if error:
                return error

            if partner.partner_type == "Individual":
                return Response(
                    {"message": "Sorry, you are enrolled as an Individual."},
                    status=status.HTTP_409_CONFLICT,
                )

            payload = _to_mutable_dict(request.data)
            normalized, error_message = self._normalize_basic_payload(payload, create=True)
            if error_message:
                return Response({"message": error_message}, status=status.HTTP_400_BAD_REQUEST)

            normalized_ranges, range_error = self._normalize_package_date_range_payload(
                payload,
                basic_payload=normalized,
            )
            if range_error:
                return Response({"message": range_error}, status=status.HTTP_400_BAD_REQUEST)

            random_key = random_six_digits()
            normalized["package_provider"] = partner.partner_id
            normalized["huz_token"] = generate_token(f"{random_key}{datetime.now()}")
            normalized["package_status"] = "Initialize"
            normalized["package_stage"] = 1

            serializer = HuzBasicSerializer(data=normalized)
            if not serializer.is_valid():
                return Response(
                    {"message": _first_error_message(serializer)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            with transaction.atomic():
                package = serializer.save()

                if normalized_ranges is None:
                    normalized_ranges = [
                        {
                            "range_id": None,
                            "start_date": package.start_date,
                            "end_date": package.end_date,
                            "group_capacity": None,
                            "package_validity": package.package_validity
                            or _derive_package_validity(package.start_date),
                        }
                    ]

                self._sync_package_date_ranges(package, normalized_ranges)

            return Response(self._serialize_package(package), status=status.HTTP_201_CREATED)
        except Exception as exc:
            logger.error(f"CreateHuzPackageView - Post: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to enroll package detail. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(operation_description="Update package basic information.")
    def put(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=True)
            if error:
                return error

            payload = _to_mutable_dict(request.data)
            huz_token = self._extract_huz_token(payload=payload)
            if not huz_token:
                return Response(
                    {"message": "Missing user or package information."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package = self._get_partner_package(partner, huz_token)
            if not package:
                return Response(
                    {"message": "Package not found with the provided detail."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            normalized, error_message = self._normalize_basic_payload(payload, create=False)
            if error_message:
                return Response({"message": error_message}, status=status.HTTP_400_BAD_REQUEST)

            range_night_source = {
                field: normalized.get(field, getattr(package, field, 0))
                for field in BASIC_INT_FIELDS
            }
            normalized_ranges, range_error = self._normalize_package_date_range_payload(
                payload,
                basic_payload=range_night_source,
            )
            if range_error:
                return Response({"message": range_error}, status=status.HTTP_400_BAD_REQUEST)

            if not normalized:
                if normalized_ranges is None:
                    return Response(
                        {"message": "No package fields were provided for update."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            with transaction.atomic():
                if normalized:
                    serializer = HuzBasicSerializer(package, data=normalized, partial=True)
                    if not serializer.is_valid():
                        return Response(
                            {"message": _first_error_message(serializer)},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    package = serializer.save()
                if normalized_ranges is not None:
                    self._sync_package_date_ranges(package, normalized_ranges)

            return Response(self._serialize_package(package), status=status.HTTP_200_OK)
        except Exception as exc:
            logger.error(f"CreateHuzPackageView - Put: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to update package detail. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CreateHuzAirlineView(AuthenticatedPartnerPackageMutationAPIView):
    @swagger_auto_schema(operation_description="Create package airline details.")
    def post(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=True)
            if error:
                return error

            payload = _to_mutable_dict(request.data)
            huz_token = self._extract_huz_token(payload=payload)
            if not huz_token:
                return Response(
                    {"message": "Missing user or package information."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package = self._get_partner_package(partner, huz_token)
            if not package:
                return Response(
                    {"message": "Package not found with the provided detail."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if HuzAirlineDetail.objects.filter(airline_for_package=package).exists():
                return Response(
                    {"message": "Airline info is already exist for this package."},
                    status=status.HTTP_409_CONFLICT,
                )

            normalized, error_message = self._normalize_airline_payload(payload, create=True)
            if error_message:
                return Response({"message": error_message}, status=status.HTTP_400_BAD_REQUEST)

            serializer = HuzAirlineSerializer(data=normalized)
            if not serializer.is_valid():
                return Response(
                    {"message": _first_error_message(serializer)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer.save(airline_for_package=package)

            if package.package_stage < 2:
                package.package_stage = 2
                package.save(update_fields=["package_stage"])

            return Response(self._serialize_package(package), status=status.HTTP_201_CREATED)
        except Exception as exc:
            logger.error(f"CreateHuzAirlineView - Post: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to enroll airline detail. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(operation_description="Update package airline details.")
    def put(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=True)
            if error:
                return error

            payload = _to_mutable_dict(request.data)
            huz_token = self._extract_huz_token(payload=payload)
            if not huz_token:
                return Response(
                    {"message": "Missing user or package information."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package = self._get_partner_package(partner, huz_token)
            if not package:
                return Response(
                    {"message": "Package not found with the provided detail."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            airline = HuzAirlineDetail.objects.filter(airline_for_package=package).first()
            if not airline:
                return Response(
                    {"message": "Airline detail not found with the provided detail."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            normalized, error_message = self._normalize_airline_payload(payload, create=False)
            if error_message:
                return Response({"message": error_message}, status=status.HTTP_400_BAD_REQUEST)

            serializer = HuzAirlineSerializer(airline, data=normalized, partial=True)
            if not serializer.is_valid():
                return Response(
                    {"message": _first_error_message(serializer)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer.save()
            return Response(self._serialize_package(package), status=status.HTTP_200_OK)
        except Exception as exc:
            logger.error(f"CreateHuzAirlineView - Put: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to update airline detail. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CreateHuzTransportView(AuthenticatedPartnerPackageMutationAPIView):
    @swagger_auto_schema(operation_description="Create package transport details.")
    def post(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=True)
            if error:
                return error

            payload = _to_mutable_dict(request.data)
            huz_token = self._extract_huz_token(payload=payload)
            if not huz_token:
                return Response(
                    {"message": "Missing package or user information."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package = self._get_partner_package(partner, huz_token)
            if not package:
                return Response(
                    {"message": "Package not found with the provided detail."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if HuzTransportDetail.objects.filter(transport_for_package=package).exists():
                return Response(
                    {"message": "Transport info is already exist for this package."},
                    status=status.HTTP_409_CONFLICT,
                )

            normalized, error_message = self._normalize_transport_payload(payload, create=True)
            if error_message:
                return Response({"message": error_message}, status=status.HTTP_400_BAD_REQUEST)

            serializer = HuzTransportSerializer(data=normalized)
            if not serializer.is_valid():
                return Response(
                    {"message": _first_error_message(serializer)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer.save(transport_for_package=package)

            if package.package_stage < 3:
                package.package_stage = 3
                package.save(update_fields=["package_stage"])

            return Response(self._serialize_package(package), status=status.HTTP_201_CREATED)
        except Exception as exc:
            logger.error(f"CreateHuzTransportView - Post: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to enroll transport detail. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(operation_description="Update package transport details.")
    def put(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=True)
            if error:
                return error

            payload = _to_mutable_dict(request.data)
            huz_token = self._extract_huz_token(payload=payload)
            if not huz_token:
                return Response(
                    {"message": "Missing package or user information."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package = self._get_partner_package(partner, huz_token)
            if not package:
                return Response(
                    {"message": "Package not found with the provided detail."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            transport = HuzTransportDetail.objects.filter(transport_for_package=package).first()
            if not transport:
                return Response(
                    {"message": "Transport info does not exist for this package."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            normalized, error_message = self._normalize_transport_payload(payload, create=False)
            if error_message:
                return Response({"message": error_message}, status=status.HTTP_400_BAD_REQUEST)

            serializer = HuzTransportSerializer(transport, data=normalized, partial=True)
            if not serializer.is_valid():
                return Response(
                    {"message": _first_error_message(serializer)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer.save()
            return Response(self._serialize_package(package), status=status.HTTP_200_OK)
        except Exception as exc:
            logger.error(f"CreateHuzTransportView - Put: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to update transportation detail. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CreateHuzZiyarahView(AuthenticatedPartnerPackageMutationAPIView):
    @swagger_auto_schema(operation_description="Create package ziyarah details.")
    def post(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=True)
            if error:
                return error

            payload = _to_mutable_dict(request.data)
            huz_token = self._extract_huz_token(payload=payload)
            if not huz_token:
                return Response(
                    {"message": "Missing package or user information."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package = self._get_partner_package(partner, huz_token)
            if not package:
                return Response(
                    {"message": "Package not found with the provided detail."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if HuzZiyarahDetail.objects.filter(ziyarah_for_package=package).exists():
                return Response(
                    {"message": "Ziyarah info is already exist for this package."},
                    status=status.HTTP_409_CONFLICT,
                )

            normalized = self._normalize_ziyarah_payload(payload)
            serializer = HuzZiyarahSerializer(data=normalized)
            if not serializer.is_valid():
                return Response(
                    {"message": _first_error_message(serializer)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer.save(ziyarah_for_package=package)

            update_fields = []
            if package.package_stage < 5:
                package.package_stage = 5
                update_fields.append("package_stage")

            if package.package_status not in {"Active", "Deactivated", "Block", "Completed"}:
                package.package_status = "Completed"
                update_fields.append("package_status")

            if update_fields:
                package.save(update_fields=update_fields)

            return Response(self._serialize_package(package), status=status.HTTP_201_CREATED)
        except Exception as exc:
            logger.error(f"CreateHuzZiyarahView - Post: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to enroll ziyarah detail. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(operation_description="Update package ziyarah details.")
    def put(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=True)
            if error:
                return error

            payload = _to_mutable_dict(request.data)
            huz_token = self._extract_huz_token(payload=payload)
            if not huz_token:
                return Response(
                    {"message": "Missing package or user information."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package = self._get_partner_package(partner, huz_token)
            if not package:
                return Response(
                    {"message": "Package not found with the provided detail."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            ziyarah = HuzZiyarahDetail.objects.filter(ziyarah_for_package=package).first()
            if not ziyarah:
                return Response(
                    {"message": "Ziyarah details not found for the provided package."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            normalized = self._normalize_ziyarah_payload(payload)
            serializer = HuzZiyarahSerializer(ziyarah, data=normalized, partial=True)
            if not serializer.is_valid():
                return Response(
                    {"message": _first_error_message(serializer)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer.save()
            return Response(self._serialize_package(package), status=status.HTTP_200_OK)
        except Exception as exc:
            logger.error(f"CreateHuzZiyarahView - Put: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to update ziyarah detail. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CreateHuzHotelView(AuthenticatedPartnerPackageMutationAPIView):
    @swagger_auto_schema(operation_description="Create or update package hotel details.")
    def post(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=True)
            if error:
                return error

            payload = _to_mutable_dict(request.data)
            huz_token = self._extract_huz_token(payload=payload)
            if not huz_token:
                return Response(
                    {"message": "Missing package or user information."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package = self._get_partner_package(partner, huz_token)
            if not package:
                return Response(
                    {"message": "Package not found with the provided detail."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            target_hotel_id = payload.get("huz_hotel_id") or payload.get("hotel_id")
            existing_hotel = None
            if target_hotel_id:
                existing_hotel = HuzHotelDetail.objects.filter(
                    hotel_for_package=package,
                    hotel_id=target_hotel_id,
                ).first()

            template_hotel = existing_hotel
            if payload.get("hotel_id") and not template_hotel:
                template_hotel = self._resolve_hotel_template(payload.get("hotel_id"))

            normalized, error_message = self._normalize_hotel_payload(
                payload,
                create=True,
                template_hotel=template_hotel,
            )
            if error_message:
                return Response({"message": error_message}, status=status.HTTP_400_BAD_REQUEST)

            city_validation_error = self._validate_hotel_city_nights(
                package,
                normalized.get("hotel_city"),
            )
            if city_validation_error:
                return Response(
                    {"message": city_validation_error},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not existing_hotel and normalized.get("hotel_city"):
                existing_hotel = HuzHotelDetail.objects.filter(
                    hotel_for_package=package,
                    hotel_city__iexact=normalized.get("hotel_city"),
                ).first()

            if existing_hotel:
                serializer = HuzHotelSerializer(existing_hotel, data=normalized, partial=True)
                if not serializer.is_valid():
                    return Response(
                        {"message": _first_error_message(serializer)},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                existing_hotel = serializer.save()
                if (
                    template_hotel
                    and existing_hotel.catalog_hotel_id != template_hotel.hotel_id
                ):
                    existing_hotel.catalog_hotel = template_hotel
                    existing_hotel.save(update_fields=["catalog_hotel"])
                created = False
            else:
                serializer = HuzHotelSerializer(data=normalized)
                if not serializer.is_valid():
                    return Response(
                        {"message": _first_error_message(serializer)},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                serializer.save(
                    hotel_for_package=package,
                    catalog_hotel=template_hotel,
                )
                created = True

            package_updates = []
            if package.package_stage < 4:
                package.package_stage = 4
                package_updates.append("package_stage")

            if package_updates:
                package.save(update_fields=package_updates)

            response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
            return Response(self._serialize_package(package), status=response_status)
        except Exception as exc:
            logger.error(f"CreateHuzHotelView - Post: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to enroll hotel detail. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(operation_description="Update existing package hotel details.")
    def put(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=True)
            if error:
                return error

            payload = _to_mutable_dict(request.data)
            huz_token = self._extract_huz_token(payload=payload)
            if not huz_token:
                return Response(
                    {"message": "Missing package or user information."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package = self._get_partner_package(partner, huz_token)
            if not package:
                return Response(
                    {"message": "Package not found with the provided detail."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            target_hotel_id = payload.get("huz_hotel_id") or payload.get("hotel_id")
            existing_hotel = None
            if target_hotel_id:
                existing_hotel = HuzHotelDetail.objects.filter(
                    hotel_for_package=package,
                    hotel_id=target_hotel_id,
                ).first()

            if not existing_hotel and payload.get("hotel_city"):
                existing_hotel = HuzHotelDetail.objects.filter(
                    hotel_for_package=package,
                    hotel_city__iexact=payload.get("hotel_city"),
                ).first()

            if not existing_hotel:
                return Response(
                    {"message": "Hotel not found with the provided detail."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            template_hotel = None
            template_hotel_id = payload.get("hotel_id")
            if template_hotel_id and str(template_hotel_id) != str(existing_hotel.hotel_id):
                template_hotel = self._resolve_hotel_template(template_hotel_id)

            normalized, error_message = self._normalize_hotel_payload(
                payload,
                create=False,
                template_hotel=template_hotel,
            )
            if error_message:
                return Response({"message": error_message}, status=status.HTTP_400_BAD_REQUEST)

            effective_city = normalized.get("hotel_city") or existing_hotel.hotel_city
            city_validation_error = self._validate_hotel_city_nights(package, effective_city)
            if city_validation_error:
                return Response(
                    {"message": city_validation_error},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer = HuzHotelSerializer(existing_hotel, data=normalized, partial=True)
            if not serializer.is_valid():
                return Response(
                    {"message": _first_error_message(serializer)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            existing_hotel = serializer.save()
            if template_hotel and existing_hotel.catalog_hotel_id != template_hotel.hotel_id:
                existing_hotel.catalog_hotel = template_hotel
                existing_hotel.save(update_fields=["catalog_hotel"])

            package_updates = []
            if package.package_stage < 4:
                package.package_stage = 4
                package_updates.append("package_stage")
            if package_updates:
                package.save(update_fields=package_updates)

            return Response(self._serialize_package(package), status=status.HTTP_200_OK)
        except Exception as exc:
            logger.error(f"CreateHuzHotelView - Put: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to update hotel detail. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GetAllHotelsWithImagesView(OperatorPackageBaseView):
    @swagger_auto_schema(
        operation_description=(
            "Get all master hotels for package creation dropdowns. "
            "Returns hotel list with lightweight image placeholders. "
            "Authenticated operators use the Authorization header; "
            "`partner_session_token` is only for documented staff/admin compatibility."
        ),
        manual_parameters=[
            openapi.Parameter(
                "partner_session_token",
                openapi.IN_QUERY,
                description="Optional for documented staff/admin compatibility only.",
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                "city",
                openapi.IN_QUERY,
                description="Optional city filter",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "search",
                openapi.IN_QUERY,
                description="Optional search filter",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "page",
                openapi.IN_QUERY,
                description="Page number (default 1).",
                type=openapi.TYPE_INTEGER,
            ),
            openapi.Parameter(
                "page_size",
                openapi.IN_QUERY,
                description="Page size (default 25, max 100).",
                type=openapi.TYPE_INTEGER,
            ),
        ],
    )
    def get(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=False)
            if error:
                return error

            city_filter = (request.GET.get("city") or "").strip()
            search_filter = (request.GET.get("search") or "").strip()

            catalog_package = HuzBasicDetail.objects.filter(
                huz_token=MASTER_HOTEL_PACKAGE_TOKEN
            ).first()

            if catalog_package:
                queryset = HuzHotelDetail.objects.filter(hotel_for_package=catalog_package)
            else:
                queryset = HuzHotelDetail.objects.all()

            if city_filter:
                queryset = queryset.filter(hotel_city__iexact=city_filter)
            if search_filter:
                queryset = queryset.filter(
                    Q(hotel_city__icontains=search_filter)
                    | Q(hotel_name__icontains=search_filter)
                    | Q(hotel_rating__icontains=search_filter)
                    | Q(room_sharing_type__icontains=search_filter)
                )

            if not catalog_package:
                queryset = _apply_master_hotel_db_dedupe(queryset)

            queryset = queryset.order_by("hotel_city", "hotel_name", "hotel_id").select_related(
                "catalog_hotel"
            ).prefetch_related(
                "hotel_images",
                "catalog_hotel__hotel_images",
            )

            paginator = MasterHotelCatalogPagination()
            paginated_hotels = paginator.paginate_queryset(queryset, request)
            results = [_serialize_catalog_hotel(hotel) for hotel in paginated_hotels]
            return Response(
                {
                    "message": "Hotels fetched successfully.",
                    "count": paginator.page.paginator.count if getattr(paginator, "page", None) else len(results),
                    "next": paginator.get_next_link(),
                    "previous": paginator.get_previous_link(),
                    "results": results,
                    "requested_by": partner.partner_session_token,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            logger.error(f"GetAllHotelsWithImagesView - Get: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to fetch hotels list. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ManageHuzPackageStatusView(AuthenticatedPartnerPackageMutationAPIView):
    @swagger_auto_schema(operation_description="Update package status.")
    def put(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=True)
            if error:
                return error

            payload = _to_mutable_dict(request.data)
            huz_token = self._extract_huz_token(payload=payload)
            requested_status = payload.get("package_status")

            if not huz_token or requested_status in (None, ""):
                return Response(
                    {"message": "Missing user information."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            normalized_status = STATUS_NORMALIZER.get(str(requested_status).strip().lower())
            if not normalized_status:
                return Response(
                    {
                        "message": (
                            "Invalid package status. Allowed values: Initialize, Completed, "
                            "Active, Deactivated, Pending."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package = self._get_partner_package(partner, huz_token)
            if not package:
                return Response(
                    {"message": "Package not found with the provided detail."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if package.package_status == "Block":
                return Response(
                    {"message": "Blocked packages status cannot be changed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package.package_status = normalized_status
            package.save(update_fields=["package_status"])

            return Response(self._serialize_package(package), status=status.HTTP_200_OK)
        except Exception as exc:
            logger.error(f"ManageHuzPackageStatusView - Put: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to update package status. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GetHuzShortPackageByTokenView(OperatorPackageBaseView):
    @swagger_auto_schema(
        operation_description=(
            "Get partner packages with filtering and pagination. "
            "Authenticated operators use the Authorization header; "
            "`partner_session_token` is only for documented staff/admin compatibility."
        ),
        manual_parameters=[
            openapi.Parameter(
                "partner_session_token",
                openapi.IN_QUERY,
                description="Optional for documented staff/admin compatibility only.",
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                "package_type",
                openapi.IN_QUERY,
                description="Package type",
                type=openapi.TYPE_STRING,
                required=True,
            ),
            openapi.Parameter(
                "package_status",
                openapi.IN_QUERY,
                description="Optional status filter (supports Expired).",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "search",
                openapi.IN_QUERY,
                description="Optional text search against package name, token, and description",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter("page", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("page_size", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
    )
    def get(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=False)
            if error:
                return error

            package_type = request.GET.get("package_type")
            if not package_type:
                return Response(
                    {"message": "Missing user or package type information."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            normalized_package_type = _normalize_package_type(package_type)
            if not normalized_package_type:
                return Response(
                    {"message": "Invalid package_type. Use Hajj or Umrah."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            queryset = self._package_list_queryset().filter(
                package_provider=partner,
                package_type=normalized_package_type,
            ).order_by("-created_time")

            search_query = (request.GET.get("search") or "").strip()
            if search_query:
                safe_query = search_query[:100]
                queryset = queryset.filter(
                    Q(package_name__icontains=safe_query)
                    | Q(huz_token__icontains=safe_query)
                    | Q(description__icontains=safe_query)
                )

            package_status = request.GET.get("package_status")
            if package_status and str(package_status).strip().lower() != "all":
                raw_statuses = [
                    item.strip() for item in str(package_status).split(",") if item.strip()
                ]
                requests_expired_status = any(
                    status_item.lower() == "expired" for status_item in raw_statuses
                )
                non_expired_statuses = []
                normalized_statuses = []
                for item in raw_statuses:
                    if item.lower() == "expired":
                        continue
                    normalized_status = STATUS_NORMALIZER.get(item.lower())
                    resolved_status = normalized_status or item
                    normalized_statuses.append(resolved_status)
                    non_expired_statuses.append(resolved_status)

                if requests_expired_status and non_expired_statuses:
                    return Response(
                        {"message": "Expired status cannot be combined with other package statuses."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                if requests_expired_status:
                    queryset = _apply_expired_package_filter(queryset)
                elif normalized_statuses:
                    queryset = queryset.filter(package_status__in=normalized_statuses)

            queryset = _annotate_package_rating_summary(queryset)
            paginator = CustomPagination()
            paginated_queryset = paginator.paginate_queryset(queryset, request)
            serializer = OperatorHuzPackageSerializer(
                paginated_queryset,
                many=True,
                context={"package_rating_cache": {}, "request": request},
            )
            return paginator.get_paginated_response(serializer.data)
        except Exception as exc:
            logger.error(f"GetHuzShortPackageByTokenView: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to fetch packages list. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GetHuzPackageDetailByTokenView(OperatorPackageBaseView):
    @swagger_auto_schema(
        operation_description=(
            "Get package detail by token. Authenticated operators use the "
            "Authorization header; `partner_session_token` is only for documented "
            "staff/admin compatibility."
        ),
        manual_parameters=[
            openapi.Parameter(
                "partner_session_token",
                openapi.IN_QUERY,
                description="Optional for documented staff/admin compatibility only.",
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                "huz_token",
                openapi.IN_QUERY,
                description="Package token",
                type=openapi.TYPE_STRING,
                required=True,
            ),
        ],
    )
    def get(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=False)
            if error:
                return error

            huz_token = self._extract_huz_token(request=request)
            if not huz_token:
                return Response(
                    {"message": "Missing package or user information."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            package = (
                self._package_queryset()
                .filter(package_provider=partner)
                .filter(_build_package_token_query(huz_token))
                .first()
            )

            if not package:
                return Response(
                    {"message": "Package do not exist."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            serializer = OperatorHuzPackageSerializer(
                [package],
                many=True,
                context={"package_rating_cache": {}, "request": request},
            )
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as exc:
            logger.error(f"GetHuzPackageDetailByTokenView: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to fetch packages detail. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GetPartnersOverallPackagesStatisticsView(OperatorPackageBaseView):
    @swagger_auto_schema(
        operation_description=(
            "Get overall package statistics for a partner. Authenticated operators "
            "use the Authorization header; `partner_session_token` is only for "
            "documented staff/admin compatibility."
        ),
        manual_parameters=[
            openapi.Parameter(
                "partner_session_token",
                openapi.IN_QUERY,
                description="Optional for documented staff/admin compatibility only.",
                type=openapi.TYPE_STRING,
                required=False,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        try:
            partner, error = self._get_partner(request, require_active=False)
            if error:
                return error

            requested_statuses = [status_name for status_name, _ in HuzBasicDetail.PACKAGE_STATUS_CHOICES]
            counts = {status_name: 0 for status_name in requested_statuses}

            package_count = (
                HuzBasicDetail.objects.filter(
                    package_provider=partner,
                    package_type__in=SUPPORTED_PACKAGE_TYPES,
                )
                .values("package_status")
                .annotate(total_count=Count("huz_id"))
            )

            for item in package_count:
                status_name = item.get("package_status")
                if status_name:
                    counts[status_name] = item.get("total_count", 0)

            return Response(counts, status=status.HTTP_200_OK)
        except Exception as exc:
            logger.error(f"GetPartnersOverallPackagesStatisticsView: {exc}", exc_info=True)
            return Response(
                {"message": "Failed to fetch overall statistics. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
