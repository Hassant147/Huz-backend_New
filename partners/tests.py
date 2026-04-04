from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.utils import timezone
from rest_framework import status
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, APITransactionTestCase

from booking.models import BookingRatingAndReview
from common.utility import hash_password

from .models import (
    HuzAirlineDetail,
    HuzBasicDetail,
    HuzHotelDetail,
    HuzPackageDateRange,
    HuzTransportDetail,
    HuzZiyarahDetail,
    PartnerBankAccount,
    PartnerMailingDetail,
    PartnerProfile,
    PartnerServices,
    PartnerTransactionHistory,
    PartnerWithdraw,
    Wallet,
)
from .package_management import (
    GetHuzPackageDetailForWebsiteView,
    GetHuzShortPackageForWebsiteView,
    GetSearchPackageByCityNDateView,
)
from .partner_accounts_and_transactions import (
    GetPartnerAllTransactionHistoryView,
    GetPartnerTransactionOverallSummaryView,
)
from .package_management_operator import (
    CreateHuzPackageView,
    GetPartnersOverallPackagesStatisticsView,
    GetHuzPackageDetailByTokenView,
    GetHuzShortPackageByTokenView,
)
from .partner_profile import (
    CreatePartnerProfileView,
    PartnerServicesView,
    dispatch_partner_verification_email,
)


def ensure_tables_for_apps(app_labels):
    existing_tables = set(connection.introspection.table_names())
    pending_models = []
    for app_label in app_labels:
        pending_models.extend(list(apps.get_app_config(app_label).get_models()))

    while pending_models:
        created_in_pass = False
        remaining_models = []

        with connection.schema_editor(atomic=False) as schema_editor:
            for model in pending_models:
                table_name = model._meta.db_table
                if table_name in existing_tables:
                    continue

                try:
                    schema_editor.create_model(model)
                    existing_tables.add(table_name)
                    created_in_pass = True
                except Exception:
                    remaining_models.append(model)

        if not created_in_pass:
            if not remaining_models:
                break
            unresolved_tables = [model._meta.db_table for model in remaining_models]
            raise RuntimeError(
                f"Unable to create tables for test setup: {', '.join(unresolved_tables)}"
            )

        pending_models = remaining_models


class PartnerProfileSignupTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners"])

    def setUp(self):
        self.factory = APIRequestFactory()
        PartnerProfile.objects.filter(email__iexact="operator-signup@example.com").delete()

    @patch("partners.partner_profile.dispatch_partner_verification_email", return_value=True)
    def test_create_partner_profile_persists_user_and_dispatches_otp_after_commit(self, mocked_dispatch):
        request = self.factory.post(
            "/api/v1/operator/auth/accounts/",
            {
                "email": "operator-signup@example.com",
                "name": "Operator Signup",
                "phone_number": "+923001234567",
                "password": "SecurePass1!",
                "sign_type": "Email",
            },
            format="json",
        )

        response = CreatePartnerProfileView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created_partner = PartnerProfile.objects.get(email="operator-signup@example.com")
        self.assertEqual(created_partner.country_code, "+92")
        self.assertEqual(created_partner.phone_number, "3001234567")
        self.assertTrue(Wallet.objects.filter(wallet_session=created_partner).exists())
        mocked_dispatch.assert_called_once_with(
            created_partner.email,
            created_partner.name,
            created_partner.otp,
            "+923001234567",
            wait_for_result=False,
        )

    @patch("partners.partner_profile.dispatch_partner_verification_email", return_value=True)
    def test_canonical_operator_auth_accounts_alias_creates_partner_profile(self, mocked_dispatch):
        response = self.client.post(
            "/api/v1/operator/auth/accounts/",
            {
                "email": "operator-signup@example.com",
                "name": "Operator Signup",
                "phone_number": "+923001234567",
                "password": "SecurePass1!",
                "sign_type": "Email",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created_partner = PartnerProfile.objects.get(email="operator-signup@example.com")
        self.assertTrue(Wallet.objects.filter(wallet_session=created_partner).exists())
        mocked_dispatch.assert_called_once()

    def test_canonical_operator_auth_login_alias_authenticates_partner(self):
        PartnerProfile.objects.create(
            partner_session_token="canonical-login-session-token",
            email="operator-login@example.com",
            name="Operator Login",
            country_code="+92",
            phone_number="3001234599",
            partner_type="NA",
            sign_type="Email",
            password=hash_password("SecurePass1!"),
        )

        response = self.client.post(
            "/api/v1/operator/auth/login/",
            {
                "email": "operator-login@example.com",
                "password": "SecurePass1!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "operator-login@example.com")
        self.assertEqual(
            response.data["partner_session_token"],
            "canonical-login-session-token",
        )

    def test_canonical_operator_auth_users_exists_alias_supports_email_lookup(self):
        PartnerProfile.objects.create(
            partner_session_token="canonical-exists-session-token",
            email="operator-exists@example.com",
            name="Operator Exists",
            country_code="+92",
            phone_number="3001234588",
            partner_type="NA",
            sign_type="Email",
        )

        response = self.client.post(
            "/api/v1/operator/auth/users/exists/",
            {"email": "operator-exists@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "operator-exists@example.com")

    def test_canonical_operator_username_exists_alias_accepts_bearer_auth(self):
        partner = PartnerProfile.objects.create(
            partner_session_token="canonical-username-session-token",
            email="operator-username@example.com",
            name="Operator Username",
            country_code="+92",
            phone_number="3001234591",
            partner_type="Company",
            sign_type="Email",
            user_name="current-name",
        )

        response = self.client.post(
            "/api/v1/operator/me/usernames/exists/",
            {"user_name": "available_name"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {partner.partner_session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "This username is available.")

    @patch("partners.partner_profile.send_partner_verification_sms", return_value=True)
    @patch("partners.partner_profile.send_verification_email", return_value=False)
    def test_dispatch_partner_verification_email_falls_back_to_sms_when_email_send_fails(
        self,
        mocked_email,
        mocked_sms,
    ):
        is_sent = dispatch_partner_verification_email(
            "operator-signup@example.com",
            "Operator Signup",
            "123456",
            "+923001234567",
            wait_for_result=True,
        )

        self.assertTrue(is_sent)
        mocked_email.assert_called_once_with(
            "operator-signup@example.com",
            "Operator Signup",
            "123456",
            wait_for_result=True,
        )
        mocked_sms.assert_called_once_with("+923001234567", "123456")

    @patch("partners.partner_profile.send_verification_email", return_value=True)
    def test_resend_otp_returns_success_and_updates_partner_otp(self, mocked_email):
        partner = PartnerProfile.objects.create(
            partner_session_token="partner-otp-session-token",
            email="operator-resend@example.com",
            name="Operator Resend",
            country_code="+92",
            phone_number="3001234568",
            partner_type="NA",
            sign_type="Email",
        )

        response = self.client.put(
            "/api/v1/operator/auth/otp/resend/",
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {partner.partner_session_token}",
        )

        partner.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "OTP sent successfully.")
        self.assertRegex(partner.otp or "", r"^\d{6}$")
        mocked_email.assert_called_once_with(partner.email, partner.name, partner.otp, wait_for_result=True)

    @patch("partners.partner_profile.send_verification_email", return_value=True)
    def test_canonical_operator_auth_resend_otp_accepts_bearer_token(self, mocked_email):
        partner = PartnerProfile.objects.create(
            partner_session_token="canonical-resend-session-token",
            email="operator-canonical-resend@example.com",
            name="Operator Canonical Resend",
            country_code="+92",
            phone_number="3001234572",
            partner_type="NA",
            sign_type="Email",
        )

        response = self.client.put(
            "/api/v1/operator/auth/otp/resend/",
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {partner.partner_session_token}",
        )

        partner.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertRegex(partner.otp or "", r"^\d{6}$")
        mocked_email.assert_called_once_with(partner.email, partner.name, partner.otp, wait_for_result=True)

    @patch("partners.partner_profile.send_partner_verification_sms", return_value=True)
    @patch("partners.partner_profile.send_verification_email", return_value=False)
    def test_resend_otp_falls_back_to_sms_when_email_send_fails(
        self,
        mocked_email,
        mocked_sms,
    ):
        partner = PartnerProfile.objects.create(
            partner_session_token="partner-otp-fallback-session-token",
            email="operator-resend-fallback@example.com",
            name="Operator Resend Fallback",
            country_code="+92",
            phone_number="3001234570",
            partner_type="NA",
            sign_type="Email",
        )

        response = self.client.put(
            "/api/v1/operator/auth/otp/resend/",
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {partner.partner_session_token}",
        )

        partner.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertRegex(partner.otp or "", r"^\d{6}$")
        mocked_email.assert_called_once_with(partner.email, partner.name, partner.otp, wait_for_result=True)
        mocked_sms.assert_called_once_with("+923001234570", partner.otp)

    @patch("partners.partner_profile.send_partner_verification_sms", side_effect=Exception("sms unavailable"))
    @patch("partners.partner_profile.send_verification_email", return_value=False)
    def test_resend_otp_returns_502_when_email_and_sms_delivery_fail(
        self,
        mocked_email,
        mocked_sms,
    ):
        partner = PartnerProfile.objects.create(
            partner_session_token="partner-otp-failure-session-token",
            email="operator-resend-failure@example.com",
            name="Operator Resend Failure",
            country_code="+92",
            phone_number="3001234571",
            partner_type="NA",
            sign_type="Email",
        )

        response = self.client.put(
            "/api/v1/operator/auth/otp/resend/",
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {partner.partner_session_token}",
        )

        partner.refresh_from_db()
        attempted_otp = mocked_email.call_args.args[2]
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIn("Failed to send OTP", response.data["message"])
        self.assertFalse(partner.otp)
        self.assertRegex(attempted_otp, r"^\d{6}$")
        mocked_email.assert_called_once_with(partner.email, partner.name, attempted_otp, wait_for_result=True)
        mocked_sms.assert_called_once_with("+923001234571", attempted_otp)

    def test_verify_otp_marks_email_verified(self):
        partner = PartnerProfile.objects.create(
            partner_session_token="partner-verify-session-token",
            email="operator-verify@example.com",
            name="Operator Verify",
            country_code="+92",
            phone_number="3001234569",
            partner_type="NA",
            sign_type="Email",
            otp="123456",
            is_email_verified=False,
        )

        response = self.client.put(
            "/api/v1/operator/auth/otp/verify/",
            {"otp": "123456"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {partner.partner_session_token}",
        )

        partner.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(partner.is_email_verified)
        self.assertEqual(partner.otp, "")

    def test_canonical_operator_auth_verify_otp_accepts_bearer_token(self):
        partner = PartnerProfile.objects.create(
            partner_session_token="canonical-verify-session-token",
            email="operator-canonical-verify@example.com",
            name="Operator Canonical Verify",
            country_code="+92",
            phone_number="3001234573",
            partner_type="NA",
            sign_type="Email",
            otp="123456",
            is_email_verified=False,
        )

        response = self.client.put(
            "/api/v1/operator/auth/otp/verify/",
            {"otp": "123456"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {partner.partner_session_token}",
        )

        partner.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(partner.is_email_verified)
        self.assertEqual(partner.otp, "")


class PackageManagementOperatorViewTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        self.factory = APIRequestFactory()
        PartnerProfile.objects.filter(
            partner_session_token__in=[
                "partner-package-session-token",
                "partner-package-session-token-2",
            ]
        ).delete()
        self.partner = PartnerProfile.objects.create(
            partner_session_token="partner-package-session-token",
            user_name="partner-package-user",
            name="Package Partner",
            partner_type="Company",
            account_status="Active",
        )
        self.other_partner = PartnerProfile.objects.create(
            partner_session_token="partner-package-session-token-2",
            user_name="partner-package-user-2",
            name="Package Partner 2",
            partner_type="Company",
            account_status="Active",
        )

        start_date = timezone.now() + timedelta(days=10)
        end_date = start_date + timedelta(days=7)
        self.package = HuzBasicDetail.objects.create(
            huz_token="package-huz-token-001",
            package_type="Hajj",
            package_name="Package Test",
            start_date=start_date,
            end_date=end_date,
            description="Package description",
            package_status="Active",
            package_provider=self.partner,
        )
        self.completed_package = HuzBasicDetail.objects.create(
            huz_token="package-huz-token-002",
            package_type="Hajj",
            package_name="Sacred Journey Package",
            start_date=start_date + timedelta(days=3),
            end_date=end_date + timedelta(days=3),
            description="Premium sacred package",
            package_status="Completed",
            package_provider=self.partner,
        )
        self.other_partner_package = HuzBasicDetail.objects.create(
            huz_token="package-huz-token-003",
            package_type="Hajj",
            package_name="Other Partner Package",
            start_date=start_date,
            end_date=end_date,
            description="Should never appear in partner 1 queries",
            package_status="Active",
            package_provider=self.other_partner,
        )

    def _auth_headers(self, partner=None):
        active_partner = partner or self.partner
        return {"HTTP_AUTHORIZATION": f"Bearer {active_partner.partner_session_token}"}

    def _request_short_packages(self, *, authenticated=True, partner=None, **query_params):
        headers = self._auth_headers(partner) if authenticated else {}
        return self.client.get("/api/v1/operator/me/packages/", query_params, **headers)

    def test_get_package_detail_returns_single_item_list(self):
        response = self.client.get(
            "/api/v1/operator/me/packages/detail/",
            {
                "huz_token": self.package.huz_token,
            },
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0].get("huz_token"), self.package.huz_token)
        self.assertNotIn("airline_detail_list", response.data[0])
        self.assertNotIn("transport_detail_list", response.data[0])
        self.assertNotIn("ziyarah_detail_list", response.data[0])
        self.assertNotIn("start_date", response.data[0])
        self.assertNotIn("end_date", response.data[0])
        self.assertNotIn("package_validity", response.data[0])
        self.assertNotIn("package_seats", response.data[0])

    def test_operator_package_detail_exposes_flat_hotel_contract_without_nested_duplicates(self):
        HuzHotelDetail.objects.create(
            hotel_city="Makkah",
            hotel_name="Operator Flat Contract Hotel",
            hotel_rating="4 Star",
            room_sharing_type="Quad",
            hotel_distance="10",
            distance_type="KM",
            hotel_for_package=self.package,
        )
        response = self.client.get(
            "/api/v1/operator/me/packages/detail/",
            {
                "huz_token": self.package.huz_token,
            },
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        hotel_payload = response.data[0]["hotel_detail"][0]

        self.assertIn("images", hotel_payload)
        self.assertNotIn("hotel_images", hotel_payload)
        self.assertNotIn("hotel_detail", hotel_payload)
        self.assertNotIn("huz_hotel_id", hotel_payload)

    def test_operator_create_package_accepts_package_date_range_without_summary_dates(self):
        range_start = timezone.now() + timedelta(days=14)
        range_end = range_start + timedelta(days=10)
        response = self.client.post(
            "/api/v1/operator/me/packages/basic/",
            {
                "package_type": "Umrah",
                "package_name": "Range Only Contract Package",
                "description": "Created without top-level summary dates.",
                "mecca_nights": 5,
                "madinah_nights": 5,
                "package_date_range": [
                    {
                        "start_date": range_start.isoformat(),
                        "end_date": range_end.isoformat(),
                        "group_capacity": 9,
                        "package_validity": range_end.isoformat(),
                    }
                ],
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.partner.partner_session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("package_date_range", response.data)
        self.assertNotIn("start_date", response.data)
        self.assertNotIn("end_date", response.data)
        self.assertNotIn("package_validity", response.data)

        created_package = HuzBasicDetail.objects.get(huz_token=response.data["huz_token"])
        created_range = HuzPackageDateRange.objects.get(date_range_for_package=created_package)

        self.assertEqual(created_package.start_date, created_range.start_date)
        self.assertEqual(created_package.end_date, created_range.end_date)
        self.assertEqual(
            created_range.package_validity,
            range_start - timedelta(days=2),
        )
        self.assertEqual(created_package.package_validity, created_range.package_validity)

    def test_get_package_detail_returns_404_for_unknown_token(self):
        response = self.client.get(
            "/api/v1/operator/me/packages/detail/",
            {
                "huz_token": "unknown-token",
            },
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data.get("message"), "Package do not exist.")

    def test_get_short_packages_are_scoped_to_partner(self):
        response = self._request_short_packages(
            partner_session_token=self.partner.partner_session_token,
            package_type="Hajj",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results") or []
        returned_tokens = {item.get("huz_token") for item in results}

        self.assertIn(self.package.huz_token, returned_tokens)
        self.assertIn(self.completed_package.huz_token, returned_tokens)
        self.assertNotIn(self.other_partner_package.huz_token, returned_tokens)

    def test_get_short_packages_supports_text_search(self):
        response = self._request_short_packages(
            partner_session_token=self.partner.partner_session_token,
            package_type="Hajj",
            search="sacred",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results") or []
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("huz_token"), self.completed_package.huz_token)

    def test_get_short_packages_normalize_status_filter(self):
        response = self._request_short_packages(
            partner_session_token=self.partner.partner_session_token,
            package_type="Hajj",
            package_status="completed",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results") or []
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("huz_token"), self.completed_package.huz_token)

    def test_get_short_packages_preserves_paginated_shape_with_rating_summary(self):
        BookingRatingAndReview.objects.create(
            partner_total_stars=4,
            partner_comment="Strong support",
            rating_for_partner=self.partner,
            rating_for_package=self.package,
        )

        response = self._request_short_packages(
            partner_session_token=self.partner.partner_session_token,
            package_type="Hajj",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("count", response.data)
        self.assertIn("results", response.data)
        self.assertGreaterEqual(response.data.get("count"), 2)
        results_by_token = {
            item.get("huz_token"): item for item in response.data.get("results") or []
        }
        rating_payload = results_by_token[self.package.huz_token].get("rating_count")
        self.assertIsInstance(rating_payload, dict)
        self.assertEqual(rating_payload.get("rating_count"), 1)
        self.assertEqual(rating_payload.get("average_stars"), 4.0)

    def test_get_short_packages_accept_bearer_authorization(self):
        response = self.client.get(
            "/api/v1/operator/me/packages/",
            {"package_type": "Hajj"},
            **self._auth_headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)
        returned_tokens = {item.get("huz_token") for item in response.data.get("results") or []}
        self.assertIn(self.package.huz_token, returned_tokens)
        self.assertNotIn("X-Auth-Deprecated", response)

    def test_canonical_operator_packages_list_accepts_bearer_authorization(self):
        response = self.client.get(
            "/api/v1/operator/me/packages/",
            {"package_type": "Hajj"},
            HTTP_AUTHORIZATION=f"Bearer {self.partner.partner_session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)
        returned_tokens = {item.get("huz_token") for item in response.data.get("results") or []}
        self.assertIn(self.package.huz_token, returned_tokens)

    def test_get_short_packages_rejects_unsupported_package_type(self):
        response = self._request_short_packages(
            partner_session_token=self.partner.partner_session_token,
            package_type="Ziyarah",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data.get("message"),
            "Invalid package_type. Use Hajj or Umrah.",
        )

    def test_get_short_packages_rejects_unauthenticated_requests(self):
        response = self._request_short_packages(authenticated=False, package_type="Hajj")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_overall_package_statistics_include_all_supported_statuses(self):
        start_date = timezone.now() + timedelta(days=20)
        end_date = start_date + timedelta(days=7)
        HuzBasicDetail.objects.create(
            huz_token="package-huz-token-004",
            package_type="Hajj",
            package_name="Blocked package",
            start_date=start_date,
            end_date=end_date,
            description="Blocked status package",
            package_status="Block",
            package_provider=self.partner,
        )
        HuzBasicDetail.objects.create(
            huz_token="package-huz-token-005",
            package_type="Hajj",
            package_name="Pending package",
            start_date=start_date,
            end_date=end_date,
            description="Pending status package",
            package_status="Pending",
            package_provider=self.partner,
        )
        HuzBasicDetail.objects.create(
            huz_token="package-huz-token-006",
            package_type="Hajj",
            package_name="Not active package",
            start_date=start_date,
            end_date=end_date,
            description="NotActive status package",
            package_status="NotActive",
            package_provider=self.partner,
        )

        response = self.client.get(
            "/api/v1/operator/me/packages/statistics/",
            {},
            **self._auth_headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("Active"), 1)
        self.assertEqual(response.data.get("Completed"), 1)
        self.assertEqual(response.data.get("Block"), 1)
        self.assertEqual(response.data.get("Pending"), 1)
        self.assertEqual(response.data.get("NotActive"), 1)

    def test_overall_package_statistics_ignore_legacy_ziyarah_rows(self):
        start_date = timezone.now() + timedelta(days=20)
        end_date = start_date + timedelta(days=7)
        HuzBasicDetail.objects.create(
            huz_token="legacy-ziyarah-token-001",
            package_type="Ziyarah",
            package_name="Legacy Ziyarah Package",
            start_date=start_date,
            end_date=end_date,
            description="Legacy package type that should be ignored.",
            package_status="Active",
            package_provider=self.partner,
        )

        response = self.client.get(
            "/api/v1/operator/me/packages/statistics/",
            {},
            **self._auth_headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("Active"), 1)
        self.assertEqual(response.data.get("Completed"), 1)


class PackageManagementOperatorMutationAuthTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        self.partner_a = self._create_partner("package-auth-a")
        self.partner_b = self._create_partner("package-auth-b")

        self.package_a_with_details = self._create_package(
            self.partner_a,
            "package-auth-a-with-details",
        )
        self.package_a_for_creates = self._create_package(
            self.partner_a,
            "package-auth-a-for-creates",
        )
        self.package_b_with_details = self._create_package(
            self.partner_b,
            "package-auth-b-with-details",
        )
        self.package_b_for_creates = self._create_package(
            self.partner_b,
            "package-auth-b-for-creates",
        )

        self.airline_a = HuzAirlineDetail.objects.create(
            airline_name="Airline A",
            ticket_type="Economy",
            flight_from="Karachi",
            flight_to="Jeddah",
            return_flight_from="Jeddah",
            return_flight_to="Karachi",
            is_return_flight_included=True,
            airline_for_package=self.package_a_with_details,
        )
        self.transport_a = HuzTransportDetail.objects.create(
            transport_name="Transport A",
            transport_type="Shared",
            routes="Karachi,Jeddah",
            transport_for_package=self.package_a_with_details,
        )
        self.hotel_a = HuzHotelDetail.objects.create(
            hotel_city="Makkah",
            hotel_name="Hotel A",
            hotel_rating="5 Star",
            room_sharing_type="Quad",
            hotel_distance="5",
            distance_type="KM",
            hotel_for_package=self.package_a_with_details,
        )
        self.ziyarah_a = HuzZiyarahDetail.objects.create(
            ziyarah_list="Masjid Quba",
            ziyarah_for_package=self.package_a_with_details,
        )

        self.airline_b = HuzAirlineDetail.objects.create(
            airline_name="Airline B",
            ticket_type="Economy",
            flight_from="Lahore",
            flight_to="Madinah",
            return_flight_from="Madinah",
            return_flight_to="Lahore",
            is_return_flight_included=True,
            airline_for_package=self.package_b_with_details,
        )
        self.transport_b = HuzTransportDetail.objects.create(
            transport_name="Transport B",
            transport_type="Private",
            routes="Lahore,Madinah",
            transport_for_package=self.package_b_with_details,
        )
        self.hotel_b = HuzHotelDetail.objects.create(
            hotel_city="Makkah",
            hotel_name="Hotel B",
            hotel_rating="4 Star",
            room_sharing_type="Double",
            hotel_distance="7",
            distance_type="KM",
            hotel_for_package=self.package_b_with_details,
        )
        self.ziyarah_b = HuzZiyarahDetail.objects.create(
            ziyarah_list="Jabal Uhud",
            ziyarah_for_package=self.package_b_with_details,
        )

    def _create_partner(self, slug):
        return PartnerProfile.objects.create(
            partner_session_token=f"{slug}-session-token",
            user_name=f"{slug}-username",
            name=f"{slug}-name",
            partner_type="Company",
            account_status="Active",
        )

    def _create_package(self, partner, huz_token):
        start_date = timezone.now() + timedelta(days=30)
        end_date = start_date + timedelta(days=10)
        return HuzBasicDetail.objects.create(
            huz_token=huz_token,
            package_type="Umrah",
            package_name=f"Package {huz_token}",
            start_date=start_date,
            end_date=end_date,
            description="Auth hardening test package",
            mecca_nights=5,
            madinah_nights=5,
            package_status="Active",
            package_provider=partner,
        )

    def _auth_headers(self, partner):
        return {"HTTP_AUTHORIZATION": f"Bearer {partner.partner_session_token}"}

    def test_package_mutations_reject_unauthenticated_requests_even_with_legacy_partner_tokens(self):
        initial_package_count = HuzBasicDetail.objects.count()
        original_package_name = self.package_a_with_details.package_name
        original_package_status = self.package_a_with_details.package_status
        original_airline_name = self.airline_a.airline_name
        original_transport_name = self.transport_a.transport_name
        original_hotel_name = self.hotel_a.hotel_name
        original_ziyarah_list = self.ziyarah_a.ziyarah_list

        cases = [
            (
                "basic_post_body_token",
                lambda: self.client.post(
                    "/api/v1/operator/me/packages/basic/",
                    {
                        "partner_session_token": self.partner_a.partner_session_token,
                        "package_type": "Umrah",
                        "package_name": "Unauthorized Package Create",
                        "description": "Should be rejected.",
                        "mecca_nights": 5,
                        "madinah_nights": 5,
                    },
                    format="json",
                ),
            ),
            (
                "basic_put_query_token",
                lambda: self.client.put(
                    f"/api/v1/operator/me/packages/basic/?partner_session_token={self.partner_a.partner_session_token}",
                    {
                        "huz_token": self.package_a_with_details.huz_token,
                        "package_name": "Unauthorized Package Update",
                    },
                    format="json",
                ),
            ),
            (
                "airline_post_body_token",
                lambda: self.client.post(
                    "/api/v1/operator/me/packages/airline/",
                    {
                        "partner_session_token": self.partner_a.partner_session_token,
                        "huz_token": self.package_a_for_creates.huz_token,
                        "airline_name": "Unauthorized Airline",
                        "ticket_type": "Economy",
                        "flight_from": "Karachi",
                        "flight_to": "Jeddah",
                        "return_flight_from": "Jeddah",
                        "return_flight_to": "Karachi",
                    },
                    format="json",
                ),
            ),
            (
                "airline_put_query_token",
                lambda: self.client.put(
                    f"/api/v1/operator/me/packages/airline/?partner_session_token={self.partner_a.partner_session_token}",
                    {
                        "huz_token": self.package_a_with_details.huz_token,
                        "airline_name": "Unauthorized Airline Update",
                    },
                    format="json",
                ),
            ),
            (
                "transport_post_body_token",
                lambda: self.client.post(
                    "/api/v1/operator/me/packages/transport/",
                    {
                        "partner_session_token": self.partner_a.partner_session_token,
                        "huz_token": self.package_a_for_creates.huz_token,
                        "transport_name": "Unauthorized Transport",
                        "transport_type": "Shared",
                        "routes": "Karachi,Jeddah",
                    },
                    format="json",
                ),
            ),
            (
                "transport_put_query_token",
                lambda: self.client.put(
                    f"/api/v1/operator/me/packages/transport/?partner_session_token={self.partner_a.partner_session_token}",
                    {
                        "huz_token": self.package_a_with_details.huz_token,
                        "transport_name": "Unauthorized Transport Update",
                    },
                    format="json",
                ),
            ),
            (
                "hotel_post_body_token",
                lambda: self.client.post(
                    "/api/v1/operator/me/packages/hotel/",
                    {
                        "partner_session_token": self.partner_a.partner_session_token,
                        "huz_token": self.package_a_for_creates.huz_token,
                        "hotel_city": "Makkah",
                        "hotel_name": "Unauthorized Hotel",
                        "hotel_rating": "5 Star",
                        "room_sharing_type": "Quad",
                    },
                    format="json",
                ),
            ),
            (
                "hotel_put_query_token",
                lambda: self.client.put(
                    f"/api/v1/operator/me/packages/hotel/?partner_session_token={self.partner_a.partner_session_token}",
                    {
                        "huz_token": self.package_a_with_details.huz_token,
                        "hotel_id": str(self.hotel_a.hotel_id),
                        "hotel_name": "Unauthorized Hotel Update",
                    },
                    format="json",
                ),
            ),
            (
                "ziyarah_post_body_token",
                lambda: self.client.post(
                    "/api/v1/operator/me/packages/ziyarah/",
                    {
                        "partner_session_token": self.partner_a.partner_session_token,
                        "huz_token": self.package_a_for_creates.huz_token,
                        "ziyarah_list": "Unauthorized Ziyarah",
                    },
                    format="json",
                ),
            ),
            (
                "ziyarah_put_query_token",
                lambda: self.client.put(
                    f"/api/v1/operator/me/packages/ziyarah/?partner_session_token={self.partner_a.partner_session_token}",
                    {
                        "huz_token": self.package_a_with_details.huz_token,
                        "ziyarah_list": "Unauthorized Ziyarah Update",
                    },
                    format="json",
                ),
            ),
            (
                "status_put_query_token",
                lambda: self.client.put(
                    f"/api/v1/operator/me/packages/status/?partner_session_token={self.partner_a.partner_session_token}",
                    {
                        "huz_token": self.package_a_with_details.huz_token,
                        "package_status": "Deactivated",
                    },
                    format="json",
                ),
            ),
        ]

        for label, make_request in cases:
            with self.subTest(label=label):
                response = make_request()
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        self.assertEqual(HuzBasicDetail.objects.count(), initial_package_count)
        self.package_a_with_details.refresh_from_db()
        self.airline_a.refresh_from_db()
        self.transport_a.refresh_from_db()
        self.hotel_a.refresh_from_db()
        self.ziyarah_a.refresh_from_db()

        self.assertEqual(self.package_a_with_details.package_name, original_package_name)
        self.assertEqual(self.package_a_with_details.package_status, original_package_status)
        self.assertEqual(self.airline_a.airline_name, original_airline_name)
        self.assertEqual(self.transport_a.transport_name, original_transport_name)
        self.assertEqual(self.hotel_a.hotel_name, original_hotel_name)
        self.assertEqual(self.ziyarah_a.ziyarah_list, original_ziyarah_list)
        self.assertFalse(
            HuzAirlineDetail.objects.filter(
                airline_for_package=self.package_a_for_creates,
            ).exists()
        )
        self.assertFalse(
            HuzTransportDetail.objects.filter(
                transport_for_package=self.package_a_for_creates,
            ).exists()
        )
        self.assertFalse(
            HuzHotelDetail.objects.filter(
                hotel_for_package=self.package_a_for_creates,
            ).exists()
        )
        self.assertFalse(
            HuzZiyarahDetail.objects.filter(
                ziyarah_for_package=self.package_a_for_creates,
            ).exists()
        )

    def test_authenticated_partner_package_mutations_accept_bearer_auth_without_body_token(self):
        headers = self._auth_headers(self.partner_a)

        create_basic_response = self.client.post(
            "/api/v1/operator/me/packages/basic/",
            {
                "package_type": "Umrah",
                "package_name": "Bearer Created Package",
                "description": "Created with bearer auth only.",
                "mecca_nights": 4,
                "madinah_nights": 4,
            },
            format="json",
            **headers,
        )
        self.assertEqual(create_basic_response.status_code, status.HTTP_201_CREATED)
        created_package = HuzBasicDetail.objects.get(
            huz_token=create_basic_response.data["huz_token"]
        )
        self.assertEqual(created_package.package_provider, self.partner_a)

        update_basic_response = self.client.put(
            "/api/v1/operator/me/packages/basic/",
            {
                "huz_token": self.package_a_with_details.huz_token,
                "package_name": "Bearer Updated Package",
            },
            format="json",
            **headers,
        )
        self.assertEqual(update_basic_response.status_code, status.HTTP_200_OK)
        self.package_a_with_details.refresh_from_db()
        self.assertEqual(self.package_a_with_details.package_name, "Bearer Updated Package")

        create_airline_response = self.client.post(
            "/api/v1/operator/me/packages/airline/",
            {
                "huz_token": self.package_a_for_creates.huz_token,
                "airline_name": "Bearer Airline",
                "ticket_type": "Economy",
                "flight_from": "Karachi",
                "flight_to": "Jeddah",
                "return_flight_from": "Jeddah",
                "return_flight_to": "Karachi",
            },
            format="json",
            **headers,
        )
        self.assertEqual(create_airline_response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            HuzAirlineDetail.objects.filter(
                airline_for_package=self.package_a_for_creates,
                airline_name="Bearer Airline",
            ).exists()
        )

        update_airline_response = self.client.put(
            "/api/v1/operator/me/packages/airline/",
            {
                "huz_token": self.package_a_with_details.huz_token,
                "airline_name": "Bearer Updated Airline",
            },
            format="json",
            **headers,
        )
        self.assertEqual(update_airline_response.status_code, status.HTTP_200_OK)
        self.airline_a.refresh_from_db()
        self.assertEqual(self.airline_a.airline_name, "Bearer Updated Airline")

        create_transport_response = self.client.post(
            "/api/v1/operator/me/packages/transport/",
            {
                "huz_token": self.package_a_for_creates.huz_token,
                "transport_name": "Bearer Transport",
                "transport_type": "Shared",
                "routes": "Karachi,Jeddah",
            },
            format="json",
            **headers,
        )
        self.assertEqual(create_transport_response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            HuzTransportDetail.objects.filter(
                transport_for_package=self.package_a_for_creates,
                transport_name="Bearer Transport",
            ).exists()
        )

        update_transport_response = self.client.put(
            "/api/v1/operator/me/packages/transport/",
            {
                "huz_token": self.package_a_with_details.huz_token,
                "transport_name": "Bearer Updated Transport",
            },
            format="json",
            **headers,
        )
        self.assertEqual(update_transport_response.status_code, status.HTTP_200_OK)
        self.transport_a.refresh_from_db()
        self.assertEqual(self.transport_a.transport_name, "Bearer Updated Transport")

        create_hotel_response = self.client.post(
            "/api/v1/operator/me/packages/hotel/",
            {
                "huz_token": self.package_a_for_creates.huz_token,
                "hotel_city": "Makkah",
                "hotel_name": "Bearer Hotel",
                "hotel_rating": "5 Star",
                "room_sharing_type": "Quad",
            },
            format="json",
            **headers,
        )
        self.assertEqual(create_hotel_response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            HuzHotelDetail.objects.filter(
                hotel_for_package=self.package_a_for_creates,
                hotel_name="Bearer Hotel",
            ).exists()
        )

        update_hotel_response = self.client.put(
            "/api/v1/operator/me/packages/hotel/",
            {
                "huz_token": self.package_a_with_details.huz_token,
                "hotel_id": str(self.hotel_a.hotel_id),
                "hotel_name": "Bearer Updated Hotel",
            },
            format="json",
            **headers,
        )
        self.assertEqual(update_hotel_response.status_code, status.HTTP_200_OK)
        self.hotel_a.refresh_from_db()
        self.assertEqual(self.hotel_a.hotel_name, "Bearer Updated Hotel")

        create_ziyarah_response = self.client.post(
            "/api/v1/operator/me/packages/ziyarah/",
            {
                "huz_token": self.package_a_for_creates.huz_token,
                "ziyarah_list": "Masjid Qiblatain",
            },
            format="json",
            **headers,
        )
        self.assertEqual(create_ziyarah_response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            HuzZiyarahDetail.objects.filter(
                ziyarah_for_package=self.package_a_for_creates,
                ziyarah_list="Masjid Qiblatain",
            ).exists()
        )

        update_ziyarah_response = self.client.put(
            "/api/v1/operator/me/packages/ziyarah/",
            {
                "huz_token": self.package_a_with_details.huz_token,
                "ziyarah_list": "Masjid al-Jinn",
            },
            format="json",
            **headers,
        )
        self.assertEqual(update_ziyarah_response.status_code, status.HTTP_200_OK)
        self.ziyarah_a.refresh_from_db()
        self.assertEqual(self.ziyarah_a.ziyarah_list, "Masjid al-Jinn")

        update_status_response = self.client.put(
            "/api/v1/operator/me/packages/status/",
            {
                "huz_token": self.package_a_with_details.huz_token,
                "package_status": "Deactivated",
            },
            format="json",
            **headers,
        )
        self.assertEqual(update_status_response.status_code, status.HTTP_200_OK)
        self.package_a_with_details.refresh_from_db()
        self.assertEqual(self.package_a_with_details.package_status, "Deactivated")

    def test_canonical_operator_package_basic_mutation_accepts_bearer_auth(self):
        response = self.client.post(
            "/api/v1/operator/me/packages/basic/",
            {
                "package_type": "Umrah",
                "package_name": "Canonical Bearer Package",
                "description": "Created through the canonical operator package route.",
                "mecca_nights": 4,
                "madinah_nights": 4,
            },
            format="json",
            **self._auth_headers(self.partner_a),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created_package = HuzBasicDetail.objects.get(huz_token=response.data["huz_token"])
        self.assertEqual(created_package.package_provider, self.partner_a)

    def test_authenticated_partner_package_mutations_are_scoped_to_authenticated_principal(self):
        headers = self._auth_headers(self.partner_a)

        create_basic_response = self.client.post(
            "/api/v1/operator/me/packages/basic/",
            {
                "partner_session_token": self.partner_b.partner_session_token,
                "package_type": "Umrah",
                "package_name": "Scoped Create Package",
                "description": "Body token should not override bearer principal.",
                "mecca_nights": 3,
                "madinah_nights": 3,
            },
            format="json",
            **headers,
        )
        self.assertEqual(create_basic_response.status_code, status.HTTP_201_CREATED)
        created_package = HuzBasicDetail.objects.get(
            huz_token=create_basic_response.data["huz_token"]
        )
        self.assertEqual(created_package.package_provider, self.partner_a)
        self.assertNotEqual(created_package.package_provider, self.partner_b)

        original_package_name = self.package_b_with_details.package_name
        original_package_status = self.package_b_with_details.package_status
        original_airline_name = self.airline_b.airline_name
        original_transport_name = self.transport_b.transport_name
        original_hotel_name = self.hotel_b.hotel_name
        original_ziyarah_list = self.ziyarah_b.ziyarah_list

        cases = [
            (
                "basic_put",
                lambda: self.client.put(
                    "/api/v1/operator/me/packages/basic/",
                    {
                        "partner_session_token": self.partner_b.partner_session_token,
                        "huz_token": self.package_b_with_details.huz_token,
                        "package_name": "Hijacked Package Name",
                    },
                    format="json",
                    **headers,
                ),
            ),
            (
                "airline_post",
                lambda: self.client.post(
                    "/api/v1/operator/me/packages/airline/",
                    {
                        "partner_session_token": self.partner_b.partner_session_token,
                        "huz_token": self.package_b_for_creates.huz_token,
                        "airline_name": "Hijacked Airline",
                        "ticket_type": "Economy",
                        "flight_from": "Karachi",
                        "flight_to": "Jeddah",
                        "return_flight_from": "Jeddah",
                        "return_flight_to": "Karachi",
                    },
                    format="json",
                    **headers,
                ),
            ),
            (
                "airline_put",
                lambda: self.client.put(
                    "/api/v1/operator/me/packages/airline/",
                    {
                        "partner_session_token": self.partner_b.partner_session_token,
                        "huz_token": self.package_b_with_details.huz_token,
                        "airline_name": "Hijacked Airline Update",
                    },
                    format="json",
                    **headers,
                ),
            ),
            (
                "transport_post",
                lambda: self.client.post(
                    "/api/v1/operator/me/packages/transport/",
                    {
                        "partner_session_token": self.partner_b.partner_session_token,
                        "huz_token": self.package_b_for_creates.huz_token,
                        "transport_name": "Hijacked Transport",
                        "transport_type": "Shared",
                        "routes": "Karachi,Jeddah",
                    },
                    format="json",
                    **headers,
                ),
            ),
            (
                "transport_put",
                lambda: self.client.put(
                    "/api/v1/operator/me/packages/transport/",
                    {
                        "partner_session_token": self.partner_b.partner_session_token,
                        "huz_token": self.package_b_with_details.huz_token,
                        "transport_name": "Hijacked Transport Update",
                    },
                    format="json",
                    **headers,
                ),
            ),
            (
                "hotel_post",
                lambda: self.client.post(
                    "/api/v1/operator/me/packages/hotel/",
                    {
                        "partner_session_token": self.partner_b.partner_session_token,
                        "huz_token": self.package_b_for_creates.huz_token,
                        "hotel_city": "Makkah",
                        "hotel_name": "Hijacked Hotel",
                        "hotel_rating": "5 Star",
                        "room_sharing_type": "Quad",
                    },
                    format="json",
                    **headers,
                ),
            ),
            (
                "hotel_put",
                lambda: self.client.put(
                    "/api/v1/operator/me/packages/hotel/",
                    {
                        "partner_session_token": self.partner_b.partner_session_token,
                        "huz_token": self.package_b_with_details.huz_token,
                        "hotel_id": str(self.hotel_b.hotel_id),
                        "hotel_name": "Hijacked Hotel Update",
                    },
                    format="json",
                    **headers,
                ),
            ),
            (
                "ziyarah_post",
                lambda: self.client.post(
                    "/api/v1/operator/me/packages/ziyarah/",
                    {
                        "partner_session_token": self.partner_b.partner_session_token,
                        "huz_token": self.package_b_for_creates.huz_token,
                        "ziyarah_list": "Hijacked Ziyarah",
                    },
                    format="json",
                    **headers,
                ),
            ),
            (
                "ziyarah_put",
                lambda: self.client.put(
                    "/api/v1/operator/me/packages/ziyarah/",
                    {
                        "partner_session_token": self.partner_b.partner_session_token,
                        "huz_token": self.package_b_with_details.huz_token,
                        "ziyarah_list": "Hijacked Ziyarah Update",
                    },
                    format="json",
                    **headers,
                ),
            ),
            (
                "status_put",
                lambda: self.client.put(
                    "/api/v1/operator/me/packages/status/",
                    {
                        "partner_session_token": self.partner_b.partner_session_token,
                        "huz_token": self.package_b_with_details.huz_token,
                        "package_status": "Deactivated",
                    },
                    format="json",
                    **headers,
                ),
            ),
        ]

        for label, make_request in cases:
            with self.subTest(label=label):
                response = make_request()
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        self.package_b_with_details.refresh_from_db()
        self.airline_b.refresh_from_db()
        self.transport_b.refresh_from_db()
        self.hotel_b.refresh_from_db()
        self.ziyarah_b.refresh_from_db()

        self.assertEqual(self.package_b_with_details.package_name, original_package_name)
        self.assertEqual(self.package_b_with_details.package_status, original_package_status)
        self.assertEqual(self.airline_b.airline_name, original_airline_name)
        self.assertEqual(self.transport_b.transport_name, original_transport_name)
        self.assertEqual(self.hotel_b.hotel_name, original_hotel_name)
        self.assertEqual(self.ziyarah_b.ziyarah_list, original_ziyarah_list)
        self.assertFalse(
            HuzAirlineDetail.objects.filter(
                airline_for_package=self.package_b_for_creates,
            ).exists()
        )
        self.assertFalse(
            HuzTransportDetail.objects.filter(
                transport_for_package=self.package_b_for_creates,
            ).exists()
        )
        self.assertFalse(
            HuzHotelDetail.objects.filter(
                hotel_for_package=self.package_b_for_creates,
            ).exists()
        )
        self.assertFalse(
            HuzZiyarahDetail.objects.filter(
                ziyarah_for_package=self.package_b_for_creates,
            ).exists()
        )


class PackageManagementWebsiteViewTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        self.factory = APIRequestFactory()
        PartnerProfile.objects.filter(
            partner_session_token="partner-website-session-token"
        ).delete()
        self.partner = PartnerProfile.objects.create(
            partner_session_token="partner-website-session-token",
            user_name="partner-website-user",
            name="Website Partner",
            partner_type="Company",
            account_status="Active",
        )

        early_start = timezone.now() + timedelta(days=5)
        early_end = early_start + timedelta(days=7)
        expired_future_start = timezone.now() + timedelta(days=1)
        expired_future_end = expired_future_start + timedelta(days=7)
        next_visible_start = timezone.now() + timedelta(days=22)
        next_visible_end = next_visible_start + timedelta(days=7)
        later_start = timezone.now() + timedelta(days=28)
        later_end = later_start + timedelta(days=7)

        self.ranged_package = HuzBasicDetail.objects.create(
            huz_token="website-package-token-001",
            package_type="Umrah",
            package_name="Window Package",
            start_date=early_start,
            end_date=early_end,
            description="Uses future package ranges",
            package_status="Active",
            package_provider=self.partner,
        )
        self.expired_future_range = HuzPackageDateRange.objects.create(
            start_date=expired_future_start,
            end_date=expired_future_end,
            group_capacity=8,
            package_validity=timezone.now() - timedelta(days=1),
            date_range_for_package=self.ranged_package,
        )
        HuzPackageDateRange.objects.create(
            start_date=early_start,
            end_date=early_end,
            group_capacity=12,
            package_validity=early_start - timedelta(days=2),
            date_range_for_package=self.ranged_package,
        )
        self.future_range = HuzPackageDateRange.objects.create(
            start_date=next_visible_start,
            end_date=next_visible_end,
            group_capacity=18,
            package_validity=next_visible_start - timedelta(days=2),
            date_range_for_package=self.ranged_package,
        )
        HuzAirlineDetail.objects.create(
            airline_name="Saudia",
            ticket_type="Economy",
            flight_from="Karachi",
            flight_to="Jeddah",
            return_flight_from="Jeddah",
            return_flight_to="Karachi",
            is_return_flight_included=True,
            airline_for_package=self.ranged_package,
        )

        self.landed_package = HuzBasicDetail.objects.create(
            huz_token="website-package-token-002",
            package_type="Umrah",
            package_name="Land Package",
            start_date=next_visible_start + timedelta(days=1),
            end_date=next_visible_end + timedelta(days=1),
            description="No flight configured",
            package_status="Active",
            package_provider=self.partner,
        )
        HuzPackageDateRange.objects.create(
            start_date=next_visible_start + timedelta(days=1),
            end_date=next_visible_end + timedelta(days=1),
            group_capacity=None,
            package_validity=next_visible_start - timedelta(days=1),
            date_range_for_package=self.landed_package,
        )

        self.unrated_package = HuzBasicDetail.objects.create(
            huz_token="website-package-token-003",
            package_type="Umrah",
            package_name="Second Package",
            start_date=later_start,
            end_date=later_end,
            description="Same partner, no reviews",
            package_status="Active",
            package_provider=self.partner,
        )
        HuzPackageDateRange.objects.create(
            start_date=later_start,
            end_date=later_end,
            group_capacity=10,
            package_validity=later_start - timedelta(days=2),
            date_range_for_package=self.unrated_package,
        )
        HuzHotelDetail.objects.create(
            hotel_city="Makkah",
            hotel_name="Flat Contract Hotel",
            hotel_rating="4 Star",
            room_sharing_type="Quad",
            hotel_distance="10",
            distance_type="KM",
            hotel_for_package=self.ranged_package,
        )

    def _request_website_packages(self, **query_params):
        request = self.factory.get(
            "/api/v1/packages/public/",
            query_params,
        )
        return GetHuzShortPackageForWebsiteView.as_view()(request)

    def _request_website_search(self, **query_params):
        request = self.factory.get(
            "/api/v1/packages/public/search/",
            query_params,
        )
        return GetSearchPackageByCityNDateView.as_view()(request)

    def test_website_list_uses_future_date_ranges_for_visibility_and_capacity(self):
        response = self._request_website_packages(package_type="Umrah")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results") or []
        ranged_package_payload = next(
            item for item in results if item.get("huz_token") == self.ranged_package.huz_token
        )

        ranges = ranged_package_payload.get("package_date_range") or []
        self.assertEqual(len(ranges), 3)
        self.assertTrue(any(range_item.get("is_expired") for range_item in ranges))
        self.assertTrue(any(not range_item.get("is_expired") for range_item in ranges))
        self.assertEqual(ranged_package_payload.get("package_capacity"), 12)
        self.assertFalse(ranged_package_payload.get("is_landed"))
        self.assertNotIn("package_seats", ranged_package_payload)
        self.assertNotIn("start_date", ranged_package_payload)
        self.assertNotIn("end_date", ranged_package_payload)
        self.assertNotIn("package_validity", ranged_package_payload)

    def test_website_detail_returns_expired_future_ranges_with_explicit_status(self):
        request = self.factory.get(
            "/api/v1/packages/public/detail/",
            {"huz_token": self.ranged_package.huz_token},
        )

        response = GetHuzPackageDetailForWebsiteView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        package_payload = response.data[0]
        ranges = package_payload.get("package_date_range") or []

        self.assertEqual(len(ranges), 3)
        self.assertIn("is_expired", ranges[0])
        expired_range = next(
            range_item for range_item in ranges if range_item.get("range_id") == str(self.expired_future_range.range_id)
        )
        self.assertTrue(expired_range.get("is_expired"))

    def test_website_search_respects_future_package_ranges_even_with_old_package_start_date(self):
        response = self._request_website_search(
            package_type="Umrah",
            start_date=(timezone.now() + timedelta(days=20)).date().isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results") or []
        returned_tokens = {item.get("huz_token") for item in results}

        self.assertIn(self.ranged_package.huz_token, returned_tokens)

    def test_website_list_filters_by_makkah_hotel_distance(self):
        close_start = timezone.now() + timedelta(days=45)
        close_end = close_start + timedelta(days=7)
        close_package = HuzBasicDetail.objects.create(
            huz_token="website-package-token-close-makkah",
            package_type="Umrah",
            package_name="Close Makkah Hotel Package",
            start_date=close_start,
            end_date=close_end,
            description="Walkable Makkah stay",
            package_status="Active",
            package_provider=self.partner,
        )
        HuzPackageDateRange.objects.create(
            start_date=close_start,
            end_date=close_end,
            group_capacity=8,
            package_validity=close_start - timedelta(days=2),
            date_range_for_package=close_package,
        )
        HuzHotelDetail.objects.create(
            hotel_city="Makkah",
            hotel_name="Close Haram Hotel",
            hotel_rating="5 Star",
            room_sharing_type="Double",
            hotel_distance="3",
            distance_type="KM",
            hotel_for_package=close_package,
        )

        response = self._request_website_packages(
            package_type="Umrah",
            makkah_hotel_distance="5",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results") or []
        returned_tokens = {item.get("huz_token") for item in results}

        self.assertIn(close_package.huz_token, returned_tokens)
        self.assertNotIn(self.ranged_package.huz_token, returned_tokens)

    def test_website_search_filters_by_madinah_hotel_distance(self):
        close_start = timezone.now() + timedelta(days=50)
        close_end = close_start + timedelta(days=8)
        close_package = HuzBasicDetail.objects.create(
            huz_token="website-package-token-close-madinah",
            package_type="Umrah",
            package_name="Close Madinah Hotel Package",
            start_date=close_start,
            end_date=close_end,
            description="Near Masjid Nabawi",
            package_status="Active",
            package_provider=self.partner,
        )
        HuzPackageDateRange.objects.create(
            start_date=close_start,
            end_date=close_end,
            group_capacity=6,
            package_validity=close_start - timedelta(days=2),
            date_range_for_package=close_package,
        )
        HuzAirlineDetail.objects.create(
            airline_name="Saudia",
            ticket_type="Economy",
            flight_from="Karachi",
            flight_to="Jeddah",
            return_flight_from="Jeddah",
            return_flight_to="Karachi",
            is_return_flight_included=True,
            airline_for_package=close_package,
        )
        HuzHotelDetail.objects.create(
            hotel_city="Madinah",
            hotel_name="Close Nabawi Hotel",
            hotel_rating="5 Star",
            room_sharing_type="Double",
            hotel_distance="2",
            distance_type="KM",
            hotel_for_package=close_package,
        )
        HuzHotelDetail.objects.create(
            hotel_city="Madinah",
            hotel_name="Far Nabawi Hotel",
            hotel_rating="4 Star",
            room_sharing_type="Quad",
            hotel_distance="7",
            distance_type="KM",
            hotel_for_package=self.ranged_package,
        )

        response = self._request_website_search(
            package_type="Umrah",
            flight_from="Karachi",
            start_date=(timezone.now() + timedelta(days=20)).date().isoformat(),
            madinah_hotel_distance="3",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results") or []
        returned_tokens = {item.get("huz_token") for item in results}

        self.assertIn(close_package.huz_token, returned_tokens)
        self.assertNotIn(self.ranged_package.huz_token, returned_tokens)

    def test_website_list_excludes_packages_after_booking_validity_passes(self):
        expired_start = timezone.now() + timedelta(days=4)
        expired_end = expired_start + timedelta(days=7)
        expired_package = HuzBasicDetail.objects.create(
            huz_token="website-package-token-expired",
            package_type="Umrah",
            package_name="Expired Booking Window",
            start_date=expired_start,
            end_date=expired_end,
            description="Future trip but booking window already closed.",
            package_status="Active",
            package_provider=self.partner,
        )
        HuzPackageDateRange.objects.create(
            start_date=expired_start,
            end_date=expired_end,
            group_capacity=6,
            package_validity=timezone.now() - timedelta(days=1),
            date_range_for_package=expired_package,
        )

        response = self._request_website_packages(package_type="Umrah")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results") or []
        returned_tokens = {item.get("huz_token") for item in results}

        self.assertNotIn(expired_package.huz_token, returned_tokens)

    def test_website_detail_exposes_landed_packages_without_flight_data(self):
        request = self.factory.get(
            "/api/v1/packages/public/detail/",
            {"huz_token": self.landed_package.huz_token},
        )

        response = GetHuzPackageDetailForWebsiteView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 1)
        package_payload = response.data[0]

        self.assertTrue(package_payload.get("is_landed"))
        self.assertIsNone(package_payload.get("airline_detail"))
        self.assertIn("package_date_range", package_payload)
        self.assertNotIn("airline_detail_list", package_payload)
        self.assertNotIn("transport_detail_list", package_payload)
        self.assertNotIn("ziyarah_detail_list", package_payload)
        self.assertNotIn("start_date", package_payload)
        self.assertNotIn("end_date", package_payload)
        self.assertNotIn("package_validity", package_payload)

    def test_website_detail_exposes_flat_hotel_contract_without_nested_duplicates(self):
        request = self.factory.get(
            "/api/v1/packages/public/detail/",
            {"huz_token": self.ranged_package.huz_token},
        )

        response = GetHuzPackageDetailForWebsiteView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        package_payload = response.data[0]

        self.assertIsInstance(package_payload.get("hotel_detail"), list)
        self.assertGreaterEqual(len(package_payload["hotel_detail"]), 1)

        hotel_payload = package_payload["hotel_detail"][0]
        self.assertIn("images", hotel_payload)
        self.assertNotIn("hotel_images", hotel_payload)
        self.assertNotIn("hotel_detail", hotel_payload)
        self.assertNotIn("huz_hotel_id", hotel_payload)

    def test_website_ratings_are_package_specific_not_partner_wide(self):
        BookingRatingAndReview.objects.create(
            partner_total_stars=5,
            partner_comment="Excellent service",
            rating_for_partner=self.partner,
            rating_for_package=self.ranged_package,
        )

        response = self._request_website_packages(package_type="Umrah")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results") or []
        payload_by_token = {item.get("huz_token"): item for item in results}

        self.assertEqual(
            payload_by_token[self.ranged_package.huz_token]["rating_count"]["rating_count"],
            1,
        )
        self.assertEqual(
            payload_by_token[self.unrated_package.huz_token]["rating_count"]["rating_count"],
            0,
        )

    def test_website_list_rejects_unsupported_package_type(self):
        response = self._request_website_packages(package_type="Ziyarah")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data.get("message"),
            "Invalid package_type. Use Hajj or Umrah.",
        )

    def test_canonical_public_packages_alias_returns_results(self):
        response = self.client.get(
            "/api/v1/packages/public/",
            {"package_type": "Umrah"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_tokens = {item.get("huz_token") for item in response.data.get("results", [])}
        self.assertIn(self.ranged_package.huz_token, returned_tokens)


class PartnerWalletEndpointAccessTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        self.factory = APIRequestFactory()
        PartnerProfile.objects.filter(
            partner_session_token="partner-wallet-session-token"
        ).delete()
        self.partner = PartnerProfile.objects.create(
            partner_session_token="partner-wallet-session-token",
            user_name="partner-wallet-user",
            name="Wallet Partner",
            partner_type="Company",
            account_status="Active",
        )
        self.wallet = Wallet.objects.create(
            wallet_code="wallet-code-partner-wallet-tests",
            wallet_session=self.partner,
        )
        PartnerTransactionHistory.objects.create(
            transaction_code="credit-code-1",
            transaction_amount=250.0,
            transaction_type="Credit",
            transaction_for_partner=self.partner,
            transaction_wallet_token=self.wallet,
        )
        PartnerTransactionHistory.objects.create(
            transaction_code="debit-code-1",
            transaction_amount=80.0,
            transaction_type="Debit",
            transaction_for_partner=self.partner,
            transaction_wallet_token=self.wallet,
        )

    def test_transaction_summary_endpoint_works_without_admin_auth(self):
        response = self.client.get(
            "/api/v1/operator/me/wallet/summary/",
            HTTP_AUTHORIZATION=f"Bearer {self.partner.partner_session_token}",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("credit_transaction_amount"), 250.0)
        self.assertEqual(response.data.get("debit_transaction_amount"), 80.0)
        self.assertEqual(response.data.get("credit_number_transactions"), 1)
        self.assertEqual(response.data.get("debit_number_transactions"), 1)

    def test_transaction_history_endpoint_works_without_admin_auth(self):
        response = self.client.get(
            "/api/v1/operator/me/wallet/transactions/",
            HTTP_AUTHORIZATION=f"Bearer {self.partner.partner_session_token}",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_transaction_summary_requires_partner_session_token(self):
        response = self.client.get("/api/v1/operator/me/wallet/summary/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_canonical_wallet_endpoints_accept_bearer_auth_without_partner_session_token(self):
        summary_response = self.client.get(
            "/api/v1/operator/me/wallet/summary/",
            HTTP_AUTHORIZATION=f"Bearer {self.partner.partner_session_token}",
        )
        transactions_response = self.client.get(
            "/api/v1/operator/me/wallet/transactions/",
            HTTP_AUTHORIZATION=f"Bearer {self.partner.partner_session_token}",
        )
        profile_response = self.client.get(
            "/api/v1/operator/me/profile/",
            HTTP_AUTHORIZATION=f"Bearer {self.partner.partner_session_token}",
        )

        self.assertEqual(summary_response.status_code, status.HTTP_200_OK)
        self.assertEqual(summary_response.data.get("credit_transaction_amount"), 250.0)
        self.assertEqual(transactions_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(transactions_response.data), 2)
        self.assertEqual(profile_response.status_code, status.HTTP_200_OK)
        self.assertEqual(profile_response.data.get("partner_session_token"), self.partner.partner_session_token)


class PartnerServicesViewTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        self.factory = APIRequestFactory()
        PartnerProfile.objects.filter(
            partner_session_token="partner-services-session-token"
        ).delete()
        self.partner = PartnerProfile.objects.create(
            partner_session_token="partner-services-session-token",
            user_name="partner-services-user",
            name="Services Partner",
            partner_type="NA",
            account_status="Pending",
        )

    def test_partner_services_endpoint_accepts_hajj_and_umrah_only(self):
        response = self.client.post(
            "/api/v1/operator/me/services/",
            {
                "is_hajj_service_offer": True,
                "is_umrah_service_offer": False,
                "is_ziyarah_service_offer": True,
                "is_transport_service_offer": True,
                "is_visa_service_offer": True,
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.partner.partner_session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.partner.refresh_from_db()
        services = PartnerServices.objects.get(services_of_partner=self.partner)

        self.assertEqual(self.partner.partner_type, "Company")
        self.assertTrue(services.is_hajj_service_offer)
        self.assertFalse(services.is_umrah_service_offer)
        self.assertFalse(services.is_ziyarah_service_offer)
        self.assertFalse(services.is_transport_service_offer)
        self.assertFalse(services.is_visa_service_offer)

    def test_partner_services_endpoint_rejects_empty_supported_service_selection(self):
        response = self.client.post(
            "/api/v1/operator/me/services/",
            {
                "is_hajj_service_offer": False,
                "is_umrah_service_offer": False,
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.partner.partner_session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data.get("message"),
            "Select at least one supported service: Hajj or Umrah.",
        )
        self.assertFalse(
            PartnerServices.objects.filter(services_of_partner=self.partner).exists()
        )

    def test_partner_services_endpoint_rejects_unauthenticated_requests(self):
        response = self.client.post(
            "/api/v1/operator/me/services/",
            {
                "partner_session_token": self.partner.partner_session_token,
                "is_hajj_service_offer": True,
                "is_umrah_service_offer": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class PartnerProfileAndFinancialMutationAuthTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners"])

    def setUp(self):
        self.partner_a = PartnerProfile.objects.create(
            partner_session_token="partner-auth-a-session-token",
            user_name="partner-auth-a",
            name="Partner A",
            partner_type="Company",
            account_status="Active",
            country_code="+92",
            phone_number="3001111111",
        )
        self.partner_b = PartnerProfile.objects.create(
            partner_session_token="partner-auth-b-session-token",
            user_name="partner-auth-b",
            name="Partner B",
            partner_type="Company",
            account_status="Active",
            country_code="+92",
            phone_number="3002222222",
        )
        self.address_a = PartnerMailingDetail.objects.create(
            street_address="Street A",
            city="Karachi",
            state="Sindh",
            country="Pakistan",
            postal_code="74000",
            mailing_of_partner=self.partner_a,
        )
        self.address_b = PartnerMailingDetail.objects.create(
            street_address="Street B",
            city="Lahore",
            state="Punjab",
            country="Pakistan",
            postal_code="54000",
            mailing_of_partner=self.partner_b,
        )
        self.bank_a = PartnerBankAccount.objects.create(
            account_title="Partner A",
            account_number="111111",
            bank_name="A Bank",
            branch_code="001",
            bank_account_for_partner=self.partner_a,
        )
        self.bank_b = PartnerBankAccount.objects.create(
            account_title="Partner B",
            account_number="222222",
            bank_name="B Bank",
            branch_code="002",
            bank_account_for_partner=self.partner_b,
        )
        self.wallet_a = Wallet.objects.create(
            wallet_code="wallet-code-partner-auth-a",
            wallet_amount=500.0,
            wallet_session=self.partner_a,
        )
        Wallet.objects.create(
            wallet_code="wallet-code-partner-auth-b",
            wallet_amount=250.0,
            wallet_session=self.partner_b,
        )

    def _auth_headers(self, partner):
        return {"HTTP_AUTHORIZATION": f"Bearer {partner.partner_session_token}"}

    def test_sensitive_profile_and_financial_mutations_reject_unauthenticated_requests(self):
        response = self.client.put(
            f"/api/v1/operator/me/address/upsert/?partner_session_token={self.partner_a.partner_session_token}",
            {
                "street_address": "Unauthorized Street",
                "city": "Karachi",
                "country": "Pakistan",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        response = self.client.post(
            "/api/v1/operator/me/wallet/banks/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "account_title": "Unauthorized",
                "account_number": "999999",
                "bank_name": "Unauthorized Bank",
                "branch_code": "009",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        response = self.client.post(
            "/api/v1/operator/me/wallet/withdrawals/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "account_id": str(self.bank_a.account_id),
                "withdraw_amount": 25.0,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_partner_address_update_is_scoped_to_authenticated_principal(self):
        response = self.client.put(
            "/api/v1/operator/me/address/upsert/",
            {
                "partner_session_token": self.partner_b.partner_session_token,
                "address_id": str(self.address_b.address_id),
                "street_address": "Attempted takeover",
                "city": "Karachi",
                "country": "Pakistan",
            },
            format="json",
            **self._auth_headers(self.partner_a),
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.address_b.refresh_from_db()
        self.assertEqual(self.address_b.street_address, "Street B")

    def test_authenticated_partner_can_update_own_address_without_body_token(self):
        response = self.client.put(
            "/api/v1/operator/me/address/upsert/",
            {
                "address_id": str(self.address_a.address_id),
                "street_address": "Updated Street A",
                "city": "Karachi",
                "country": "Pakistan",
            },
            format="json",
            **self._auth_headers(self.partner_a),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.address_a.refresh_from_db()
        self.assertEqual(self.address_a.street_address, "Updated Street A")

    def test_authenticated_partner_bank_delete_is_scoped_to_authenticated_principal(self):
        response = self.client.delete(
            "/api/v1/operator/me/wallet/banks/",
            {
                "partner_session_token": self.partner_b.partner_session_token,
                "account_id": str(self.bank_b.account_id),
            },
            format="json",
            **self._auth_headers(self.partner_a),
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(
            PartnerBankAccount.objects.filter(account_id=self.bank_b.account_id).exists()
        )

    def test_authenticated_partner_can_create_own_bank_account(self):
        response = self.client.post(
            "/api/v1/operator/me/wallet/banks/",
            {
                "account_title": "Partner A Savings",
                "account_number": "333333",
                "bank_name": "Savings Bank",
                "branch_code": "003",
            },
            format="json",
            **self._auth_headers(self.partner_a),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            PartnerBankAccount.objects.filter(
                bank_account_for_partner=self.partner_a,
                account_number="333333",
            ).exists()
        )

    def test_authenticated_partner_withdraw_request_is_scoped_to_authenticated_principal(self):
        response = self.client.post(
            "/api/v1/operator/me/wallet/withdrawals/",
            {
                "partner_session_token": self.partner_b.partner_session_token,
                "account_id": str(self.bank_b.account_id),
                "withdraw_amount": 25.0,
            },
            format="json",
            **self._auth_headers(self.partner_a),
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data.get("message"), "Bank account details not found.")
        self.assertFalse(
            PartnerWithdraw.objects.filter(withdraw_for_partner=self.partner_b).exists()
        )

    def test_authenticated_partner_can_create_withdraw_request(self):
        response = self.client.post(
            "/api/v1/operator/me/wallet/withdrawals/",
            {
                "account_id": str(self.bank_a.account_id),
                "withdraw_amount": 125.0,
            },
            format="json",
            **self._auth_headers(self.partner_a),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.wallet_amount, 375.0)
        self.assertTrue(
            PartnerWithdraw.objects.filter(
                withdraw_for_partner=self.partner_a,
                withdraw_bank=self.bank_a,
                withdraw_amount=125.0,
            ).exists()
        )

    def test_authenticated_partner_can_update_avatar_without_form_token(self):
        response = self.client.put(
            "/api/v1/operator/me/profile/avatar/",
            {
                "user_photo": SimpleUploadedFile(
                    "partner-avatar.jpg",
                    b"avatar-image",
                    content_type="image/jpeg",
                )
            },
            format="multipart",
            **self._auth_headers(self.partner_a),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.partner_a.refresh_from_db()
        self.assertTrue(bool(self.partner_a.user_photo))
        self.assertTrue(self.partner_a.user_photo.name.endswith(".jpg"))
