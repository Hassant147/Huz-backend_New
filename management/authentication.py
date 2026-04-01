from rest_framework.authentication import BaseAuthentication


class ManagementSessionAuthentication(BaseAuthentication):
    def authenticate(self, request):
        user = getattr(getattr(request, "_request", None), "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return None
        return user, None

    def authenticate_header(self, request):
        return "Session"
