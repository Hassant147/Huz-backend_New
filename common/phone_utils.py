import re

import phonenumbers
from django.db.models import CharField
from django.db.models.functions import Concat
from phonenumbers import NumberParseException, PhoneNumberFormat
from rest_framework import serializers

from .models import UserProfile


INVALID_PHONE_MESSAGE = "Enter a valid phone number."
INVALID_SELECTED_COUNTRY_PHONE_MESSAGE = "Enter a valid phone number for the selected country."
INVALID_COUNTRY_MESSAGE = "Please select a valid country."
PHONE_MISMATCH_MESSAGE = "Phone number entries do not match."
LOCAL_PHONE_DIGIT_MIN = 4
LOCAL_PHONE_DIGIT_MAX = 15
COUNTRY_CODE_DIGIT_MIN = 1
COUNTRY_CODE_DIGIT_MAX = 4
SUPPORTED_COUNTRY_REGIONS = frozenset(phonenumbers.SUPPORTED_REGIONS)


def _digits_only(value):
    return re.sub(r"\D", "", str(value or ""))


def _parse_phone_number(value, region=None, *, message=INVALID_PHONE_MESSAGE):
    candidate = str(value or "").strip()
    if not candidate:
        raise serializers.ValidationError("Phone number is required.")

    try:
        parsed_number = phonenumbers.parse(candidate, region or None)
    except NumberParseException:
        raise serializers.ValidationError(message)

    if not phonenumbers.is_possible_number(parsed_number):
        raise serializers.ValidationError(message)

    if not phonenumbers.is_valid_number(parsed_number):
        raise serializers.ValidationError(message)

    return parsed_number


def _format_country_code(country_code):
    return f"+{country_code}"


def _to_e164(parsed_number):
    return phonenumbers.format_number(parsed_number, PhoneNumberFormat.E164)


def _to_local_phone_number(parsed_number):
    return phonenumbers.national_significant_number(parsed_number)


def _build_phone_identity(parsed_number, *, country_iso_code=""):
    return {
        "country_code": _format_country_code(parsed_number.country_code),
        "country_iso_code": (
            phonenumbers.region_code_for_number(parsed_number) or country_iso_code or ""
        ),
        "local_phone_number": _to_local_phone_number(parsed_number),
        "full_phone_number": _to_e164(parsed_number),
    }


def normalize_country_iso_code(value, *, required=False):
    normalized_country_iso_code = str(value or "").strip().upper()
    if not normalized_country_iso_code:
        if required:
            raise serializers.ValidationError("Country is required.")
        return ""

    if normalized_country_iso_code not in SUPPORTED_COUNTRY_REGIONS:
        raise serializers.ValidationError(INVALID_COUNTRY_MESSAGE)

    return normalized_country_iso_code


def normalize_country_code(value, country_iso_code=""):
    digits = _digits_only(value)
    if not digits:
        raise serializers.ValidationError("Country code is required.")

    if len(digits) < COUNTRY_CODE_DIGIT_MIN or len(digits) > COUNTRY_CODE_DIGIT_MAX:
        raise serializers.ValidationError("You've entered an invalid country code.")

    normalized_country_iso_code = normalize_country_iso_code(country_iso_code) if country_iso_code else ""
    normalized_country_code = f"+{digits}"

    if normalized_country_iso_code:
        expected_country_code = phonenumbers.country_code_for_region(
            normalized_country_iso_code
        )
        if not expected_country_code:
            raise serializers.ValidationError(INVALID_COUNTRY_MESSAGE)

        if digits != str(expected_country_code):
            raise serializers.ValidationError(
                "Country code does not match the selected country."
            )

    return normalized_country_code


def _validate_phone_selection(parsed_number, *, country_code="", country_iso_code=""):
    normalized_country_iso_code = normalize_country_iso_code(country_iso_code) if country_iso_code else ""
    normalized_country_code = (
        normalize_country_code(country_code, country_iso_code=normalized_country_iso_code)
        if country_code
        else ""
    )

    if normalized_country_code and _format_country_code(parsed_number.country_code) != normalized_country_code:
        raise serializers.ValidationError(
            "Country code does not match the phone number."
        )

    if normalized_country_iso_code:
        resolved_country_iso_code = phonenumbers.region_code_for_number(parsed_number)
        if resolved_country_iso_code and resolved_country_iso_code != normalized_country_iso_code:
            raise serializers.ValidationError(
                "Phone number does not match the selected country."
            )

        expected_country_code = phonenumbers.country_code_for_region(
            normalized_country_iso_code
        )
        if not expected_country_code:
            raise serializers.ValidationError(INVALID_COUNTRY_MESSAGE)

        if str(parsed_number.country_code) != str(expected_country_code):
            raise serializers.ValidationError(
                "Phone number does not match the selected country."
            )


def _parse_full_phone_number(phone_number, *, country_code="", country_iso_code=""):
    normalized_country_iso_code = normalize_country_iso_code(country_iso_code) if country_iso_code else ""
    normalized_country_code = (
        normalize_country_code(country_code, country_iso_code=normalized_country_iso_code)
        if country_code
        else ""
    )
    raw_phone_number = str(phone_number or "").strip()

    if raw_phone_number.startswith("+"):
        parsed_number = _parse_phone_number(
            raw_phone_number,
            message=(
                INVALID_SELECTED_COUNTRY_PHONE_MESSAGE
                if normalized_country_iso_code
                else INVALID_PHONE_MESSAGE
            ),
        )
    elif normalized_country_iso_code:
        parsed_number = _parse_phone_number(
            raw_phone_number,
            normalized_country_iso_code,
            message=INVALID_SELECTED_COUNTRY_PHONE_MESSAGE,
        )
    elif normalized_country_code:
        parsed_number = _parse_phone_number(
            f"{normalized_country_code}{_digits_only(raw_phone_number)}",
            message=INVALID_PHONE_MESSAGE,
        )
    else:
        parsed_number = _parse_phone_number(
            raw_phone_number,
            message=INVALID_PHONE_MESSAGE,
        )

    _validate_phone_selection(
        parsed_number,
        country_code=normalized_country_code,
        country_iso_code=normalized_country_iso_code,
    )
    return parsed_number


def normalize_local_phone_number(
    value,
    country_code="",
    country_iso_code="",
    phone_number="",
):
    raw_local_phone_number = str(value or "").strip()
    if not raw_local_phone_number and phone_number:
        return _to_local_phone_number(
            _parse_full_phone_number(
                phone_number,
                country_code=country_code,
                country_iso_code=country_iso_code,
            )
        )

    if country_iso_code:
        return _to_local_phone_number(
            _parse_phone_number(
                raw_local_phone_number,
                normalize_country_iso_code(country_iso_code),
                message=INVALID_SELECTED_COUNTRY_PHONE_MESSAGE,
            )
        )

    if country_code:
        normalized_country_code = normalize_country_code(
            country_code,
            country_iso_code=country_iso_code,
        )
        return _to_local_phone_number(
            _parse_phone_number(
                f"{normalized_country_code}{_digits_only(raw_local_phone_number)}",
                message=INVALID_PHONE_MESSAGE,
            )
        )

    if raw_local_phone_number.startswith("+"):
        return _to_local_phone_number(
            _parse_phone_number(raw_local_phone_number, message=INVALID_PHONE_MESSAGE)
        )

    digits = _digits_only(raw_local_phone_number)
    if not digits:
        raise serializers.ValidationError("Phone number is required.")

    if len(digits) < LOCAL_PHONE_DIGIT_MIN or len(digits) > LOCAL_PHONE_DIGIT_MAX:
        raise serializers.ValidationError(INVALID_PHONE_MESSAGE)

    return digits


def normalize_full_phone_number(value, country_iso_code=""):
    return _to_e164(
        _parse_full_phone_number(
            value,
            country_iso_code=country_iso_code,
        )
    )


def resolve_phone_identity(
    *,
    phone_number="",
    country_code="",
    local_phone_number="",
    country_iso_code="",
):
    normalized_country_iso_code = normalize_country_iso_code(country_iso_code) if country_iso_code else ""
    normalized_country_code = (
        normalize_country_code(country_code, country_iso_code=normalized_country_iso_code)
        if country_code
        else ""
    )
    raw_phone_number = str(phone_number or "").strip()
    raw_local_phone_number = str(local_phone_number or "").strip()

    if raw_local_phone_number:
        if normalized_country_iso_code:
            parsed_number = _parse_phone_number(
                raw_local_phone_number,
                normalized_country_iso_code,
                message=INVALID_SELECTED_COUNTRY_PHONE_MESSAGE,
            )
        elif raw_phone_number:
            parsed_number = _parse_full_phone_number(
                raw_phone_number,
                country_code=normalized_country_code,
                country_iso_code=normalized_country_iso_code,
            )
        elif normalized_country_code:
            parsed_number = _parse_phone_number(
                f"{normalized_country_code}{_digits_only(raw_local_phone_number)}",
                message=INVALID_PHONE_MESSAGE,
            )
        else:
            raise serializers.ValidationError("Please select a country.")

        _validate_phone_selection(
            parsed_number,
            country_code=normalized_country_code,
            country_iso_code=normalized_country_iso_code,
        )
        phone_identity = _build_phone_identity(
            parsed_number,
            country_iso_code=normalized_country_iso_code,
        )

        if raw_phone_number:
            parsed_full_phone_number = _parse_full_phone_number(
                raw_phone_number,
                country_code=normalized_country_code,
                country_iso_code=normalized_country_iso_code,
            )
            if _to_e164(parsed_full_phone_number) != phone_identity["full_phone_number"]:
                raise serializers.ValidationError(PHONE_MISMATCH_MESSAGE)

        return phone_identity

    if not raw_phone_number:
        raise serializers.ValidationError("Phone number is required.")

    parsed_number = _parse_full_phone_number(
        raw_phone_number,
        country_code=normalized_country_code,
        country_iso_code=normalized_country_iso_code,
    )
    return _build_phone_identity(
        parsed_number,
        country_iso_code=normalized_country_iso_code,
    )


def resolve_signup_phone_identity(
    *,
    phone_number="",
    country_code="",
    local_phone_number="",
    country_iso_code="",
):
    return resolve_phone_identity(
        phone_number=phone_number,
        country_code=country_code,
        local_phone_number=local_phone_number,
        country_iso_code=country_iso_code,
    )


def find_user_profile_by_phone(
    *,
    phone_number="",
    country_code="",
    local_phone_number="",
):
    if country_code and local_phone_number:
        return UserProfile.objects.filter(
            country_code=country_code,
            phone_number=local_phone_number,
        ).first()

    normalized_full_phone_number = normalize_full_phone_number(phone_number)
    return (
        UserProfile.objects.annotate(
            full_phone_number=Concat(
                "country_code",
                "phone_number",
                output_field=CharField(),
            )
        )
        .filter(full_phone_number=normalized_full_phone_number)
        .first()
    )
