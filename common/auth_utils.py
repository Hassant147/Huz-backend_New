from rest_framework.exceptions import AuthenticationFailed

from .authentication import (
    get_authenticated_partner_profile,
    get_authenticated_user_profile,
    is_authenticated_staff_user,
)
from .models import UserProfile


def is_admin_request(request):
    return is_authenticated_staff_user(request)


def require_user_profile(request):
    user_profile = get_authenticated_user_profile(request)
    if user_profile is None:
        raise AuthenticationFailed("Authenticated user profile is required.")
    return user_profile


def resolve_request_user_profile(request, payload=None):
    payload = payload or {}
    try:
        return require_user_profile(request)
    except AuthenticationFailed:
        if not is_admin_request(request):
            raise

    session_token = payload.get("session_token") or request.query_params.get("session_token")
    if not session_token:
        raise AuthenticationFailed("Authenticated user profile is required.")

    user_profile = UserProfile.objects.filter(session_token=session_token).first()
    if user_profile is None:
        raise AuthenticationFailed("Authenticated user profile is required.")

    return user_profile


def require_partner_profile(request):
    partner_profile = get_authenticated_partner_profile(request)
    if partner_profile is None:
        raise AuthenticationFailed("Authenticated partner profile is required.")
    return partner_profile
