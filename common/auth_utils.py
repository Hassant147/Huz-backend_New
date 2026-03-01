from rest_framework.exceptions import AuthenticationFailed

from .authentication import (
    get_authenticated_partner_profile,
    get_authenticated_user_profile,
    is_authenticated_staff_user,
)


def is_admin_request(request):
    return is_authenticated_staff_user(request)


def require_user_profile(request):
    user_profile = get_authenticated_user_profile(request)
    if user_profile is None:
        raise AuthenticationFailed("Authenticated user profile is required.")
    return user_profile


def require_partner_profile(request):
    partner_profile = get_authenticated_partner_profile(request)
    if partner_profile is None:
        raise AuthenticationFailed("Authenticated partner profile is required.")
    return partner_profile
