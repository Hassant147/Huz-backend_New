from django.contrib.auth import authenticate, login as django_login, logout as django_logout
from django.middleware.csrf import get_token
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from .serializers import (
    AdminLoginRequestSerializer,
    AdminSessionEnvelopeSerializer,
    AdminSessionUserSerializer,
)
from .authentication import ManagementSessionAuthentication


def _build_session_payload(*, user=None, authenticated=False, message=None, csrf_token=None):
    payload = {
        "authenticated": authenticated,
        "user": AdminSessionUserSerializer(user).data if user is not None else None,
    }
    if message is not None:
        payload["message"] = message
    if csrf_token is not None:
        payload["csrf_token"] = csrf_token
    return payload


def _issue_csrf_token(request):
    raw_request = getattr(request, "_request", None)
    if raw_request is not None:
        return get_token(raw_request)
    return None


class AdminLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Create a backend-managed admin session for a Django staff user.",
        request_body=AdminLoginRequestSerializer,
        responses={
            200: openapi.Response("Authenticated", AdminSessionEnvelopeSerializer),
            400: openapi.Response("Missing credentials", AdminSessionEnvelopeSerializer),
            401: openapi.Response("Invalid credentials", AdminSessionEnvelopeSerializer),
            403: openapi.Response("Admin access required", AdminSessionEnvelopeSerializer),
        },
    )
    def post(self, request):
        username = str(request.data.get("username") or "").strip()
        password = str(request.data.get("password") or "")
        if not username or not password:
            return Response(
                {"message": "Username and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_request = getattr(request, "_request", None)
        user = authenticate(request=raw_request, username=username, password=password)
        if user is None:
            return Response(
                _build_session_payload(
                    authenticated=False,
                    user=None,
                    message="Invalid credentials.",
                ),
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if not getattr(user, "is_staff", False):
            return Response(
                _build_session_payload(
                    authenticated=False,
                    user=None,
                    message="Admin access required.",
                ),
                status=status.HTTP_403_FORBIDDEN,
            )

        django_login(raw_request, user)
        csrf_token = _issue_csrf_token(request)
        return Response(
            _build_session_payload(authenticated=True, user=user, csrf_token=csrf_token),
            status=status.HTTP_200_OK,
        )


class AdminLogoutView(APIView):
    authentication_classes = [ManagementSessionAuthentication]
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Clear the current backend-managed admin session.",
        responses={
            200: openapi.Response("Logged out", AdminSessionEnvelopeSerializer),
        },
    )
    def post(self, request):
        raw_request = getattr(request, "_request", None)
        if getattr(request.user, "is_authenticated", False) and raw_request is not None:
            django_logout(raw_request)

        return Response(
            _build_session_payload(
                authenticated=False,
                user=None,
                message="Logged out.",
            ),
            status=status.HTTP_200_OK,
        )


class AdminSessionMeView(APIView):
    authentication_classes = [ManagementSessionAuthentication]
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Return the current backend-managed admin session state.",
        responses={
            200: openapi.Response("Authenticated", AdminSessionEnvelopeSerializer),
            401: openapi.Response("Not authenticated", AdminSessionEnvelopeSerializer),
            403: openapi.Response("Admin access required", AdminSessionEnvelopeSerializer),
        },
    )
    def get(self, request):
        user = request.user
        if not getattr(user, "is_authenticated", False):
            return Response(
                _build_session_payload(
                    authenticated=False,
                    user=None,
                    message="Not authenticated.",
                ),
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if not getattr(user, "is_staff", False):
            return Response(
                _build_session_payload(
                    authenticated=False,
                    user=None,
                    message="Admin access required.",
                ),
                status=status.HTTP_403_FORBIDDEN,
            )

        csrf_token = _issue_csrf_token(request)
        return Response(
            _build_session_payload(authenticated=True, user=user, csrf_token=csrf_token),
            status=status.HTTP_200_OK,
        )
