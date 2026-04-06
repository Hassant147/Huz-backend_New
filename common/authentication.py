from dataclasses import dataclass

from rest_framework.authentication import BaseAuthentication, get_authorization_header

from common.models import UserProfile
from partners.models import PartnerProfile


EMPTY_TOKEN_VALUES = {"", "null", "none", "undefined"}
OPERATOR_PARTNER_PATH_PREFIX = "/api/v1/operator/"
OPERATOR_PARTNER_PREFETCH_PATHS = {
    "/api/v1/operator/me/profile/",
    "/api/v1/operator/me/profile/avatar/",
    "/api/v1/operator/auth/otp/verify/",
}


@dataclass(frozen=True)
class SessionTokenAuthContext:
    principal: object
    principal_type: str
    token: str
    source: str


class SessionTokenBridgePrincipal:
    def __init__(self, context):
        self._context = context
        self._principal = context.principal

    @property
    def is_authenticated(self):
        return True

    @property
    def is_staff(self):
        return False

    @property
    def is_active(self):
        return True

    @property
    def pk(self):
        return getattr(self._principal, "pk", None)

    @property
    def principal(self):
        return self._principal

    def __getattr__(self, attr_name):
        return getattr(self._principal, attr_name)

    def __str__(self):
        return str(self._principal)


def _normalize_token(raw_token):
    token = str(raw_token or "").strip()
    if not token or token.lower() in EMPTY_TOKEN_VALUES:
        return ""
    return token


def _get_request_path(request):
    path = getattr(request, "path", "") or getattr(getattr(request, "_request", None), "path", "")
    return str(path or "").strip()


def _fetch_partner_profile(token, request_path=""):
    partner_query = PartnerProfile.objects.filter(partner_session_token=token)

    if request_path in OPERATOR_PARTNER_PREFETCH_PATHS:
        partner_query = partner_query.prefetch_related(
            "wallet_session",
            "services_of_partner",
            "mailing_of_partner",
            "company_of_partner",
            "individual_profile_of_partner",
        )

    return partner_query.first()


def _build_auth_context(token, source, request_path=""):
    if request_path.startswith(OPERATOR_PARTNER_PATH_PREFIX):
        partner = _fetch_partner_profile(token, request_path=request_path)
        if partner:
            return SessionTokenAuthContext(
                principal=partner,
                principal_type="partner",
                token=token,
                source=source,
            )
        return None

    user = UserProfile.objects.filter(session_token=token).first()
    partner = PartnerProfile.objects.filter(partner_session_token=token).first()

    if user and partner:
        return None
    if user:
        return SessionTokenAuthContext(
            principal=user,
            principal_type="user",
            token=token,
            source=source,
        )
    if partner:
        return SessionTokenAuthContext(
            principal=partner,
            principal_type="partner",
            token=token,
            source=source,
        )
    return None


def _apply_request_context(request, context):
    if context is None:
        return

    setattr(request, "auth_context", context)

    raw_request = getattr(request, "_request", None)
    if raw_request is not None:
        setattr(raw_request, "auth_context", context)


class SessionTokenHeaderAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth_header = get_authorization_header(request).split()
        if not auth_header:
            return None

        if len(auth_header) != 2:
            return None

        scheme = auth_header[0].decode("utf-8").strip().lower()
        if scheme not in {"bearer", "token"}:
            return None

        token = _normalize_token(auth_header[1].decode("utf-8"))
        if not token:
            return None

        context = _build_auth_context(token, "authorization", _get_request_path(request))
        if context is None:
            return None

        _apply_request_context(request, context)
        return SessionTokenBridgePrincipal(context), context

    def authenticate_header(self, request):
        return "Bearer"


def get_session_token_auth_context(request):
    auth_context = getattr(request, "auth", None)
    if isinstance(auth_context, SessionTokenAuthContext):
        return auth_context

    auth_context = getattr(request, "auth_context", None)
    if isinstance(auth_context, SessionTokenAuthContext):
        return auth_context

    raw_request = getattr(request, "_request", None)
    auth_context = getattr(raw_request, "auth_context", None)
    if isinstance(auth_context, SessionTokenAuthContext):
        return auth_context

    return None


def is_authenticated_staff_user(request):
    user = getattr(request, "user", None)
    return bool(user and getattr(user, "is_authenticated", False) and getattr(user, "is_staff", False))


def get_authenticated_user_profile(request):
    auth_context = get_session_token_auth_context(request)
    if auth_context and auth_context.principal_type == "user":
        return auth_context.principal
    return None


def get_authenticated_partner_profile(request):
    auth_context = get_session_token_auth_context(request)
    if auth_context and auth_context.principal_type == "partner":
        return auth_context.principal
    return None
