from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework import status, pagination
from .models import (
    PartnerProfile,
    HuzBasicDetail,
    HuzAirlineDetail,
    HuzTransportDetail,
    HuzHotelDetail,
    HuzPackageDateRange,
    HuzZiyarahDetail,
)
from .serializers import (
    HuzAlignedPackageSerializer,
    HuzAirlineSerializer,
    HuzBasicSerializer,
    HuzBasicShortSerializer,
    HuzHotelSerializer,
    HuzTransportSerializer,
    HuzZiyarahSerializer,
)
from common.logs_file import logger
from common.utility import generate_token, random_six_digits, validate_required_fields, CustomPagination
from datetime import datetime
from django.db.models import Avg, Count, ExpressionWrapper, F, FloatField, IntegerField, Min, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from datetime import datetime, timedelta
from django.utils.dateparse import parse_date

SUPPORTED_PACKAGE_TYPES = ("Hajj", "Umrah")


class CreateHuzPackageView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Create a new Huz Hajj or Umrah package.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING,description='Session token of the partner'),
                'package_type': openapi.Schema(type=openapi.TYPE_STRING, description='Type of the package'),
                'package_name': openapi.Schema(type=openapi.TYPE_STRING, description='Name of the package'),
                'package_base_cost': openapi.Schema(type=openapi.TYPE_NUMBER, description='Base Cost of the package'),
                'cost_for_child': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for child'),
                'cost_for_infants': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for infants'),
                'cost_for_sharing': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for sharing room'),
                'cost_for_quad': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for quad bed room'),
                'cost_for_triple': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for tripe bed room'),
                'cost_for_double': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for double bed room'),
                'cost_for_single': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for single bed room'),
                'mecca_nights': openapi.Schema(type=openapi.TYPE_INTEGER, description='Number of nights in Mecca'),
                'madinah_nights': openapi.Schema(type=openapi.TYPE_INTEGER, description='Number of nights in Madinah'),
                'start_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME, description='Start date of the package'),
                'end_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME, description='End date of the package'),
                'description': openapi.Schema(type=openapi.TYPE_STRING, description='Description of the package'),
                'is_visa_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether visa is included in the package'),
                'is_airport_reception_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether airport reception is included in the package'),
                'is_tour_guide_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether tour guide is included in the package'),
                'is_insurance_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether insurance is included in the package'),
                'is_breakfast_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether breakfast is included in the package'),
                'is_lunch_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether lunch is included in the package'),
                'is_dinner_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether dinner is included in the package'),
                'is_package_open_for_other_date': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether the package is open for other dates'),
                'package_validity': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME, description='Validity date of the package'),
            },
            required=['partner_session_token', 'package_type', 'package_name', 'package_base_cost', 'cost_for_child',
                      'cost_for_infants', 'mecca_nights', 'madinah_nights', 'start_date', 'end_date', 'package_validity'
                      ]
        ),
        responses={
            201: openapi.Response("Successful creation", HuzBasicSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User not found.",
            409: "Conflict: Account status or type issue.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        data = request.data

        # Extract partner session token from the request data
        partner_session_token = request.data.get('partner_session_token')
        if not partner_session_token:
            return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # Check the account status and partner type
        if user.account_status != "Active":
            return Response({"message": "Your account status does not allow you to perform this task. Please contact our support team for assistance."}, status=status.HTTP_409_CONFLICT)

        if user.partner_type == "Individual":
            return Response({"message": "Sorry, you are enrolled as an Individual."}, status=status.HTTP_409_CONFLICT)

        # List of required fields for package creation
        required_fields = ['package_type', 'package_name', 'package_base_cost',  'cost_for_child', 'cost_for_infants',
                           'cost_for_sharing', 'cost_for_quad', 'cost_for_triple', 'cost_for_double', 'cost_for_single',
                           'mecca_nights', 'madinah_nights',
                           'start_date', 'end_date', 'description', 'is_visa_included', 'is_airport_reception_included',
                           'is_tour_guide_included', 'is_insurance_included', 'is_breakfast_included',
                           'is_lunch_included', 'is_dinner_included', 'is_package_open_for_other_date',
                           'package_validity'
                           ]

        # Validate required fields
        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        random_key = random_six_digits()
        data['package_provider'] = user.partner_id  # Assign the user id to package_provider
        data['huz_token'] = generate_token(str(random_key) + str(datetime.now()))
        data['package_status'] = 'Initialize'
        data['package_stage'] = 1

        # Remove the partner session token from the data
        data.pop('partner_session_token', None)
        # Serialize the package data
        serializer = HuzBasicSerializer(data=data)
        if not serializer.is_valid():
            # Extracting first error message with field name
            first_error_field = next(iter(serializer.errors))
            first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
            return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Save the new package
            package = serializer.save()
            serialized_package = HuzBasicSerializer(package)
            return Response(serialized_package.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"CreateHuzPackageView - Post: {str(e)}")
            return Response({"message": "Failed to enroll package detail. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Update an existing Huz Hajj or Umrah package.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Huz package token'),
                'package_name': openapi.Schema(type=openapi.TYPE_STRING, description='Name of the package'),
                'package_base_cost': openapi.Schema(type=openapi.TYPE_NUMBER, description='Base Cost of the package'),
                'cost_for_child': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for child'),
                'cost_for_infants': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for infants'),
                'cost_for_sharing': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for sharing room'),
                'cost_for_quad': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for quad bed room'),
                'cost_for_triple': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for triple bed room'),
                'cost_for_double': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for double bed room'),
                'cost_for_single': openapi.Schema(type=openapi.TYPE_NUMBER, description='Cost for single bed room'),
                'mecca_nights': openapi.Schema(type=openapi.TYPE_INTEGER, description='Number of nights in Mecca'),
                'madinah_nights': openapi.Schema(type=openapi.TYPE_INTEGER, description='Number of nights in Madinah'),
                'start_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME, description='Start date of the package'),
                'end_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME, description='End date of the package'),
                'description': openapi.Schema(type=openapi.TYPE_STRING, description='Description of the package'),
                'is_visa_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether visa is included in the package'),
                'is_airport_reception_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether airport reception is included in the package'),
                'is_tour_guide_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether tour guide is included in the package'),
                'is_insurance_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether insurance is included in the package'),
                'is_breakfast_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether breakfast is included in the package'),
                'is_lunch_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether lunch is included in the package'),
                'is_dinner_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether dinner is included in the package'),
                'is_package_open_for_other_date': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether the package is open for other dates'),
                'package_validity': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME, description='Validity date of the package'),
            },
            required=['partner_session_token', 'huz_token']
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
        data = request.data
        partner_session_token = request.data.get('partner_session_token')
        huz_token = request.data.get('huz_token')
        if not partner_session_token or not huz_token:
            return Response({"message": "Missing user or package information."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # Check the account status and partner type
        if user.account_status != "Active":
            return Response({"message": "Your account status does not allow you to perform this task. Please contact our support team for assistance."}, status=status.HTTP_409_CONFLICT)

        # Retrieve the package based on the huz token
        package = HuzBasicDetail.objects.filter(huz_token=huz_token).first()
        if not package:
            return Response({"message": "Package not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # List of required fields
        required_fields = ['package_name', 'package_base_cost', 'cost_for_child', 'cost_for_infants',
                            'cost_for_sharing', 'cost_for_quad', 'cost_for_triple', 'cost_for_double', 'cost_for_single',
                            'mecca_nights', 'madinah_nights', 'start_date', 'end_date', 'description', 'is_visa_included',
                            'is_airport_reception_included', 'is_tour_guide_included', 'is_insurance_included',
                            'is_breakfast_included', 'is_lunch_included', 'is_dinner_included',
                            'is_package_open_for_other_date', 'package_validity']

        # Validate required fields
        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        data.pop('partner_session_token', None)
        serializer = HuzBasicSerializer(package, data=data, partial=True)
        if not serializer.is_valid():
            # Extracting first error message with field name
            first_error_field = next(iter(serializer.errors))
            first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
            return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)
        try:
            # Save the updated package
            package = serializer.save()
            serialized_package = HuzBasicSerializer(package)
            return Response(serialized_package.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"CreateHuzPackageView - Put: {str(e)}")
            return Response({"message": "Failed to update package detail. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CreateHuzAirlineView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Create airline details for a Huz Hajj or Umrah package.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Huz package token'),
                'airline_name': openapi.Schema(type=openapi.TYPE_STRING, description='Name of the airline'),
                'ticket_type': openapi.Schema(type=openapi.TYPE_STRING, description='Type of ticket'),
                'is_return_flight_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether return flight is included'),
                'flight_from': openapi.Schema(type=openapi.TYPE_STRING, description='Departure location'),
                'flight_to': openapi.Schema(type=openapi.TYPE_STRING, description='Destination location'),
                'return_flight_from': openapi.Schema(type=openapi.TYPE_STRING, description='Return Departure location'),
                'return_flight_to': openapi.Schema(type=openapi.TYPE_STRING, description='Return Destination location'),
            },
            required=['partner_session_token', 'huz_token', 'return_flight_from', 'return_flight_to', 'airline_name', 'ticket_type', 'is_return_flight_included', 'flight_from', 'flight_to']
        ),
        responses={
            201: openapi.Response("Successful creation", HuzBasicSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or package not found.",
            409: "Conflict: Airline info already exists for this package.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        data = request.data

        # Extract partner session token and huz token from the request data
        partner_session_token = data.get('partner_session_token')
        huz_token = data.get('huz_token')

        # Check if partner session token and huz token are provided
        if not partner_session_token or not huz_token:
            return Response({"message": "Missing user or package information."}, status=status.HTTP_400_BAD_REQUEST)

        # List of required fields for airline creation
        required_fields = ['airline_name', 'ticket_type', 'return_flight_from', 'return_flight_to', 'is_return_flight_included', 'flight_from', 'flight_to']
        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # Retrieve the Huz package based on the huz token and user
        package = HuzBasicDetail.objects.filter(package_provider=user, huz_token=huz_token).first()
        if not package:
            return Response({"message": "Package not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # Check if airline info already exists for the package
        check_exist = HuzAirlineDetail.objects.filter(airline_for_package=package).first()
        if check_exist:
            return Response({"message": "Airline info is already exist for this package."}, status=status.HTTP_409_CONFLICT)

        # Assign the package to airline data and remove unnecessary fields
        data['airline_for_package'] = package.huz_id
        data.pop('partner_session_token', None)
        data.pop('huz_token', None)

        # Serialize the airline data
        serializer = HuzAirlineSerializer(data=data)
        if not serializer.is_valid():
            # Extracting first error message with field name
            first_error_field = next(iter(serializer.errors))
            first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
            return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)
        try:
            # Save the new airline detail
            serializer.save()
            package.package_stage += 1
            package.save()
            serialized_package = HuzBasicSerializer(package)
            return Response(serialized_package.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"CreateHuzAirlineView - Post: {str(e)}")
            return Response({"message": "Failed to enroll airline detail. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Update airline details for a Huz Hajj or Umrah package.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING,
                                                        description='Session token of the partner'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Huz package token'),
                'airline_name': openapi.Schema(type=openapi.TYPE_STRING, description='Name of the airline'),
                'ticket_type': openapi.Schema(type=openapi.TYPE_STRING, description='Type of ticket'),
                'is_return_flight_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether return flight is included'),
                'flight_from': openapi.Schema(type=openapi.TYPE_STRING, description='Departure location'),
                'flight_to': openapi.Schema(type=openapi.TYPE_STRING, description='Destination location'),
                'return_flight_from': openapi.Schema(type=openapi.TYPE_STRING, description='Return Departure location'),
                'return_flight_to': openapi.Schema(type=openapi.TYPE_STRING, description='Return Destination location'),

            },
            required=['partner_session_token', 'return_flight_to', 'return_flight_from', 'huz_token', 'airline_name', 'ticket_type', 'is_return_flight_included',
                      'flight_from', 'flight_to']
        ),
        responses={
            200: openapi.Response("Successful update", HuzBasicSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or package not found.",
            409: "Conflict: Airline info already exists for this package.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        data = request.data

        # Extract partner session token and huz token from the request data
        partner_session_token = data.get('partner_session_token')
        huz_token = data.get('huz_token')

        # Check if partner session token and huz token are provided
        if not partner_session_token or not huz_token:
            return Response({"message": "Missing user or package information."}, status=status.HTTP_400_BAD_REQUEST)

        # List of required fields for airline update
        required_fields = ['airline_name', 'return_flight_from', 'return_flight_to', 'ticket_type', 'is_return_flight_included', 'flight_from', 'flight_to']
        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # Retrieve the Huz package based on the huz token and user
        package = HuzBasicDetail.objects.filter(package_provider=user, huz_token=huz_token).first()
        if not package:
            return Response({"message": "Package not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # Retrieve the existing airline detail for the package
        airline = HuzAirlineDetail.objects.filter(airline_for_package=package).first()
        if not airline:
            return Response({"message": "Airline detail not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # Assign the package to airline data and remove unnecessary fields
        data['airline_for_package'] = package.huz_id
        data.pop('partner_session_token', None)
        data.pop('huz_token', None)

        # Serialize the airline data
        serializer = HuzAirlineSerializer(airline, data=data, partial=True)
        if not serializer.is_valid():
            # Extracting first error message with field name
            first_error_field = next(iter(serializer.errors))
            first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
            return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)
        try:
            # Save the updated airline detail
            serializer.save()
            serialized_package = HuzBasicSerializer(package)
            return Response(serialized_package.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"CreateHuzAirlineView - Put: {str(e)}")
            return Response({"message": "Failed to update airline detail. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CreateHuzTransportView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Create transportation details for a Huz Hajj or Umrah package.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Huz package token'),
                'transport_name': openapi.Schema(type=openapi.TYPE_STRING, description='Name of the transportation'),
                'transport_type': openapi.Schema(type=openapi.TYPE_STRING, description='Type of transportation'),
                'routes': openapi.Schema(type=openapi.TYPE_STRING, description='Routes of transportation'),
            },
            required=['partner_session_token', 'huz_token', 'transport_name', 'transport_type', 'routes']
        ),
        responses={
            201: openapi.Response("Successful creation", HuzBasicSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User, package, or transportation details not found.",
            409: "Conflict: Transportation info already exists for this package.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        # Extract partner session token and huz token from the request data
        data = request.data
        partner_session_token = data.get('partner_session_token')
        huz_token = data.get('huz_token')

        # Check if partner session token and huz token are provided
        if not partner_session_token or not huz_token:
            return Response({"message": "Missing package or user information."}, status=status.HTTP_400_BAD_REQUEST)

        # List of required fields for transportation creation
        required_fields = ['transport_name', 'transport_type', 'routes']
        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the Huz package based on the huz token and user
        package = HuzBasicDetail.objects.filter(package_provider=user, huz_token=huz_token).first()
        if not package:
            return Response({"message": "Package not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

        # Check if transportation details already exist for this package
        check_exist = HuzTransportDetail.objects.filter(transport_for_package=package).first()
        if check_exist:
            return Response({"message": "Transport info is already exist for this package."}, status=status.HTTP_400_BAD_REQUEST)

        # Assign the package to transportation data and remove unnecessary fields
        data['transport_for_package'] = package
        data.pop('partner_session_token', None)
        data.pop('huz_token', None)

        # Serialize the transportation data
        serializer = HuzTransportSerializer(data=data)
        if not serializer.is_valid():
            # fetching first error
            first_error_field = next(iter(serializer.errors))
            first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
            return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Save the new transportation detail
            serializer.create(data)
            package.package_stage += 1
            package.save()

            # Serialize and return the updated package details
            serialized_package = HuzBasicSerializer(package)
            return Response(serialized_package.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"CreateHuzTransportView - Post: {str(e)}")
            return Response({"message": "Failed to enroll transport detail. Internal server error."}, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(
        operation_description="Update transportation details for a Huz Hajj or Umrah package.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Huz package token'),
                'transport_name': openapi.Schema(type=openapi.TYPE_STRING, description='Name of the transportation'),
                'transport_type': openapi.Schema(type=openapi.TYPE_STRING, description='Type of transportation'),
                'routes': openapi.Schema(type=openapi.TYPE_STRING, description='Routes of transportation'),
            },
            required=['partner_session_token', 'huz_token', 'transport_name', 'transport_type', 'routes']
        ),
        responses={
            200: openapi.Response("Successful update", HuzBasicSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User, package, or transportation details not found.",
            409: "Conflict: Transportation info does not exist for this package.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        # Extract partner session token and huz token from the request data
        data = request.data
        partner_session_token = data.get('partner_session_token')
        huz_token = data.get('huz_token')

        # Check if partner session token and huz token are provided
        if not partner_session_token or not huz_token:
            return Response({"message": "Missing package or user information."}, status=status.HTTP_400_BAD_REQUEST)

        # List of required fields for transportation update
        required_fields = ['transport_name', 'transport_type', 'routes']
        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the Huz package based on the huz token and user
        package = HuzBasicDetail.objects.filter(package_provider=user, huz_token=huz_token).first()
        if not package:
            return Response({"message": "Package not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve existing transportation details for the package
        transport = HuzTransportDetail.objects.filter(transport_for_package=package).first()
        if not transport:
            return Response({"message": "Transport info does not exist for this package."}, status=status.HTTP_409_CONFLICT)

        # Update the transportation details
        data['transport_for_package'] = package
        data.pop('partner_session_token', None)
        data.pop('huz_token', None)

        # Serialize the updated transportation data
        serializer = HuzTransportSerializer(transport, data=data, partial=True)
        if not serializer.is_valid():
            # fetching first error
            first_error_field = next(iter(serializer.errors))
            first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
            return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Save the updated transportation detail
            serializer.save()
            serialized_package = HuzBasicSerializer(package)
            return Response(serialized_package.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"CreateHuzTransportView - Put: {str(e)}")
            return Response({"message": "Failed to update transportation detail. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CreateHuzZiyarahView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Create Ziyarah details for a Huz Hajj or Umrah package.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Huz package token'),
                'ziyarah_list': openapi.Schema(type=openapi.TYPE_STRING, description='List of Ziyarah sites')
            },
            required=['partner_session_token', 'huz_token', 'ziyarah_list']
        ),
        responses={
            201: openapi.Response("Successful creation", HuzBasicSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or package not found.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        # Extract partner session token and huz token from the request data
        data = request.data
        partner_session_token = data.get('partner_session_token')
        huz_token = data.get('huz_token')

        # Check if partner session token and huz token are provided
        if not partner_session_token or not huz_token:
            return Response({"message": "Missing package or user information."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate required fields for Ziyarah creation
        required_fields = ['ziyarah_list']
        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the Huz package based on the huz token and user
        package = HuzBasicDetail.objects.filter(package_provider=user, huz_token=huz_token).first()
        if not package:
            return Response({"message": "Package not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

        # Check if ziyarah details already exist for this package
        check_exist = HuzZiyarahDetail.objects.filter(ziyarah_for_package=package).first()
        if check_exist:
            return Response({"message": "Ziyarah info is already exist for this package."}, status=status.HTTP_400_BAD_REQUEST)

        # Assign package to Ziyarah details and remove unnecessary fields
        data['ziyarah_for_package'] = package
        data.pop('partner_session_token', None)
        data.pop('huz_token', None)

        # Serialize Ziyarah data and handle validation
        serializer = HuzZiyarahSerializer(data=data)
        if not serializer.is_valid():
            # fetching first error
            first_error_field = next(iter(serializer.errors))
            first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
            return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Save the new Ziyarah detail
            serializer.create(data)
            # Update package stage
            package.package_stage += 1
            package.save()

            # Serialize and return the updated package details
            serialized_package = HuzBasicSerializer(package)
            return Response(serialized_package.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"CreateHuzZiyarahView - Post: {str(e)}")
            return Response({"message": "Failed to enroll ziyarah detail. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Update Ziyarah details for a Huz Hajj or Umrah package.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Huz package token'),
                'ziyarah_list': openapi.Schema(type=openapi.TYPE_STRING, description='List of Ziyarah sites')
            },
            required=['partner_session_token', 'huz_token', 'ziyarah_list']
        ),
        responses={
            200: openapi.Response("Successful update", HuzBasicSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or package not found.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        data = request.data

        # Extract partner session token and huz token from the request data
        partner_session_token = data.get('partner_session_token')
        huz_token = data.get('huz_token')

        # Check if partner session token and huz token are provided
        if not partner_session_token or not huz_token:
            return Response({"message": "Missing package or user information."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the Huz package based on the huz token and user
        package = HuzBasicDetail.objects.filter(package_provider=user, huz_token=huz_token).first()
        if not package:
            return Response({"message": "Package not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate required fields for Ziyarah update
        required_fields = ['ziyarah_list']
        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        # Update existing Ziyarah details for the package
        ziyarah = HuzZiyarahDetail.objects.filter(ziyarah_for_package=package).first()
        if not ziyarah:
            return Response({"message": "Ziyarah details not found for the provided package."}, status=status.HTTP_404_NOT_FOUND)

        # Assign updated data to the Ziyarah details
        serializer = HuzZiyarahSerializer(ziyarah, data=data, partial=True)
        if not serializer.is_valid():
            # fetching first error
            first_error_field = next(iter(serializer.errors))
            first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
            return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Save the updated Ziyarah detail
            serializer.save()

            # Serialize and return the updated package details
            serialized_package = HuzBasicSerializer(package)
            return Response(serialized_package.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"CreateHuzZiyarahView - Put: {str(e)}")
            return Response({"message": "Failed to update ziyarah detail. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CreateHuzHotelView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Create or update hotel details for a Huz Hajj or Umrah package.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Huz package token'),
                'hotel_city': openapi.Schema(type=openapi.TYPE_STRING, description='City where the hotel is located'),
                'hotel_name': openapi.Schema(type=openapi.TYPE_STRING, description='Name of the hotel'),
                'hotel_rating': openapi.Schema(type=openapi.TYPE_INTEGER, description='Rating of the hotel'),
                'room_sharing_type': openapi.Schema(type=openapi.TYPE_STRING, description='Type of room sharing'),
                'hotel_distance': openapi.Schema(type=openapi.TYPE_NUMBER, description='Distance of hotel from destination'),
                'distance_type': openapi.Schema(type=openapi.TYPE_STRING, description='Type of distance measurement'),
                'is_shuttle_services_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether shuttle services are included'),
                'is_air_condition': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether air conditioning is available'),
                'is_television': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether television is available'),
                'is_wifi': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether WiFi is available'),
                'is_elevator': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether elevator is available'),
                'is_attach_bathroom': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether attached bathroom is available'),
                'is_washroom_amenities': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether washroom amenities are provided'),
                'is_english_toilet': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether English toilet is available'),
                'is_indian_toilet': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether Indian toilet is available'),
                'is_laundry': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether laundry services are available'),
            },
            required=['partner_session_token', 'huz_token', 'hotel_city', 'hotel_name', 'hotel_rating', 'room_sharing_type', 'hotel_distance', 'distance_type', 'is_shuttle_services_included', 'is_air_condition', 'is_television', 'is_wifi', 'is_elevator', 'is_attach_bathroom', 'is_washroom_amenities', 'is_english_toilet', 'is_indian_toilet', 'is_laundry']
        ),
        responses={
            201: openapi.Response("Successful creation", HuzBasicSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User, package, or hotel details not found.",
            409: "Conflict: Hotel info already exists for this package.",
            500: "Server Error: Internal server error."
        }
    )
    def post(self, request, *args, **kwargs):
        # Extract partner session token and huz token from the request data
        data = request.data
        partner_session_token = data.get('partner_session_token')
        huz_token = data.get('huz_token')

        # Check if partner session token and huz token are provided
        if not partner_session_token or not huz_token:
            return Response({"message": "Missing package or user information."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the Huz package based on the huz token and user
        package = HuzBasicDetail.objects.filter(package_provider=user, huz_token=huz_token).first()
        if not package:
            return Response({"message": "Package not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate required fields for hotel creation or update
        required_fields = ['hotel_city', 'hotel_name', 'hotel_rating', 'room_sharing_type', 'hotel_distance', 'distance_type',
                           'is_shuttle_services_included', 'is_air_condition', 'is_television', 'is_wifi', 'is_elevator',
                           'is_attach_bathroom', 'is_washroom_amenities', 'is_english_toilet', 'is_indian_toilet', 'is_laundry']
        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        # Check if hotel details already exist for the package
        existing_hotel = HuzHotelDetail.objects.filter(
            hotel_city=data['hotel_city'],
            hotel_name=data['hotel_name'],
            hotel_rating=data['hotel_rating'],
            room_sharing_type=data['room_sharing_type'],
            hotel_for_package=package
        ).first()

        if existing_hotel:
            serialized_hotel = HuzBasicSerializer(package)
            return Response(serialized_hotel.data, status=status.HTTP_200_OK)

        # Assign package to hotel details and remove unnecessary fields
        data['hotel_for_package'] = package
        data.pop('partner_session_token', None)
        data.pop('huz_token', None)

        # Serialize hotel data and handle validation
        serializer = HuzHotelSerializer(data=data)
        if not serializer.is_valid():
            # fetching first error
            first_error_field = next(iter(serializer.errors))
            first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
            return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Save the new hotel detail
            serializer.create(data)
            package.package_stage += 1
            if data['hotel_city'] == "Madinah":
                package.package_status = "Completed"
            package.save()
            serialized_package = HuzBasicSerializer(package)
            return Response(serialized_package.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"CreateHuzHotelView - Post: {str(e)}")
            return Response({"message": "Failed to enroll hotel detail. Internal server error."}, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(
        operation_description="Update existing hotel details for a Huz Hajj or Umrah package.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Huz package token'),
                'hotel_id': openapi.Schema(type=openapi.TYPE_STRING, description='Hotel id'),
                'hotel_city': openapi.Schema(type=openapi.TYPE_STRING, description='City where the hotel is located'),
                'hotel_name': openapi.Schema(type=openapi.TYPE_STRING, description='Name of the hotel'),
                'hotel_rating': openapi.Schema(type=openapi.TYPE_INTEGER, description='Rating of the hotel'),
                'room_sharing_type': openapi.Schema(type=openapi.TYPE_STRING, description='Type of room sharing'),
                'hotel_distance': openapi.Schema(type=openapi.TYPE_NUMBER, description='Distance of hotel from destination'),
                'distance_type': openapi.Schema(type=openapi.TYPE_STRING, description='Type of distance measurement'),
                'is_shuttle_services_included': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether shuttle services are included'),
                'is_air_condition': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether air conditioning is available'),
                'is_television': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether television is available'),
                'is_wifi': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether WiFi is available'),
                'is_elevator': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether elevator is available'),
                'is_attach_bathroom': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether attached bathroom is available'),
                'is_washroom_amenities': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether washroom amenities are provided'),
                'is_english_toilet': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether English toilet is available'),
                'is_indian_toilet': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether Indian toilet is available'),
                'is_laundry': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Whether laundry services are available'),
            },
            required=['partner_session_token', 'huz_token', 'hotel_id', 'hotel_city', 'hotel_name', 'hotel_rating',
                      'room_sharing_type', 'hotel_distance', 'distance_type', 'is_shuttle_services_included',
                      'is_air_condition', 'is_television', 'is_wifi', 'is_elevator', 'is_attach_bathroom',
                      'is_washroom_amenities', 'is_english_toilet', 'is_indian_toilet', 'is_laundry']
        ),
        responses={
            200: openapi.Response("Successful update", HuzBasicSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User, package, or hotel details not found.",
            409: "Conflict: Hotel info already exists for this package.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        # Extract partner session token and huz token from the request data
        data = request.data
        partner_session_token = data.get('partner_session_token')
        huz_token = data.get('huz_token')

        # Check if partner session token and huz token are provided
        if not partner_session_token or not huz_token:
            return Response({"message": "Missing package or user information."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate required fields for hotel creation or update
        required_fields = ['hotel_city', 'hotel_name', 'hotel_rating', 'room_sharing_type', 'hotel_distance',
                           'distance_type', 'is_shuttle_services_included', 'is_air_condition', 'is_television',
                           'is_wifi', 'is_elevator', 'is_attach_bathroom', 'is_washroom_amenities',
                           'is_english_toilet',
                           'is_indian_toilet', 'is_laundry']

        error_response = validate_required_fields(required_fields, data)
        if error_response:
            return error_response

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the Huz package based on the huz token and user
        package = HuzBasicDetail.objects.filter(package_provider=user, huz_token=huz_token).first()
        if not package:
            return Response({"message": "Package not found with the provided detail."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the existing hotel details for the package
        existing_hotel = HuzHotelDetail.objects.filter(
            hotel_id=data['hotel_id'],
            hotel_for_package=package
        ).first()

        if not existing_hotel:
            return Response({"message": "Hotel not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # Update existing hotel details with new data
        serializer = HuzHotelSerializer(existing_hotel, data=data, partial=True)
        if not serializer.is_valid():
            # fetching first error
            first_error_field = next(iter(serializer.errors))
            first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
            return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Save the updated hotel detail
            serializer.save()
            # Update package stage and status based on hotel city
            if data.get('hotel_city') == "Madinah" and package.package_status != "Active":
                package.package_status = "Completed"
            package.save()

            # Serialize and return the updated package details
            serialized_package = HuzBasicSerializer(package)
            return Response(serialized_package.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"UpdateHuzHotelView - Put: {str(e)}")
            return Response({"message": "Failed to update hotet detail. Internal server error."}, status=status.HTTP_400_BAD_REQUEST)


class ManageHuzPackageStatusView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Update the status of a Huz package.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Huz package token'),
                'package_status': openapi.Schema(type=openapi.TYPE_STRING, description='New status for the package')
            },
            required=['partner_session_token', 'huz_token', 'package_status']
        ),
        responses={
            200: openapi.Response("Successful update", HuzBasicSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or package not found.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            # Extract necessary fields from the request data
            partner_session_token = request.data.get('partner_session_token')
            huz_token = request.data.get('huz_token')
            package_status = request.data.get('package_status')

            # Check if all required fields are provided
            if not partner_session_token or not huz_token or not package_status:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            list_of_status = ['Completed', 'Active', 'Deactivated']

            valid_statuses = [choice for choice in list_of_status]
            if package_status not in valid_statuses:
                return Response({"message": "Invalid package status."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve the partner profile based on the session token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve the Huz package based on the huz token and user
            package = HuzBasicDetail.objects.filter(huz_token=huz_token, package_provider=user).first()
            if not package:
                return Response({"message": "Package not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the package status is not "Block"
            if package.package_status == "Block":
                return Response({"message": "Blocked packages status cannot be changed."}, status=status.HTTP_400_BAD_REQUEST)

            # Update package status and save
            package.package_status = package_status
            package.save()

            # Serialize and return the updated package details
            serialized_package = HuzBasicSerializer(package)
            return Response(serialized_package.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return a response with a server error message
            logger.error(f"ManageHuzPackageStatusView - Put: {str(e)}")
            return Response({"message": "Failed to update package status. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetHuzShortPackageByTokenView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get a list of short Huz packages detail by token of partner with pagination",
        manual_parameters=[
            openapi.Parameter('partner_session_token', openapi.IN_QUERY, description="Session token of the partner", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Type of the package", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Page size", type=openapi.TYPE_INTEGER)
        ],
        responses={
            200: openapi.Response("Successful retrieval", HuzBasicSerializer(many=True)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or packages not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            partner_session_token = request.GET.get('partner_session_token')
            requested_package_type = request.GET.get('package_type')
            package_type = _normalize_package_type(requested_package_type)
            if not partner_session_token or not requested_package_type:
                return Response({"message": "Missing user or package type information."}, status=status.HTTP_400_BAD_REQUEST)
            if not package_type:
                return Response({"message": "Invalid package_type. Use Hajj or Umrah."}, status=status.HTTP_400_BAD_REQUEST)

            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Filter HuzBasicDetail queryset by user and package type
            packages_list = _supported_package_queryset().filter(
                package_provider=user,
                package_type=package_type,
            )
            serialized_package = HuzBasicSerializer(packages_list, many=True)
            return Response(serialized_package.data, status=status.HTTP_200_OK)

            # if packages_list.exists():
            #     # Initialize pagination & Paginate queryset based on request
            #     paginator = CustomPagination()
            #     paginated_packages = paginator.paginate_queryset(packages_list, request)
            #     serialized_package = HuzBasicShortSerializer(paginated_packages, many=True)
            #     return paginator.get_paginated_response(serialized_package.data)
            # else:
            #     return Response({"message": "Packages do not exist."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"GetHuzShortPackageByTokenView: {str(e)}")
            return Response({"message": "Failed to fetch packages list. Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetHuzPackageDetailByTokenView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get a detail of Huz packages by partner token and huz token",
        manual_parameters=[
            openapi.Parameter('partner_session_token', openapi.IN_QUERY, description="Session token of the partner", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('huz_token', openapi.IN_QUERY, description="Token of the package", type=openapi.TYPE_STRING, required=True),
        ],
        responses={
            200: openapi.Response("Successful retrieval", HuzBasicSerializer(many=True)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or packages not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            partner_session_token = request.GET.get('partner_session_token')
            huz_token = request.GET.get('huz_token')
            if not partner_session_token or not huz_token:
                return Response({"message": "Missing package or user information."}, status=status.HTTP_400_BAD_REQUEST)

            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

            # Filter HuzBasicDetail queryset by user and package huz token
            packages_list = _supported_package_queryset().filter(
                package_provider=user,
                huz_token=huz_token,
            )

            if packages_list.exists():
                serialized_package = HuzBasicSerializer(packages_list, many=True)
                return Response(serialized_package.data, status=status.HTTP_200_OK)
            else:
                return Response({"message": "Package do not exist."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"GetHuzPackageDetailByTokenView: {str(e)}")
            return Response({"message": "Failed to fetch packages detail. Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetPartnersOverallPackagesStatisticsView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        manual_parameters=[openapi.Parameter('partner_session_token', openapi.IN_QUERY, description="Partner's session token for authentication", type=openapi.TYPE_STRING, required=True)],
        responses={
            200: openapi.Response('Successful operation', openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'Initialize': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'Active': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'Deactivated': openapi.Schema(type=openapi.TYPE_INTEGER),
                },
            )),
            400: "Missing required data fields or invalid token",
            401: "Unauthorized: Admin permissions required.",
            404: "User not found with the provided detail",
            500: "Internal server error"
        }
    )
    def get(self, request):
        try:
            # Check if partner session token is provided
            partner_session_token = request.GET.get('partner_session_token')
            if not partner_session_token:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve the user based on the partner session token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()

            # If user is not found, return 404 Not Found
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Define package statuses to count
            package_status = ['Initialize', 'Completed', 'Active', 'Deactivated']

            # Query to count packages by status for the user
            package_count = _supported_package_queryset().filter(package_provider=user) \
                .values('package_status') \
                .annotate(total_count=Count('huz_id')) \
                .order_by('package_status')

            # Initialize dictionary to store counts of each status
            package_status_counts = {status_wise: 0 for status_wise in package_status}

            # Populate the dictionary with counts from the query results
            for item in package_count:
                package_status_counts[item['package_status']] = item['total_count']

            # Return the counts as a JSON response with status 200 OK
            return Response(package_status_counts, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"GetPartnersOverallPackagesStatisticsView: {str(e)}", exc_info=True)
            return Response({"message": "Failed to fetch overall statistics. Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


PACKAGE_TYPE_NORMALIZER = {
    "hajj": "Hajj",
    "umrah": "Umrah",
}

WEBSITE_SORTING_MAP = {
    "newest": ["-created_time"],
    "price-high": ["-package_base_cost", "-created_time"],
    "price-low": ["package_base_cost", "-created_time"],
    "start-date": ["next_available_start_date", "-created_time"],
}

WEBSITE_PACKAGE_PREFETCH_RELATED = (
    "package_provider__company_of_partner",
    "rating_for_package",
    "airline_for_package",
    "transport_for_package",
    "ziyarah_for_package",
    Prefetch(
        "hotel_for_package",
        queryset=HuzHotelDetail.objects.select_related("catalog_hotel").prefetch_related(
            "hotel_images",
            "catalog_hotel__hotel_images",
        ),
    ),
)


def _normalize_package_type(package_type):
    if not package_type:
        return None
    return PACKAGE_TYPE_NORMALIZER.get(str(package_type).strip().lower())


def _supported_package_queryset():
    return HuzBasicDetail.objects.filter(package_type__in=SUPPORTED_PACKAGE_TYPES)


def _parse_csv_values(raw_value):
    if not raw_value:
        return []

    values = str(raw_value).split(",")
    return [value.strip() for value in values if value and value.strip()]


def _parse_int_values(raw_value):
    parsed_values = []
    for value in _parse_csv_values(raw_value):
        try:
            parsed_values.append(int(value))
        except (TypeError, ValueError):
            continue
    return parsed_values


def _resolve_website_min_start_date(request, base_minimum_start_date):
    departure_date = request.GET.get("start_date") or request.GET.get("departure_date")
    parsed_departure_date = parse_date(departure_date) if departure_date else None
    if parsed_departure_date and parsed_departure_date > base_minimum_start_date:
        return parsed_departure_date
    return base_minimum_start_date


def _get_website_visibility_date():
    return datetime.now().date()


def _build_website_range_visibility_query(minimum_start_date, visibility_date):
    return (
        Q(package_date_ranges__start_date__date__gte=minimum_start_date)
        & (
            Q(package_date_ranges__package_validity__date__gte=visibility_date)
            | Q(
                package_date_ranges__package_validity__isnull=True,
                package_date_ranges__start_date__date__gte=visibility_date,
            )
        )
    )


def _build_website_package_visibility_query(minimum_start_date, visibility_date):
    return (
        Q(start_date__date__gte=minimum_start_date)
        & (
            Q(package_validity__date__gte=visibility_date)
            | Q(package_validity__isnull=True, start_date__date__gte=visibility_date)
        )
    )


def _get_website_date_range_prefetch(minimum_start_date, visibility_date):
    queryset = HuzPackageDateRange.objects.order_by("start_date", "end_date")
    if minimum_start_date:
        queryset = queryset.filter(start_date__date__gte=minimum_start_date)
    if visibility_date:
        queryset = queryset.filter(
            Q(package_validity__date__gte=visibility_date)
            | Q(package_validity__isnull=True, start_date__date__gte=visibility_date)
        )
    return Prefetch("package_date_ranges", queryset=queryset)


def _filter_available_website_packages(queryset, minimum_start_date, visibility_date):
    return queryset.filter(
        _build_website_range_visibility_query(minimum_start_date, visibility_date)
        | (
            Q(package_date_ranges__isnull=True)
            & _build_website_package_visibility_query(minimum_start_date, visibility_date)
        )
    )


def _optimize_website_package_queryset(queryset, minimum_start_date, visibility_date):
    upcoming_range_filter = _build_website_range_visibility_query(
        minimum_start_date,
        visibility_date,
    )
    return (
        queryset.select_related("package_provider")
        .annotate(
            next_available_start_date=Coalesce(
                Min("package_date_ranges__start_date", filter=upcoming_range_filter),
                F("start_date"),
            ),
        )
        .prefetch_related(
            *WEBSITE_PACKAGE_PREFETCH_RELATED,
            _get_website_date_range_prefetch(minimum_start_date, visibility_date),
        )
    )


def _build_website_package_queryset(package_type, minimum_start_date, visibility_date=None):
    visibility_date = visibility_date or _get_website_visibility_date()
    base_queryset = _supported_package_queryset().filter(
        package_type=package_type,
        package_status="Active",
    )
    base_queryset = _filter_available_website_packages(
        base_queryset,
        minimum_start_date,
        visibility_date,
    )
    return _optimize_website_package_queryset(
        base_queryset,
        minimum_start_date,
        visibility_date,
    )


def _apply_website_filters(queryset, request):
    query_params = request.GET

    search_query = (query_params.get("search") or "").strip()[:100]
    if search_query:
        queryset = queryset.filter(
            Q(package_name__icontains=search_query)
            | Q(huz_token__icontains=search_query)
            | Q(description__icontains=search_query)
            | Q(package_provider__name__icontains=search_query)
            | Q(package_provider__user_name__icontains=search_query)
            | Q(package_provider__company_of_partner__company_name__icontains=search_query)
            | Q(airline_for_package__flight_from__icontains=search_query)
            | Q(airline_for_package__flight_to__icontains=search_query)
        )

    operator_query = (query_params.get("operator") or "").strip()[:100]
    if operator_query:
        queryset = queryset.filter(
            Q(package_provider__name__icontains=operator_query)
            | Q(package_provider__user_name__icontains=operator_query)
            | Q(package_provider__company_of_partner__company_name__icontains=operator_query)
        )

    departure_values = _parse_csv_values(
        query_params.get("departure_cities")
        or query_params.get("departure_city")
        or query_params.get("flight_from")
    )
    if departure_values:
        departure_query = Q()
        for city in departure_values[:20]:
            departure_query |= Q(airline_for_package__flight_from__icontains=city)
        queryset = queryset.filter(departure_query)

    destination_values = _parse_csv_values(
        query_params.get("destination_cities")
        or query_params.get("destination_city")
        or query_params.get("flight_to")
    )
    if destination_values:
        destination_query = Q()
        for city in destination_values[:20]:
            destination_query |= Q(airline_for_package__flight_to__icontains=city)
        queryset = queryset.filter(destination_query)

    meals = {meal.lower() for meal in _parse_csv_values(query_params.get("meals"))}
    if meals:
        meal_query = Q()
        if "breakfast" in meals:
            meal_query |= Q(is_breakfast_included=True)
        if "lunch" in meals:
            meal_query |= Q(is_lunch_included=True)
        if "dinner" in meals:
            meal_query |= Q(is_dinner_included=True)
        if meal_query:
            queryset = queryset.filter(meal_query)

    ziyarah_values = _parse_csv_values(query_params.get("ziyarah"))
    if ziyarah_values:
        ziyarah_query = Q()
        for city in ziyarah_values[:20]:
            ziyarah_query |= Q(ziyarah_for_package__ziyarah_list__icontains=city)
        queryset = queryset.filter(ziyarah_query)

    trip_duration_values = _parse_int_values(query_params.get("trip_duration"))
    if trip_duration_values:
        max_trip_duration = max(trip_duration_values)
        queryset = queryset.annotate(
            total_nights=ExpressionWrapper(
                F("mecca_nights")
                + F("madinah_nights")
                + F("jeddah_nights")
                + F("taif_nights")
                + F("riyadah_nights"),
                output_field=IntegerField(),
            )
        ).filter(total_nights__lte=max_trip_duration)

    air_ticket_values = _parse_csv_values(query_params.get("air_tickets"))
    if air_ticket_values:
        normalized_tickets = {ticket.strip().lower() for ticket in air_ticket_values}
        ticket_query = Q()
        includes_without_ticket = any(
            ticket in {"without air tickets", "without-air-tickets", "without_air_tickets"}
            for ticket in normalized_tickets
        )
        filtered_ticket_types = [
            ticket for ticket in air_ticket_values if ticket.strip().lower() not in {
                "without air tickets",
                "without-air-tickets",
                "without_air_tickets",
            }
        ]
        if filtered_ticket_types:
            ticket_query |= Q(airline_for_package__ticket_type__in=filtered_ticket_types)
        if includes_without_ticket:
            ticket_query |= Q(airline_for_package__isnull=True)
        if ticket_query:
            queryset = queryset.filter(ticket_query)

    return queryset


def _apply_website_sorting(queryset, ordering):
    sort_key = (ordering or "newest").strip().lower()

    if sort_key == "top-rated":
        return queryset.annotate(
            average_package_rating=Avg("rating_for_package__partner_total_stars"),
            package_rating_count=Count("rating_for_package", distinct=True),
        ).order_by("-average_package_rating", "-package_rating_count", "-created_time")

    return queryset.order_by(*WEBSITE_SORTING_MAP.get(sort_key, WEBSITE_SORTING_MAP["newest"]))


class GetHuzShortPackageForWebsiteView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Get a paginated list of active website packages with optional filters, search, and sorting.",
        manual_parameters=[
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Type of package: Hajj or Umrah", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('search', openapi.IN_QUERY, description="Text search against package/operator fields", type=openapi.TYPE_STRING),
            openapi.Parameter('operator', openapi.IN_QUERY, description="Operator name search", type=openapi.TYPE_STRING),
            openapi.Parameter('departure_cities', openapi.IN_QUERY, description="Comma-separated departure cities", type=openapi.TYPE_STRING),
            openapi.Parameter('destination_cities', openapi.IN_QUERY, description="Comma-separated destination cities", type=openapi.TYPE_STRING),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Start date lower bound in YYYY-MM-DD", type=openapi.TYPE_STRING),
            openapi.Parameter('trip_duration', openapi.IN_QUERY, description="Comma-separated trip duration caps in days", type=openapi.TYPE_STRING),
            openapi.Parameter('air_tickets', openapi.IN_QUERY, description="Comma-separated ticket types", type=openapi.TYPE_STRING),
            openapi.Parameter('meals', openapi.IN_QUERY, description="Comma-separated meal filters", type=openapi.TYPE_STRING),
            openapi.Parameter('ziyarah', openapi.IN_QUERY, description="Comma-separated ziyarah city filters", type=openapi.TYPE_STRING),
            openapi.Parameter('ordering', openapi.IN_QUERY, description="Sort key: newest, price-high, price-low, top-rated, start-date", type=openapi.TYPE_STRING),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Page size", type=openapi.TYPE_INTEGER),
        ],
        responses={
            200: openapi.Response("Successful retrieval", HuzBasicShortSerializer(many=True)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            requested_package_type = request.GET.get('package_type')
            package_type = _normalize_package_type(requested_package_type)
            if not package_type:
                return Response({"message": "Invalid package_type. Use Hajj or Umrah."}, status=status.HTTP_400_BAD_REQUEST)

            default_min_start_date = _get_website_visibility_date()
            min_start_date = _resolve_website_min_start_date(request, default_min_start_date)
            packages_list = _build_website_package_queryset(package_type, min_start_date)
            packages_list = _apply_website_filters(packages_list, request)
            packages_list = _apply_website_sorting(packages_list, request.GET.get("ordering")).distinct()

            paginator = CustomPagination()
            paginated_packages = paginator.paginate_queryset(packages_list, request)
            serialized_package = HuzAlignedPackageSerializer(paginated_packages, many=True)
            return paginator.get_paginated_response(serialized_package.data)
        except Exception as e:
            logger.error(f"GetHuzShortPackageForWebsiteView: {str(e)}")
            return Response({"message": "Failed to fetch packages list. Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetHuzPackageDetailForWebsiteView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Get a detail of Huz packages by partner token and huz token",
        manual_parameters=[
            openapi.Parameter('huz_token', openapi.IN_QUERY, description="Token of the package", type=openapi.TYPE_STRING, required=True),
        ],
        responses={
            200: openapi.Response("Successful retrieval", HuzBasicSerializer(many=True)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or packages not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            huz_token = request.GET.get('huz_token')
            if not huz_token:
                return Response({"message": "Missing package information."}, status=status.HTTP_400_BAD_REQUEST)

            min_start_date = _get_website_visibility_date()
            packages_list = _filter_available_website_packages(
                _supported_package_queryset().filter(huz_token=huz_token, package_status="Active"),
                min_start_date,
                _get_website_visibility_date(),
            )
            packages_list = _optimize_website_package_queryset(
                packages_list,
                min_start_date,
                _get_website_visibility_date(),
            )

            if packages_list.exists():
                serialized_package = HuzAlignedPackageSerializer(packages_list, many=True)
                return Response(serialized_package.data, status=status.HTTP_200_OK)
            else:
                return Response({"message": "Package do not exist."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"GetHuzPackageDetailForWebsiteView: {str(e)}")
            return Response({"message": "Failed to fetch packages detail. Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetPackageCountCitiesWiseForWebsiteView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Type of the package to filter by", type=openapi.TYPE_STRING, required=True),],
        responses={
            200: openapi.Response('Successful operation', schema=openapi.Schema(
                type=openapi.TYPE_ARRAY,
                items=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'city_name': openapi.Schema(type=openapi.TYPE_STRING),
                        'package_count': openapi.Schema(type=openapi.TYPE_INTEGER),
                    },
                ),
            )),
            400: "package_type parameter is required",
            401: "Unauthorized: Admin permissions required.",
            404: "No Package exist for the provided package_type",
            500: "Internal server error"
        }
    )
    def get(self, request):
        try:
            # Check if package_type parameter is provided
            requested_package_type = self.request.GET.get('package_type', None)
            package_type = _normalize_package_type(requested_package_type)
            if not package_type:
                return Response({"message": "Invalid package_type. Use Hajj or Umrah."}, status=status.HTTP_400_BAD_REQUEST)
            min_start_date = _get_website_visibility_date()

            active_package_ids = _build_website_package_queryset(
                package_type,
                min_start_date,
            ).values_list('huz_id', flat=True).distinct()

            # Check if any active packages exist for the given package_type
            if not active_package_ids:
                return Response({"message": f"No Package exist for package type '{package_type}'."}, status=status.HTTP_404_NOT_FOUND)

            # Query to count packages grouped by flight_from (cities)
            flight_from_counts = HuzAirlineDetail.objects.filter(airline_for_package__in=active_package_ids).values('flight_from').annotate(package_count=Count('airline_for_package'))

            # Format the response as a list of dictionaries
            count_cities = [{"city_name": entry['flight_from'], "package_count": entry['package_count']} for entry in flight_from_counts]
            return Response(count_cities, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return 500 Internal Server Error for unexpected errors
            logger.error(f"GetPackageCountCitiesWiseForWebsiteView: {str(e)}")
            return Response({"message": "Failed to fetch city wise packages detail. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetHuzFeaturedPackageForWebsiteView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Get paginated featured website packages.",
        manual_parameters=[
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Type of package: Hajj or Umrah", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('ordering', openapi.IN_QUERY, description="Sort key: newest, price-high, price-low, top-rated, start-date", type=openapi.TYPE_STRING),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Page size", type=openapi.TYPE_INTEGER),
        ],
        responses={
            200: openapi.Response("Successful retrieval", HuzBasicShortSerializer(many=True)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            requested_package_type = request.GET.get('package_type')
            package_type = _normalize_package_type(requested_package_type)
            if not package_type:
                return Response({"message": "Invalid package_type. Use Hajj or Umrah."}, status=status.HTTP_400_BAD_REQUEST)

            default_min_start_date = _get_website_visibility_date()
            min_start_date = _resolve_website_min_start_date(request, default_min_start_date)
            packages_list = _build_website_package_queryset(package_type, min_start_date).filter(is_featured=True)
            packages_list = _apply_website_sorting(packages_list, request.GET.get("ordering")).distinct()

            paginator = CustomPagination()
            paginated_packages = paginator.paginate_queryset(packages_list, request)
            serialized_package = HuzAlignedPackageSerializer(paginated_packages, many=True)
            return paginator.get_paginated_response(serialized_package.data)
        except Exception as e:
            logger.error(f"GetHuzShortPackageForWebsiteView: {str(e)}")
            return Response({"message": "Failed to fetch packages list. Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetSearchPackageByCityNDateView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary="Retrieve a list of active packages based on search criteria",
        operation_description="Fetches a paginated list of active packages based on city/date and optional listing filters.",
        manual_parameters=[
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Type of package: Hajj or Umrah",type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Start date lower bound in YYYY-MM-DD format", type=openapi.TYPE_STRING),
            openapi.Parameter('flight_from', openapi.IN_QUERY, description="Departure location of the flight",type=openapi.TYPE_STRING),
            openapi.Parameter('search', openapi.IN_QUERY, description="Text search against package/operator fields", type=openapi.TYPE_STRING),
            openapi.Parameter('operator', openapi.IN_QUERY, description="Operator name search", type=openapi.TYPE_STRING),
            openapi.Parameter('destination_cities', openapi.IN_QUERY, description="Comma-separated destination cities", type=openapi.TYPE_STRING),
            openapi.Parameter('trip_duration', openapi.IN_QUERY, description="Comma-separated trip duration caps in days", type=openapi.TYPE_STRING),
            openapi.Parameter('air_tickets', openapi.IN_QUERY, description="Comma-separated ticket types", type=openapi.TYPE_STRING),
            openapi.Parameter('meals', openapi.IN_QUERY, description="Comma-separated meal filters", type=openapi.TYPE_STRING),
            openapi.Parameter('ziyarah', openapi.IN_QUERY, description="Comma-separated ziyarah city filters", type=openapi.TYPE_STRING),
            openapi.Parameter('ordering', openapi.IN_QUERY, description="Sort key: newest, price-high, price-low, top-rated, start-date", type=openapi.TYPE_STRING),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Page size", type=openapi.TYPE_INTEGER),
        ],
        responses={
            200: openapi.Response("Successful retrieval", HuzBasicShortSerializer(many=True)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            requested_package_type = request.GET.get('package_type')
            package_type = _normalize_package_type(requested_package_type)
            if not package_type:
                return Response({"message": "Invalid package_type. Use Hajj or Umrah."}, status=status.HTTP_400_BAD_REQUEST)

            default_min_start_date = _get_website_visibility_date()
            min_start_date = _resolve_website_min_start_date(request, default_min_start_date)
            packages_list = _build_website_package_queryset(package_type, min_start_date)
            packages_list = _apply_website_filters(packages_list, request)
            packages_list = _apply_website_sorting(packages_list, request.GET.get("ordering")).distinct()

            paginator = CustomPagination()
            paginated_packages = paginator.paginate_queryset(packages_list, request)
            serialized_package = HuzAlignedPackageSerializer(paginated_packages, many=True)
            return paginator.get_paginated_response(serialized_package.data)
        except Exception as e:
            logger.error(f"GetHuzShortPackageForWebsiteView: {str(e)}")
            return Response({"message": "Failed to fetch packages list. Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
