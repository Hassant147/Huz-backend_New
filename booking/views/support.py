from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.auth_utils import require_user_profile
from common.permissions import IsAdminOrAuthenticatedUserProfile

from ..models import BookingComplaints, BookingRequest
from ..serializers import BookingComplaintsSerializer, BookingRequestSerializer


class CurrentUserComplaintListView(APIView):
    permission_classes = [IsAdminOrAuthenticatedUserProfile]

    def get(self, request):
        user = require_user_profile(request)
        complaints = BookingComplaints.objects.filter(complaint_by_user=user).order_by(
            "-complaint_time"
        )
        serializer = BookingComplaintsSerializer(complaints, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class CurrentUserRequestListView(APIView):
    permission_classes = [IsAdminOrAuthenticatedUserProfile]

    def get(self, request):
        user = require_user_profile(request)
        requests = BookingRequest.objects.filter(request_by_user=user).order_by("-created_at")
        serializer = BookingRequestSerializer(requests, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
