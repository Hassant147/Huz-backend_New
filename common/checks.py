from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, register


LOCAL_HOSTS = {"127.0.0.1", "0.0.0.0", "localhost"}


def _origin_key(value):
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""

    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.lower()

    return f"{parsed.scheme}://{parsed.netloc}".lower()


def _hostname(value):
    raw = (value or "").strip()
    if not raw:
        return ""

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or "").lower()


def _site_key(value):
    raw = (value or "").strip()
    if not raw:
        return ""

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower()
    if not host:
        return ""

    if host in LOCAL_HOSTS:
        registrable_host = host
    else:
        labels = [label for label in host.split(".") if label]
        # Best-effort registrable-domain normalization for deployment checks.
        registrable_host = ".".join(labels[-2:]) if len(labels) >= 2 else host

    scheme = (parsed.scheme or "https").lower()
    return f"{scheme}://{registrable_host}"


def _is_local_target(value):
    host = _hostname(value)
    return host in LOCAL_HOSTS or host.endswith(".localhost")


def _host_allowed(host, allowed_hosts):
    normalized_host = (host or "").strip().lower()
    if not normalized_host:
        return False

    for allowed_host in allowed_hosts:
        raw_allowed = (allowed_host or "").strip().lower()
        if not raw_allowed:
            continue

        if raw_allowed.startswith("."):
            suffix = raw_allowed[1:]
            if normalized_host == suffix or normalized_host.endswith(f".{suffix}"):
                return True
            continue

        if normalized_host == raw_allowed:
            return True

    return False


@register()
def production_origin_contract_check(app_configs, **kwargs):
    if not getattr(settings, "IS_PRODUCTION", False):
        return []

    errors = []

    if getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False):
        errors.append(
            Error(
                "Production config must not enable CORS_ALLOW_ALL_ORIGINS.",
                hint="Set CORS_ALLOW_ALL_ORIGINS=False and declare exact browser origins in CORS_ALLOWED_ORIGINS.",
                id="common.E001",
            )
        )

    declared_topology = {
        "API_PUBLIC_ORIGIN": getattr(settings, "API_PUBLIC_ORIGIN", ""),
        "WEB_APP_ORIGIN": getattr(settings, "WEB_APP_ORIGIN", ""),
        "OPERATOR_APP_ORIGIN": getattr(settings, "OPERATOR_APP_ORIGIN", ""),
        "ADMIN_APP_ORIGIN": getattr(settings, "ADMIN_APP_ORIGIN", ""),
    }
    missing_topology = [name for name, value in declared_topology.items() if not _origin_key(value)]
    if missing_topology:
        errors.append(
            Error(
                f"Production topology env is incomplete: {', '.join(missing_topology)}.",
                hint=(
                    "Set API_PUBLIC_ORIGIN, WEB_APP_ORIGIN, OPERATOR_APP_ORIGIN, "
                    "and ADMIN_APP_ORIGIN to the reviewed production origins before "
                    "release. A separately deployed admin frontend must declare its "
                    "real browser origin instead of leaving ADMIN_APP_ORIGIN blank."
                ),
                id="common.E002",
            )
        )

    if not settings.ALLOWED_HOSTS:
        errors.append(
            Error(
                "Production config must declare ALLOWED_HOSTS explicitly.",
                hint="Set ALLOWED_HOSTS in the production env file instead of relying on development defaults.",
                id="common.E003",
            )
        )

    if not settings.CORS_ALLOWED_ORIGINS:
        errors.append(
            Error(
                "Production config must declare CORS_ALLOWED_ORIGINS explicitly.",
                hint="List the exact browser origins that are allowed to call the backend in production.",
                id="common.E004",
            )
        )

    if not settings.CSRF_TRUSTED_ORIGINS:
        errors.append(
            Error(
                "Production config must declare CSRF_TRUSTED_ORIGINS explicitly.",
                hint="List the exact HTTPS origins that may submit browser requests in production.",
                id="common.E005",
            )
        )

    api_origin = _origin_key(getattr(settings, "API_PUBLIC_ORIGIN", ""))
    api_host = _hostname(api_origin)
    if api_origin and _is_local_target(api_origin):
        errors.append(
            Error(
                "API_PUBLIC_ORIGIN cannot point at localhost in production.",
                hint="Set API_PUBLIC_ORIGIN to the public HTTPS API origin used by deployed clients.",
                id="common.E006",
            )
        )
    elif api_host and not _host_allowed(api_host, settings.ALLOWED_HOSTS):
        errors.append(
            Error(
                "API_PUBLIC_ORIGIN host is not covered by ALLOWED_HOSTS.",
                hint="Add the public API hostname to ALLOWED_HOSTS or fix API_PUBLIC_ORIGIN.",
                id="common.E007",
            )
        )

    configured_cors = {_origin_key(origin) for origin in settings.CORS_ALLOWED_ORIGINS}
    configured_csrf = {_origin_key(origin) for origin in settings.CSRF_TRUSTED_ORIGINS}

    required_browser_origins = {
        "WEB_APP_ORIGIN": _origin_key(getattr(settings, "WEB_APP_ORIGIN", "")),
        "OPERATOR_APP_ORIGIN": _origin_key(getattr(settings, "OPERATOR_APP_ORIGIN", "")),
        "ADMIN_APP_ORIGIN": _origin_key(getattr(settings, "ADMIN_APP_ORIGIN", "")),
    }

    for setting_name, required_origin in required_browser_origins.items():
        if not required_origin:
            continue

        if required_origin not in configured_cors:
            errors.append(
                Error(
                    f"{setting_name} ({required_origin}) is missing from CORS_ALLOWED_ORIGINS.",
                    hint=(
                        "Add every deployed browser origin that calls the backend, "
                        "including the separately deployed admin frontend, to "
                        "CORS_ALLOWED_ORIGINS."
                    ),
                    id="common.E008",
                )
            )

        if required_origin not in configured_csrf:
            errors.append(
                Error(
                    f"{setting_name} ({required_origin}) is missing from CSRF_TRUSTED_ORIGINS.",
                    hint=(
                        "Add every deployed browser origin that sends session or "
                        "cookie-backed requests, including the admin frontend, to "
                        "CSRF_TRUSTED_ORIGINS."
                    ),
                    id="common.E009",
                )
            )

    if not getattr(settings, "ALLOW_LOCALHOST_ORIGINS", False):
        local_entries = [
            origin
            for origin in (
                list(settings.CORS_ALLOWED_ORIGINS)
                + list(settings.CSRF_TRUSTED_ORIGINS)
                + [
                    getattr(settings, "API_PUBLIC_ORIGIN", ""),
                    getattr(settings, "WEB_APP_ORIGIN", ""),
                    getattr(settings, "OPERATOR_APP_ORIGIN", ""),
                    getattr(settings, "ADMIN_APP_ORIGIN", ""),
                    getattr(settings, "OPERATOR_PANEL_BASE_URL", ""),
                ]
            )
            if origin and _is_local_target(origin)
        ]
        if local_entries:
            errors.append(
                Error(
                    "Production config still allows localhost origins.",
                    hint="Set ALLOW_LOCALHOST_ORIGINS=False and remove localhost entries before release.",
                    id="common.E010",
                )
            )

    operator_origin = _origin_key(getattr(settings, "OPERATOR_APP_ORIGIN", ""))
    operator_panel_base_url = _origin_key(getattr(settings, "OPERATOR_PANEL_BASE_URL", ""))
    if operator_origin and operator_panel_base_url and operator_origin != operator_panel_base_url:
        errors.append(
            Error(
                "OPERATOR_PANEL_BASE_URL does not match OPERATOR_APP_ORIGIN.",
                hint="Keep operator email/reset links on the same reviewed operator origin used in the deployment contract.",
                id="common.E011",
            )
        )

    admin_origin = _origin_key(getattr(settings, "ADMIN_APP_ORIGIN", ""))
    if admin_origin and api_origin and admin_origin != api_origin and not getattr(settings, "CORS_ALLOW_CREDENTIALS", False):
        errors.append(
            Error(
                "Admin frontend is cross-origin to the API but CORS_ALLOW_CREDENTIALS is disabled.",
                hint=(
                    "Set CORS_ALLOW_CREDENTIALS=True for deployed admin auth. "
                    "The admin frontend uses credentialed browser requests against "
                    "the API origin."
                ),
                id="common.E012",
            )
        )

    if admin_origin and api_origin and _site_key(admin_origin) != _site_key(api_origin):
        session_cookie_samesite = str(getattr(settings, "SESSION_COOKIE_SAMESITE", "")).strip().lower()
        csrf_cookie_samesite = str(getattr(settings, "CSRF_COOKIE_SAMESITE", "")).strip().lower()

        if session_cookie_samesite != "none":
            errors.append(
                Error(
                    "Cross-site admin deployment requires SESSION_COOKIE_SAMESITE=None.",
                    hint=(
                        "The deployed admin origin and API origin are on different "
                        "sites, so session cookies must use SameSite=None."
                    ),
                    id="common.E013",
                )
            )

        if csrf_cookie_samesite != "none":
            errors.append(
                Error(
                    "Cross-site admin deployment requires CSRF_COOKIE_SAMESITE=None.",
                    hint=(
                        "The deployed admin origin and API origin are on different "
                        "sites, so the CSRF cookie must use SameSite=None."
                    ),
                    id="common.E014",
                )
            )

        if not getattr(settings, "SESSION_COOKIE_SECURE", False) or not getattr(settings, "CSRF_COOKIE_SECURE", False):
            errors.append(
                Error(
                    "Cross-site admin deployment requires secure session and CSRF cookies.",
                    hint=(
                        "Set SESSION_COOKIE_SECURE=True and CSRF_COOKIE_SECURE=True "
                        "when the admin frontend is deployed on a different site."
                    ),
                    id="common.E015",
                )
            )

    return errors
