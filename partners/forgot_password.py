from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAdminUser, AllowAny
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import PartnerProfile, PasswordResetToken
from common.utility import forgot_password_email, hash_password
from .serializers import PartnerProfileSerializer
from django.utils import timezone
from datetime import timedelta
from rest_framework import status, serializers
from common.logs_file import logger
import re
from django.conf import settings


class ForgotEmail(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Send a password reset link to the partner's email if the email exists.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'email': openapi.Schema(type=openapi.TYPE_STRING, description='Email address of the user')
            }
        ),
        responses={
            200: openapi.Response(
                description="Password reset link sent successfully.",
                examples={
                    "application/json": {
                        "message": "Password reset link has been sent to your email."
                    }
                }
            ),
            400: openapi.Response(description="Bad request, invalid email format or missing email."),
            404: openapi.Response(description="No account found with this email.")
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            serializer = PartnerProfileSerializer(data=request.data)
            email = (request.data.get('email') or '').strip().lower()  # Use .data to handle JSON in request body
            if not email:
                return Response({"message": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

            try:
                serializer.validate_email(email)
            except serializers.ValidationError as e:
                return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

            partner = PartnerProfile.objects.filter(email__iexact=email).first()
            if partner:
                reset_token = PasswordResetToken.objects.create(partner=partner)
                base_url = settings.OPERATOR_PANEL_BASE_URL.rstrip('/')
                reset_url = f"{base_url}/reset-password/{reset_token.token}"
                forgot_password_email(partner.email, reset_url)  # Make sure email sending function works properly
                return Response({"message": "Password reset link has been sent to your email."}, status=status.HTTP_200_OK)
            else:
                return Response({"message": "No account found with this email."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            # add in logs
            logger.error("ForgotEmail error: %s", str(e))
            return Response({"message": "Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UpdatePassword(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Reset the password for the user using a valid token.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'token': openapi.Schema(type=openapi.TYPE_STRING, description='Password reset token'),
                'password': openapi.Schema(type=openapi.TYPE_STRING, description='New password for the user')
            }
        ),
        responses={
            200: openapi.Response(
                description="Password reset successful.",
                examples={
                    "application/json": {
                        "message": "Your password has been successfully reset!"
                    }
                }
            ),
            400: openapi.Response(
                description="Bad request, invalid password or missing data."
            ),
            404: openapi.Response(
                description="Token not found or expired."
            )
        }
    )
    def put(self, request, *args, **kwargs):
        serializer = PartnerProfileSerializer(data=request.data)
        try:
            # Get the token and password from request data
            token = request.data.get('token')
            password = request.data.get('password')

            if not token or not password:
                return Response({"message": "Token and password are required."}, status=status.HTTP_400_BAD_REQUEST)

            # Fetch the reset token from the database
            reset_token = PasswordResetToken.objects.filter(token=token, is_used=False).first()

            if not reset_token:
                return Response({"message": "Invalid or expired token."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the token has expired.
            if reset_token.created_at < timezone.now() - timedelta(minutes=settings.PASSWORD_RESET_EXPIRY_MINUTES):
                return Response({"message": "This link has expired. Please request a new one."}, status=status.HTTP_400_BAD_REQUEST)

            try:
                serializer.validate_password(password)
            except serializers.ValidationError as e:
                return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

            partner = reset_token.partner
            partner.password = hash_password(password)
            partner.save()

            # Mark the reset token as used
            reset_token.is_used = True
            reset_token.save()
            return Response({"message": "Your password has been successfully reset!"}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error("UpdatePassword error: %s", str(e))
            return Response({"message": "Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
