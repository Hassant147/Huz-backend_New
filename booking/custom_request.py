from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAdminUser
from .models import CustomPackages
from .serializers import CustomPackageSerializer
from common.models import UserProfile
from common.logs_file import logger
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
import random


class CustomPackageAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Retrieve custom packages requested by the user or all packages if user not found",
        responses={
            200: openapi.Response('Success', CustomPackageSerializer),
            404: openapi.Response('User not found'),
            500: openapi.Response('Internal Server Error'),
        }
    )
    def get(self, request):
        """ Retrieve custom packages requested by the user or all packages """
        data = request.data
        session_token = data.get('session_token')

        try:
            if session_token:
                user = UserProfile.objects.filter(session_token=session_token).first()
                if not user:
                    return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)
                packages = CustomPackages.objects.filter(request_by=user)
            else:
                packages = CustomPackages.objects.all()

            serializer = CustomPackageSerializer(packages, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error in retrieving custom packages: {str(e)}")
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Create a new custom package request",
        request_body=CustomPackageSerializer,
        responses={
            201: openapi.Response('Created', CustomPackageSerializer),
            400: openapi.Response('Invalid Data'),
            404: openapi.Response('User not found'),
            500: openapi.Response('Internal Server Error'),
        }
    )
    def post(self, request):
        data = request.data
        try:
            user = UserProfile.objects.filter(session_token=data.get('session_token')).first()

            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            data.pop('session_token', None)
            data['request_by'] = user.user_id
            data['request_number'] = self.generate_unique_booking_number()

            serializer = CustomPackageSerializer(data=data)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            logger.error(f"Error in creating custom package: {str(e)}")
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def generate_unique_booking_number(self):
        """ Generate a unique booking number for the custom package """
        while True:
            request_number = random.randint(1000000000, 9999999999)
            if not CustomPackages.objects.filter(request_number=request_number).exists():
                return request_number

    @swagger_auto_schema(
        operation_description="Update an existing custom package request",
        request_body=CustomPackageSerializer,
        responses={
            200: openapi.Response('Success', CustomPackageSerializer),
            400: openapi.Response('Invalid Data'),
            404: openapi.Response('Custom Request not found or User not found'),
            500: openapi.Response('Internal Server Error'),
        }
    )
    def put(self, request):
        data = request.data
        request_number = data.get('request_number')

        if not request_number:
            return Response({"detail": "Request number is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = UserProfile.objects.filter(session_token=data.get('session_token')).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            package = CustomPackages.objects.get(request_number=request_number, request_by=user.user_id)

            data.pop('session_token', None)

            serializer = CustomPackageSerializer(package, data=data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_200_OK)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        except CustomPackages.DoesNotExist:
            return Response({"detail": "No custom request found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error in updating custom package: {str(e)}")
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_description="Delete a custom package request",
        responses={
            204: openapi.Response('Deleted successfully'),
            400: openapi.Response('Request ID is required'),
            404: openapi.Response('Custom Request not found or User not found'),
            500: openapi.Response('Internal Server Error'),
        }
    )
    def delete(self, request):
        """ Delete a custom package request """
        data = request.data
        request_number = data.get('request_number')

        if not request_number:
            return Response({"detail": "Request ID is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = UserProfile.objects.filter(session_token=data.get('session_token')).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            package = CustomPackages.objects.get(request_number=request_number, request_by=user.user_id)
            package.delete()
            return Response({"detail": "Deleted successfully."}, status=status.HTTP_204_NO_CONTENT)

        except CustomPackages.DoesNotExist:
            return Response({"detail": "No custom request found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error in deleting custom package: {str(e)}")
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



