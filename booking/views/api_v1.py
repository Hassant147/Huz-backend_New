from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from random import randint

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from common.utility import check_file_format_and_size, save_file_in_directory, send_complaint_email

from .bookings import _payload_with_user_session
from ..request_serializers import (
    BookingPaymentCreateRequestSerializer,
    BookingPaymentUpdateRequestSerializer,
    PassportValidityCreateRequestSerializer,
    PassportValidityUpdateRequestSerializer,
    validate_serializer_or_raise,
)
from ..serializers import BookingComplaintsSerializer, BookingRequestSerializer, DetailBookingSerializer
from ..models import BookingComplaints, BookingObjections, BookingRatingAndReview, BookingRequest
from ..services import (
    get_booking_by_identifier_for_user,
    record_booking_payment,
    update_booking_payment,
    update_passport_validation,
    validate_passport,
)
from .bookings import BookingViewSet as BaseBookingViewSet


COMPLAINT_AUDIO_EXTENSIONS = {".aac", ".mp3", ".wav", ".m4a"}
REQUEST_ATTACHMENT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".pdf"}
SUPPORT_FILE_MAX_SIZE_BYTES = 10 * 1024 * 1024


def _normalize_partner_star_rating(value):
    try:
        parsed_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if parsed_value < Decimal("1") or parsed_value > Decimal("5"):
        return None

    rounded = parsed_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if rounded != parsed_value:
        return None

    return int(rounded)


def _build_support_ticket_number():
    return str(randint(1000000000, 9999999999))


def _validate_optional_audio_file(uploaded_file):
    if uploaded_file is None:
        return

    extension = Path(uploaded_file.name or "").suffix.lower()
    if extension not in COMPLAINT_AUDIO_EXTENSIONS:
        raise ValidationError(
            {"message": "Invalid file format or size.", "audio_message": ["Unsupported audio format."]}
        )

    if uploaded_file.size > SUPPORT_FILE_MAX_SIZE_BYTES:
        raise ValidationError(
            {"message": "Invalid file format or size.", "audio_message": ["Audio file exceeds the 10 MB limit."]}
        )


def _validate_optional_request_attachment(uploaded_file):
    if uploaded_file is None:
        return

    extension = Path(uploaded_file.name or "").suffix.lower()
    if extension not in REQUEST_ATTACHMENT_EXTENSIONS:
        raise ValidationError(
            {"message": "Invalid file format or size.", "request_attachment": ["Unsupported attachment format."]}
        )

    if uploaded_file.size > SUPPORT_FILE_MAX_SIZE_BYTES:
        raise ValidationError(
            {
                "message": "Invalid file format or size.",
                "request_attachment": ["Attachment exceeds the 10 MB limit."],
            }
        )


class BookingViewSet(BaseBookingViewSet):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @action(detail=True, methods=["post", "put"], url_path="payments")
    def payments(self, request, pk=None):
        payload, user_profile = _payload_with_user_session(request, request.data)
        booking = get_booking_by_identifier_for_user(
            user_profile,
            pk,
            must_be_future=request.method.lower() == "post",
        )

        payload["booking_number"] = booking.booking_number
        if request.method.lower() == "post":
            input_serializer = BookingPaymentCreateRequestSerializer(data=payload)
            validated_data = validate_serializer_or_raise(input_serializer)
            updated_booking = record_booking_payment(validated_data)
            response_status = status.HTTP_201_CREATED
        else:
            input_serializer = BookingPaymentUpdateRequestSerializer(data=payload)
            validated_data = validate_serializer_or_raise(input_serializer)
            updated_booking = update_booking_payment(validated_data)
            response_status = status.HTTP_200_OK

        serializer = DetailBookingSerializer(updated_booking, context={"request": request})
        return Response(serializer.data, status=response_status)

    @action(detail=True, methods=["post", "put"], url_path="passports")
    def passports(self, request, pk=None):
        payload, user_profile = _payload_with_user_session(request, request.data)
        booking = get_booking_by_identifier_for_user(
            user_profile,
            pk,
            must_be_future=False,
        )

        payload["booking_number"] = booking.booking_number
        if request.method.lower() == "post":
            input_serializer = PassportValidityCreateRequestSerializer(data=payload)
            validated_data = validate_serializer_or_raise(input_serializer)
            updated_booking = validate_passport(validated_data)
            response_status = status.HTTP_201_CREATED
        else:
            input_serializer = PassportValidityUpdateRequestSerializer(data=payload)
            validated_data = validate_serializer_or_raise(input_serializer)
            updated_booking = update_passport_validation(validated_data)
            response_status = status.HTTP_200_OK

        serializer = DetailBookingSerializer(updated_booking, context={"request": request})
        return Response(serializer.data, status=response_status)

    @action(detail=True, methods=["post"], url_path="reviews")
    def reviews(self, request, pk=None):
        payload, user_profile = _payload_with_user_session(request, request.data)
        booking = get_booking_by_identifier_for_user(user_profile, pk, must_be_future=False)

        if BookingRatingAndReview.objects.filter(rating_for_booking=booking).exists():
            return Response(
                {"message": "Rating & review record already exists."},
                status=status.HTTP_409_CONFLICT,
            )

        if booking.booking_status not in ["Completed", "Closed", "Close"]:
            return Response(
                {
                    "message": "Reviews and ratings can only be submitted after your booking is completed or closed."
                },
                status=status.HTTP_409_CONFLICT,
            )

        normalized_partner_stars = _normalize_partner_star_rating(
            payload.get("partner_total_stars")
        )
        if normalized_partner_stars is None:
            return Response(
                {"message": "partner_total_stars must be a whole number between 1 and 5."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        BookingRatingAndReview.objects.create(
            huz_concierge=payload.get("huz_concierge", 0),
            huz_support=payload.get("huz_support", 0),
            huz_platform=payload.get("huz_platform", 0),
            huz_service_quality=payload.get("huz_service_quality", 0),
            huz_response_time=payload.get("huz_response_time", 0),
            huz_comment=payload.get("huz_comment", ""),
            partner_total_stars=normalized_partner_stars,
            partner_comment=payload.get("partner_comment", ""),
            rating_for_booking=booking,
            rating_for_partner=booking.order_to,
            rating_for_package=booking.package_token,
            rating_by_user=user_profile,
        )

        serializer = DetailBookingSerializer(booking, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="complaints")
    def complaints(self, request, pk=None):
        payload, user_profile = _payload_with_user_session(request, request.data)
        booking = get_booking_by_identifier_for_user(user_profile, pk, must_be_future=False)

        complaint_title = str(payload.get("complaint_title") or "").strip()
        complaint_message = str(payload.get("complaint_message") or "").strip()
        if not complaint_title or not complaint_message:
            return Response(
                {"message": "Missing required data fields."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if booking.booking_status not in ["Pending", "Completed", "Active", "Closed", "Close"]:
            return Response(
                {
                    "message": "Complaint can only be raised when the booking status is Pending, Complete, Active, or Closed."
                },
                status=status.HTTP_409_CONFLICT,
            )

        audio_file = request.data.get("audio_message")
        complaint_attachment = request.data.get("complaint_attachment")
        _validate_optional_audio_file(audio_file)
        if complaint_attachment and not check_file_format_and_size(complaint_attachment):
            return Response(
                {"message": "Invalid attachment file format or size."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            complaint = BookingComplaints.objects.create(
                complaint_ticket=_build_support_ticket_number(),
                complaint_status="Open",
                complaint_title=complaint_title,
                complaint_message=complaint_message,
                response_message=payload.get("response_message") or None,
                audio_message=save_file_in_directory(audio_file) if audio_file else None,
                complaint_attachment=(
                    save_file_in_directory(complaint_attachment)
                    if complaint_attachment
                    else None
                ),
                complaint_for_booking=booking,
                complaint_for_partner=booking.order_to,
                complaint_for_package=booking.package_token,
                complaint_by_user=user_profile,
            )

        send_complaint_email(
            booking.order_to.email,
            booking.order_to.name,
            booking.booking_number,
            complaint_title,
        )
        serializer = BookingComplaintsSerializer(complaint, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="requests")
    def requests(self, request, pk=None):
        payload, user_profile = _payload_with_user_session(request, request.data)
        booking = get_booking_by_identifier_for_user(user_profile, pk, must_be_future=False)

        request_title = str(payload.get("request_title") or "").strip()
        request_message = str(payload.get("request_message") or "").strip()
        if not request_title or not request_message:
            return Response(
                {"message": "Missing required fields."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if booking.booking_status not in ["Completed", "Active", "Closed"]:
            return Response(
                {
                    "message": "Request can only be raised when the booking status is Completed, Active or Closed."
                },
                status=status.HTTP_409_CONFLICT,
            )

        request_attachment = request.data.get("request_attachment")
        _validate_optional_request_attachment(request_attachment)

        with transaction.atomic():
            request_record = BookingRequest.objects.create(
                request_ticket=_build_support_ticket_number(),
                request_status="Open",
                request_title=request_title,
                request_message=request_message,
                request_attachment=(
                    save_file_in_directory(request_attachment)
                    if request_attachment
                    else None
                ),
                request_for_booking=booking,
                request_for_partner=booking.order_to,
                request_for_package=booking.package_token,
                request_by_user=user_profile,
            )

        serializer = BookingRequestSerializer(request_record, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["put"],
        url_path=r"objections/(?P<objection_id>[^/.]+)/response",
    )
    def objection_response(self, request, pk=None, objection_id=None):
        payload, user_profile = _payload_with_user_session(request, request.data)
        booking = get_booking_by_identifier_for_user(user_profile, pk, must_be_future=False)

        if booking.booking_status != "Objection":
            return Response(
                {"message": "Invalid booking status. Booking status should be 'Objection'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        objection_document = request.data.get("objection_document")
        client_remarks = str(payload.get("client_remarks") or "").strip()
        if objection_document is None or not client_remarks:
            return Response(
                {
                    "message": "Missing required data fields.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not check_file_format_and_size(objection_document):
            return Response(
                {"message": "Invalid file format or size."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        objection_detail = BookingObjections.objects.filter(
            objection_id=objection_id,
            objection_for_booking=booking,
        ).first()
        if not objection_detail:
            return Response({"message": "Objection detail not found."}, status=status.HTTP_404_NOT_FOUND)

        objection_detail.required_document_for_objection = save_file_in_directory(
            objection_document
        )
        objection_detail.client_remarks = client_remarks
        objection_detail.save()

        booking.booking_status = "Pending"
        booking.save(update_fields=["booking_status"])

        serializer = DetailBookingSerializer(booking, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)
