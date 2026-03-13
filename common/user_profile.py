from rest_framework.views import APIView
from rest_framework.permissions import IsAdminUser, AllowAny
from rest_framework.response import Response
from rest_framework import status, serializers
from .models import UserProfile, Wallet, UserOTP
from .serializers import UserProfileSerializer, UserOTPSerializer
import requests
from .utility import random_six_digits, generate_token, save_notification, delete_file_from_directory, save_file_in_directory, check_photo_format_and_size, validate_required_fields, send_verification_email, new_user_welcome_email
from .logs_file import logger
from .throttling import OTPAnonRateThrottle, OTPUserRateThrottle
from datetime import datetime
from django.db import transaction
from rest_framework.parsers import MultiPartParser, FormParser
from decouple import config
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

GENDER_CHOICES = ['male', 'female', 'non_binary', 'prefer_not_to_say', 'other']
SMS_GATEWAY_TIMEOUT_SECONDS = 6
SMS_GATEWAY_MAX_ATTEMPTS = 2
SMS_GATEWAY_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
DEV_OTP_BYPASS_ENABLED = config('DEV_OTP_BYPASS_ENABLED', cast=bool, default=False)
DEV_OTP_BYPASS_CODE = '123456'
LOCAL_DEV_HOSTS = {'127.0.0.1', 'localhost'}


class OTPDeliveryError(Exception):
    pass


def send_sms_gateway_request(url):
    last_exception = None

    for attempt in range(1, SMS_GATEWAY_MAX_ATTEMPTS + 1):
        try:
            response = requests.post(url, timeout=SMS_GATEWAY_TIMEOUT_SECONDS)
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


def is_dev_otp_bypass_enabled(request=None):
    host = ''

    if request is not None:
        try:
            host = request.get_host().split(':')[0].lower()
        except Exception:
            host = ''

    return settings.DEBUG or DEV_OTP_BYPASS_ENABLED or host in LOCAL_DEV_HOSTS


def upsert_user_otp(phone_number, otp_code):
    user_otp, _ = UserOTP.objects.get_or_create(phone_number=phone_number)
    user_otp.otp_password = otp_code
    user_otp.save()
    return user_otp


def user_exists_for_phone(phone_number):
    country_code = phone_number[:-10]
    local_phone_number = phone_number[-10:]
    return UserProfile.objects.filter(
        country_code=country_code,
        phone_number=local_phone_number,
    ).exists()


class SendOTPSMSAPIView(APIView):
    permission_classes = [IsAdminUser]
    throttle_classes = [OTPAnonRateThrottle, OTPUserRateThrottle]
    @swagger_auto_schema(
        operation_description="Send OTP SMS to User",
        request_body=UserOTPSerializer,
        responses={
            200: "Success: SMS Sent successfully",
            400: "Bad Request: Invalid input data",
            401: "Unauthorized: Admin permissions required",
            500: "Server Error: Internal server error"
        }
    )
    def post(self, request):
        phone_number = request.data.get('phone_number')
        # Checking Required parameters
        if not phone_number:
            return Response({"message": "Phone number is required."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = UserOTPSerializer(data=request.data)
        # Validate phone number format
        try:
            serializer.validate_phone_number(phone_number)
        except serializers.ValidationError as e:
            return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

        # Check country code
        country_code = phone_number[:-10]
        if country_code != '+92':
            return Response({"message": "Sending OTP to this country is not allowed."}, status=status.HTTP_400_BAD_REQUEST)

        if is_dev_otp_bypass_enabled(request):
            upsert_user_otp(phone_number, DEV_OTP_BYPASS_CODE)
            logger.warning("DEV OTP bypass enabled for send_otp_sms on %s.", phone_number)
            return Response({"message": "OTP sent successfully."}, status=status.HTTP_200_OK)

        # Getting a random 6-digit OTP from Utility
        otp_code = random_six_digits()

        sender = 'VTvOTP'
        # SMS message
        otp_message = f'HajjUmrah.co One-Time Password: {otp_code}. Please do not share OTP with anyone.'

        # Construct API URL with credentials
        API_Key = config('APIKey')  # Getting APIKey from environment file
        url = f'https://api.veevotech.com/v3/sendsms?hash={API_Key}&receivernum={phone_number}&sendernum={sender}&textmessage={otp_message}'

        try:
            # Send SMS using requests module
            response = send_sms_gateway_request(url)

            # Check response status
            if response.status_code == 200:
                upsert_user_otp(phone_number, otp_code)

                return Response({"message": "OTP sent successfully."}, status=status.HTTP_200_OK)
            else:
                return Response({"message": "Failed to send OTP. Please try again later."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except requests.exceptions.RequestException as e:
            return Response({"message": "An error occurred while sending OTP."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MatchOTPSMSAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Match OTP",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'phone_number': openapi.Schema(type=openapi.TYPE_STRING, description='Phone number of the user'),
                'otp_password': openapi.Schema(type=openapi.TYPE_STRING, description='OTP password'),
            },
            required=['phone_number', 'otp_password'],
        ),
        responses={
            200: "Success: OTP matched successfully",
            400: "Bad Request: Invalid input data",
            401: "Unauthorized: Admin permissions required",
            500: "Server Error: Internal server error"
        }
    )
    def put(self, request):
        phone_number = request.data.get('phone_number')
        otp_entered = request.data.get('otp_password')

        # Checking Required Parameters
        if not phone_number or not otp_entered:
            return Response({"message": "Phone number and OTP are required."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = UserOTPSerializer(data=request.data)
        # Validate phone number format
        try:
            serializer.validate_phone_number(phone_number)
        except serializers.ValidationError as e:
            return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

        if is_dev_otp_bypass_enabled(request) and otp_entered == DEV_OTP_BYPASS_CODE:
            if not user_exists_for_phone(phone_number):
                return Response({"message": "OTP not found for this phone number."}, status=status.HTTP_400_BAD_REQUEST)

            UserOTP.objects.filter(phone_number=phone_number).delete()
            logger.warning("DEV OTP bypass accepted for verify_otp on %s.", phone_number)
            return Response({"message": "OTP matched successfully."}, status=status.HTTP_200_OK)

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

        return Response({"message": "OTP matched successfully."}, status=status.HTTP_200_OK)


class IsUserExistView(APIView):
    permission_classes = [AllowAny]
    @swagger_auto_schema(
        operation_description="Check if a user exists by phone number.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'phone_number': openapi.Schema(type=openapi.TYPE_STRING),
            },
            required=['phone_number'],
        ),
        responses={
            200: openapi.Response(description="User exists", schema=UserProfileSerializer),
            404: openapi.Response(description="User does not exist", schema=openapi.Schema(type=openapi.TYPE_OBJECT)),
            400: "Bad Request: Invalid input data",
            500: "Server Error: Internal server error"
        }
    )
    def post(self, request, *args, **kwargs):
        # Deserialize request data using UserProfileSerializer
        serializer = UserProfileSerializer(data=request.data)

        # checking that phone_number is provided
        phone_number = request.data.get('phone_number')
        if not phone_number:
            return Response({"message": "Phone number is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Validate the phone_number format using serializer validation
            serializer.validate_phone_number(phone_number)
        except serializers.ValidationError as e:
            return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

        # Split into country_code and phone_number parts
        country_code, phone_number = phone_number[:-10], phone_number[-10:]

        try:
            user = UserProfile.objects.get(country_code=country_code, phone_number=phone_number)
            serializer = UserProfileSerializer(user)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except UserProfile.DoesNotExist:
            return Response({"message": "User with this phone number does not exist."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            # creating Logs
            logger.error("IsUserExistView: An unexpected error occurred: %s", str(e))
            return Response({"message": "An unexpected error occurred. Please try again later."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CreateMemberProfileView(APIView):
    permission_classes = [IsAdminUser]

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
            201: openapi.Response("Successful creation", UserProfileSerializer),
            401: "Unauthorized: Admin permissions required",
            400: "Bad Request: Invalid input data",
            500: "Server Error: Internal server error"
        }
    )
    def post(self, request):
        serializer = UserProfileSerializer(data=request.data)

        # Check if phone_number and email are provided
        phone_number = request.data.get('phone_number')
        phone_number_1 = phone_number
        email = request.data.get('email')
        if not phone_number or not email:
            return Response({"message": "Phone number and email are required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Validate phone_number and email format using serializer validation
            serializer.validate_phone_number(phone_number)
            serializer.validate_email(email)
        except serializers.ValidationError as e:
            return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

        # Extract country_code and phone_number from phone_number
        country_code = phone_number[:-10]
        phone_number = phone_number[-10:]

        if country_code != '+92':
            return Response({"message": "Sending OTP to this country is not allowed."}, status=status.HTTP_400_BAD_REQUEST)

        # Generate session token
        key = int(phone_number) * 52955917
        token_key = str(country_code) + str(key)
        token_key = generate_token(token_key)

        # Check if user with session_token already exists
        if UserProfile.objects.filter(session_token=token_key).exists():
            return Response({"message": "User with this phone number already exists."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            data = request.data
            data['session_token'] = token_key
            data['country_code'] = country_code
            data['phone_number'] = phone_number
            data['account_status'] = "Active"

            serializer = UserProfileSerializer(data=data)
            # Add user record into DB
            if serializer.is_valid():
                with transaction.atomic():
                    user = serializer.save()
                    self.handle_new_user_setup(user, data)

                    if is_dev_otp_bypass_enabled(request):
                        upsert_user_otp(phone_number_1, DEV_OTP_BYPASS_CODE)
                        logger.warning("DEV OTP bypass enabled for signup flow on %s.", phone_number_1)
                    else:
                        otp_code = random_six_digits()
                        sender = 'VTvOTP'
                        otp_message = f'HajjUmrah.co One-Time Password: {otp_code}. Please do not share OTP with anyone.'
                        API_Key = config('APIKey')
                        url = f'https://api.veevotech.com/v3/sendsms?hash={API_Key}&receivernum={phone_number_1}&sendernum={sender}&textmessage={otp_message}'
                        response = send_sms_gateway_request(url)
                        if response.status_code != 200:
                            raise OTPDeliveryError("Failed to send OTP. Please try again later.")
                        upsert_user_otp(phone_number_1, otp_code)

                serialized_user = UserProfileSerializer(user)
                new_user_welcome_email(user.email, user.name)
                return Response(serialized_user.data, status=status.HTTP_201_CREATED)
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
