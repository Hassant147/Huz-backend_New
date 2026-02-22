from rest_framework.views import APIView
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework import status, pagination
from .models import PartnerProfile, HuzBasicDetail, HuzAirlineDetail, HuzTransportDetail, HuzHotelDetail, HuzZiyarahDetail
from .serializers import HuzBasicSerializer, HuzAirlineSerializer, HuzTransportSerializer, HuzHotelSerializer, HuzZiyarahSerializer, HuzBasicShortSerializer
from common.logs_file import logger
from common.utility import generate_token, random_six_digits, validate_required_fields, CustomPagination
from datetime import datetime
from django.db.models import Sum, Count
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from datetime import datetime, timedelta
from django.utils.dateparse import parse_date


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
            package_type = request.GET.get('package_type')
            if not partner_session_token or not package_type:
                return Response({"message": "Missing user or package type information."}, status=status.HTTP_400_BAD_REQUEST)

            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Filter HuzBasicDetail queryset by user and package type
            packages_list = HuzBasicDetail.objects.filter(package_provider=user, package_type=package_type)
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
            packages_list = HuzBasicDetail.objects.filter(package_provider=user, huz_token=huz_token)

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
            package_count = HuzBasicDetail.objects.filter(package_provider=user) \
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


class GetHuzShortPackageForWebsiteView(APIView):

    @swagger_auto_schema(
        operation_description="Get a list of short Huz packages detail by token of partner with pagination",
        manual_parameters=[
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Type of the package", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Page size", type=openapi.TYPE_INTEGER)
        ],
        responses={
            200: openapi.Response("Successful retrieval", HuzBasicShortSerializer(many=True)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or packages not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            package_type = request.GET.get('package_type')

            min_start_date = datetime.now().date() + timedelta(days=10)
            # Filter HuzBasicDetail queryset by user and package type
            packages_list = HuzBasicDetail.objects.filter(package_type=package_type, package_status="Active", start_date__gte=min_start_date)
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
            logger.error(f"GetHuzShortPackageForWebsiteView: {str(e)}")
            return Response({"message": "Failed to fetch packages list. Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetHuzPackageDetailForWebsiteView(APIView):

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

            # Filter HuzBasicDetail queryset by user and package huz token
            packages_list = HuzBasicDetail.objects.filter(huz_token=huz_token, package_status="Active")

            if packages_list.exists():
                serialized_package = HuzBasicSerializer(packages_list, many=True)
                return Response(serialized_package.data, status=status.HTTP_200_OK)
            else:
                return Response({"message": "Package do not exist."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"GetHuzPackageDetailForWebsiteView: {str(e)}")
            return Response({"message": "Failed to fetch packages detail. Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetPackageCountCitiesWiseForWebsiteView(APIView):

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
            package_type = self.request.GET.get('package_type', None)
            if not package_type:
                return Response({"message": "package_type parameter is required"}, status=status.HTTP_400_BAD_REQUEST)
            min_start_date = datetime.now().date() + timedelta(days=10)

            # Get active package IDs for the specified package type
            active_package_ids = HuzBasicDetail.objects.filter(package_status="Active", package_type=package_type, start_date__gte=min_start_date).values_list('huz_id', flat=True)

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

    @swagger_auto_schema(
        operation_description="Get a list of Feature Huz packages detail with pagination",
        manual_parameters=[
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Type of the package", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Page size", type=openapi.TYPE_INTEGER)
        ],
        responses={
            200: openapi.Response("Successful retrieval", HuzBasicShortSerializer(many=True)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or packages not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            package_type = request.GET.get('package_type')
            min_start_date = datetime.now().date() + timedelta(days=10)
            # Filter HuzBasicDetail queryset by user and package type
            packages_list = HuzBasicDetail.objects.filter(is_featured=True, package_type=package_type, package_status="Active", start_date__gte=min_start_date)

            if packages_list.exists():
                serialized_package = HuzBasicSerializer(packages_list, many=True)
                return Response(serialized_package.data, status=status.HTTP_200_OK)
                # # Initialize pagination & Paginate queryset based on request
                # paginator = CustomPagination()
                # paginated_packages = paginator.paginate_queryset(packages_list, request)
                # serialized_package = HuzBasicShortSerializer(paginated_packages, many=True)
                # return paginator.get_paginated_response(serialized_package.data)
            else:
                return Response({"message": "Packages do not exist."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"GetHuzShortPackageForWebsiteView: {str(e)}")
            return Response({"message": "Failed to fetch packages list. Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetSearchPackageByCityNDateView(APIView):

    @swagger_auto_schema(
        operation_summary="Retrieve a list of active packages based on search criteria",
        operation_description="Fetches a paginated list of HuzBasicDetail packages based on `package_type`, `start_date`, and `flight_from` filter parameters. Only packages with an active status are returned.",
        manual_parameters=[
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Type of the package (e.g., Hajj, Umrah, Ziyarah)",type=openapi.TYPE_STRING),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Start date of the package in YYYY-MM-DD format", type=openapi.FORMAT_DATE),
            openapi.Parameter('flight_from', openapi.IN_QUERY, description="Departure location of the flight",type=openapi.TYPE_STRING),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Page size", type=openapi.TYPE_INTEGER)
        ],
        responses={
            200: openapi.Response("Successful retrieval", HuzBasicShortSerializer(many=True)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or packages not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            package_type = request.GET.get('package_type')
            start_date = request.GET.get('start_date')
            flight_from = request.GET.get('flight_from')

            start_date1 = parse_date(start_date)
            if start_date1 and start_date1 >= (datetime.now() + timedelta(days=10)).date():
                # Filter HuzBasicDetail queryset by user and package type
                packages_list = HuzBasicDetail.objects.filter(package_type=package_type, start_date=start_date, package_status="Active", airline_for_package__flight_from=flight_from)
                if packages_list.exists():
                    serialized_package = HuzBasicSerializer(packages_list, many=True)
                    return Response(serialized_package.data, status=status.HTTP_200_OK)
                    # # Initialize pagination & Paginate queryset based on request
                    # paginator = CustomPagination()
                    # paginated_packages = paginator.paginate_queryset(packages_list, request)
                    # serialized_package = HuzBasicShortSerializer(paginated_packages, many=True)
                    # return paginator.get_paginated_response(serialized_package.data)
                else:
                    min_start_date = datetime.now().date() + timedelta(days=10)
                    # Filter HuzBasicDetail queryset by user and package type
                    packages_list = HuzBasicDetail.objects.filter(package_type=package_type, package_status="Active",
                                                                  start_date__gte=min_start_date, airline_for_package__flight_from=flight_from)

                    if packages_list.exists():
                        # Initialize pagination & Paginate queryset based on request
                        serialized_package = HuzBasicSerializer(packages_list, many=True)
                        return Response(serialized_package.data, status=status.HTTP_200_OK)
                        # paginator = CustomPagination()
                        # paginated_packages = paginator.paginate_queryset(packages_list, request)
                        # serialized_package = HuzBasicShortSerializer(paginated_packages, many=True)
                        # return paginator.get_paginated_response(serialized_package.data)
                    else:
                        return Response({"message": "Packages do not exist."}, status=status.HTTP_404_NOT_FOUND)
            else:
                min_start_date = datetime.now().date() + timedelta(days=10)
                # Filter HuzBasicDetail queryset by user and package type
                packages_list = HuzBasicDetail.objects.filter(package_type=package_type, package_status="Active",
                                                              start_date__gte=min_start_date,
                                                              airline_for_package__flight_from=flight_from)

                if packages_list.exists():
                    serialized_package = HuzBasicSerializer(packages_list, many=True)
                    return Response(serialized_package.data, status=status.HTTP_200_OK)
                    # Initialize pagination & Paginate queryset based on request
                    # paginator = CustomPagination()
                    # paginated_packages = paginator.paginate_queryset(packages_list, request)
                    # serialized_package = HuzBasicShortSerializer(paginated_packages, many=True)
                    # return paginator.get_paginated_response(serialized_package.data)
                else:
                    return Response({"message": "Packages do not exist."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"GetHuzShortPackageForWebsiteView: {str(e)}")
            return Response({"message": "Failed to fetch packages list. Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)