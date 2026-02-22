from rest_framework.views import APIView
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from django.db import transaction
from common.utility import user_new_booking_email, validate_required_fields, check_file_format_and_size, save_file_in_directory, delete_file_from_directory, send_new_order_email, send_complaint_email
from common.logs_file import logger
from common.models import UserProfile
from partners.models import PartnerProfile, HuzBasicDetail
from .models import BookingRequest, Booking, PassportValidity, BookingObjections, DocumentsStatus, Payment, UserRequiredDocuments, BookingRatingAndReview, BookingComplaints
from .serializers import BookingRequestSerializer, ShortBookingSerializer, DetailBookingSerializer, PassportValiditySerializer, PaymentSerializer, BookingComplaintsSerializer
import random
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from datetime import datetime, timedelta
from django.utils.dateparse import parse_date


class ManageBookingsView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Create a new booking.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=[
                'session_token', 'partner_session_token', 'huz_token', 'adults', 'child', 'infants', 'sharing', 'quad',
                'triple', 'double',
                'start_date', 'end_date', 'total_price', 'special_request', 'payment_type'
            ],
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Token of the package'),
                'adults': openapi.Schema(type=openapi.TYPE_INTEGER, description='Number of adults'),
                'child': openapi.Schema(type=openapi.TYPE_INTEGER, description='Number of children'),
                'infants': openapi.Schema(type=openapi.TYPE_INTEGER, description='Number of infants'),
                'sharing': openapi.Schema(type=openapi.TYPE_INTEGER, description='Total bed in sharing'),
                'quad': openapi.Schema(type=openapi.TYPE_INTEGER, description='Total bed in quad'),
                'triple': openapi.Schema(type=openapi.TYPE_INTEGER, description='Total bed in triple'),
                'double': openapi.Schema(type=openapi.TYPE_INTEGER, description='Total bed in double'),
                'start_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME, description='Start date of the booking'),
                'end_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME, description='End date of the booking'),
                'total_price': openapi.Schema(type=openapi.TYPE_NUMBER, format=openapi.FORMAT_FLOAT, description='Total price of the booking'),
                'special_request': openapi.Schema(type=openapi.TYPE_STRING, description='Special requests for the booking'),
                'payment_type': openapi.Schema(type=openapi.TYPE_STRING, description='Type of payment')
            }
        ),
        responses={
            201: openapi.Response(description="Booking created successfully.", schema=DetailBookingSerializer),
            400: "Bad Request: Missing or invalid input data",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, Partner, or Package not found.",
            500: "Server error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):

        try:
            data = request.data
            required_fields = ['session_token', 'partner_session_token', 'huz_token', 'adults', 'child', 'infants',
                               'sharing', 'quad', 'triple', 'double', 'single', 'start_date', 'end_date', 'total_price',
                               'special_request', 'payment_type']

            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Retrieve user by session token
            user = UserProfile.objects.filter(session_token=data.get('session_token')).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve partner by session token
            partner_detail = PartnerProfile.objects.filter(partner_session_token=data.get('partner_session_token')).first()
            if not partner_detail:
                return Response({"message": "Package provider detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve package by huz token
            package_detail = HuzBasicDetail.objects.filter(huz_token=data.get('huz_token')).first()
            if not package_detail:
                return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # calculate total cost of package
            # total_cost = self.calculate_total_cost(package_detail, data)

            # Remove session tokens from data
            for key in ['partner_session_token', 'session_token', 'huz_token']:
                data.pop(key, None)

            # Set default booking status and relations
            data['booking_status'] = "Initialize"
            data['total_price'] = data.get('total_price')
            data['order_by'] = user.user_id
            data['order_to'] = partner_detail.partner_id
            data['package_token'] = package_detail.huz_id
            data['booking_number'] = self.generate_unique_booking_number()

            serializer = ShortBookingSerializer(data=data)
            if not serializer.is_valid():
                first_error_field = next(iter(serializer.errors))
                first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

            # Create booking and related document status within an atomic transaction
            with transaction.atomic():
                booking = serializer.save()
                DocumentsStatus.objects.create(status_for_booking=booking)
                serialized_booking = DetailBookingSerializer(booking)
                # user_new_booking_email(user.email, user.name, package_detail.package_type, package_detail.package_name,
                #                        booking.booking_number, booking.adults, booking.child, booking.infants,
                #                        booking.start_date, booking.total_amount)
                return Response(serialized_booking.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            # Log the error and return a failure response
            logger.error(f"Post - ManageBookingsView: {str(e)}")
            return Response({"message": "Failed to create booking request. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Update booking details",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token'),
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Partner session token'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Huz token'),
                'adults': openapi.Schema(type=openapi.TYPE_INTEGER, description='Number of adults'),
                'child': openapi.Schema(type=openapi.TYPE_INTEGER, description='Number of children'),
                'infants': openapi.Schema(type=openapi.TYPE_INTEGER, description='Number of infants'),
                'sharing': openapi.Schema(type=openapi.TYPE_INTEGER, description='Total bed in sharing'),
                'quad': openapi.Schema(type=openapi.TYPE_INTEGER, description='Total bed in quad'),
                'triple': openapi.Schema(type=openapi.TYPE_INTEGER, description='Total bed in triple'),
                'double': openapi.Schema(type=openapi.TYPE_INTEGER, description='Total bed in double'),
                'start_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE, description='Start date'),
                'end_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE, description='End date'),
                'total_price': openapi.Schema(type=openapi.TYPE_NUMBER, description='Total price'),
                'special_request': openapi.Schema(type=openapi.TYPE_STRING, description='Special request'),
                'payment_type': openapi.Schema(type=openapi.TYPE_STRING, description='Payment type')
            },
            required=['booking_number', 'session_token', 'partner_session_token', 'huz_token', 'adults',
                      'child', 'infants', 'sharing', 'quad', 'triple', 'double', 'start_date', 'end_date', 'total_price',
                      'special_request', 'payment_type']
        ),
        responses={
            200: openapi.Response('Booking updated successfully', DetailBookingSerializer),
            400: "Bad Request: Missing or invalid input data",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, Partner, or Package not found.",
            500: "Server error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            data = request.data
            required_fields = ['booking_number', 'session_token', 'partner_session_token', 'huz_token', 'adults',
                               'child', 'infants', 'sharing', 'quad', 'triple', 'double', 'single', 'start_date', 'end_date', 'total_price',
                               'special_request', 'payment_type']

            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            booking_number = data.get('booking_number')
            booking = Booking.objects.filter(booking_number=booking_number).first()
            if not booking:
                return Response({"message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

            # Ensure booking status is "Initialize"
            if booking.booking_status != "Initialize":
                return Response(
                    {"message": "Oops, this request cannot be processed. Please contact the support team."}, status=status.HTTP_400_BAD_REQUEST)

            user = UserProfile.objects.filter(session_token=data.get('session_token')).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            partner_detail = PartnerProfile.objects.filter(partner_session_token=data.get('partner_session_token')).first()
            if not partner_detail:
                return Response({"message": "Package provider detail not found."}, status=status.HTTP_404_NOT_FOUND)

            package_detail = HuzBasicDetail.objects.filter(huz_token=data.get('huz_token')).first()
            if not package_detail:
                return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

            total_cost = self.calculate_total_cost(package_detail, data)
            data['total_price'] = total_cost

            for key in ['partner_session_token', 'session_token', 'huz_token']:
                data.pop(key, None)

            serializer = ShortBookingSerializer(booking, data=data, partial=True)
            if not serializer.is_valid():
                first_error_field = next(iter(serializer.errors))
                first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

            with transaction.atomic():
                updated_booking = serializer.save()
                serialized_booking = DetailBookingSerializer(updated_booking)
                return Response(serialized_booking.data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Put - ManageBookingsView: {str(e)}")
            return Response({"message": "Failed to update booking request. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def calculate_total_cost(self, package_detail, data):
        def calculate_individual_cost(cost, quantity):
            try:
                return cost * int(quantity)
            except (TypeError, ValueError):
                return 0

        total_cost = calculate_individual_cost(package_detail.package_base_cost, data.get('adults', 0))
        total_cost += calculate_individual_cost(package_detail.cost_for_child, data.get('child', 0))
        total_cost += calculate_individual_cost(package_detail.cost_for_infants, data.get('infants', 0))

        room_type = data.get('room_type')
        room_type_costs = {
            "Sharing": package_detail.cost_for_sharing,
            "Quad": package_detail.cost_for_quad,
            "Triple": package_detail.cost_for_triple,
            "Double": package_detail.cost_for_double,
            "Single": package_detail.cost_for_single
        }

        if room_type in room_type_costs:
            total_cost += calculate_individual_cost(room_type_costs[room_type], data.get('adults', 0))

        return total_cost

    def generate_unique_booking_number(self):
        while True:
            booking_number = random.randint(1000000000, 9999999999)
            if not Booking.objects.filter(booking_number=booking_number).exists():
                return booking_number


    @swagger_auto_schema(
        operation_description="Retrieve booking details by user session token and booking number.",
        manual_parameters=[
            openapi.Parameter('session_token', openapi.IN_QUERY, description="Session token of the user", type=openapi.TYPE_STRING, required=True),
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
            session_token = request.GET.get('session_token')
            booking_number = request.GET.get('booking_number')

            # Check for required parameters
            if not session_token or not booking_number:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve user by session token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve booking by user and booking number
            booking = Booking.objects.filter(order_by=user, booking_number=booking_number).first()
            if not booking:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Serialize and return booking data
            serialized_package = DetailBookingSerializer(booking)
            return Response(serialized_package.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return a generic error response
            logger.error(f"Error in ManageBookingsView-Get: {str(e)}")
            return Response({"message": "Failed to get booking request. Internal server error.."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Delete a booking",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number to delete'),
            },
            required=['session_token', 'booking_number']
        ),
        responses={
            200: "Success: Booking request removed successfully",
            400: "Bad Request: Missing or invalid input data",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, Partner, or Package not found.",
            500: "Server error: Internal server error."
        }
    )
    def delete(self, request):
        try:
            session_token = request.data.get("session_token")
            booking_number = request.data.get("booking_number")
            # Check for missing required fields
            if not session_token or not booking_number:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            # Find the user by session token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Find the booking detail by user and booking number
            booking_detail = Booking.objects.filter(order_by=user, booking_number=booking_number).first()
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Ensure booking status is "Initialize"
            if booking_detail.booking_status != "Initialize":
                return Response({"message": "Oops, this request cannot be processed. Please contact the support team."}, status=status.HTTP_400_BAD_REQUEST)

            # Delete the booking
            booking_detail.delete()
            return Response({"message": "Selected booking request has been removed."}, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return a generic error response
            logger.error(f"Error in ManageBookingsView-Delete: {str(e)}")
            return Response({"message": "Failed to delete booking request. Internal server error.."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManagePassportValidityView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Submit a request to validate passport for a booking.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['session_token', 'first_name',  'last_name', 'date_of_birth', 'booking_number', 'passport_number', 'passport_country', 'expiry_date'],
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='User session token'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number associated with the user'),
                'first_name': openapi.Schema(type=openapi.TYPE_STRING, description='Enter user first name.'),
                'last_name': openapi.Schema(type=openapi.TYPE_STRING, description='Enter user last name'),
                'passport_number': openapi.Schema(type=openapi.TYPE_STRING, description='User passport number'),
                'passport_country': openapi.Schema(type=openapi.TYPE_STRING, description='Country of passport issuance'),
                'date_of_birth': openapi.Schema(type=openapi.TYPE_STRING, format='date', description='User D.O.B in YYYY-MM-DD format'),
                'expiry_date': openapi.Schema(type=openapi.TYPE_STRING, format='date', description='Passport expiry date in YYYY-MM-DD format'),
            }
        ),
        responses={
            201: openapi.Response(description="Passport validity request created", schema=DetailBookingSerializer),
            400: openapi.Response(description="Bad request (validation error or other issues)"),
            401: "Unauthorized: Admin permissions required",
            404: openapi.Response(description="User or booking not found"),
            409: openapi.Response(description="Conflict (Passport detail already exists)"),
            500: openapi.Response(description="Internal server error"),
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.data
            required_fields = ['session_token', 'booking_number', 'passport_number', 'passport_country', 'first_name',  'last_name', 'date_of_birth', 'expiry_date']

            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Find the user by session token
            user = UserProfile.objects.filter(session_token=data.get('session_token')).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Find the booking detail by user and booking number
            booking_detail = Booking.objects.filter(order_by=user, booking_number=data.get('booking_number')).first()
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if passport detail already exists
            check_passport = PassportValidity.objects.filter(passport_for_booking_number=booking_detail, passport_number=data.get('passport_number')).first()
            if check_passport:
                return Response({"message": "Passport detail already exists."}, status=status.HTTP_409_CONFLICT)

            # Remove session_token and booking_number from data
            for key in ['session_token', 'booking_number']:
                data.pop(key, None)

            # Add booking token to data
            data['passport_for_booking_number'] = booking_detail

            # Validate and save the payment transaction
            serializer = PassportValiditySerializer(data=data)
            if not serializer.is_valid():
                # Returning first error
                first_error_field = next(iter(serializer.errors))
                first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

            try:
                with transaction.atomic():
                    # Create the payment and update booking status
                    serializer.create(data)
                    booking_detail.booking_status = "Passport_Validation"
                    booking_detail.save()

                    # Serialize the updated booking detail
                    serialized_booking = DetailBookingSerializer(booking_detail)
                    return Response(serialized_booking.data, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.error(f"ManagePassportValidityView encountered an error: {str(e)}")
                return Response({"message": "Failed to create Passport validity request. Try again."}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Error in ManagePassportValidityView: {str(e)}")
            return Response({"message": "Failed to submit passport validity request. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Update passport validity details using passport_id.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['session_token', 'booking_number', 'first_name',  'last_name', 'date_of_birth', 'passport_id', 'passport_number', 'passport_country', 'expiry_date'],
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='User session token'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number associated with the user'),
                'passport_id': openapi.Schema(type=openapi.TYPE_STRING, description='ID of the passport validity entry to update'),
                'first_name': openapi.Schema(type=openapi.TYPE_STRING, description='Enter user first name.'),
                'last_name': openapi.Schema(type=openapi.TYPE_STRING, description='Enter user last name'),
                'passport_number': openapi.Schema(type=openapi.TYPE_STRING, description='Updated passport number'),
                'passport_country': openapi.Schema(type=openapi.TYPE_STRING, description='Updated country of passport issuance'),
                'date_of_birth': openapi.Schema(type=openapi.TYPE_STRING, format='date', description='User D.O.B in YYYY-MM-DD format'),
                'expiry_date': openapi.Schema(type=openapi.TYPE_STRING, format='date',description='Updated passport expiry date in YYYY-MM-DD format'),
            }
        ),
        responses={
            200: openapi.Response(description="Passport validity updated successfully", schema=DetailBookingSerializer),
            400: openapi.Response(description="Bad request (validation error or other issues)"),
            401: "Unauthorized: Admin permissions required",
            404: openapi.Response(description="User or booking not found"),
            409: openapi.Response(description="Conflict (Passport detail does not exist)"),
            500: openapi.Response(description="Internal server error"),
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            data = request.data
            required_fields = ['session_token', 'booking_number',  'first_name',  'last_name', 'date_of_birth', 'passport_id', 'passport_number', 'passport_country', 'expiry_date']
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Find the user by session token
            user = UserProfile.objects.filter(session_token=data.get('session_token')).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Find the booking detail by user and booking number
            booking_detail = Booking.objects.filter(order_by=user, booking_number=data.get('booking_number')).first()
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if passport detail already exists
            check_passport = PassportValidity.objects.filter(passport_id=data.get('passport_id')).first()
            if not check_passport:
                return Response({"message": "Passport detail not exists."}, status=status.HTTP_409_CONFLICT)

            # Update the passport detail
            serializer = PassportValiditySerializer(check_passport, data=data, partial=True)
            if not serializer.is_valid():
                # Returning first error
                first_error_field = next(iter(serializer.errors))
                first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

            try:
                with transaction.atomic():
                    # Update the passport validity entry
                    serializer.save()
                    traveller_status = PassportValidity.objects.filter(passport_for_booking_number=booking_detail)
                    is_completed = True  # Start by assuming all records are filled
                    for passport in traveller_status:
                        if not passport.user_passport or not passport.user_photo or not passport.first_name or not passport.last_name or not passport.date_of_birth or not passport.passport_number or not passport.passport_country or not passport.expiry_date:
                            is_completed = False
                            break
                    # Update the booking status if all documents are completed
                    if is_completed:
                        booking_detail.booking_status = "Pending"
                        booking_detail.save()

                    serialized_booking = DetailBookingSerializer(check_passport.passport_for_booking_number)
                    return Response(serialized_booking.data, status=status.HTTP_200_OK)

            except Exception as e:
                logger.error(f"ManagePassportValidityView PUT encountered an error: {str(e)}")
                return Response({"message": "Failed to update Passport validity request. Try again."}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            logger.error(f"Error in ManagePassportValidityView PUT: {str(e)}")
            return Response({"message": "Failed to update passport validity request. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetAllBookingsByUserView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get all bookings detail for a user",
        manual_parameters=[
            openapi.Parameter('session_token', openapi.IN_QUERY, description="Session token of the user", type=openapi.TYPE_STRING, required=True)
        ],
        responses={
            200: openapi.Response('Successful retrieval of booking details', DetailBookingSerializer(many=True)),
            400: "Bad Request: Missing or invalid input data",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, Partner, or Package not found.",
            500: "Server error: Internal server error."
        }
    )
    def get(self, request):
        try:
            # Retrieve the session token from the request
            session_token = self.request.GET.get('session_token', None)

            # Check if session token is provided
            if not session_token:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            # Find the user by session token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve all bookings for the user
            bookings = Booking.objects.filter(order_by=user)
            if not bookings:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Serialize the booking details
            serialized_bookings = DetailBookingSerializer(bookings, many=True)
            return Response(serialized_bookings.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return a generic error response
            logger.error(f"GetAllBookingsByUserView: {str(e)}")
            return Response({"message": "Failed to get booking details. Internal server error.."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PaidAmountByTransactionNumberView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Create - Record a payment transaction for a booking",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'transaction_number': openapi.Schema(type=openapi.TYPE_STRING, description='Transaction number'),
                'transaction_type': openapi.Schema(type=openapi.TYPE_STRING, description='Transaction type: Full or minimum'),
                'transaction_amount': openapi.Schema(type=openapi.TYPE_NUMBER, description='Transaction amount')
            },
            required=['session_token', 'booking_number', 'transaction_number', 'transaction_amount']
        ),
        responses={
            201: openapi.Response('Payment transaction created successfully', DetailBookingSerializer),
            400: "Bad Request: Missing or invalid input data",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, Partner, or Package not found.",
            409: "Conflict: Payment detail already exists.",
            500: "Server error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.data
            required_fields = ['session_token', 'booking_number', 'transaction_number', 'transaction_amount', 'transaction_type']

            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Find the user by session token
            user = UserProfile.objects.filter(session_token=data.get('session_token')).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)
            min_start_date = datetime.now().date() + timedelta(days=10)
            # Find the booking detail by user and booking number
            booking_detail = Booking.objects.filter(order_by=user, start_date__gte=min_start_date, booking_number=data.get('booking_number')).first()
            if not booking_detail:
                return Response({"message": "Booking detail not found or expire."}, status=status.HTTP_404_NOT_FOUND)

            package_detail = HuzBasicDetail.objects.filter(huz_id=booking_detail.package_token.huz_id).first()
            if not package_detail:
                return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if payment detail already exists
            check_payment = Payment.objects.filter(booking_token=booking_detail).first()

                # return Response({"message": "Payment detail already exists."}, status=status.HTTP_409_CONFLICT)

            # Remove session_token and booking_number from data
            for key in ['session_token', 'booking_number']:
                data.pop(key, None)

            # Add booking token to data
            data['booking_token'] = booking_detail

            # Validate and save the payment transaction
            serializer = PaymentSerializer(data=data)
            if not serializer.is_valid():
                # Returning first error
                first_error_field = next(iter(serializer.errors))
                first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

            try:
                with transaction.atomic():
                    # Create the payment and update booking status
                    serializer.create(data)
                    if not check_payment:
                        booking_detail.booking_status = "Paid"
                        booking_detail.save()

                        user_new_booking_email(user.email, user.name, package_detail.package_type,
                                               package_detail.package_name,
                                               booking_detail.booking_number, booking_detail.adults, booking_detail.child,
                                               booking_detail.infants,
                                               booking_detail.start_date, booking_detail.total_price, data.get('transaction_amount'))

                    # Serialize the updated booking detail
                    serialized_booking = DetailBookingSerializer(booking_detail)
                    return Response(serialized_booking.data, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.error(f"PaidAmountByTransactionNumberView encountered an error: {str(e)}")
                return Response({"message": "Failed to create payment request. Try again."}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Error in PaidAmountByTransactionNumberView: {str(e)}")
            return Response({"message": "Failed to submit payment request. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Update a payment transaction for a booking",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'payment_id': openapi.Schema(type=openapi.TYPE_STRING, description='ID of the payment record to update'),
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'transaction_number': openapi.Schema(type=openapi.TYPE_STRING, description='Transaction number'),
                'transaction_type': openapi.Schema(type=openapi.TYPE_STRING, description='transaction type: Full or minimum'),
                'transaction_amount': openapi.Schema(type=openapi.TYPE_NUMBER, description='Transaction amount')
            },
            required=['payment_id', 'session_token', 'booking_number', 'transaction_number', 'transaction_amount']
        ),
        responses={
            200: openapi.Response('Payment transaction updated successfully', DetailBookingSerializer),
            400: "Bad Request: Missing or invalid input data",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, Partner, or Package not found.",
            500: "Server error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            data = request.data
            required_fields = ['payment_id', 'session_token', 'booking_number', 'transaction_number', 'transaction_amount', 'transaction_type']

            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Find the user by session token
            user = UserProfile.objects.filter(session_token=data.get('session_token')).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Find the booking detail by user and booking number
            booking_detail = Booking.objects.filter(order_by=user, booking_number=data.get('booking_number')).first()
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Find the payment record by payment ID
            payment = Payment.objects.filter(payment_id=data.get('payment_id')).first()
            if not payment:
                return Response({"message": "Record not found."}, status=status.HTTP_404_NOT_FOUND)

            # Remove session_token and booking_number from data
            for key in ['session_token', 'booking_number']:
                data.pop(key, None)

            # Add booking token to data
            data['booking_token'] = booking_detail

            # Validate and update the payment transaction
            serializer = PaymentSerializer(payment, data=data, partial=True)
            if not serializer.is_valid():
                # Returning first error
                first_error_field = next(iter(serializer.errors))
                first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

            try:
                with transaction.atomic():
                    # Update the payment and booking status
                    serializer.save()
                    # booking_detail.booking_status = "Paid"
                    # booking_detail.save()

                    # Serialize the updated booking detail
                    serialized_booking = DetailBookingSerializer(booking_detail)
                    return Response(serialized_booking.data, status=status.HTTP_200_OK)
            except Exception as e:
                logger.error(f"Put - PaidAmountTransactionView: {str(e)}")
                return Response({"message": "Failed to update payment request. Try again."}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Put - PaidAmountTransactionView: {str(e)}")
            return Response({"message": "Failed to update payment request. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PaidAmountTransactionPhotoView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_description="Upload transaction photos and record a payment transaction for a booking",
        manual_parameters=[
            openapi.Parameter('transaction_photo', openapi.IN_FORM, type=openapi.TYPE_FILE, description='Transaction photo or file', required=True),
            openapi.Parameter('session_token', openapi.IN_FORM, type=openapi.TYPE_STRING, description='Session token of the user', required=True),
            openapi.Parameter('booking_number', openapi.IN_FORM, type=openapi.TYPE_STRING, description='Booking number', required=True),
            openapi.Parameter('transaction_amount', openapi.IN_FORM, type=openapi.TYPE_NUMBER, description='Transaction amount', required=True),
            openapi.Parameter('transaction_type', openapi.IN_FORM, type=openapi.TYPE_NUMBER, description='Transaction type: full or minimum', required=True)
        ],
        responses={
            201: openapi.Response('Transaction photo uploaded and payment transaction created successfully', DetailBookingSerializer(many=False)),
            400: "Bad Request: Missing or invalid input data",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, Partner, or Package not found.",
            500: "Server error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        files = request.FILES.getlist('transaction_photo')
        session_token = request.data.get('session_token')

        # Validate required files and session token
        if not all([files, session_token]):
            return Response({"message": "Missing file or required information."}, status=status.HTTP_400_BAD_REQUEST)
        min_start_date = datetime.now().date() + timedelta(days=10)
        data = request.data
        required_fields = ['session_token', 'booking_number', 'transaction_amount', 'transaction_type']
        # Validating required fields
        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        # Validate each file
        for file in files:
            if not check_file_format_and_size(file):
                return Response({"message": "Invalid file format or size."}, status=status.HTTP_400_BAD_REQUEST)

        # Find the user by session token
        user = UserProfile.objects.filter(session_token=session_token).first()
        if not user:
            return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        # Find the booking detail by user and booking number
        booking_detail = Booking.objects.filter(order_by=user, start_date__gte=min_start_date, booking_number=data.get('booking_number')).first()
        if not booking_detail:
            return Response({"message": "Booking detail not found or expire."}, status=status.HTTP_404_NOT_FOUND)

        package_detail = HuzBasicDetail.objects.filter(huz_id=booking_detail.package_token.huz_id).first()
        if not package_detail:
            return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

        check_payment = Payment.objects.filter(booking_token=booking_detail).first()

        # Remove session_token and booking_number from data
        for key in ['session_token', 'booking_number']:
            data.pop(key, None)

        try:
            with transaction.atomic():
                # Save each file and create payment record
                for file in files:
                    file_path = save_file_in_directory(file)
                    Payment.objects.create(
                        transaction_photo=file_path,
                        transaction_type=data.get('transaction_type'),
                        transaction_amount=data.get('transaction_amount'),
                        booking_token=booking_detail
                    )
                if not check_payment:
                    # Update booking status
                    booking_detail.booking_status = "Paid"
                    booking_detail.save()

                    user_new_booking_email(user.email, user.name, package_detail.package_type,
                                           package_detail.package_name,
                                           booking_detail.booking_number, booking_detail.adults, booking_detail.child,
                                           booking_detail.infants,
                                           booking_detail.start_date, booking_detail.total_price,
                                           data.get('transaction_amount'))

                # Serialize the updated booking detail
                serialized_booking = DetailBookingSerializer(booking_detail)
                return Response(serialized_booking.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Post - PaidAmountTransactionPhotoView: {str(e)}")
            return Response({"message": "Failed to update payment request. Internal server error."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DeleteAmountTransactionPhotoView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Delete a payment transaction record for a booking",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'payment_id': openapi.Schema(type=openapi.TYPE_STRING, description='Payment ID'),
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user')
            },
            required=['booking_number', 'payment_id', 'session_token']
        ),
        responses={
            200: "Success: Record deleted successfully",
            400: "Bad Request: Missing or invalid input data",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, Partner, or Package not found.",
            500: "Server error: Internal server error."
        }
    )
    def delete(self, request, *args, **kwargs):
        booking_number = request.data.get('booking_number')
        payment_id = request.data.get('payment_id')
        session_token = request.data.get('session_token')

        # Validate required fields
        if not all([booking_number, payment_id, session_token]):
            return Response({"message": "Missing required information."}, status=status.HTTP_400_BAD_REQUEST)

        # Find the user by session token
        user = UserProfile.objects.filter(session_token=session_token).first()
        if not user:
            return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        # Find the booking detail by user and booking number
        booking_detail = Booking.objects.filter(order_by=user, booking_number=booking_number).first()
        if not booking_detail:
            return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

        # Find the payment record by payment ID and booking token
        check_payment = Payment.objects.filter(payment_id=payment_id, booking_token=booking_detail).first()
        if not check_payment:
            return Response({"message": "Payment record not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            # Delete the associated transaction photo if it exists
            if check_payment.transaction_photo:
                delete_file_from_directory(check_payment.transaction_photo.name)
            check_payment.delete()
            return Response({"message": "Record deleted successfully."}, status=status.HTTP_200_OK)
        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Delete - PaidAmountTransactionPhotoView: {str(e)}")
            return Response({"message": "Failed to delete payment request. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManageUserPassportView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_description="Upload User Passport for a user's booking.",
        manual_parameters=[
            openapi.Parameter('user_passport', openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description='User passport'),
            openapi.Parameter('passport_id', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='Passport ID of user'),
            openapi.Parameter('session_token', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='User session token'),
            openapi.Parameter('booking_number', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='Booking number'),
        ],
        responses={
            201: openapi.Response('Passport uploaded successfully.', DetailBookingSerializer(many=False)),
            400: "Bad Request: Invalid file format or size, or missing required information.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, booking detail, or package detail not found.",
            409: 'Conflict: Payment issues or all documents already uploaded.',
            500: "Server error: Internal server error."
        },
    )
    def post(self, request, *args, **kwargs):
        # Extract files and required data from request
        user_passport = request.FILES.get('user_passport')
        passport_id = request.data.get('passport_id')
        session_token = request.data.get('session_token')
        booking_number = request.data.get('booking_number')

        # Validate required fields
        if not all([user_passport, passport_id, session_token, booking_number]):
            return Response({"message": "Missing file or required information."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate file format and size
        if not check_file_format_and_size(user_passport):
            return Response({"message": "Invalid file format or size."}, status=status.HTTP_400_BAD_REQUEST)

        # Find the user by session token
        user = UserProfile.objects.filter(session_token=session_token).first()
        if not user:
            return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        # Find the booking detail by user and booking number
        booking_detail = Booking.objects.filter(order_by=user, booking_number=booking_number).first()
        if not booking_detail:
            return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

        # Check booking status
        if booking_detail.booking_status in ["Initialize", "Paid"]:
            return Response({"message": "Payment issue. Please resolve payment first."}, status=status.HTTP_409_CONFLICT)

        # Find the package detail by huz_id
        package_detail = HuzBasicDetail.objects.filter(huz_id=booking_detail.package_token.huz_id).first()
        if not package_detail:
            return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

        # Check if the traveller information exists for the booking number

        # Check if the specific passport exists in traveller information
        traveller_info = PassportValidity.objects.filter(passport_id=passport_id).first()
        if not traveller_info:
            return Response({"message": "Traveller information not found."}, status=status.HTTP_404_NOT_FOUND)


        try:
            # Save the user passport file
            user_document_path = save_file_in_directory(user_passport)
            traveller_info.user_passport = user_document_path
            traveller_info.save()

            traveller_status = PassportValidity.objects.filter(passport_for_booking_number=booking_detail)
            if not traveller_status.exists():
                return Response({"message": "No Traveller information found."}, status=status.HTTP_404_NOT_FOUND)
            is_completed = True  # Start by assuming all records are filled
            for passport in traveller_status:
                if not passport.user_passport or not passport.user_photo or not passport.first_name or not passport.last_name or not passport.date_of_birth or not passport.passport_number or not passport.passport_country or not passport.expiry_date:
                    is_completed = False
                    break
            # Update the booking status if all documents are completed
            if is_completed:
                booking_detail.booking_status = "Pending"
                booking_detail.save()

            # Send new order notification email to partner
            partner_profile = PartnerProfile.objects.filter(partner_session_token=booking_detail.order_to).first()
            send_new_order_email(partner_profile.email,
                                 partner_profile.name,
                                 package_detail.package_type,
                                 package_detail.package_name,
                                 booking_detail.start_date,
                                 booking_detail.adults,
                                 booking_detail.infants,
                                 booking_detail.child,
                                 booking_detail.total_price,
                                 booking_detail.booking_number
                                 )

            # Serialize and return booking details
            serialized_booking = DetailBookingSerializer(booking_detail)
            return Response(serialized_booking.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Error during passport upload: {str(e)}")
            return Response({"message": "Failed to submit required documents. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManageUserPassportPhotoView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_description="Upload User Photo for a user's booking.",
        manual_parameters=[
            openapi.Parameter('user_photo', openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description='User photo'),
            openapi.Parameter('passport_id', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='Passport ID of user'),
            openapi.Parameter('session_token', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='User session token'),
            openapi.Parameter('booking_number', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='Booking number'),
        ],
        responses={
            201: openapi.Response('Passport uploaded successfully.', DetailBookingSerializer(many=False)),
            400: "Bad Request: Invalid file format or size, or missing required information.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, booking detail, or package detail not found.",
            409: 'Conflict: Payment issues or all documents already uploaded.',
            500: "Server error: Internal server error."
        },
    )
    def post(self, request, *args, **kwargs):
        # Extract files and required data from request
        user_photo = request.FILES.get('user_photo')
        passport_id = request.data.get('passport_id')
        session_token = request.data.get('session_token')
        booking_number = request.data.get('booking_number')

        # Validate required fields
        if not all([user_photo, passport_id, session_token, booking_number]):
            return Response({"message": "Missing file or required information."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate file format and size
        if not check_file_format_and_size(user_photo):
            return Response({"message": "Invalid file format or size."}, status=status.HTTP_400_BAD_REQUEST)

        # Find the user by session token
        user = UserProfile.objects.filter(session_token=session_token).first()
        if not user:
            return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        # Find the booking detail by user and booking number
        booking_detail = Booking.objects.filter(order_by=user, booking_number=booking_number).first()
        if not booking_detail:
            return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

        # Check booking status
        if booking_detail.booking_status in ["Initialize", "Paid"]:
            return Response({"message": "Payment issue. Please resolve payment first."}, status=status.HTTP_409_CONFLICT)

        # Find the package detail by huz_id
        package_detail = HuzBasicDetail.objects.filter(huz_id=booking_detail.package_token.huz_id).first()
        if not package_detail:
            return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

        # Check if the specific passport exists in traveller information
        traveller_info = PassportValidity.objects.filter(passport_id=passport_id).first()
        if not traveller_info:
            return Response({"message": "Traveller information not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            # Save the user passport file
            user_document_path = save_file_in_directory(user_photo)
            traveller_info.user_photo = user_document_path
            traveller_info.save()

            traveller_status = PassportValidity.objects.filter(passport_for_booking_number=booking_detail)
            if not traveller_status.exists():
                return Response({"message": "No Traveller information found."}, status=status.HTTP_404_NOT_FOUND)

            is_completed = True  # Start by assuming all records are filled
            for passport in traveller_status:
                if not passport.user_passport or not passport.user_photo or not passport.first_name or not passport.last_name or not passport.date_of_birth or not passport.passport_number or not passport.passport_country or not passport.expiry_date:
                    is_completed = False
                    break
            # Update the booking status if all documents are completed
            if is_completed:
                booking_detail.booking_status = "Pending"
                booking_detail.save()

            # Send new order notification email to partner
            partner_profile = PartnerProfile.objects.filter(partner_session_token=booking_detail.order_to).first()
            send_new_order_email(partner_profile.email,
                                 partner_profile.name,
                                 package_detail.package_type,
                                 package_detail.package_name,
                                 booking_detail.start_date,
                                 booking_detail.adults,
                                 booking_detail.infants,
                                 booking_detail.child,
                                 booking_detail.total_price,
                                 booking_detail.booking_number
                                 )

            # Serialize and return booking details
            serialized_booking = DetailBookingSerializer(booking_detail)
            return Response(serialized_booking.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Error during passport upload: {str(e)}")
            return Response({"message": "Failed to submit required documents. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManageUserRequiredDocumentsView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_description="Upload required documents for a user's booking.",
        manual_parameters=[
            openapi.Parameter('user_document', openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description='User passport or photo file'),
            openapi.Parameter('document_type', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='type of document user_passport or user_passport_photo'),
            openapi.Parameter('traveller_number', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='Traveller 1, Traveller 2 etc'),
            openapi.Parameter('session_token', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='User session token'),
            openapi.Parameter('booking_number', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='Booking number'),
        ],
        responses={
            201: openapi.Response('Documents uploaded successfully.', DetailBookingSerializer(many=False)),
            400: "Bad Request: Invalid file format or size, or missing required information.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, booking detail, or package detail not found.",
            409: 'Conflict: Payment issues or all documents already uploaded.',
            500: "Server error: Internal server error."
        },
    )
    def post(self, request, *args, **kwargs):
        # Extract files and required data from request
        user_document = request.FILES.get('user_document')
        document_type = request.data.get('document_type')
        traveller_number = request.data.get('traveller_number')
        session_token = request.data.get('session_token')
        booking_number = request.data.get('booking_number')

        # Validate required fields
        if not all([user_document, document_type, session_token, booking_number]):
            return Response({"message": "Missing file or required information."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate file format and size
        if not check_file_format_and_size(user_document):
            return Response({"message": "Invalid file format or size."}, status=status.HTTP_400_BAD_REQUEST)

        # Find the user by session token
        user = UserProfile.objects.filter(session_token=session_token).first()
        if not user:
            return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        # Find the booking detail by user and booking number
        booking_detail = Booking.objects.filter(order_by=user, booking_number=booking_number).first()
        if not booking_detail:
            return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

        # Check the booking status
        if booking_detail.booking_status == "Initialize":
            return Response({"message": "Payment is pending, please submit payment first."}, status=status.HTTP_409_CONFLICT)

        if booking_detail.booking_status == "Paid":
            return Response({"message": "Payment is not verified. Please wait."}, status=status.HTTP_409_CONFLICT)

        # Find the package detail by huz_id
        package_detail = HuzBasicDetail.objects.filter(huz_id=booking_detail.package_token.huz_id).first()
        if not package_detail:
            return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

        # Calculate the total number of users
        total_users = booking_detail.adults + booking_detail.child + booking_detail.infants
        total_users = total_users * 2
        # Check existing documents for the booking
        existing_docs_count = UserRequiredDocuments.objects.filter(user_document_for_booking_token=booking_detail).count()
        if total_users == existing_docs_count:
            return Response({"message": "All user documents have already been uploaded."}, status=status.HTTP_409_CONFLICT)
        # Increment the document count for new document
        new_doc_count = existing_docs_count + 1

        try:
            # Save files and create a new document record
            user_document_path = save_file_in_directory(user_document)
            UserRequiredDocuments.objects.create(
                comment=traveller_number,
                user_document=user_document_path,
                document_type=document_type,
                user_document_for_booking_token=booking_detail
            )

            # Update document status and booking status if all documents are uploaded
            if total_users == new_doc_count:
                doc_status = DocumentsStatus.objects.filter(status_for_booking=booking_detail).first()
                if doc_status:
                    doc_status.is_user_passport_completed = True
                    doc_status.save()
                booking_detail.booking_status = "Pending"
                booking_detail.save()

                # Send new order notification email to partner
                partner_profile = PartnerProfile.objects.filter(partner_session_token=booking_detail.order_to).first()
                send_new_order_email(partner_profile.email,
                                     partner_profile.name,
                                     package_detail.package_type,
                                     package_detail.package_name,
                                     booking_detail.start_date,
                                     booking_detail.adults,
                                     booking_detail.infants,
                                     booking_detail.child,
                                     booking_detail.total_price,
                                     booking_detail.booking_number
                                     )

            # Serialize booking details and return response
            serialized_booking = DetailBookingSerializer(booking_detail)
            return Response(serialized_booking.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"ManageUserRequiredDocumentsView: {str(e)}")
            return Response({"message": "Failed to submit required documents. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DeleteUserRequiredDocumentsView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Delete a user's required document record.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['booking_number', 'user_document_id', 'session_token'],
            properties={
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'user_document_id': openapi.Schema(type=openapi.TYPE_INTEGER,  description='ID of the user document to delete'),
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='User session token'),
            },
        ),
        responses={
            200: openapi.Response('Document record deleted successfully.'),
            400: "Bad Request: Missing required information.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User, booking detail, or document record not found.",
            500: "Server error: Internal server error."
        },
    )
    def delete(self, request, *args, **kwargs):
        booking_number = request.data.get('booking_number')
        user_document_id = request.data.get('user_document_id')
        session_token = request.data.get('session_token')

        # Validate required fields
        if not all([booking_number, user_document_id, session_token]):
            return Response({"message": "Missing required information."}, status=status.HTTP_400_BAD_REQUEST)

        # Find the user by session token
        user = UserProfile.objects.filter(session_token=session_token).first()
        if not user:
            return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        # Find the booking detail by user and booking number
        booking_detail = Booking.objects.filter(order_by=user, booking_number=booking_number).first()
        if not booking_detail:
            return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

        # Find the payment record by payment ID and booking token
        check_document = UserRequiredDocuments.objects.filter(user_document_id=user_document_id, user_document_for_booking_token=booking_detail).first()
        if not check_document:
            return Response({"message": "Document record not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            # Delete the associated transaction photo if it exists
            if check_document.user_document:
                delete_file_from_directory(check_document.user_document.name)
            check_document.delete()
            return Response({"message": "Record deleted successfully."}, status=status.HTTP_200_OK)
        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"DeleteUserRequiredDocumentsView: {str(e)}")
            return Response({"message": "Failed to delete document request. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BookingRatingAndReviewView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Submit a rating and review for a completed or closed booking.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['session_token', 'huz_concierge', 'huz_support', 'huz_platform', 'huz_service_quality',
                      'huz_response_time', 'huz_comment', 'partner_total_stars', 'partner_comment'],
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='User session token'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'huz_concierge': openapi.Schema(type=openapi.TYPE_INTEGER, description='Rating for Huz Concierge'),
                'huz_support': openapi.Schema(type=openapi.TYPE_INTEGER, description='Rating for Huz Support'),
                'huz_platform': openapi.Schema(type=openapi.TYPE_INTEGER, description='Rating for Huz Platform'),
                'huz_service_quality': openapi.Schema(type=openapi.TYPE_INTEGER, description='Rating for Huz Service Quality'),
                'huz_response_time': openapi.Schema(type=openapi.TYPE_INTEGER, description='Rating for Huz Response Time'),
                'huz_comment': openapi.Schema(type=openapi.TYPE_STRING, description='Comment for Huz services'),
                'partner_total_stars': openapi.Schema(type=openapi.TYPE_INTEGER, description='Total stars for Partner'),
                'partner_comment': openapi.Schema(type=openapi.TYPE_STRING, description='Comment for Partner services'),
            },
        ),
        responses={
            201: openapi.Response('Rating and review submitted successfully.', DetailBookingSerializer(many=False)),
            400: 'Bad Request: Missing required data fields or invalid input format.',
            401: "Unauthorized: Admin permissions required",
            404: 'Not Found:: User, booking detail, package provider, or package detail not found, or review already exists.',
            409: 'Conflict: Reviews and ratings can only be submitted after your booking is completed or closed.',
            500: "Server error: Internal server error."
        },
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.data

            # Validate required fields
            required_fields = ['session_token', 'huz_concierge', 'huz_support', 'huz_platform', 'huz_service_quality',
                               'huz_response_time', 'huz_comment', 'partner_total_stars', 'partner_comment']
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Find the user by session token
            user = UserProfile.objects.filter(session_token=data.get('session_token')).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Find the booking detail by user and booking number
            booking_detail = Booking.objects.filter(order_by=user, booking_number=data.get('booking_number')).first()
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if a rating and review already exists for this booking
            check_exist = BookingRatingAndReview.objects.filter(rating_for_booking=booking_detail).first()
            if check_exist:
                return Response({"message": "Rating & review record already exists."}, status=status.HTTP_404_NOT_FOUND)

            # Find the partner and package details
            partner_detail = PartnerProfile.objects.filter(partner_id=booking_detail.order_to.partner_id).first()
            if not partner_detail:
                return Response({"message": "Package provider detail not found."}, status=status.HTTP_404_NOT_FOUND)

            package_detail = HuzBasicDetail.objects.filter(huz_id=booking_detail.package_token.huz_id).first()
            if not package_detail:
                return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

            statuss = ["Completed", "Close"]
            # Check if the booking status allows for submitting reviews
            if booking_detail.booking_status not in statuss:
                return Response({"message": "Reviews and ratings can only be submitted after your booking is completed or closed."}, status=status.HTTP_409_CONFLICT)

            # Submit the rating and review within a transaction
            with transaction.atomic():
                BookingRatingAndReview.objects.create(
                    huz_concierge=data.get('huz_concierge'),
                    huz_support=data.get('huz_support'),
                    huz_platform=data.get('huz_platform'),
                    huz_service_quality=data.get('huz_service_quality'),
                    huz_response_time=data.get('huz_response_time'),
                    huz_comment=data.get('huz_comment'),
                    partner_total_stars=data.get('partner_total_stars'),
                    partner_comment=data.get('partner_comment'),
                    rating_for_booking=booking_detail,
                    rating_for_partner=partner_detail,
                    rating_for_package=package_detail,
                    rating_by_user=user
                )

                # Update the booking status if review submission is successful
                serialized_package = DetailBookingSerializer(booking_detail)
                return Response(serialized_package.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"BookingRatingAndReviewView: {str(e)}")
            return Response({"message": "Failed to submit review. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BookingComplaintsView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]
    MEGABYTE_LIMIT = 10.0

    @swagger_auto_schema(
        operation_description="Submit a complaint regarding a booking.",
        manual_parameters=[
            openapi.Parameter('session_token', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='User session token'),
            openapi.Parameter('booking_number', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='Booking number'),
            openapi.Parameter('complaint_title', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='Title of the complaint'),
            openapi.Parameter('complaint_message', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='Description of the complaint'),
            openapi.Parameter('response_message', openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description='Response message (optional)'),
            openapi.Parameter('audio_message', openapi.IN_FORM, type=openapi.TYPE_FILE, required=False, description='Audio message file (optional)'),
            openapi.Parameter('complaint_attachment', openapi.IN_FORM, type=openapi.TYPE_FILE, required=False, description='Complaint attachment file (optional)'),
        ],
        responses={
            201: openapi.Response('Complaint submitted successfully.', BookingComplaintsSerializer(many=False)),
            400: 'Bad Request: Missing required data fields, invalid file format, or size limit exceeded.',
            401: 'Unauthorized: Admin permissions required',
            404: 'Not Found:User, booking detail, package provider, or package detail not found.',
            409: 'Conflict: Complaint can only be raised when the booking status is Pending, Complete, Active, or Close.',
            500: 'Server error: Internal server error.'
        },
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.data
            file = request.data.get('audio_message')
            complaint_file = request.data.get('complaint_attachment')

            # Validate required fields and file if present
            if not file:
                required_fields = ['session_token', 'booking_number', 'complaint_title', 'complaint_message']
            else:
                required_fields = ['session_token', 'booking_number', 'complaint_title', 'complaint_message', 'audio_message']
                if not self.is_valid_file(file):
                    return Response({"message": "Invalid file format or size."}, status=status.HTTP_400_BAD_REQUEST)

            if complaint_file:
                if not check_file_format_and_size(complaint_file):
                    return Response({"message": "Invalid attachment file format or size."},
                                    status=status.HTTP_400_BAD_REQUEST)

            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Find user, booking detail, and related details
            user = UserProfile.objects.filter(session_token=data.get('session_token')).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            booking_detail = Booking.objects.filter(order_by=user, booking_number=data.get('booking_number')).first()
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            partner_detail = PartnerProfile.objects.filter(partner_id=booking_detail.order_to.partner_id).first()
            if not partner_detail:
                return Response({"message": "Package provider detail not found."}, status=status.HTTP_404_NOT_FOUND)

            package_detail = HuzBasicDetail.objects.filter(huz_id=booking_detail.package_token.huz_id).first()
            if not package_detail:
                return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the booking status allows for raising a complaint
            if booking_detail.booking_status not in ["Pending", "Completed", "Active", "Close"]:
                return Response({
                                    "message": "Complaint can only be raised when the booking status is Pending, Complete, Active, or Close."},
                                status=status.HTTP_409_CONFLICT)

            with transaction.atomic():
                booking_number = random.randint(1000000000, 9999999999)
                if not file and not complaint_file:
                    complaints = BookingComplaints.objects.create(
                        complaint_ticket=booking_number,
                        complaint_status='Open',
                        complaint_title=data.get('complaint_title'),
                        complaint_message=data.get('complaint_message'),
                        response_message=data.get('response_message', None),
                        complaint_for_booking=booking_detail,
                        complaint_for_partner=partner_detail,
                        complaint_for_package=package_detail,
                        complaint_by_user=user
                    )
                else:
                    # Save audio file if exists
                    audio_path = None
                    if file:
                        audio_path = save_file_in_directory(file)

                    # Save complaint attachment file if exists
                    attachment_path = None
                    if complaint_file:
                        attachment_path = save_file_in_directory(complaint_file)

                    complaints = BookingComplaints.objects.create(
                        audio_message=audio_path,
                        complaint_attachment=attachment_path,
                        complaint_ticket=booking_number,
                        complaint_status='Open',
                        complaint_title=data.get('complaint_title'),
                        complaint_message=data.get('complaint_message'),
                        response_message=data.get('response_message', None),
                        complaint_for_booking=booking_detail,
                        complaint_for_partner=partner_detail,
                        complaint_for_package=package_detail,
                        complaint_by_user=user
                    )

                send_complaint_email(partner_detail.email, partner_detail.name, booking_detail.booking_number, data.get('complaint_title'))

                serialized_package = BookingComplaintsSerializer(complaints)
                return Response(serialized_package.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Error in BookingComplaintsView: {str(e)}")
            return Response({"message": "An unexpected error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def is_valid_file(self, file):
        if not file.name.lower().endswith(('.aac', '.mp3', '.wav', '.m4a')):
            return False
        if file.size > self.MEGABYTE_LIMIT * 1024 * 1024:
            return False
        return True


class GetUserComplaintsView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Retrieve complaints submitted by a user.",
        manual_parameters=[
            openapi.Parameter('session_token', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True,
                              description='User session token')
        ],
        responses={
            200: openapi.Response('Complaints retrieved successfully.', BookingComplaintsSerializer(many=True)),
            400: 'Bad Request: Missing required data fields.',
            401: 'Unauthorized: Admin permissions required',
            404: 'Not Found: User not found or no complaints found.',
            500: 'Server error: Internal server error.'
        }
    )
    def get(self, request):
        try:
            session_token = request.GET.get('session_token')
            if not session_token:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            # Find the user by session token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve complaints submitted by the user
            check_complaint = BookingComplaints.objects.filter(complaint_by_user=user)

            if check_complaint.exists():
                serialized_complaints = BookingComplaintsSerializer(check_complaint, many=True)
                return Response(serialized_complaints.data, status=status.HTTP_200_OK)

            return Response({"message": "No complaints found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            # Log the exception with traceback
            logger.error(f"Error in GetUserComplaintsView: {str(e)}", exc_info=True)
            return Response({"message": "An unexpected error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ObjectionResponseView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_description="Respond to booking objection with required document and client remarks",
        manual_parameters=[
            openapi.Parameter('objection_document', openapi.IN_FORM, description="Document required for the objection", type=openapi.TYPE_FILE, required=True),
            openapi.Parameter('client_remarks', openapi.IN_FORM, description="Remarks from the client", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('objection_id', openapi.IN_FORM, description="ID of the objection", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('booking_number', openapi.IN_FORM, description="Booking number", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('session_token', openapi.IN_FORM, description="Session token of the client", type=openapi.TYPE_STRING, required=True),
        ],
        responses={
            201: openapi.Response(description='Booking status updated successfully'),
            400: openapi.Response(description='Invalid input or booking status'),
            401: 'Unauthorized: Admin permissions required',
            404: openapi.Response(description='User, booking, or objection detail not found'),
            500: openapi.Response(description='Internal server error')
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            # Extract data from request
            file = request.data.get('objection_document')
            client_remarks = request.data.get('client_remarks')
            objection_id = request.data.get('objection_id')
            booking_number = request.data.get('booking_number')
            session_token = request.data.get('session_token')

            data = request.data
            required_fields = ['objection_document', 'client_remarks', 'objection_id', 'booking_number', 'session_token']

            # Validate required fields
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Validate file format and size
            if not check_file_format_and_size(file):
                return Response({"message": "Invalid file format or size."}, status=status.HTTP_400_BAD_REQUEST)

            # Find the user with the provided session token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Find the booking detail associated with the user and booking number
            booking_detail = Booking.objects.filter(order_by=user, booking_number=booking_number).first()
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Ensure the booking status is 'Objection'
            if booking_detail.booking_status != "Objection":
                return Response({"message": "Invalid booking status. Booking status should be 'Objection'."}, status=status.HTTP_400_BAD_REQUEST)

            # Find the objection detail associated with the booking detail and objection ID
            objection_detail = BookingObjections.objects.filter(objection_id=objection_id, objection_for_booking=booking_detail).first()
            if not objection_detail:
                return Response({"message": "Objection detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Save the new file in the directory and update the objection detail
            file_path = save_file_in_directory(file)
            objection_detail.required_document_for_objection = file_path
            objection_detail.client_remarks = client_remarks
            objection_detail.save()

            # Update the booking status to 'Pending'
            booking_detail.booking_status = "Pending"
            booking_detail.save()

            # Serialize the updated booking detail and return the response
            serialized_booking = DetailBookingSerializer(booking_detail)
            return Response(serialized_booking.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Error in ObjectionResponseView: {str(e)}")
            return Response({"message": "Failed to update booking status. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManageHotelCheckIn(APIView):
    @swagger_auto_schema(
        operation_description="Update the check-in status for a booking in Makkah and Madinah.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=[
                'booking_number', 'session_token',
                'partner_session_token', 'is_check_in_makkah',
                'is_check_in_madinah'
            ],
            properties={
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING,description='Booking number for the reservation'),
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='User session token'),
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Partner session token'),
                'is_check_in_makkah': openapi.Schema(type=openapi.TYPE_BOOLEAN,description='Check-in status for Makkah'),
                'is_check_in_madinah': openapi.Schema(type=openapi.TYPE_BOOLEAN,description='Check-in status for Madinah'),
            },
        ),
        responses={
            201: openapi.Response(description="Booking check-in status updated successfully", schema=DetailBookingSerializer),
            400: openapi.Response(description="Bad Request - Missing or invalid fields"),
            401: 'Unauthorized: Admin permissions required',
            404: openapi.Response(description="Not Found - User, partner, or booking detail not found"),
            500: openapi.Response(description="Internal Server Error"),
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            data = request.data
            required_fields = [
                'booking_number', 'session_token',
                'partner_session_token', 'is_check_in_makkah',
                'is_check_in_madinah'
            ]

            # Validate that all required fields are present in the request data
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Retrieve user associated with the provided session token
            user = UserProfile.objects.filter(session_token=data.get('session_token')).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve partner associated with the provided partner session token
            partner_detail = PartnerProfile.objects.filter(partner_session_token=data.get('partner_session_token')).first()
            if not partner_detail:
                return Response({"message": "Package provider detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve booking details associated with the provided booking number
            booking_detail = Booking.objects.filter(booking_number=data.get('booking_number')).first()
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            if booking_detail.booking_status != "Completed":
                return Response(
                    {"message": "Check-in can only be managed when the booking status is 'Completed'."},
                    status=status.HTTP_409_CONFLICT
                )
            # Update booking check-in status for Makkah and Madinah
            booking_detail.is_check_in_makkah = data.get("is_check_in_makkah")
            booking_detail.is_check_in_madinah = data.get("is_check_in_madinah")
            booking_detail.save()

            # Serialize booking details and return within an atomic transaction
            with transaction.atomic():
                serialized_booking = DetailBookingSerializer(booking_detail)
                return Response(serialized_booking.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            # Log the error and return an internal server error response
            logger.error(f"PUT - ManageHotelCheckIn: {str(e)}")
            return Response(
                {"message": "Failed to update booking check-in request. Internal server error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class BookingRequestView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]
    MEGABYTE_LIMIT = 10.0

    @swagger_auto_schema(
        operation_description="Submit a complaint regarding a booking.",
        manual_parameters=[
            openapi.Parameter('session_token', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='User session token'),
            openapi.Parameter('booking_number', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='Booking number'),
            openapi.Parameter('request_title', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='Title of the request'),
            openapi.Parameter('request_message', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description='Description of the request'),
            openapi.Parameter('request_attachment', openapi.IN_FORM, type=openapi.TYPE_FILE, required=False,
                              description='request_attachment file (optional)'),
        ],
        responses={
            201: openapi.Response('Request submitted successfully.', BookingRequestSerializer(many=False)),
            400: 'Bad Request: Missing required data fields, invalid file format, or size limit exceeded.',
            401: 'Unauthorized: Admin permissions required',
            404: 'Not Found:User, booking detail, package provider, or package detail not found.',
            409: 'Conflict: Request can only be raised when the booking status is Completed or Closed.',
            500: 'Server error: Internal server error.'
        },
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.data
            file = request.data.get('request_attachment')

            # Validate required fields and file if present
            if not file:
                required_fields = ['session_token', 'booking_number', 'request_title', 'request_message']
            else:
                required_fields = ['session_token', 'booking_number', 'request_title', 'request_message', 'request_attachment']
                if not self.is_valid_file(file):
                    return Response({"message": "Invalid file format or size."}, status=status.HTTP_400_BAD_REQUEST)

            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Find user, booking detail, and related details
            user = UserProfile.objects.filter(session_token=data.get('session_token')).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            booking_detail = Booking.objects.filter(order_by=user, booking_number=data.get('booking_number')).first()
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            partner_detail = PartnerProfile.objects.filter(partner_id=booking_detail.order_to.partner_id).first()
            if not partner_detail:
                return Response({"message": "Package provider detail not found."}, status=status.HTTP_404_NOT_FOUND)

            package_detail = HuzBasicDetail.objects.filter(huz_id=booking_detail.package_token.huz_id).first()
            if not package_detail:
                return Response({"message": "Package detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the booking status allows for raising a complaint
            if booking_detail.booking_status not in ["Completed", "Active", "Closed"]:
                return Response(
                    {"message": "Request can only be raised when the booking status is Completed, Active or Closed."},
                    status=status.HTTP_409_CONFLICT)

            # Submit the complaint within a transaction
            with transaction.atomic():
                booking_number = random.randint(1000000000, 9999999999)
                if not file:
                    complaints = BookingRequest.objects.create(
                        request_ticket=booking_number,
                        request_status='Open',
                        request_title=data.get('request_title'),
                        request_message=data.get('request_message'),
                        request_for_booking=booking_detail,
                        request_for_partner=partner_detail,
                        request_for_package=package_detail,
                        request_by_user=user
                    )
                else:
                    file_path = save_file_in_directory(file)
                    complaints = BookingRequest.objects.create(
                        request_attachment=file_path,
                        request_ticket=booking_number,
                        request_status='Open',
                        request_title=data.get('request_title'),
                        request_message=data.get('request_message'),
                        request_for_booking=booking_detail,
                        request_for_partner=partner_detail,
                        request_for_package=package_detail,
                        request_by_user=user
                    )

            serialized_package = BookingRequestSerializer(complaints)
            return Response(serialized_package.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"Error in BookingRequestView: {str(e)}")
            return Response({"message": "An unexpected error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def is_valid_file(self, file):
        if not file.name.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.pdf')):
            return False
        if file.size > self.MEGABYTE_LIMIT * 1024 * 1024:
            return False
        return True


class GetUserRequestsView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Retrieve all request submitted by a user.",
        manual_parameters=[
            openapi.Parameter('session_token', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True,
                              description='User session token')
        ],
        responses={
            200: openapi.Response('Request retrieved successfully.', BookingRequestSerializer(many=True)),
            400: 'Bad Request: Missing required data fields.',
            401: 'Unauthorized: Admin permissions required',
            404: 'Not Found: User not found or no complaints found.',
            500: 'Server error: Internal server error.'
        }
    )
    def get(self, request):
        try:
            session_token = request.GET.get('session_token')
            if not session_token:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            # Find the user by session token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve complaints submitted by the user
            check_requests = BookingRequest.objects.filter(request_by_user=user)

            if check_requests.exists():
                serialized_complaints = BookingRequestSerializer(check_requests, many=True)
                return Response(serialized_complaints.data, status=status.HTTP_200_OK)

            return Response({"message": "No Request found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            # Log the exception with traceback
            logger.error(f"Error in GetUserRequestsView: {str(e)}", exc_info=True)
            return Response({"message": "An unexpected error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


