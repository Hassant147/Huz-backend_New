from rest_framework.views import APIView
from rest_framework.permissions import IsAdminUser, AllowAny
from rest_framework.response import Response
from rest_framework import status, serializers
from .models import UserProfile, Wallet, UserOTP
from .serializers import UserProfileSerializer, UserOTPSerializer
from .phone_utils import (
    find_user_profile_by_phone,
    resolve_phone_identity,
    resolve_signup_phone_identity,
)
import requests
from .utility import random_six_digits, generate_token, save_notification, delete_file_from_directory, save_file_in_directory, check_photo_format_and_size, validate_required_fields, send_verification_email, new_user_welcome_email
from .logs_file import logger
from .throttling import OTPAnonRateThrottle, OTPUserRateThrottle
from datetime import datetime
from django.db import transaction
from rest_framework.parsers import MultiPartParser, FormParser
from decouple import config
from django.utils import timezone
from datetime import timedelta
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

GENDER_CHOICES = ['male', 'female', 'non_binary', 'prefer_not_to_say', 'other']
SMS_GATEWAY_URL = "https://api.veevotech.com/v3/sendsms"
SMS_GATEWAY_TIMEOUT_SECONDS = 6
SMS_GATEWAY_MAX_ATTEMPTS = 2
SMS_GATEWAY_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class OTPDeliveryError(Exception):
    pass


def send_sms_gateway_request(params):
    last_exception = None

    for attempt in range(1, SMS_GATEWAY_MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                SMS_GATEWAY_URL,
                params=params,
                timeout=SMS_GATEWAY_TIMEOUT_SECONDS,
            )
            if (
                response.status_code in SMS_GATEWAY_RETRYABLE_STATUS_CODES
                and attempt < SMS_GATEWAY_MAX_ATTEMPTS
            ):
                logger.warning(
                    "SMS gateway retry due to status code %s (attempt %s/%s).",
                    response.status_code,
                    attempt,
                    SMS_GATEWAY_MAX_ATTEMPTS,
                )
                continue
            return response
        except requests.exceptions.Timeout as exc:
            last_exception = exc
            logger.warning(
                "SMS gateway timeout (attempt %s/%s).",
                attempt,
                SMS_GATEWAY_MAX_ATTEMPTS,
            )
            continue
        except requests.exceptions.RequestException as exc:
            last_exception = exc
            logger.error("SMS gateway request failed: %s", str(exc))
            break

    if last_exception:
        raise last_exception

    raise requests.exceptions.RequestException("SMS gateway request failed.")


def upsert_user_otp(phone_number, otp_code):
    user_otp, _ = UserOTP.objects.get_or_create(phone_number=phone_number)
    user_otp.otp_password = otp_code
    user_otp.save()
    return user_otp


def get_sms_gateway_api_key():
    primary_key = config('SMS_GATEWAY_API_KEY', default='').strip()
    if primary_key:
        return primary_key
    return config('APIKey', default='').strip()


def send_otp_via_sms_gateway(phone_number):
    otp_code = random_six_digits()
    sender = 'VTvOTP'
    otp_message = f'HajjUmrah.co One-Time Password: {otp_code}. Please do not share OTP with anyone.'
    api_key = get_sms_gateway_api_key()

    if not api_key:
        logger.error(
            "SMS gateway API key is missing while sending OTP to %s. Checked SMS_GATEWAY_API_KEY and APIKey.",
            phone_number,
        )
        raise OTPDeliveryError("Failed to send OTP. Please try again later.")

    params = {
        'hash': api_key,
        'receivernum': phone_number,
        'sendernum': sender,
        'textmessage': otp_message,
    }

    try:
        response = send_sms_gateway_request(params)
    except requests.exceptions.RequestException as exc:
        logger.error("OTP delivery request failed for %s: %s", phone_number, str(exc))
        raise OTPDeliveryError("An error occurred while sending OTP.")

    if response.status_code != 200:
        logger.error(
            "OTP delivery failed for %s with gateway status %s and body %r.",
            phone_number,
            response.status_code,
            getattr(response, 'text', ''),
        )
        raise OTPDeliveryError("Failed to send OTP. Please try again later.")

    upsert_user_otp(phone_number, otp_code)
    return otp_code


def get_phone_identity_from_request(request, *, allow_lookup_only=False):
    try:
        if allow_lookup_only:
            return resolve_phone_identity(
                phone_number=request.data.get('phone_number'),
                country_code=request.data.get('country_code'),
                local_phone_number=request.data.get('local_phone_number'),
                country_iso_code=request.data.get('country_iso_code'),
            )

        return resolve_signup_phone_identity(
            phone_number=request.data.get('phone_number'),
            country_code=request.data.get('country_code'),
            local_phone_number=request.data.get('local_phone_number'),
            country_iso_code=request.data.get('country_iso_code'),
        )
    except serializers.ValidationError as exc:
        detail = exc.detail[0] if isinstance(exc.detail, list) else exc.detail
        raise serializers.ValidationError(detail)


def get_user_for_phone_identity(phone_identity):
    return find_user_profile_by_phone(
        phone_number=phone_identity.get('full_phone_number'),
        country_code=phone_identity.get('country_code'),
        local_phone_number=phone_identity.get('local_phone_number'),
    )


def build_verified_user_response(user, message="OTP matched successfully."):
    user.is_phone_verified = True
    user.save(update_fields=['is_phone_verified'])
    serializer = UserProfileSerializer(user)
    return Response(
        {
            "message": message,
            "data": serializer.data,
        },
        status=status.HTTP_200_OK,
    )


class SendOTPSMSAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [OTPAnonRateThrottle, OTPUserRateThrottle]
    @swagger_auto_schema(
        operation_description="Send OTP SMS to User",
        request_body=UserOTPSerializer,
        responses={
            200: "Success: SMS Sent successfully",
            400: "Bad Request: Invalid input data",
            500: "Server Error: Internal server error"
        }
    )
    def post(self, request):
        try:
            phone_identity = get_phone_identity_from_request(request, allow_lookup_only=True)
            phone_number = phone_identity['full_phone_number']
        except serializers.ValidationError as exc:
            return Response({"message": str(exc.detail)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            send_otp_via_sms_gateway(phone_number)
            return Response({"message": "OTP sent successfully."}, status=status.HTTP_200_OK)
        except OTPDeliveryError as exc:
            return Response({"message": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MatchOTPSMSAPIView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Match OTP",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'phone_number': openapi.Schema(type=openapi.TYPE_STRING, description='Phone number of the user'),
                'country_code': openapi.Schema(type=openapi.TYPE_STRING, description='Selected country code'),
                'country_iso_code': openapi.Schema(type=openapi.TYPE_STRING, description='Selected ISO country code'),
                'local_phone_number': openapi.Schema(type=openapi.TYPE_STRING, description='Local phone number'),
                'otp_password': openapi.Schema(type=openapi.TYPE_STRING, description='OTP password'),
            },
            required=['phone_number', 'otp_password'],
        ),
        responses={
            200: "Success: OTP matched successfully",
            400: "Bad Request: Invalid input data",
            500: "Server Error: Internal server error"
        }
    )
    def put(self, request):
        otp_entered = request.data.get('otp_password')

        if not otp_entered:
            return Response({"message": "Phone number and OTP are required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            phone_identity = get_phone_identity_from_request(request, allow_lookup_only=True)
            phone_number = phone_identity['full_phone_number']
        except serializers.ValidationError as exc:
            return Response({"message": str(exc.detail)}, status=status.HTTP_400_BAD_REQUEST)

        # If OTP record exists for the provided phone number
        try:
            user_otp = UserOTP.objects.get(phone_number=phone_number)
        except UserOTP.DoesNotExist:
            return Response({"message": "OTP not found for this phone number."}, status=status.HTTP_400_BAD_REQUEST)

        # If OTP has expired (within 2 minute)
        time_difference = timezone.now() - user_otp.created_time
        if time_difference > timedelta(minutes=5):
            return Response({"message": "OTP has expired. Please request a new OTP."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Matching OTP
        if otp_entered != user_otp.otp_password:
            return Response({"message": "Invalid OTP."}, status=status.HTTP_400_BAD_REQUEST)

        # Deleting the OTP record to ensure it is not reused
        user_otp.delete()

        user = get_user_for_phone_identity(phone_identity)
        if not user:
            return Response({"message": "User with this phone number does not exist."}, status=status.HTTP_404_NOT_FOUND)

        return build_verified_user_response(user)


class IsUserExistView(APIView):
    permission_classes = [AllowAny]
    @swagger_auto_schema(
        operation_description="Check if a user exists by phone number.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'phone_number': openapi.Schema(type=openapi.TYPE_STRING),
                'country_code': openapi.Schema(type=openapi.TYPE_STRING),
                'country_iso_code': openapi.Schema(type=openapi.TYPE_STRING),
                'local_phone_number': openapi.Schema(type=openapi.TYPE_STRING),
            },
            required=['phone_number'],
        ),
        responses={
            200: openapi.Response(description="User exists", schema=openapi.Schema(type=openapi.TYPE_OBJECT)),
            404: openapi.Response(description="User does not exist", schema=openapi.Schema(type=openapi.TYPE_OBJECT)),
            400: "Bad Request: Invalid input data",
            500: "Server Error: Internal server error"
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            phone_identity = get_phone_identity_from_request(request, allow_lookup_only=True)
        except serializers.ValidationError as exc:
            return Response({"message": str(exc.detail)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = get_user_for_phone_identity(phone_identity)
            if not user:
                return Response(
                    {
                        "exists": False,
                        "message": "User with this phone number does not exist.",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(
                {
                    "exists": True,
                    "message": "User exists.",
                },
                status=status.HTTP_200_OK,
            )
        except serializers.ValidationError as exc:
            return Response({"message": str(exc.detail)}, status=status.HTTP_400_BAD_REQUEST)
        except UserProfile.DoesNotExist:
            return Response({"message": "User with this phone number does not exist."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            # creating Logs
            logger.error("IsUserExistView: An unexpected error occurred: %s", str(e))
            return Response({"message": "An unexpected error occurred. Please try again later."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CreateMemberProfileView(APIView):
    permission_classes = [AllowAny]

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [IsAdminUser()]
        return [AllowAny()]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user to be deleted')
            },
            required=['session_token']
        ),
        responses={
            200: "Success: Selected user has been removed.",
            400: "Bad Request: Missing required information.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not found.",
            500: "Server Error: Internal server error"
        },
        operation_description="Delete a member profile"
    )
    def delete(self, request):
        try:
            # Extract the session_token from request data
            session_token = request.data.get('session_token')
            if not session_token:
                return Response({"message": "Missing required information."}, status=status.HTTP_400_BAD_REQUEST)

            # Find the user profile by session_token
            profile = UserProfile.objects.filter(session_token=session_token).first()
            if not profile:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Delete the user profile
            profile.delete()
            return Response({"message": "Selected user has been removed."}, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return a server error response
            logger.error("Delete - CreateMemberProfileView: %s", str(e))
            return Response({"message": "Failed to delete user. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Create User Profile",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'phone_number': openapi.Schema(type=openapi.TYPE_STRING),
                'country_code': openapi.Schema(type=openapi.TYPE_STRING),
                'country_iso_code': openapi.Schema(type=openapi.TYPE_STRING),
                'local_phone_number': openapi.Schema(type=openapi.TYPE_STRING),
                'name': openapi.Schema(type=openapi.TYPE_STRING),
                'email': openapi.Schema(type=openapi.TYPE_STRING),
                'user_type': openapi.Schema(type=openapi.TYPE_STRING),
                'firebase_token': openapi.Schema(type=openapi.TYPE_STRING),
                'web_firebase_token': openapi.Schema(type=openapi.TYPE_STRING),
                'is_notification_allowed': openapi.Schema(type=openapi.TYPE_BOOLEAN),
            },
            required=['phone_number', 'name', 'email', 'user_type'],
        ),
        responses={
            201: openapi.Response("Successful creation", openapi.Schema(type=openapi.TYPE_OBJECT)),
            400: "Bad Request: Invalid input data",
            500: "Server Error: Internal server error"
        }
    )
    def post(self, request):
        email = request.data.get('email')
        if not (request.data.get('phone_number') or request.data.get('local_phone_number')) or not email:
            return Response({"message": "Phone number and email are required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            phone_identity = get_phone_identity_from_request(request)
            serializer = UserProfileSerializer(data=request.data)
            serializer.validate_email(email)
        except serializers.ValidationError as exc:
            detail = exc.detail[0] if isinstance(exc.detail, list) else exc.detail
            return Response({"message": str(detail)}, status=status.HTTP_400_BAD_REQUEST)

        country_code = phone_identity['country_code']
        phone_number = phone_identity['local_phone_number']
        full_phone_number = phone_identity['full_phone_number']

        # Generate session token
        key = int(phone_number[-10:]) * 52955917 if len(phone_number) >= 10 else int(phone_number) * 52955917
        token_key = str(country_code) + str(key)
        token_key = generate_token(token_key)

        if UserProfile.objects.filter(country_code=country_code, phone_number=phone_number).exists():
            return Response({"message": "User with this phone number already exists."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            data = request.data.copy()
            data['session_token'] = token_key
            data['country_code'] = country_code
            data['phone_number'] = phone_number
            data['country_iso_code'] = phone_identity.get('country_iso_code', '')
            data['account_status'] = "Active"

            serializer = UserProfileSerializer(data=data)
            # Add user record into DB
            if serializer.is_valid():
                with transaction.atomic():
                    user = serializer.save()
                    self.handle_new_user_setup(user, data)
                    send_otp_via_sms_gateway(full_phone_number)

                new_user_welcome_email(user.email, user.name)
                return Response(
                    {
                        "message": "Account created successfully. OTP sent successfully.",
                        "data": {
                            "country_code": country_code,
                            "phone_number": phone_number,
                            "email": user.email,
                            "name": user.name,
                        },
                    },
                    status=status.HTTP_201_CREATED,
                )
            else:
                first_error_field = next(iter(serializer.errors))
                first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

        except OTPDeliveryError as e:
            return Response({"message": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            logger.error("CreateMemberProfileView: %s", str(e))
            return Response({"message": "Failed to create user. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def handle_new_user_setup(self, user, data):
        wallet_token = generate_token(f'wallet{datetime.now()}0.0')
        wallet = Wallet.objects.create(
            wallet_code=wallet_token,
            wallet_session=user
        )
        # Save Notification into DB
        title = "Welcome to Hajjumrah.co Family"
        message = "Hajjumrah.co is the world's largest platform offering Hajj, Umrah, and transport packages. Our aim is to provide the best services at competitive rates. \nThank you for joining us."
        save_notification(user, title, message, data.get('firebase_token', ''), data.get('web_firebase_token', ''))


class UploadUserImageView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('session_token', openapi.IN_FORM, type=openapi.TYPE_STRING, description='Session token of the user', required=True),
            openapi.Parameter('user_photo', openapi.IN_FORM, type=openapi.TYPE_FILE, description='User photo file', required=True)
        ],
        responses={
            200: openapi.Response("Success: User photo updated successfully", UserProfileSerializer),
            400: "Bad Request: Missing file or user information, invalid file format or size, or user not recognized",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not recognized.",
            500: "Server Error: Internal server error"
        },
        operation_description="Upload or update user profile photo"
    )
    def put(self, request, *args, **kwargs):
        try:
            # Extract the file and session_token from the request data
            file = request.data.get('user_photo')
            session_token = request.data.get('session_token')

            # Validate the presence of required data
            if not file or not session_token:
                return Response({"message": "Missing file or user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Validate the file format and size
            if not check_photo_format_and_size(file):
                return Response({"message": "Invalid file format or size."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve the user profile associated with the session_token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not recognized."}, status=status.HTTP_404_NOT_FOUND)

            # Delete the old user photo if it exists
            if user.user_photo:
                delete_file_from_directory(user.user_photo.name)

            # Save the new file in the directory and update the user profile
            file_path = save_file_in_directory(file)
            user.user_photo = file_path
            user.save()

            # Serialize the updated user profile
            serialized_user = UserProfileSerializer(user)
            return Response(serialized_user.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return a server error response
            logger.error("UploadUserImageView: %s", str(e))
            return Response({"message": "Failed to upload profile photo. Internal server error."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UpdateFirebaseTokenView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'firebase_token': openapi.Schema(type=openapi.TYPE_STRING, description='Firebase token for mobile devices', nullable=True),
                'web_firebase_token': openapi.Schema(type=openapi.TYPE_STRING, description='Firebase token for web browsers', nullable=True),
            },
            required=['session_token']
        ),
        responses={
            200: openapi.Response("Success: Firebase token updated successfully", UserProfileSerializer),
            400: "Bad Request: Missing required information or user not recognized",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not recognized",
            500: "Server Error: Internal server error"
        },
        operation_description="Update Firebase token for mobile or web browsers"
    )
    def put(self, request, *args, **kwargs):
        try:
            # Extract the data from the request data
            session_token = request.data.get('session_token')
            firebase_token = request.data.get('firebase_token')
            web_firebase_token = request.data.get('web_firebase_token')

            # Validate the presence of the session_token
            if not session_token:
                return Response({"message": "Missing session token."}, status=status.HTTP_400_BAD_REQUEST)

            # Validate that at least one token is given
            if not firebase_token and not web_firebase_token:
                return Response({"message": "Missing firebase token for mobile or web."}, status=status.HTTP_400_BAD_REQUEST)

            # fetching user profile associated with the session_token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not recognized."}, status=status.HTTP_404_NOT_FOUND)

            if firebase_token:
                user.firebase_token = firebase_token

            if web_firebase_token:
                user.web_firebase_token = web_firebase_token

            user.save()

            serialized_user = UserProfileSerializer(user)
            return Response(serialized_user.data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error("UpdateFirebaseTokenView: %s", str(e))
            return Response({"message": "Failed to update firebase token. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
