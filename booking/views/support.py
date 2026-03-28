from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.auth_utils import resolve_request_user_profile
from common.permissions import IsAdminOrAuthenticatedUserProfile

from ..models import BookingComplaints, BookingRequest
from ..serializers import BookingComplaintsSerializer, BookingRequestSerializer


class CurrentUserComplaintListView(APIView):
    permission_classes = [IsAdminOrAuthenticatedUserProfile]

    def get(self, request):
        user = resolve_request_user_profile(request, request.query_params)
        complaints = (
            BookingComplaints.objects.select_related(
                "complaint_by_user",
                "complaint_for_partner",
                "complaint_for_package",
                "complaint_for_booking",
            )
            .prefetch_related(
                "complaint_by_user__mailing_session",
                "complaint_for_partner__company_of_partner",
            )
            .filter(complaint_by_user=user)
            .order_by("-complaint_time")
        )
        serializer = BookingComplaintsSerializer(complaints, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class CurrentUserRequestListView(APIView):
    permission_classes = [IsAdminOrAuthenticatedUserProfile]

    def get(self, request):
        user = resolve_request_user_profile(request, request.query_params)
        requests = (
            BookingRequest.objects.select_related(
                "request_by_user",
                "request_for_partner",
                "request_for_package",
                "request_for_booking",
            )
            .prefetch_related(
                "request_by_user__mailing_session",
                "request_for_partner__company_of_partner",
            )
            .filter(request_by_user=user)
            .order_by("-created_at")
        )
        serializer = BookingRequestSerializer(requests, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
