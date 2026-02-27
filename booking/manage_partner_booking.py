from django.db.models import Sum, Count, Q
from django.utils.dateparse import parse_date
from rest_framework.views import APIView
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from common.utility import CustomPagination, validate_required_fields, check_file_format_and_size, save_file_in_directory, delete_file_from_directory, send_objection_email, send_booking_documents_email
from common.logs_file import logger
from partners.models import PartnerProfile, HuzBasicDetail
from .models import Booking, BookingObjections, PassportValidity, DocumentsStatus, BookingDocuments, PartnersBookingPayment, BookingRatingAndReview, BookingComplaints, BookingAirlineDetail, BookingHotelAndTransport
from .serializers import ShortBookingSerializer, DetailBookingSerializer, PartnersBookingPaymentSerializer, BookingComplaintsSerializer, PartnerRatingSerializer
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi


def extract_partner_session_token(request):
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


class IsAdminOrPartnerSessionToken(BasePermission):
    """
    Booking partner endpoints are session-token based in this backend.
    Keep staff access for admin workflows, and allow partner requests that
    include partner_session_token in query/body.
    """

    message = "Authentication credentials were not provided."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and getattr(user, "is_staff", False):
            return True
        return bool(extract_partner_session_token(request))


VALID_BOOKING_STATUSES = (
    "Pending",
    "Active",
    "Completed",
    "Closed",
    "Objection",
    "Report",
    "Rejected",
)

BOOKING_STATUS_NORMALIZER = {
    status_name.lower(): status_name for status_name in VALID_BOOKING_STATUSES
}
VALID_BOOKING_UPDATE_STATUSES = ("Active", "Completed")
VALID_BOOKING_DOCUMENT_TYPES = ("eVisa", "airline", "hotel", "transport")
VALID_ARRANGEMENT_DETAIL_TYPES = ("Hotel", "Transport")
VALID_COMPLAINT_STATUSES = ("Open", "InProgress", "Solved", "Close")

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
    "passport_for_booking_number",
    "booking_token",
)

BOOKING_DETAIL_SELECT_RELATED = (
    "order_by",
    "order_to",
    "package_token",
    "package_token__package_provider",
)
BOOKING_DETAIL_PREFETCH_RELATED = (
    "order_by__mailing_session",
    "order_to__company_of_partner",
    "order_to__mailing_of_partner",
    "package_token__airline_for_package",
    "objection_for_booking",
    "passport_for_booking_number",
    "booking_token",
    "status_for_booking",
    "document_for_booking_token",
    "user_document_for_booking_token",
    "airline_for_booking",
    "hotel_or_transport_for_booking",
    "rating_for_booking",
)


def get_partner_bookings_queryset(include_detail_relations=False):
    if include_detail_relations:
        return Booking.objects.select_related(*BOOKING_DETAIL_SELECT_RELATED).prefetch_related(
            *BOOKING_DETAIL_PREFETCH_RELATED
        )
    return Booking.objects.select_related(*BOOKING_LIST_SELECT_RELATED).prefetch_related(
        *BOOKING_LIST_PREFETCH_RELATED
    )


def get_partner_booking_detail(partner, booking_number):
    return (
        get_partner_bookings_queryset(include_detail_relations=True)
        .filter(order_to=partner, booking_number=booking_number)
        .first()
    )


def normalize_document_type(document_for):
    normalized = str(document_for or "").strip().lower()
    return BOOKING_DOCUMENT_TYPE_NORMALIZER.get(normalized, "")


def normalize_arrangement_detail_type(detail_for):
    normalized = str(detail_for or "").strip().lower()
    return ARRANGEMENT_DETAIL_TYPE_NORMALIZER.get(normalized, "")


def can_update_booking_documents(booking_detail):
    return booking_detail.booking_status in VALID_BOOKING_UPDATE_STATUSES


def normalize_complaint_status(value):
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    return COMPLAINT_STATUS_NORMALIZER.get(normalized, "")


def finalize_booking_if_all_documents_completed(booking_detail, doc, package_detail, partner):
    is_complete = all(getattr(doc, flag, False) for flag in COMPLETE_BOOKING_STATUS_FLAGS)
    if not is_complete:
        return False

    if booking_detail.booking_status != "Completed":
        booking_detail.booking_status = "Completed"
        booking_detail.save(update_fields=["booking_status"])

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
            openapi.Parameter('booking_status', openapi.IN_QUERY, description="Booking status", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('booking_number', openapi.IN_QUERY, description="Booking number search filter", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Page size", type=openapi.TYPE_INTEGER)
        ],
        responses={
            200: openapi.Response('Success', ShortBookingSerializer(many=True)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or booking not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            # Extract the session token from the query parameters
            partner_session_token = request.GET.get('partner_session_token')
            booking_status = request.GET.get('booking_status')
            booking_number = str(request.GET.get('booking_number') or "").strip()
            if not partner_session_token or not booking_status:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            # Validate booking status
            normalized_booking_status = BOOKING_STATUS_NORMALIZER.get(str(booking_status).strip().lower())
            if not normalized_booking_status:
                return Response(
                    {"message": f"Invalid booking_status. Must be one of: {', '.join(VALID_BOOKING_STATUSES)}."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Find the partner user with the provided session token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            bookings = (
                get_partner_bookings_queryset(include_detail_relations=False)
                .filter(order_to=user, booking_status=normalized_booking_status)
                .order_by('-order_time')
            )

            if booking_number:
                bookings = bookings.filter(booking_number__icontains=booking_number)

            paginator = CustomPagination()
            paginated_packages = paginator.paginate_queryset(bookings, request)
            serialized_package = ShortBookingSerializer(paginated_packages, many=True)
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
            partner_session_token = request.GET.get('partner_session_token')
            booking_number = request.GET.get('booking_number')

            # Check for required parameters
            if not partner_session_token or not booking_number:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve user by session token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve booking by user and booking number
            booking = get_partner_booking_detail(user, booking_number)
            if not booking:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Serialize and return booking data
            serialized_package = DetailBookingSerializer(booking)
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
            booking_detail = get_partner_booking_detail(partner, data.get('booking_number'))
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the provided booking status is valid
            requested_status = str(data.get('booking_status', '')).strip()
            normalized_status = {
                'active': 'Active',
                'objection': 'Objection',
            }.get(requested_status.lower())
            if not normalized_status:
                return Response({"message": "Invalid booking status. Booking status should be 'Active' or 'Objection'."}, status=status.HTTP_400_BAD_REQUEST)

            # Only allow updates to bookings with 'Pending' status
            if booking_detail.booking_status == "Pending":
                if normalized_status == "Objection":
                    BookingObjections.objects.create(
                        remarks_or_reason=request.data.get('partner_remarks'),
                        objection_for_booking=booking_detail
                    )
                    user = booking_detail.order_by
                    if user:
                        send_objection_email(user.email, user.name, booking_detail.booking_number, request.data.get('partner_remarks'))

                booking_detail.booking_status = normalized_status
                booking_detail.partner_remarks = request.data.get('partner_remarks')
                booking_detail.save(update_fields=['booking_status', 'partner_remarks'])

                serialized_package = DetailBookingSerializer(booking_detail)
                return Response(serialized_package.data, status=status.HTTP_201_CREATED)

            # Return an error if the booking is not in 'Pending' status
            return Response({"message": "Only pending status can be updated."}, status=status.HTTP_400_BAD_REQUEST)
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
            409: "Conflict: Only bookings with 'Active' or 'Completed' statuses can perform this task.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        files = request.FILES.getlist('document_link')
        document_for = request.data.get('document_for')
        normalized_document_for = normalize_document_type(document_for)
        booking_number = request.data.get('booking_number')
        partner_session_token = request.data.get('partner_session_token')

        # Validate the presence of required data
        if not all([files, document_for, booking_number, partner_session_token]):
            return Response({"message": "Missing file or required information."},
                            status=status.HTTP_400_BAD_REQUEST)

        if normalized_document_for not in VALID_BOOKING_DOCUMENT_TYPES:
            return Response(
                {"message": f"Invalid document_for. Must be one of: {', '.join(VALID_BOOKING_DOCUMENT_TYPES)}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate each file's format and size
        for file in files:
            if not check_file_format_and_size(file):
                return Response({"message": "Invalid file format or size."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve partner profile
        partner = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not partner:
            return Response({"message": "Partner agency detail not found."}, status=status.HTTP_404_NOT_FOUND)

        # Retrieve booking detail
        booking_detail = get_partner_booking_detail(partner, booking_number)
        if not booking_detail:
            return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

        # Check booking status
        if not can_update_booking_documents(booking_detail):
            return Response({"message": "only bookings with 'Active' or 'Completed' statuses can perform this task."}, status=status.HTTP_409_CONFLICT)

        # Retrieve package detail
        package_detail = booking_detail.package_token
        if not package_detail:
            return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

        # Retrieve user profile
        user = booking_detail.order_by
        if not user:
            return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        # logger.error(f"user email here -Post: {str(user.email)}")
        # Process files and update documents and statuses
        try:
            for file in files:
                file_path = save_file_in_directory(file)
                BookingDocuments.objects.create(
                    document_link=file_path,
                    document_for_booking_token=booking_detail,
                    document_for=normalized_document_for
                )

            doc, _ = DocumentsStatus.objects.get_or_create(status_for_booking=booking_detail)

            if normalized_document_for == "eVisa":
                doc.is_visa_completed = True
                doc.save(update_fields=["is_visa_completed"])
                send_booking_documents_email(user.email, user.name, booking_number, "Visa")

            elif normalized_document_for == "airline":
                doc.is_airline_completed = True
                doc.save(update_fields=["is_airline_completed"])
                send_booking_documents_email(user.email, user.name, booking_number, "Airline Tickets")

            finalize_booking_if_all_documents_completed(
                booking_detail=booking_detail,
                doc=doc,
                package_detail=package_detail,
                partner=partner,
            )

            serialized_booking = DetailBookingSerializer(booking_detail)
            return Response(serialized_booking.data, status=status.HTTP_201_CREATED)
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
        booking_number = request.data.get('booking_number')
        document_id = request.data.get('document_id')
        partner_session_token = request.data.get('partner_session_token')

        # Validate the presence of required parameters
        if not all([booking_number, document_id, partner_session_token]):
            return Response({"message": "Missing required information."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve partner profile
        partner = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not partner:
            return Response({"message": "Partner agency not found."}, status=status.HTTP_404_NOT_FOUND)

        # Retrieve booking detail
        booking_detail = get_partner_booking_detail(partner, booking_number)
        if not booking_detail:
            return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

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
            409: "Conflict: Airline details already exist or Only bookings with 'Active' or 'Completed' statuses can perform this task.",
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
            partner = PartnerProfile.objects.filter(
                partner_session_token=request.data.get('partner_session_token')).first()
            if not partner:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve booking details using partner and booking number
            booking_detail = get_partner_booking_detail(partner, request.data.get('booking_number'))
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # # Retrieve client details from booking details
            # client_detail = booking_detail.order_by
            # if not client_detail:
            #     return Response({"message": "Client detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve package details using package token from booking details
            package_detail = booking_detail.package_token
            if not package_detail:
                return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if airline details already exist for the booking
            check_exist = BookingAirlineDetail.objects.filter(airline_for_booking=booking_detail).first()
            if check_exist:
                return Response({"message": "Airline details already exist."}, status=status.HTTP_409_CONFLICT)

            # Check if the booking status allows for adding airline details
            if can_update_booking_documents(booking_detail):
                # Retrieve document status for the booking
                doc, _ = DocumentsStatus.objects.get_or_create(status_for_booking=booking_detail)

                # Create new airline detail entry
                BookingAirlineDetail.objects.create(
                    flight_date=request.data.get('flight_date'),
                    flight_time=request.data.get('flight_time'),
                    flight_from=request.data.get('flight_from'),
                    flight_to=request.data.get('flight_to'),
                    airline_for_booking=booking_detail
                )

                # Mark airline detail as completed in document status
                doc.is_airline_detail_completed = True
                doc.save(update_fields=["is_airline_detail_completed"])

                finalize_booking_if_all_documents_completed(
                    booking_detail=booking_detail,
                    doc=doc,
                    package_detail=package_detail,
                    partner=partner,
                )

                # Serialize and return the updated booking details
                serialized_package = DetailBookingSerializer(booking_detail)
                return Response(serialized_package.data, status=status.HTTP_201_CREATED)

            # If booking status is not 'Active' or 'Completed', return an error response
            return Response({"message": "Only bookings with 'Active' or 'Completed' statuses can perform this task."}, status=status.HTTP_409_CONFLICT)

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
            409: "Conflict: Only bookings with 'Active' or 'Completed' statuses can perform this task.",
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

            partner = PartnerProfile.objects.filter(partner_session_token=data.get('partner_session_token')).first()
            if not partner:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            booking_detail = get_partner_booking_detail(partner, data.get('booking_number'))
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve the existing airline details for the booking
            airline_detail = BookingAirlineDetail.objects.filter(
                airline_for_booking=booking_detail,
                booking_airline_id=data.get('booking_airline_id')
            ).first()
            if not airline_detail:
                return Response({"message": "Airline details not found."}, status=status.HTTP_404_NOT_FOUND)

            if can_update_booking_documents(booking_detail):
                # Update the airline detail fields with the new data
                airline_detail.flight_date = data.get('flight_date')
                airline_detail.flight_time = data.get('flight_time')
                airline_detail.flight_from = data.get('flight_from')
                airline_detail.flight_to = data.get('flight_to')
                airline_detail.save()

                serialized_package = DetailBookingSerializer(booking_detail)
                return Response(serialized_package.data, status=status.HTTP_200_OK)

            return Response({"message": "Only bookings with 'Active' or 'Completed' statuses can be managed."}, status=status.HTTP_409_CONFLICT)

        except Exception as e:
            logger.error(f"BookingAirlineDetailsView - Put: {str(e)}")
            return Response({"message": "Failed to update record. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BookingHotelAndTransportDetailsView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]

    @swagger_auto_schema(
        operation_description="Add hotel and transport details for a booking.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['partner_session_token', 'booking_number', 'jeddah_name', 'jeddah_number', 'mecca_name', 'mecca_number', 'madinah_name', 'madinah_number', 'comment_1',  'comment_2', 'detail_for'],
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Partner session token'),
                'jeddah_name': openapi.Schema(type=openapi.TYPE_STRING, description='Contact Name for Jeddah hotel'),
                'jeddah_number': openapi.Schema(type=openapi.TYPE_STRING, description='Contact Number for Jeddah hotel'),
                'mecca_name': openapi.Schema(type=openapi.TYPE_STRING, description='Contact Name for Mecca hotel'),
                'mecca_number': openapi.Schema(type=openapi.TYPE_STRING, description='Contact Number for Mecca hotel'),
                'madinah_name': openapi.Schema(type=openapi.TYPE_STRING, description='Contact Name for Madinah hotel'),
                'madinah_number': openapi.Schema(type=openapi.TYPE_STRING, description='Contact Number for Madinah hotel'),
                'comment_1': openapi.Schema(type=openapi.TYPE_STRING, description='Additional comment or note 1'),
                'comment_2': openapi.Schema(type=openapi.TYPE_STRING, description='Additional comment or note 2'),
                'detail_for': openapi.Schema(type=openapi.TYPE_STRING, description='Detail type (Hotel or Transport)'),
            },
        ),
        responses={
            201: openapi.Response('Hotel or transport details created successfully', DetailBookingSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User, Booking detail, Client detail, Package detail, or Record already exists",
            409: "Conflict: Only bookings with 'Active' or 'Completed' statuses can perform this task.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.data
            required_fields = ['partner_session_token', 'booking_number', 'jeddah_name', 'jeddah_number', 'mecca_name', 'mecca_number', 'madinah_name', 'madinah_number', 'comment_1',  'comment_2', 'detail_for']

            # Check for missing required fields
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            normalized_detail_for = normalize_arrangement_detail_type(data.get('detail_for'))
            if normalized_detail_for not in VALID_ARRANGEMENT_DETAIL_TYPES:
                return Response(
                    {"message": f"Invalid detail_for. Must be one of: {', '.join(VALID_ARRANGEMENT_DETAIL_TYPES)}."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Retrieve the user based on the provided session token
            partner = PartnerProfile.objects.filter(partner_session_token=data.get('partner_session_token')).first()
            if not partner:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve booking details using the user and booking number
            booking_detail = get_partner_booking_detail(partner, data.get('booking_number'))
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve client details using the session token from booking details
            client_detail = booking_detail.order_by
            if not client_detail:
                return Response({"message": "Client detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the record already exists for the given detail type
            check_exist = BookingHotelAndTransport.objects.filter(
                hotel_or_transport_for_booking=booking_detail,
                detail_for=normalized_detail_for
            ).first()
            if check_exist:
                return Response({"message": "Record already exists."}, status=status.HTTP_409_CONFLICT)

            # Retrieve package details using the package token from booking details
            package_detail = booking_detail.package_token
            if not package_detail:
                return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the booking status allows for adding hotel or transport details
            if can_update_booking_documents(booking_detail):
                # Retrieve the document status for the booking
                doc, _ = DocumentsStatus.objects.get_or_create(status_for_booking=booking_detail)
                BookingHotelAndTransport.objects.create(
                    jeddah_name=data.get('jeddah_name'),
                    jeddah_number=data.get('jeddah_number'),
                    mecca_name=data.get('mecca_name'),
                    mecca_number=data.get('mecca_number'),
                    madinah_name=data.get('madinah_name'),
                    madinah_number=data.get('madinah_number'),
                    comment_1=data.get('comment_1'),
                    comment_2=data.get('comment_2'),
                    detail_for=normalized_detail_for,
                    hotel_or_transport_for_booking=booking_detail
                )
                send_booking_documents_email(
                    client_detail.email,
                    client_detail.name,
                    booking_detail.booking_number,
                    normalized_detail_for,
                )
                # send_email_notification(client_detail.email, booking_detail.booking_number, data.get('detail_for'))

                # Update document status and send confirmation email based on the detail type
                if normalized_detail_for == "Hotel":
                    doc.is_hotel_completed = True
                    doc.save(update_fields=["is_hotel_completed"])
                elif normalized_detail_for == "Transport":
                    doc.is_transport_completed = True
                    doc.save(update_fields=["is_transport_completed"])

                finalize_booking_if_all_documents_completed(
                    booking_detail=booking_detail,
                    doc=doc,
                    package_detail=package_detail,
                    partner=partner,
                )

                # Serialize and return the updated booking details
                serialized_package = DetailBookingSerializer(booking_detail)
                return Response(serialized_package.data, status=status.HTTP_201_CREATED)

            # Return an error response if the booking status is not 'Active' or 'Completed'
            return Response({"message": "Only bookings with 'Active' or 'Completed' statuses can be managed."}, status=status.HTTP_409_CONFLICT)

        except Exception as e:
            # Log the error and return a generic error response
            logger.error(f"BookingHotelAndTransportDetailsView: {str(e)}")
            return Response({"message": "Failed to add record. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Update hotel or transport details for a booking",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'hotel_or_transport_id': openapi.Schema(type=openapi.TYPE_STRING, description='ID of the hotel or transport detail'),
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Partner session token'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'jeddah_name': openapi.Schema(type=openapi.TYPE_STRING, description='Name of the Jeddah hotel/transport'),
                'jeddah_number': openapi.Schema(type=openapi.TYPE_STRING, description='Contact number for Jeddah hotel/transport'),
                'mecca_name': openapi.Schema(type=openapi.TYPE_STRING, description='Name of the Mecca hotel/transport'),
                'mecca_number': openapi.Schema(type=openapi.TYPE_STRING, description='Contact number for Mecca hotel/transport'),
                'madinah_name': openapi.Schema(type=openapi.TYPE_STRING, description='Name of the Madinah hotel/transport'),
                'madinah_number': openapi.Schema(type=openapi.TYPE_STRING, description='Contact number for Madinah hotel/transport'),
                'comment_1': openapi.Schema(type=openapi.TYPE_STRING, description='First comment'),
                'comment_2': openapi.Schema(type=openapi.TYPE_STRING, description='Second comment'),
                'detail_for': openapi.Schema(type=openapi.TYPE_STRING, description='Detail type (Hotel/Transport)')
            },
            required=['hotel_or_transport_id', 'partner_session_token', 'booking_number', 'jeddah_name', 'jeddah_number', 'mecca_name',
                      'mecca_number', 'madinah_name', 'madinah_number', 'comment_1', 'comment_2', 'detail_for']
        ),
        responses={
            200: openapi.Response('Hotel or transport details updated successfully', DetailBookingSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User, Booking detail, Client detail, Package detail, or Record not exists",
            409: "Conflict: Only bookings with 'Active' or 'Completed' statuses can perform this task.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            data = request.data
            required_fields = ['hotel_or_transport_id', 'partner_session_token', 'booking_number', 'jeddah_name', 'jeddah_number',
                               'mecca_name', 'mecca_number', 'madinah_name', 'madinah_number', 'comment_1', 'comment_2',
                               'detail_for']

            # Check for missing required fields
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            normalized_detail_for = normalize_arrangement_detail_type(data.get('detail_for'))
            if normalized_detail_for not in VALID_ARRANGEMENT_DETAIL_TYPES:
                return Response(
                    {"message": f"Invalid detail_for. Must be one of: {', '.join(VALID_ARRANGEMENT_DETAIL_TYPES)}."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Retrieve partner profile using session token
            partner = PartnerProfile.objects.filter(partner_session_token=data.get('partner_session_token')).first()
            if not partner:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve booking details using partner and booking number
            booking_detail = get_partner_booking_detail(partner, data.get('booking_number'))
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if hotel or transport details exist for the booking
            detail_exists = BookingHotelAndTransport.objects.filter(hotel_or_transport_for_booking=booking_detail,
                                                                    hotel_or_transport_id=data.get('hotel_or_transport_id'),
                                                                    detail_for=normalized_detail_for).first()
            if not detail_exists:
                return Response({"message": "Details not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the booking status allows for managing details
            if can_update_booking_documents(booking_detail):
                detail_exists.jeddah_name = data.get('jeddah_name')
                detail_exists.jeddah_number = data.get('jeddah_number')
                detail_exists.mecca_name = data.get('mecca_name')
                detail_exists.mecca_number = data.get('mecca_number')
                detail_exists.madinah_name = data.get('madinah_name')
                detail_exists.madinah_number = data.get('madinah_number')
                detail_exists.comment_1 = data.get('comment_1')
                detail_exists.comment_2 = data.get('comment_2')
                detail_exists.detail_for = normalized_detail_for
                detail_exists.save()

                serialized_booking = DetailBookingSerializer(booking_detail)
                return Response(serialized_booking.data, status=status.HTTP_200_OK)

            return Response({"message": "Only bookings with 'Active' or 'Completed' statuses can be managed."}, status=status.HTTP_409_CONFLICT)

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

            # Initialize dictionary to hold star rating counts
            total_star_counts = {}

            # Retrieve the count of ratings for each star level (5 to 1)
            for star in range(5, 0, -1):
                star_count = BookingRatingAndReview.objects.filter(
                    rating_for_partner=user,
                    partner_total_stars=star
                ).aggregate(total_count=Count('rating_id'))

                total_star_counts[f'total_star_{star}'] = star_count['total_count'] if star_count['total_count'] is not None else 0

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
            openapi.Parameter('huz_token', openapi.IN_QUERY, description="Token of the package for which ratings are to be fetched", type=openapi.TYPE_STRING, required=True)
        ],
        responses={
            200: openapi.Response('Success', PartnerRatingSerializer(many=True)),
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

            # Check for missing required parameters
            if not partner_session_token or not huz_token:
                return Response({"message": "Missing user or package info."},status=status.HTTP_400_BAD_REQUEST)

            # Retrieve partner profile using session token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve package detail using package token and partner profile
            package_detail = HuzBasicDetail.objects.filter(huz_token=huz_token, package_provider=user).first()
            if not package_detail:
                return Response({"message": "Package detail not found for the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve ratings for the package
            check_rating = BookingRatingAndReview.objects.filter(rating_for_package=package_detail)

            if check_rating.exists():
                serialized_bookings = PartnerRatingSerializer(check_rating, many=True)
                return Response(serialized_bookings.data, status=status.HTTP_200_OK)

            return Response({"message": "No ratings found for this package."}, status=status.HTTP_404_NOT_FOUND )

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

            # Calculate the total star counts for each rating (1 to 5 stars)
            total_star_counts = {}
            for star in range(5, 0, -1):
                star_count = BookingRatingAndReview.objects.filter(
                    rating_for_partner=user,
                    rating_for_package=package_detail,
                    partner_total_stars=star
                ).aggregate(total_count=Count('rating_id'))

                total_star_counts[f'total_package_star_{star}'] = star_count['total_count'] if star_count['total_count'] is not None else 0

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
                    complaint_status_counts[raw_status] = item['total_count']

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
            partner_session_token = request.GET.get('partner_session_token')
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

            # Retrieve the partner user using the session token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            complaints = (
                BookingComplaints.objects.select_related(
                    'complaint_by_user',
                    'complaint_for_booking',
                    'complaint_for_package',
                    'complaint_for_partner',
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
            serialized_package = BookingComplaintsSerializer(paginated_packages, many=True)
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
                    'Initialize': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'Paid': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'Confirm': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'Documents': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'Pending': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'Active': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'Completed': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'Closed': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'Rejected': openapi.Schema(type=openapi.TYPE_INTEGER),
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
            partner_session_token = request.GET.get('partner_session_token')
            if not partner_session_token:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            booking_status = ['Initialize', 'Paid', 'Confirm', 'Objection', 'Pending', 'Active', 'Completed', 'Closed', 'Report', 'Rejected']
            bookings_count = Booking.objects.filter(order_to=user).values(
                'booking_status').annotate(total_count=Count('booking_id')).order_by('booking_status')

            booking_status_counts = {statuses: 0 for statuses in booking_status}
            for item in bookings_count:
                booking_status_counts[item['booking_status']] = item['total_count']

            return Response(booking_status_counts, status=status.HTTP_200_OK)

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
            partner_session_token = request.GET.get('partner_session_token')
            year = request.GET.get('year')
            if not partner_session_token or not year:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            booking_status = ['Completed', 'Closed', 'Report']
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
            404: "User not found for the provided session token or no payment records found.",
            500: "An unexpected error occurred."
        }
    )
    def get(self, request):

        try:
            partner_session_token = request.GET.get('partner_session_token')
            if not partner_session_token:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found for the provided session token."},
                                status=status.HTTP_404_NOT_FOUND)

            partner_payments = PartnersBookingPayment.objects.filter(payment_for_partner=user)
            if not partner_payments:
                return Response({"message": "No payment records found for the user."}, status=status.HTTP_404_NOT_FOUND)

            serialized_payments = PartnersBookingPaymentSerializer(partner_payments, many=True)
            return Response(serialized_payments.data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error in PartnersBookingPaymentView: {str(e)}", exc_info=True)
            return Response({"message": "An unexpected error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CloseBookingView(APIView):
    permission_classes = [IsAdminOrPartnerSessionToken]
    @swagger_auto_schema(
        operation_description="Update the booking status to 'Closed' for a given booking number.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['booking_number', 'partner_session_token'],
            properties={
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number to close'),
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Partner session token'),
            },
        ),
        responses={
            200: openapi.Response(description="Booking status successfully updated to 'Closed'", schema=DetailBookingSerializer),
            400: openapi.Response(description="Bad Request - Missing or invalid fields"),
            401: 'Unauthorized: Partner permissions required',
            404: openapi.Response(description="Not Found - Partner or booking detail not found"),
            409: openapi.Response(description="Conflict - Booking status is not 'Completed' or 'Report'"),
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
            booking_detail = get_partner_booking_detail(partner_detail, data.get('booking_number'))
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the booking status is not 'Completed' or 'Report'
            if booking_detail.booking_status not in ['Completed', 'Report']:
                return Response(
                    {"message": "Booking can only be closed if its status is 'Completed' or 'Report'."},
                    status=status.HTTP_409_CONFLICT
                )

            # Update booking status to 'Closed'
            booking_detail.booking_status = 'Closed'
            booking_detail.save()

            # Serialize updated booking details
            serialized_booking = DetailBookingSerializer(booking_detail)
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
        operation_description="Update the booking status to 'Report' for the associated passport.",
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
                description="Booking status updated to 'Report' and passport report_rabbit set to True."),
            400: openapi.Response(description="Bad Request - Missing required fields."),
            401: "Unauthorized: Admin permissions required.",
            404: openapi.Response(description="Not Found - Booking, partner, or passport not found."),
            409: openapi.Response(description="Conflict - Booking status must be 'Completed' or 'Closed'."),
            500: openapi.Response(description="Internal Server Error."),
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            # Extract required data from the request
            passport_id = request.data.get('passport_id')
            partner_session_token = request.data.get('partner_session_token')
            booking_number = request.data.get('booking_number')

            # Validate input parameters
            if not passport_id or not partner_session_token or not booking_number:
                return Response(
                    {"message": "Missing required data fields."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Retrieve partner based on partner_session_token
            partner = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not partner:
                return Response({"message": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve the booking associated with the booking_number
            booking = get_partner_booking_detail(partner, booking_number)
            if not booking:
                return Response({"message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the booking status is either 'Completed' or 'Closed'
            if booking.booking_status not in ['Completed', 'Closed', 'Report']:
                return Response(
                    {"message": "Booking status must be 'Completed', 'Closed' or 'Report' to be updated."},
                    status=status.HTTP_409_CONFLICT
                )

            passport = PassportValidity.objects.filter(
                passport_id=passport_id,
                passport_for_booking_number=booking
            ).first()
            if not passport:
                return Response({"message": "Passport not found for the provided booking."},
                                status=status.HTTP_404_NOT_FOUND)

            # Update the report_rabbit field to True
            passport.report_rabbit = True
            passport.save()

            booking.booking_status = 'Report'
            booking.save()

            # Return success response
            serialized_booking = DetailBookingSerializer(booking)
            return Response(serialized_booking.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return an internal server error response
            logger.error(f"Error in ReportBooking: {str(e)}")
            return Response(
                {"message": "Failed to update booking status and passport. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
