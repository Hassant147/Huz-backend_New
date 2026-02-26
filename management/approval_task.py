from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from rest_framework.permissions import IsAdminUser
from django.db import transaction
from django.db.models import Max, Prefetch, Q
from django.core.cache import cache
from datetime import timedelta
from common.models import UserProfile
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
from common.utility import send_company_approval_email, send_payment_verification_email, preparation_email
from booking.models import Booking, PartnersBookingPayment, Payment, PassportValidity
from booking.serializers import DetailBookingSerializer, PartnersBookingPaymentSerializer, AdminPaidBookingSerializer
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


def _invalidate_management_cache():
    cache.delete_many(MANAGEMENT_CACHE_KEYS)


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
        operation_description="Confirm payment and update booking status",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='User session token'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'payment_id': openapi.Schema(type=openapi.TYPE_STRING, description='Optional payment Id')
            },
            required=['session_token', 'booking_number']
        ),
        responses={
            200: openapi.Response('Booking status updated successfully', DetailBookingSerializer(many=False)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: Booking detail or user detail not found.",
            409: "Conflict: Only bookings with 'Paid' status can be confirmed.",
            500: "Server Error: Internal server error."
        }
    )
    @transaction.atomic
    def put(self, request, *args, **kwargs):
        try:
            # Extract required data from the request
            session_token = (request.data.get("session_token") or "").strip()
            booking_number = (request.data.get("booking_number") or "").strip()
            payment_id = (request.data.get("payment_id") or "").strip()

            # Check for missing required fields
            if not session_token or not booking_number:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

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

            current_status = (booking_detail.booking_status or "").strip()
            if current_status not in {"Paid", "Confirm"}:
                return Response({"message": "Only bookings with 'Paid' status can be confirmed."},
                                status=status.HTTP_409_CONFLICT)

            payment_queryset = Payment.objects.select_for_update().filter(booking_token=booking_detail)
            if payment_id:
                check_payment = payment_queryset.filter(payment_id=payment_id).first()
            else:
                check_payment = payment_queryset.exclude(payment_status="Approved").order_by('-transaction_time').first()
                if not check_payment:
                    check_payment = payment_queryset.order_by('-transaction_time').first()

            if not check_payment:
                return Response({"message": "Payment record not found."}, status=status.HTTP_404_NOT_FOUND)

            booking_already_confirmed = (
                current_status == "Confirm" and booking_detail.is_payment_received
            )
            payment_already_approved = ((check_payment.payment_status or "").strip() == "Approved")

            if not payment_already_approved:
                check_payment.payment_status = "Approved"
                check_payment.save(update_fields=['payment_status'])

            transitioned_to_confirm = False
            if not booking_already_confirmed:
                booking_detail.booking_status = "Confirm"
                booking_detail.is_payment_received = True
                booking_detail.save(update_fields=['booking_status', 'is_payment_received'])
                transitioned_to_confirm = True

            # Create only missing PassportValidity records to keep endpoint idempotent.
            required_passports = max(int(booking_detail.adults or 0), 0)
            existing_passports = PassportValidity.objects.filter(passport_for_booking_number=booking_detail).count()
            missing_passports = max(required_passports - existing_passports, 0)
            if missing_passports:
                PassportValidity.objects.bulk_create(
                    [PassportValidity(passport_for_booking_number=booking_detail) for _ in range(missing_passports)]
                )

            if transitioned_to_confirm:
                send_payment_verification_email(user.email, user.name, booking_number)
                preparation_email(user.email, user.name, booking_detail.package_token.package_type)

            # Serialize the updated booking detail and return response
            serialized_booking = DetailBookingSerializer(booking_detail)
            _invalidate_management_cache()
            return Response(serialized_booking.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Error in ConfirmPaymentView: {str(e)}")
            return Response({"message": "Failed to update payment status. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class FetchPaidBookingView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Fetch all bookings with status 'Paid'",
        responses={
            200: openapi.Response('Successfully retrieved booking details', AdminPaidBookingSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: Booking detail not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            cached_payload = cache.get(CACHE_KEY_PAID_BOOKINGS)
            if cached_payload is not None:
                return Response(cached_payload, status=status.HTTP_200_OK)

            # Retrieve all bookings with status "Paid"
            booking_details_qs = Booking.objects.filter(booking_status="Paid").select_related(
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
                'booking_token',
            )

            booking_details = list(booking_details_qs)

            # Check if any bookings were found
            if booking_details:
                # Serialize the booking details
                serialized_booking = AdminPaidBookingSerializer(booking_details, many=True)
                response_payload = serialized_booking.data
                cache.set(CACHE_KEY_PAID_BOOKINGS, response_payload, MANAGEMENT_CACHE_TIMEOUT_SECONDS)
                return Response(response_payload, status=status.HTTP_200_OK)

            # Return response if no bookings were found
            return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

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
        operation_description="Fetch all bookings payment which are not 'Paid' to partners",
        responses={
            200: openapi.Response('Successfully retrieved partner receive able details', PartnersBookingPaymentSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: payment detail not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            cached_payload = cache.get(CACHE_KEY_PARTNER_RECEIVABLES)
            if cached_payload is not None:
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
            )
            receive_able_details = list(receive_able_qs)

            # Check if any bookings were found
            if receive_able_details:
                # Serialize the booking details
                serialized_booking = PartnersBookingPaymentSerializer(receive_able_details, many=True)
                response_payload = serialized_booking.data
                cache.set(CACHE_KEY_PARTNER_RECEIVABLES, response_payload, MANAGEMENT_CACHE_TIMEOUT_SECONDS)
                return Response(response_payload, status=status.HTTP_200_OK)

            # Return response if no bookings were found
            return Response({"message": "Payment detail not found."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Error in FetchPaidBookingView: {str(e)}")
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

        # Ensure the booking status is either "Completed" or "Closed"
        if booking_detail.booking_status not in ["Completed", "Closed"]:
            return Response({"message": "Only completed or closed case payments can be processed."},
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
