from rest_framework.views import APIView
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework import status, serializers
from .models import PartnerProfile, IndividualProfile, BusinessProfile, PartnerServices, PartnerMailingDetail, Wallet
from .serializers import PartnerProfileSerializer, PartnerMailingDetailSerializer
from django.db import transaction
import re
from common.logs_file import logger
from common.utility import generate_token, random_six_digits, send_verification_email, hash_password, check_password, validate_required_fields, check_photo_format_and_size, check_file_format_and_size, save_file_in_directory, delete_file_from_directory
from rest_framework.parsers import MultiPartParser, FormParser
from datetime import datetime
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.utils import timezone
from datetime import timedelta


class PartnerLoginView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Authenticate Partner profile with email and password & update their web Firebase token.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['email', 'password'],
            properties={
                'email': openapi.Schema(type=openapi.TYPE_STRING, description="User's email address"),
                'password': openapi.Schema(type=openapi.TYPE_STRING, description="User's password"),
                'web_firebase_token': openapi.Schema(type=openapi.TYPE_STRING, description="Firebase token for the web")
            }
        ),
        responses={
            200: openapi.Response(description="User authenticated successfully", schema=PartnerProfileSerializer),
            400: "Bad Request: Missing or invalid input data",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User does not exist",
            500: "Server Error: Internal server error"
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            # Extract email, password, and web Firebase token from the request data
            data = request.data
            email = data.get('email')
            password = data.get('password')
            web_firebase_token = data.get('web_firebase_token')

            # Check if email and password are provided
            if not email or not password:
                return Response({"message": "Email and password are required."}, status=status.HTTP_400_BAD_REQUEST)

            serializer = PartnerProfileSerializer(data=request.data)
            # Validate email format
            try:
                serializer.validate_email(email)
            except serializers.ValidationError as e:
                return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve the user based on the email provided
            user = PartnerProfile.objects.get(email=email)

            # Check if the provided password matches the stored password
            if not check_password(user.password, password):
                return Response({"message": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)

            # Update the user's web Firebase token if provided
            if web_firebase_token:
                user.web_firebase_token = web_firebase_token
                user.save()

            # Return the serialized user data
            return Response(PartnerProfileSerializer(user).data, status=status.HTTP_200_OK)

        except PartnerProfile.DoesNotExist:
            return Response({"message": "User does not exist."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            # add in logs
            logger.error("Partner-LoginView error: %s", str(e))
            return Response({"message": "Failed to user authenticated. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class IsPartnerExistView(APIView):
    permission_classes = [IsAdminUser]
    @swagger_auto_schema(
        operation_description="Check if a user exists by phone number.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'email': openapi.Schema(type=openapi.TYPE_STRING),
            },
            required=['email'],
        ),
        responses={
            200: openapi.Response(description="Partner exists", schema=PartnerProfileSerializer),
            404: "Not Found: User does not exist",
            400: "Bad Request: Invalid input data",
            401: "Unauthorized: Admin permissions required",
            500: "Server Error: Internal server error"
        }
    )
    def post(self, request, *args, **kwargs):
        # Deserialize request data using UserProfileSerializer
        serializer = PartnerProfileSerializer(data=request.data)

        # checking that phone_number is provided
        email = request.data.get('email')
        if not email:
            return Response({"message": "Email address is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Validate the email format using serializer validation
            serializer.validate_email(email)
        except serializers.ValidationError as e:
            return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

        try:
            partner = PartnerProfile.objects.get(email=email)
            serializer = PartnerProfileSerializer(partner)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except PartnerProfile.DoesNotExist:
            return Response({"message": "User with this email does not exist."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            # add in Log
            logger.error("Partner-IsUserExistView: An unexpected error occurred: %s", str(e))
            return Response({"message": "Failed to get user detail. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CreatePartnerProfileView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Delete a partner profile based on the provided session token.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['partner_session_token'],
            properties={'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description="Session token of the partner to be deleted"),}
        ),
        responses={
            200: "Selected user has been removed.",
            400: "Bad Request: User not recognized.",
            401: "Unauthorized: Admin permissions required",
            500: "Server Error: Internal server error"
        }
    )
    def delete(self, request):
        try:
            # Extract the session token from the request data
            session_token = request.data.get('partner_session_token')
            if not session_token:
                return Response({"message": "Missing partner session token."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve the partner profile using the session token
            profile = PartnerProfile.objects.filter(partner_session_token=session_token).first()

            # Check if the profile exists
            if profile:
                profile.delete()
                response = {"message": "Selected user has been removed."}
                return Response(response, status=status.HTTP_200_OK)
            else:
                response = {"message": "User not recognized."}
                return Response(response, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            # Log the error and return a generic error message
            logger.error("Delete - CreatePartnerProfileView: %s", str(e))
            return Response({"message": "Failed to delete user. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Create a new partner profile.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['email', 'name', 'phone_number', 'password', 'sign_type'],
            properties={
                'email': openapi.Schema(type=openapi.TYPE_STRING, description="Email address of the partner"),
                'name': openapi.Schema(type=openapi.TYPE_STRING, description="Name of the partner"),
                'phone_number': openapi.Schema(type=openapi.TYPE_STRING, description="Phone number of the partner"),
                'password': openapi.Schema(type=openapi.TYPE_STRING, description="Password for the partner account"),
                'sign_type': openapi.Schema(type=openapi.TYPE_STRING, description="Sign up type (e.g., Email)")
            }
        ),
        responses={
            201: openapi.Response(description="Partner profile created successfully", schema=PartnerProfileSerializer),
            400: "Bad Request: Missing or invalid input data",
            401: "Unauthorized: Admin permissions required",
            409: "Conflict: User already exists",
            500: "Server Error: Internal server error"
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.data

            # Check if email is provided
            email = request.data.get('email')
            phone_number = request.data.get('phone_number')
            password = request.data.get('password')
            if not email or not phone_number:
                return Response({"message": "Email address is required."}, status=status.HTTP_400_BAD_REQUEST)

            # Validate the request data using the serializer
            serializer = PartnerProfileSerializer(data=data)
            try:
                # Validate the email format using serializer validation
                serializer.validate_email(email)
            except serializers.ValidationError as e:
                return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

            try:
                # Validate the phone number using serializer validation
                serializer.validate_phone_number(phone_number)
            except serializers.ValidationError as e:
                return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

            try:
                # Validate the password using serializer validation
                serializer.validate_password(password)
            except serializers.ValidationError as e:
                return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

            # Generate a unique session token for the partner
            token_key = generate_token(email + str(52955917))

            # Check if a user with the same token already exists
            if PartnerProfile.objects.filter(partner_session_token=token_key).exists():
                return Response({"message": "This user already exists."}, status=status.HTTP_409_CONFLICT)

            if data.get('sign_type') == "Email":
                required_fields = ['name', 'phone_number', 'password']
                error_response = validate_required_fields(required_fields, data)
                if error_response:
                    return error_response

                try:
                    # Use a transaction to ensure atomicity
                    with transaction.atomic():
                        otp = random_six_digits()

                        # Extract country code and phone number
                        country_code, phone_number = phone_number[:-10], phone_number[-10:]
                        data['partner_session_token'] = token_key
                        data['phone_number'] = phone_number
                        data['country_code'] = country_code
                        data['otp'] = str(otp)
                        data['partner_type'] = "NA"
                        data['password'] = hash_password(data['password'])

                        # Create the user profile
                        user = serializer.create(data)

                        # Generate a wallet token and create a wallet for the user
                        wallet_token = generate_token(f'wallet{datetime.now()}0.0')
                        Wallet.objects.create(wallet_code=wallet_token, wallet_session=user)

                        # Send a verification email with the OTP
                        send_verification_email(user.email, user.name, otp)

                        # Serialize and return the user data
                        serialized_user = PartnerProfileSerializer(user)
                        return Response(serialized_user.data, status=status.HTTP_201_CREATED)
                except Exception as e:
                    # add logs in file
                    logger.error(f"Post - CreatePartnerProfileView: {str(e)}")
                    return Response({"message": "Failed to create user due to an internal error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            return Response({"message": "The request could not be processed due to invalid input."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            # add logs in file
            logger.error(f"Post - CreatePartnerProfileView: {str(e)}")
            return Response({"message": "Failed to create user due to an internal error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SendEmailOTPView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Send or Resend OTP to the partner's email based on session token",
        request_body=openapi.Schema(type=openapi.TYPE_OBJECT, required=['partner_session_token'], properties={'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description="Session token of the partner")}),
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
            partner_session_token = request.data.get('partner_session_token')
            if not partner_session_token:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve user profile based on session_token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()

            # Check if user exists
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            otp = random_six_digits()
            send_verification_email(user.email, user.name, otp)
            user.otp = otp
            user.save()
            return Response({"message": "OTP sent successfully."}, status=status.HTTP_200_OK)
        except Exception as e:
            # Adding logs
            logger.error("Partner - SendEmailOTPView error: %s", str(e))
            return Response({"message": "Failed to send otp. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MatchEmailOTPView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'otp': openapi.Schema(type=openapi.TYPE_STRING, description='OTP Password of user'),
            },
            required=['partner_session_token', 'otp']
        ),
        responses={
            200: openapi.Response("Success: OTP Matched successfully", PartnerProfileSerializer),
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
            partner_session_token = request.data.get('partner_session_token')
            otp = request.data.get('otp')
            if not otp or not partner_session_token:
                return Response({"message": "Missing OTP or user information."}, status=status.HTTP_400_BAD_REQUEST)

            #  Retrieve user profile based on session_token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # If OTP has expired (within 2 minute)
            time_difference = timezone.now() - user.otp_time
            if time_difference > timedelta(minutes=2):
                return Response({"message": "OTP has expired. Please request a new OTP."}, status=status.HTTP_400_BAD_REQUEST)

            # Matching otp
            if user.otp != otp:
                return Response({"message": "Invalid OTP."}, status=status.HTTP_400_BAD_REQUEST)

            with transaction.atomic():
                user.is_email_verified = True
                user.email_otp = ""
                user.save()

            # Returning user profile
            serialized_user = PartnerProfileSerializer(user)
            return Response(serialized_user.data, status=status.HTTP_200_OK)
        except Exception as e:
            # adding logs
            logger.error("Partner - MatchEmailOTPView error: %s", str(e))
            return Response({"message": "Failed to verify otp. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PartnerServicesView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Create services for a partner based on the provided session token.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['partner_session_token', 'is_hajj_service_offer', 'is_umrah_service_offer', 'is_ziyarah_service_offer', 'is_transport_service_offer', 'is_visa_service_offer'],
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description="Session token of the partner"),
                'is_hajj_service_offer': openapi.Schema(type=openapi.TYPE_BOOLEAN, description="Hajj service offer"),
                'is_umrah_service_offer': openapi.Schema(type=openapi.TYPE_BOOLEAN, description="Umrah service offer"),
                'is_ziyarah_service_offer': openapi.Schema(type=openapi.TYPE_BOOLEAN, description="Ziyarah service offer"),
                'is_transport_service_offer': openapi.Schema(type=openapi.TYPE_BOOLEAN, description="Transport service offer"),
                'is_visa_service_offer': openapi.Schema(type=openapi.TYPE_BOOLEAN, description="Visa service offer"),
            }
        ),
        responses={
            201: openapi.Response("Success: created successfully", PartnerProfileSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not found with the provided session token.",
            409: "Conflict: Record already exists.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        data = request.data
        # Validate presence of the session token early
        partner_session_token = request.data.get('partner_session_token')
        if not partner_session_token:
            return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the partner profile using the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()

        # Check if the user exists
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # Field Validation
        required_fields = [
            'is_hajj_service_offer', 'is_umrah_service_offer', 'is_ziyarah_service_offer',
            'is_transport_service_offer', 'is_visa_service_offer'
        ]
        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        # Check if the user already has a partner type other than "NA"
        if user.partner_type != "NA":
            return Response({"message": "Sorry, record already exists."}, status=status.HTTP_409_CONFLICT)

        return self.add_partner_services(user, data)

    def add_partner_services(self, user, data):
        try:
            # Create partner services within a transaction
            with transaction.atomic():
                partner_services = PartnerServices.objects.create(
                    is_hajj_service_offer=data['is_hajj_service_offer'],
                    is_umrah_service_offer=data['is_umrah_service_offer'],
                    is_ziyarah_service_offer=data['is_ziyarah_service_offer'],
                    is_transport_service_offer=data['is_transport_service_offer'],
                    is_visa_service_offer=data['is_visa_service_offer'],
                    services_of_partner=user
                )

                # Update the user's partner type based on the services offered
                self.update_partner_type(user, data)


                # Serialize and return the updated user profile
                serialized_user = PartnerProfileSerializer(user)
                return Response(serialized_user.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error("PartnerServicesView: %s", str(e))
            return Response({"message": "Failed to add partner services. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def update_partner_type(self, user, data):
        # Check if any specific services are offered and set the partner type accordingly
        if any([data[field] for field in ['is_hajj_service_offer', 'is_umrah_service_offer', 'is_ziyarah_service_offer', 'is_visa_service_offer']]):
            user.partner_type = "Company"
        elif data['is_transport_service_offer']:
            user.partner_type = "Individual"
        user.save()


class IndividualPartnerView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_description="Create an individual partner profile with driving license details and mailing address.",
        manual_parameters=[
            openapi.Parameter('contact_name', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Contact name of the partner"),
            openapi.Parameter('contact_number', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Contact number of the partner"),
            openapi.Parameter('driving_license_number', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Driving license number of the partner"),
            openapi.Parameter('front_side_photo', openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Front side photo of the driving license"),
            openapi.Parameter('back_side_photo', openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Back side photo of the driving license"),
            openapi.Parameter('partner_session_token', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Session token of the partner"),
            openapi.Parameter('street_address', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Street address of the partner"),
            openapi.Parameter('address_line2', openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Address line 2 of the partner"),
            openapi.Parameter('city', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="City of the partner"),
            openapi.Parameter('state', openapi.IN_FORM, type=openapi.TYPE_STRING, description="State of the partner"),
            openapi.Parameter('country', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Country of the partner"),
            openapi.Parameter('postal_code', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Postal code of the partner"),
            openapi.Parameter('lat', openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Latitude coordinate of the address"),
            openapi.Parameter('long', openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Longitude coordinate of the address"),
        ],
        responses={
            201: openapi.Response("Success: created successfully", PartnerProfileSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not found.",
            409: "Conflict: Record already exists or update Service section first.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        data = request.data

        # Validate required fields
        required_fields = [
            'contact_name', 'contact_number', 'driving_license_number', 'front_side_photo', 'back_side_photo',
            'partner_session_token', 'street_address', 'city', 'state', 'country', 'postal_code'
        ]
        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        # Extract and validate photos
        front_side_photo = request.data.get('front_side_photo')
        back_side_photo = request.data.get('back_side_photo')
        if not check_photo_format_and_size(front_side_photo) or not check_photo_format_and_size(back_side_photo):
            return Response({"message": "Invalid file format or size."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate the phone number using serializer validation
        serializer1 = PartnerProfileSerializer(data=data)
        try:
            serializer1.validate_phone_number(data["contact_number"])
        except serializers.ValidationError as e:
            return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve partner profile
        partner_session_token = data.get('partner_session_token')
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        # Check partner type
        if user.partner_type == "NA":
            return Response({"message": "Please update the Service section first."}, status=status.HTTP_409_CONFLICT)
        if user.partner_type == "Company":
            return Response({"message": "User is enrolled as a company."}, status=status.HTTP_409_CONFLICT)

        if PartnerMailingDetail.objects.filter(mailing_of_partner=user).exists():
            return Response({"message": "Address detail already exists."}, status=status.HTTP_409_CONFLICT)

        # Check if individual profile already exists
        if IndividualProfile.objects.filter(individual_profile_of_partner=user).exists():
            return Response({"message": "Record already exists for this user."}, status=status.HTTP_409_CONFLICT)

        try:
            # Create individual profile and mailing detail within a transaction
            with transaction.atomic():
                # Create Address detail
                serializer = PartnerProfileSerializer(data=data)
                if serializer.is_valid():
                    serializer.save(mailing_of_partner=user)
                else:
                    first_error_field = next(iter(serializer.errors))
                    first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                    return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

                front_path = save_file_in_directory(front_side_photo)
                back_path = save_file_in_directory(back_side_photo)

                # Create individual profile
                IndividualProfile.objects.create(
                    contact_name=data['contact_name'],
                    contact_number=data['contact_number'],
                    driving_license_number=data['driving_license_number'],
                    front_side_photo=front_path,
                    back_side_photo=back_path,
                    individual_profile_of_partner=user
                )
                # Create mailing detail
            # Return serialized user data
            return Response(PartnerProfileSerializer(user).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"IndividualPartnerView: {str(e)}")
            return Response({"message": "An internal server error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UpdatePartnerIndividualProfileView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Update the individual partner profile with new details.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description="Session token of the partner"),
                'contact_name': openapi.Schema(type=openapi.TYPE_STRING, description="Contact name of the partner"),
                'contact_number': openapi.Schema(type=openapi.TYPE_STRING, description="Contact number of the partner"),
            },
            required=['partner_session_token', 'contact_name', 'contact_number']
        ),
        responses={
            200: openapi.Response("Success: Individual partner profile updated", PartnerProfileSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User or individual partner detail not found.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        """
        Handle PUT requests to update an individual partner's profile.
        """
        try:
            data = request.data
            partner_session_token = request.data.get('partner_session_token')
            contact_name = request.data.get('contact_name')
            contact_number = request.data.get('contact_number')

            # Check if partner session token is provided
            if not partner_session_token:
                return Response({"message": "Missing partner session token."}, status=status.HTTP_400_BAD_REQUEST)

            # Validate required fields
            required_fields = ['contact_name', 'contact_number']
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Validate the phone number using serializer validation
            serializer1 = PartnerProfileSerializer(data=data)
            try:
                serializer1.validate_phone_number(contact_number)
            except serializers.ValidationError as e:
                return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

            try:
                # Fetch user based on the partner session token
                user = PartnerProfile.objects.get(partner_session_token=partner_session_token)
            except PartnerProfile.DoesNotExist:
                return Response({"message": "User not found with the provided partner session token."}, status=status.HTTP_404_NOT_FOUND)

            try:
                # Fetch individual profile associated with the user
                ind_profile = IndividualProfile.objects.get(individual_profile_of_partner=user)
            except IndividualProfile.DoesNotExist:
                return Response({"message": "Partner profile detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Update individual profile details
            ind_profile.contact_name = contact_name
            ind_profile.contact_number = contact_number
            ind_profile.save()

            # Serialize the updated user profile
            serialized_package = PartnerProfileSerializer(user)
            return Response(serialized_package.data, status=status.HTTP_200_OK)
        except Exception as e:
            # Add in logs file
            logger.error(f"UpdatePartnerIndividualProfileView: {str(e)}")
            return Response({"message": "An internal server error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BusinessPartnerView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_description="Create a business partner profile with company details and mailing address.",
        manual_parameters=[
            openapi.Parameter('company_name', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Name of the company"),
            openapi.Parameter('contact_name', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Contact name of the partner"),
            openapi.Parameter('contact_number', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Contact number of the partner"),
            openapi.Parameter('company_website', openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Website of the company"),
            openapi.Parameter('license_type', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="License type of the company"),
            openapi.Parameter('license_number', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="License number of the company"),
            openapi.Parameter('total_experience', openapi.IN_FORM, type=openapi.TYPE_INTEGER, required=True, description="Total experience of the company"),
            openapi.Parameter('company_bio', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Bio of the company"),
            openapi.Parameter('company_logo', openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Logo of the company"),
            openapi.Parameter('license_certificate', openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Certificate of the company"),
            openapi.Parameter('partner_session_token', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Session token of the partner"),
            openapi.Parameter('street_address', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Street address of the partner"),
            openapi.Parameter('address_line2', openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Address line 2 of the partner"),
            openapi.Parameter('city', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="City of the partner"),
            openapi.Parameter('state', openapi.IN_FORM, type=openapi.TYPE_STRING, description="State of the partner"),
            openapi.Parameter('country', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Country of the partner"),
            openapi.Parameter('postal_code', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Postal code of the partner"),
            openapi.Parameter('lat', openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Latitude coordinate of the address"),
            openapi.Parameter('long', openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Longitude coordinate of the address"),
            openapi.Parameter('user_name', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Username of the partner"),
        ],
        responses={
            201: openapi.Response("Success: Business partner profile created", PartnerProfileSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not found with the provided detail.",
            409: "Conflict: Record already exists or update Service section first.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        data = request.data
        required_fields = ['company_name', 'contact_name', 'contact_number', 'company_website', 'license_type',
                           'license_number', 'total_experience', 'company_bio', 'company_logo', 'license_certificate',
                           'partner_session_token', 'street_address', 'city', 'state', 'country', 'postal_code', 'user_name']

        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        company_logo = data.get('company_logo')
        license_certificate = data.get('license_certificate')
        partner_session_token = data.get('partner_session_token')

        # Fetch the user based on the partner session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # Validate the username format
        if not re.match(r'^\w+$', data['user_name']):
            return Response({"message": "Invalid user name. Only alphanumeric characters and underscores are allowed."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate the phone number using serializer validation
        serializer1 = PartnerProfileSerializer(data=data)
        try:
            serializer1.validate_phone_number(data["contact_number"])
        except serializers.ValidationError as e:
            return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

        # Check if the username is already taken
        check_username = PartnerProfile.objects.filter(user_name=data['user_name'].lower()).first()
        if check_username:
            return Response({"message": "Sorry, this User name is already taken."}, status=status.HTTP_409_CONFLICT)

        # Check if the user needs to update the Service section first
        if user.partner_type == "NA":
            return Response({"message": "Sorry, update Service section first."}, status=status.HTTP_409_CONFLICT)

        # Check if the user is enrolled as an Individual
        if user.partner_type == "Individual":
            return Response({"message": "Sorry, you're enrolled as Individual."}, status=status.HTTP_409_CONFLICT)

        # Validate the format and size of the company logo
        if not check_photo_format_and_size(company_logo):
            return Response({"message": "Invalid file format or size for company logo."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate the format and size of the license certificate
        if not check_file_format_and_size(license_certificate):
            return Response({"message": "Invalid file format or size for certificate."}, status=status.HTTP_400_BAD_REQUEST)

        if PartnerMailingDetail.objects.filter(mailing_of_partner=user).exists():
            return Response({"message": "Address detail already exists."}, status=status.HTTP_409_CONFLICT)

        # Check if a record already exists for this user
        already_exist = BusinessProfile.objects.filter(company_of_partner=user).first()
        if already_exist:
            return Response({"message": "Record already exists for this user."}, status=status.HTTP_409_CONFLICT)

        try:
            with transaction.atomic():
                # Create address detail
                serializer = PartnerMailingDetailSerializer(data=data)
                if serializer.is_valid():
                    serializer.save(mailing_of_partner=user)
                else:
                    first_error_field = next(iter(serializer.errors))
                    first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                    return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

                # Save company logo and certificate to file system
                company_logo_path = save_file_in_directory(company_logo)
                license_certificate_path = save_file_in_directory(license_certificate)

                # Create a new BusinessProfile instance
                BusinessProfile.objects.create(
                    company_name=data['company_name'],
                    contact_name=data['contact_name'],
                    contact_number=data['contact_number'],
                    company_website=data['company_website'],
                    license_type=data['license_type'],
                    license_number=data['license_number'],
                    total_experience=data['total_experience'],
                    company_bio=data['company_bio'],
                    company_logo=company_logo_path,
                    license_certificate=license_certificate_path,
                    company_of_partner=user
                )

                # Update user's username
                user.user_name = data['user_name'].lower()
                user.save()
                return Response(PartnerProfileSerializer(user).data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"BusinessPartnerView: {str(e)}")
            return Response({"message": "An internal server error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UpdateBusinessProfileView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Update the business partner profile with new details.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description="Session token of the partner"),
                'user_name': openapi.Schema(type=openapi.TYPE_STRING, description="Username of the partner"),
                'contact_name': openapi.Schema(type=openapi.TYPE_STRING, description="Contact name of the partner"),
                'contact_number': openapi.Schema(type=openapi.TYPE_STRING, description="Contact number of the partner"),
                'company_website': openapi.Schema(type=openapi.TYPE_STRING, description="Website of the company"),
                'total_experience': openapi.Schema(type=openapi.TYPE_INTEGER, description="Total experience of the company"),
                'company_bio': openapi.Schema(type=openapi.TYPE_STRING, description="Bio of the company"),
            },
            required=['partner_session_token', 'user_name', 'contact_name', 'contact_number', 'total_experience', 'company_bio']
        ),
        responses={
            200: openapi.Response("Success: Business partner profile updated", PartnerProfileSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User or company detail not found.",
            409: "Conflict: Username already taken.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        data = request.data
        partner_session_token = request.data.get('partner_session_token')
        user_name = request.data.get('user_name')

        # Check if the partner_session_token is provided
        if not partner_session_token:
            return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate required fields
        required_fields = ['user_name', 'contact_name', 'contact_number', 'company_website', 'total_experience', 'company_bio']
        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        # Validate the phone number using serializer validation
        serializer1 = PartnerProfileSerializer(data=data)
        try:
            serializer1.validate_phone_number(data["contact_number"])
        except serializers.ValidationError as e:
            return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

        # Validate the format of the user_name
        if not re.match(r'^\w+$', user_name):
            return Response({"message": "Invalid user name. Only alphanumeric characters and underscores are allowed."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Fetch the user based on the partner_session_token
            user = PartnerProfile.objects.get(partner_session_token=partner_session_token)

            # Check if the user_name needs to be updated
            if user.user_name != user_name.lower():
                # Check if the new user_name is already taken
                if PartnerProfile.objects.filter(user_name=user_name.lower()).exists():
                    return Response({"message": "Sorry, this User name is already taken."}, status=status.HTTP_409_CONFLICT)
                user.user_name = user_name.lower()
                user.save()

            try:
                # Use transaction.atomic to ensure atomicity
                with transaction.atomic():
                    # Fetch the BusinessProfile associated with the user
                    bus_profile = BusinessProfile.objects.get(company_of_partner=user)
                    # Update the BusinessProfile fields with the new data
                    bus_profile.contact_name = data.get('contact_name')
                    bus_profile.contact_number = data.get('contact_number')
                    bus_profile.company_website = data.get('company_website')
                    bus_profile.total_experience = data.get('total_experience')
                    bus_profile.company_bio = data.get('company_bio')
                    bus_profile.save()

                    # Serialize and return the updated PartnerProfile
                    serialized_package = PartnerProfileSerializer(user)
                    return Response(serialized_package.data, status=status.HTTP_200_OK)

            # Handle case where the BusinessProfile does not exist
            except BusinessProfile.DoesNotExist:
                return Response({"message": "Company detail not exist."}, status=status.HTTP_404_NOT_FOUND)

        # Handle case where the PartnerProfile does not exist
        except PartnerProfile.DoesNotExist:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # Handle any unexpected exceptions
        except Exception as e:
            logger.error(f"UpdatePartnerBusinessProfileView: {str(e)}")
            return Response({"message": "An internal server error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CheckPartnerUsernameAvailabilityView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Check if a given username is available for a partner.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description="Session token of the partner"),
                'user_name': openapi.Schema(type=openapi.TYPE_STRING, description="Desired username of the partner"),
            },
            required=['partner_session_token', 'user_name']
        ),
        responses={
            200: "This username is available.",
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required",
            409: "Conflict: Username already taken.",
            404: "Not Found: User not found with the provided detail.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.data
            partner_session_token = request.data.get('partner_session_token')
            user_name = request.data.get('user_name')

            # Validate required fields
            required_fields = ['user_name', 'partner_session_token']
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Validate username format
            if not re.match(r'^\w+$', user_name):
                return Response({"message": "Invalid username. Only alphanumeric characters and underscores are allowed."}, status=status.HTTP_400_BAD_REQUEST)

            # Fetch user based on the partner session token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided partner session token."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the username is already taken by another user
            if user.user_name != user_name.lower():
                if PartnerProfile.objects.filter(user_name=user_name.lower()).exists():
                    return Response({"message": "Sorry, this username is already taken."}, status=status.HTTP_409_CONFLICT)

            return Response({"message": "This username is available."}, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"CheckPartnerUsernameAvailabilityView: {str(e)}")
            return Response({"message": "An internal server error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetPartnerAddressView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('partner_session_token', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True, description='Session token of the partner'),
        ],
        responses={
            200: openapi.Response("Success: Address details retrieved successfully", PartnerMailingDetailSerializer),
            400: "Bad Request: Missing required information or user not recognized",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: Address details not found for the user",
            500: "Server Error: Internal server error"
        },
        operation_description="Retrieve the mailing address details of a partner."
    )
    def get(self, request):
        try:
            # Check if the partner_session_token is provided
            partner_session_token = self.request.GET.get('partner_session_token')
            if not partner_session_token:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Fetch the user based on the partner_session_token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Fetch the mailing address details of the user
            address_detail = PartnerMailingDetail.objects.filter(mailing_of_partner=user)

            # Check if address details exist for the user
            if address_detail.exists():
                serialized_package = PartnerMailingDetailSerializer(address_detail, many=True)
                return Response(serialized_package.data, status=status.HTTP_200_OK)
            else:
                return Response({"message": "Address detail not exist."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            # Add in logs file
            logger.error(f"GetPartnerAddressView: {str(e)}")
            return Response({"message": "An internal server error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UpdatePartnerAddressView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
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
            200: openapi.Response("Success: Address details updated successfully", PartnerMailingDetailSerializer),
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
            partner_session_token = request.data.get('partner_session_token')
            address_id = request.data.get('address_id')

            # Validate session_token and address_id presence
            if not partner_session_token or not address_id:
                return Response({"message": "Missing user information or address ID."}, status=status.HTTP_400_BAD_REQUEST)

            # Validate required fields
            required_fields = ['address_id', 'street_address', 'city', 'state', 'country', 'postal_code']
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Retrieve user profile
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve address detail based on address_id and mailing_session (user)
            address_detail = PartnerMailingDetail.objects.filter(mailing_of_partner=user, address_id=address_id).first()
            if not address_detail:
                return Response({"message": "Address detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Validate and update mailing detail
            serializer = PartnerMailingDetailSerializer(address_detail, data=data, partial=True)
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
            logger.error(f"Missing key error in UpdatePartnerAddressView: {str(e)}")
            return Response({"message": f"Missing key: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            # Log the error and return a server error response
            logger.error("UpdatePartnerAddressView: %s", str(e))
            return Response({"message": "Failed to update address detail. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UpdateCompanyLogoView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_description="Update the company logo for a business partner.",
        manual_parameters=[
            openapi.Parameter(
                'partner_session_token',
                openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                required=True,
                description="Session token of the partner"
            ),
            openapi.Parameter(
                'company_logo',
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                required=True,
                description="New company logo file"
            )
        ],
        responses={
            200: openapi.Response("Success: Company logo updated", PartnerProfileSerializer),
            400: "Bad Request: Missing file or user information, invalid file format or size.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or company record not found.",
            409: "Conflict: Company record already exists for this user.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            file = request.data.get('company_logo')
            partner_session_token = request.data.get('partner_session_token')

            if not file or not partner_session_token:
                return Response({"message": "Missing file or user information."}, status=status.HTTP_400_BAD_REQUEST)

            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            check_exist = BusinessProfile.objects.filter(company_of_partner=user).first()
            if not check_exist:
                return Response({"message": "Company record not exists for this user."}, status=status.HTTP_409_CONFLICT)

            # Validate file format and size
            if not check_photo_format_and_size(file):
                return Response({"message": "Invalid file format or size."}, status=status.HTTP_400_BAD_REQUEST)

            # Delete existing file if it exists
            if check_exist.company_logo:
                delete_file_from_directory(check_exist.company_logo.name)

            # Save new file path to the database
            file_path = save_file_in_directory(file)
            check_exist.company_logo = file_path
            check_exist.save()

            # Serialize user data for response
            serialized_user = PartnerProfileSerializer(user)
            return Response(serialized_user.data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error("UpdateCompanyLogoView: %s", str(e))
            return Response({"message": "Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChangePasswordView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['partner_session_token', 'current_password', 'new_password'],
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING),
                'current_password': openapi.Schema(type=openapi.TYPE_STRING),
                'new_password': openapi.Schema(type=openapi.TYPE_STRING),
            }
        ),
        responses={
            200: openapi.Response("Success: Password changed successfully"),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not found with the provided detail.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            # Retrieve data from request
            partner_session_token = request.data.get('partner_session_token')
            current_password = request.data.get('current_password')
            new_password = request.data.get('new_password')

            # Check if all required fields are present in the request
            if not current_password or not new_password:
                return Response({"message": "All fields are required."}, status=status.HTTP_400_BAD_REQUEST)

            # Check if partner session token is provided
            if not partner_session_token:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            serializer = PartnerProfileSerializer(data=request.data)
            try:
                # Validate the password using serializer validation
                serializer.validate_password(new_password)
            except serializers.ValidationError as e:
                return Response({"message": str(e.detail[0])}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve the user based on the partner session token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Verify the current password before changing
            if not check_password(user.password, current_password):
                return Response({"message": "Current password is incorrect."}, status=status.HTTP_401_UNAUTHORIZED)

            # Update the user's password to the new password
            user.password = hash_password(new_password)
            user.save()

            # Return success message
            return Response({"message": "Password changed successfully."}, status=status.HTTP_200_OK)

        except Exception as e:
            # add in logs file
            logger.error("ChangePasswordView error: %s", str(e))
            return Response({"message": "An unexpected error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
