from pathlib import Path

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from common.logs_file import logger

from .. import manage_bookings as legacy_manage_bookings
from ..request_serializers import (
    BookingPaymentPhotoUploadRequestSerializer,
    validate_serializer_or_raise,
)
from ..serializers import DetailBookingSerializer
from ..services import record_booking_payment_photo_uploads


PaidAmountByTransactionNumberView = legacy_manage_bookings.PaidAmountByTransactionNumberView
DeleteAmountTransactionPhotoView = legacy_manage_bookings.DeleteAmountTransactionPhotoView


ALLOWED_PAYMENT_UPLOAD_TYPES = {
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".pdf": {"application/pdf"},
    ".doc": {"application/msword"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
}
MAX_PAYMENT_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024


def _payment_upload_error(message):
    raise ValidationError({"message": message, "transaction_photo": [message]})


def _validate_payment_upload_files(files):
    if not files:
        _payment_upload_error("transaction_photo: At least one file is required.")

    for uploaded_file in files:
        extension = Path(uploaded_file.name or "").suffix.lower()
        if extension not in ALLOWED_PAYMENT_UPLOAD_TYPES:
            _payment_upload_error("transaction_photo: Unsupported file type.")

        if uploaded_file.size > MAX_PAYMENT_UPLOAD_SIZE_BYTES:
            _payment_upload_error("transaction_photo: File exceeds the 10 MB limit.")

        content_type = (getattr(uploaded_file, "content_type", "") or "").lower()
        allowed_content_types = ALLOWED_PAYMENT_UPLOAD_TYPES[extension]
        if content_type and content_type not in allowed_content_types:
            _payment_upload_error("transaction_photo: File content type does not match the extension.")


class PaidAmountTransactionPhotoView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_description="Upload transaction photos and record a payment transaction for a booking",
        manual_parameters=[
            openapi.Parameter(
                "transaction_photo",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                description="Transaction photo or file",
                required=True,
            ),
            openapi.Parameter(
                "session_token",
                openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                description="Session token of the user",
                required=True,
            ),
            openapi.Parameter(
                "booking_number",
                openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                description="Booking number",
                required=True,
            ),
            openapi.Parameter(
                "transaction_amount",
                openapi.IN_FORM,
                type=openapi.TYPE_NUMBER,
                description="Transaction amount",
                required=True,
            ),
            openapi.Parameter(
                "transaction_type",
                openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                description="Transaction type: full or minimum",
                required=True,
            ),
        ],
        responses={
            201: openapi.Response(
                "Transaction photo uploaded and payment transaction created successfully",
                DetailBookingSerializer(many=False),
            ),
            400: "Bad Request: Missing or invalid input data",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, Partner, or Package not found.",
            500: "Server error: Internal server error.",
        },
    )
    def post(self, request, *args, **kwargs):
        try:
            files = request.FILES.getlist("transaction_photo")
            _validate_payment_upload_files(files)

            input_serializer = BookingPaymentPhotoUploadRequestSerializer(data=request.data)
            validated_data = validate_serializer_or_raise(input_serializer)
            booking = record_booking_payment_photo_uploads(validated_data, files)
            serializer = DetailBookingSerializer(booking, context={"request": request})
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except APIException:
            raise
        except Exception as exc:
            logger.error("Post - PaidAmountTransactionPhotoView: %s", str(exc))
            return Response(
                {"message": "Failed to update payment request. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
