from rest_framework.permissions import BasePermission

from .authentication import (
    get_authenticated_partner_profile,
    get_authenticated_user_profile,
    is_authenticated_staff_user,
)


class IsAdminOrAuthenticatedUserProfile(BasePermission):
    message = "Authentication credentials were not provided."

    def has_permission(self, request, view):
        if is_authenticated_staff_user(request):
            return True
        return get_authenticated_user_profile(request) is not None


class IsAdminOrAuthenticatedPartnerProfile(BasePermission):
    message = "Authentication credentials were not provided."

    def has_permission(self, request, view):
        if is_authenticated_staff_user(request):
            return True
        return get_authenticated_partner_profile(request) is not None
