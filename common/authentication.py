from dataclasses import dataclass

from rest_framework.authentication import BaseAuthentication, get_authorization_header

from common.models import UserProfile
from partners.models import PartnerProfile


EMPTY_TOKEN_VALUES = {"", "null", "none", "undefined"}


@dataclass(frozen=True)
class SessionTokenAuthContext:
    principal: object
    principal_type: str
    token: str
    source: str

    @property
    def legacy(self):
        return self.source != "authorization"


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


def _extract_legacy_token(request, field_name):
    query_params = getattr(request, "query_params", None)
    if query_params is not None:
        token = _normalize_token(query_params.get(field_name))
        if token:
            return token, f"{field_name}_in_query"

    try:
        payload = request.data
    except Exception:
        payload = None

    if hasattr(payload, "get"):
        token = _normalize_token(payload.get(field_name))
        if token:
            return token, f"{field_name}_in_payload"

    return "", ""


def _build_auth_context(token, source):
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
    if context.legacy:
        setattr(request, "_legacy_token_used", context.source)

    raw_request = getattr(request, "_request", None)
    if raw_request is not None:
        setattr(raw_request, "auth_context", context)
        if context.legacy:
            setattr(raw_request, "_legacy_token_used", context.source)


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

        context = _build_auth_context(token, "authorization")
        if context is None:
            return None

        _apply_request_context(request, context)
        return SessionTokenBridgePrincipal(context), context

    def authenticate_header(self, request):
        return "Bearer"


class LegacySessionTokenAuthentication(BaseAuthentication):
    def authenticate(self, request):
        for field_name in ("session_token", "partner_session_token"):
            token, source = _extract_legacy_token(request, field_name)
            if not token:
                continue

            context = _build_auth_context(token, source)
            if context is None:
                continue

            _apply_request_context(request, context)
            return SessionTokenBridgePrincipal(context), context

        return None


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


def resolve_authenticated_user_profile(request, token_field="session_token"):
    user = get_authenticated_user_profile(request)
    if user is not None:
        return user

    if not is_authenticated_staff_user(request):
        return None

    token, _ = _extract_legacy_token(request, token_field)
    if not token:
        return None
    return UserProfile.objects.filter(session_token=token).first()


def resolve_authenticated_partner_profile(request, token_field="partner_session_token"):
    partner = get_authenticated_partner_profile(request)
    if partner is not None:
        return partner

    if not is_authenticated_staff_user(request):
        return None

    token, _ = _extract_legacy_token(request, token_field)
    if not token:
        return None
    return PartnerProfile.objects.filter(partner_session_token=token).first()
