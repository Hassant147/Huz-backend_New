from rest_framework.views import APIView
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework import status, serializers
from .models import UserProfile, Wallet, UserOTP, MailingDetail, SubscribeUser
from .serializers import UserProfileSerializer, UserOTPSerializer, MailingDetailSerializer, SubscribeSerializer
import requests
from .utility import random_six_digits, generate_token, save_notification, delete_file_from_directory, save_file_in_directory, check_photo_format_and_size, validate_required_fields, send_verification_email, new_user_welcome_email, user_subscribe_email
from .logs_file import logger
from datetime import datetime
from django.db import transaction
from rest_framework.parsers import MultiPartParser, FormParser
from decouple import config
from django.utils import timezone
from datetime import timedelta
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

GENDER_CHOICES = ['male', 'female', 'non_binary', 'prefer_not_to_say', 'other']


class SubscribeAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Subscribe a user with a valid email.",
        request_body=SubscribeSerializer,
        responses={
            200: "Success: Successfully subscribed.",
            400: "Bad Request: Invalid input data",
            401: "Unauthorized: Admin permissions required",
            500: "Server Error: Internal server error"
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            # Deserialize the incoming request data
            serializer = SubscribeSerializer(data=request.data)

            # Check if data is valid
            if serializer.is_valid():
                email = serializer.validated_data['email']

                # Check if the email is already subscribed
                if SubscribeUser.objects.filter(email=email).exists():
                    return Response(
                        {"error": "This email is already subscribed."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Save the new subscription data
                subscribe_user = SubscribeUser.objects.create(email=email)
                if subscribe_user:
                    user_subscribe_email(email)
                return Response(
                    {"message": "Successfully subscribed."},
                    status=status.HTTP_201_CREATED
                )
            else:
                logger.error("Subscribe: An unexpected error occurred: %s", str(serializer.errors))
                # Return validation errors if serializer is not valid
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            # Handle unexpected errors
            logger.error("Subscribe: An unexpected error occurred: %s", str(e))
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SendOTPSMSAPIView(APIView):
    permission_classes = [IsAdminUser]
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
            response = requests.post(url)

            # Check response status
            if response.status_code == 200:
                # Check if OTP record exists for this phone number, update or create accordingly
                user_otp, created = UserOTP.objects.get_or_create(phone_number=phone_number)
                user_otp.otp_password = otp_code
                user_otp.save()

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
    permission_classes = [IsAdminUser]
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
                serialized_user = UserProfileSerializer(user)
                if country_code != '+92':
                    return Response({"message": "Sending OTP to this country is not allowed."}, status=status.HTTP_400_BAD_REQUEST)
                otp_code = random_six_digits()
                sender = 'VTvOTP'
                otp_message = f'HajjUmrah.co One-Time Password: {otp_code}. Please do not share OTP with anyone.'
                API_Key = config('APIKey')
                url = f'https://api.veevotech.com/v3/sendsms?hash={API_Key}&receivernum={phone_number_1}&sendernum={sender}&textmessage={otp_message}'
                response = requests.post(url)
                if response.status_code == 200:
                    user_otp, created = UserOTP.objects.get_or_create(phone_number=phone_number_1)
                    user_otp.otp_password = otp_code
                    user_otp.save()

                    new_user_welcome_email(user.email, user.name)
                    return Response(serialized_user.data, status=status.HTTP_201_CREATED)
                else:
                    return Response({"message": "Failed to send OTP. Please try again later."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            else:
                first_error_field = next(iter(serializer.errors))
                first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

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


class UpdateUserNameView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'name': openapi.Schema(type=openapi.TYPE_STRING, description='New name for the user')
            },
            required=['session_token', 'name']
        ),
        responses={
            200: openapi.Response("Success: User name updated successfully", UserProfileSerializer),
            400: "Bad Request: Missing required information or user not recognized",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not recognized",
            500: "Server Error: Internal server error"
        },
        operation_description="Update the name of a user"
    )
    def put(self, request, *args, **kwargs):
        try:
            # Extract the session_token and name from the request data
            session_token = request.data.get('session_token')
            name = request.data.get('name')

            # Validate the presence of session_token and name
            if not session_token or not name:
                return Response({"message": "Missing user information or new name."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve the user profile associated with the session_token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not recognized."}, status=status.HTTP_404_NOT_FOUND)

            # Update the user's name
            user.name = name
            user.save()

            # Serialize the updated user profile
            serialized_user = UserProfileSerializer(user)
            return Response(serialized_user.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return a server error response
            logger.error("UpdateUserNameView: %s", str(e))
            return Response({"message": "Failed to update new name. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UpdateUserGenderView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'user_gender': openapi.Schema(type=openapi.TYPE_STRING, description='Gender for the user', enum=GENDER_CHOICES)
            },
            required=['session_token', 'user_gender']
        ),
        responses={
            200: openapi.Response("Success: User gender updated successfully", UserProfileSerializer),
            400: "Bad Request: Missing required information or user not recognized",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not recognized",
            500: "Server Error: Internal server error"
        },
        operation_description="Update the gender of a user"
    )
    def put(self, request, *args, **kwargs):
        try:
            # Extract the session_token and user_gender from the request data
            session_token = request.data.get('session_token')
            user_gender = request.data.get('user_gender')

            # Validate the presence of session_token and user_gender
            if not session_token or not user_gender:
                return Response({"message": "Missing user gender or user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Validate that the user_gender is one of the allowed choices
            if user_gender not in GENDER_CHOICES:
                return Response({"message": "Invalid user gender choice."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve the user profile associated with the session_token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not recognized."}, status=status.HTTP_404_NOT_FOUND)

            # Update the user's gender
            user.user_gender = user_gender
            user.save()

            # Serialize the updated user profile
            serialized_user = UserProfileSerializer(user)
            return Response(serialized_user.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return a server error response
            logger.error("UpdateUserNameView: %s", str(e))
            return Response({"message": "Failed to update user gender. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UpdateEmailAddressView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'email': openapi.Schema(type=openapi.TYPE_STRING, description='New email address for the user')
            },
            required=['session_token', 'email']
        ),
        responses={
            200: openapi.Response("Success: User email updated successfully", UserProfileSerializer),
            400: "Bad Request: Missing required information, invalid email format, or user not recognized",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not recognized",
            500: "Server Error: Internal server error"
        },
        operation_description="Update the email address of a user"
    )
    def put(self, request, *args, **kwargs):
        try:
            # Extract the session_token and email from the request data
            session_token = request.data.get('session_token')
            email = request.data.get('email')

            # Validate the presence of session_token and email
            if not session_token or not email:
                return Response({"message": "Missing user email or user information."}, status=status.HTTP_400_BAD_REQUEST)

            serializer = UserProfileSerializer(data=request.data)
            try:
                # Validate the email format using serializer validation
                serializer.validate_email(email)
            except serializers.ValidationError as e:
                return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve the user profile
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not recognized."}, status=status.HTTP_404_NOT_FOUND)

            # Update the user's email
            user.email = email
            user.save()
            serialized_user = UserProfileSerializer(user)
            return Response(serialized_user.data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error("UpdateEmailAddressView: %s", str(e))
            return Response({"message": "Failed to update user email. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManageUserAddressView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('session_token', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True, description='Session token of the user'),
        ],
        responses={
            200: openapi.Response("Success: Address details retrieved successfully", MailingDetailSerializer),
            400: "Bad Request: Missing required information or user not recognized",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: Address details not found for the user",
            500: "Server Error: Internal server error"
        },
        operation_description="Retrieve the address details of a user"
    )
    def get(self, request):
        try:
            # Extract session_token & validate
            session_token = self.request.GET.get('session_token', None)
            if not session_token:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieving the user profile
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve the address details associated with the user
            address_detail = MailingDetail.objects.filter(mailing_session=user).first()
            if not address_detail:
                return Response({"message": "Address detail not exist."}, status=status.HTTP_404_NOT_FOUND)

            # Serialize the address detail
            serialized_address = MailingDetailSerializer(address_detail)
            return Response(serialized_address.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return a server error response
            logger.error("Get - ManageUserAddressView: %s", str(e))
            return Response({"message": "Failed to fetch address detail. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'street_address': openapi.Schema(type=openapi.TYPE_STRING, description='Street address'),
                'address_line2': openapi.Schema(type=openapi.TYPE_STRING, description='Address line 2'),
                'city': openapi.Schema(type=openapi.TYPE_STRING, description='City'),
                'state': openapi.Schema(type=openapi.TYPE_STRING, description='State'),
                'country': openapi.Schema(type=openapi.TYPE_STRING, description='Country'),
                'postal_code': openapi.Schema(type=openapi.TYPE_STRING, description='Postal code')
            },
            required=['session_token', 'street_address', 'city', 'state', 'country', 'postal_code']
        ),
        responses={
            201: openapi.Response("Created: Address details saved successfully", MailingDetailSerializer),
            400: "Bad Request: Missing required information or invalid data format",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not recognized",
            409: "Conflict: Address detail already exists for the user",
            500: "Server Error: Internal server error"
        },
        operation_description="Save address details for a user"
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.data
            session_token = data.get('session_token')

            # Validate session_token presence
            if not session_token:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Validate required fields
            required_fields = ['street_address', 'city', 'state', 'country', 'postal_code']
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Retrieve user profile
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."},
                                status=status.HTTP_404_NOT_FOUND)

            # Check if address detail already exists for the user
            if MailingDetail.objects.filter(mailing_session=user).exists():
                return Response({"message": "Address detail already exists."}, status=status.HTTP_409_CONFLICT)

            # Serialize and save mailing detail
            serializer = MailingDetailSerializer(data=data)
            if serializer.is_valid():
                user.is_address_exist = True
                user.save()
                serializer.save(mailing_session=user)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                # Extracting first error message with field name
                first_error_field = next(iter(serializer.errors))
                first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

        except KeyError as e:
            # Handle missing key error
            logger.error(f"Missing key error in Post - ManageUserAddressView: {str(e)}")
            return Response({"message": f"Missing key: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            # Log the error and return a server error response
            logger.error("Post - ManageUserAddressView: %s", str(e))
            return Response({"message": "Failed to add address detail. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'address_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='ID of the address detail'),
                'street_address': openapi.Schema(type=openapi.TYPE_STRING, description='Street address'),
                'address_line2': openapi.Schema(type=openapi.TYPE_STRING, description='Address line 2'),
                'city': openapi.Schema(type=openapi.TYPE_STRING, description='City'),
                'state': openapi.Schema(type=openapi.TYPE_STRING, description='State'),
                'country': openapi.Schema(type=openapi.TYPE_STRING, description='Country'),
                'postal_code': openapi.Schema(type=openapi.TYPE_STRING, description='Postal code')
            },
            required=['session_token', 'address_id']
        ),
        responses={
            200: openapi.Response("Success: Address details updated successfully", MailingDetailSerializer),
            400: "Bad Request: Missing required information or invalid data format",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not recognized or address detail not found",
            500: "Server Error: Internal server error"
        },
        operation_description="Update address details for a user"
    )
    def put(self, request, *args, **kwargs):
        try:
            data = request.data
            session_token = data.get('session_token')
            address_id = data.get('address_id')

            # Validate session_token and address_id presence
            if not session_token or not address_id:
                return Response({"message": "Missing user information or address ID."}, status=status.HTTP_400_BAD_REQUEST)

            # Validate required fields
            required_fields = ['address_id', 'street_address', 'city', 'state', 'country', 'postal_code']
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Retrieve user profile
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve address detail based on address_id and mailing_session (user)
            address_detail = MailingDetail.objects.filter(mailing_session=user, address_id=address_id).first()
            if not address_detail:
                return Response({"message": "Address detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Validate and update mailing detail
            serializer = MailingDetailSerializer(address_detail, data=data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_200_OK)
            else:
                # Extracting first error message with field name
                first_error_field = next(iter(serializer.errors))
                first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

        except KeyError as e:
            # Handle missing key error
            logger.error(f"Missing key error in Put - ManageUserAddressView: {str(e)}")
            return Response({"message": f"Missing key: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            # Log the error and return a server error response
            logger.error("Put - ManageUserAddressView: %s", str(e))
            return Response({"message": "Failed to update address detail. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SendEmailOTPView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Send or Resend OTP to the user's email based on session token",
        request_body=openapi.Schema(type=openapi.TYPE_OBJECT, required=['session_token'], properties={'session_token': openapi.Schema(type=openapi.TYPE_STRING, description="Session token of the user")}),
        responses={
            200: "OTP sent successfully.",
            400: "Bad Request: Missing or invalid input data",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not found with the provided session token",
            500: "Server Error: Internal server error"
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            # Check if session_token is provided
            session_token = request.data.get('session_token')
            if not session_token:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve user profile based on session_token
            user = UserProfile.objects.filter(session_token=session_token).first()

            # Check if user exists
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            otp = random_six_digits()
            result = send_verification_email(user.email, user.name, otp)
            # Check if email sending was successful
            if result == "Success":
                user.email_otp = otp
                user.save()
                return Response({"message": "OTP sent successfully."}, status=status.HTTP_200_OK)
            else:
                return Response({"message": "Failed to send OTP. Please try again later."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            # Adding logs
            logger.error("ResendOTPView error: %s", str(e))
            return Response({"message": "Failed to send otp. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MatchEmailOTPView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'otp': openapi.Schema(type=openapi.TYPE_STRING, description='OTP Password of user'),
            },
            required=['session_token', 'otp']
        ),
        responses={
            200: openapi.Response("Success: OTP Matched successfully", UserProfileSerializer),
            400: "Bad Request: Missing required information or invalid data format",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not recognized",
            500: "Server Error: Internal server error"
        },
        operation_description="Update address details for a user"
    )
    def put(self, request, *args, **kwargs):
        try:
            # Check if session_token is provided
            session_token = request.data.get('session_token')
            email_otp = request.data.get('otp')
            if not email_otp or not session_token:
                return Response({"message": "Missing OTP or user information."}, status=status.HTTP_400_BAD_REQUEST)

            #  Retrieve user profile based on session_token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # If OTP has expired (within 2 minute)
            time_difference = timezone.now() - user.otp_time
            if time_difference > timedelta(minutes=2):
                return Response({"message": "OTP has expired. Please request a new OTP."}, status=status.HTTP_400_BAD_REQUEST)

            # Matching otp
            if user.email_otp != email_otp:
                return Response({"message": "Invalid OTP."}, status=status.HTTP_400_BAD_REQUEST)

            with transaction.atomic():
                user.is_email_verified = True
                user.email_otp = ""
                user.save()

            # Returning user profile
            serialized_user = UserProfileSerializer(user)
            return Response(serialized_user.data, status=status.HTTP_200_OK)
        except Exception as e:
            # adding logs
            logger.error("MatchEmailOTPView error: %s", str(e))
            return Response({"message": "Failed to verify otp. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
