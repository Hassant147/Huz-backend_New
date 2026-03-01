from datetime import timedelta

from django.apps import apps
from django.db import connection
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITransactionTestCase

from booking.models import BookingRatingAndReview

from .models import HuzBasicDetail, PartnerProfile, Wallet, PartnerTransactionHistory
from .partner_accounts_and_transactions import (
    GetPartnerAllTransactionHistoryView,
    GetPartnerTransactionOverallSummaryView,
)
from .package_management_operator import (
    GetPartnersOverallPackagesStatisticsView,
    GetHuzPackageDetailByTokenView,
    GetHuzShortPackageByTokenView,
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


class PackageManagementOperatorViewTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        self.factory = APIRequestFactory()
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

    def _request_short_packages(self, **query_params):
        request = self.factory.get(
            "/partner/get_package_short_detail_by_partner_token/",
            query_params,
        )
        return GetHuzShortPackageByTokenView.as_view()(request)

    def test_get_package_detail_returns_single_item_list(self):
        request = self.factory.get(
            "/partner/get_package_detail_by_partner_token/",
            {
                "partner_session_token": self.partner.partner_session_token,
                "huz_token": self.package.huz_token,
            },
        )

        response = GetHuzPackageDetailByTokenView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0].get("huz_token"), self.package.huz_token)

    def test_get_package_detail_returns_404_for_unknown_token(self):
        request = self.factory.get(
            "/partner/get_package_detail_by_partner_token/",
            {
                "partner_session_token": self.partner.partner_session_token,
                "huz_token": "unknown-token",
            },
        )

        response = GetHuzPackageDetailByTokenView.as_view()(request)
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
        rating_payload = response.data["results"][0].get("rating_count")
        self.assertIsInstance(rating_payload, dict)
        self.assertEqual(rating_payload.get("rating_count"), 1)
        self.assertEqual(rating_payload.get("average_stars"), 4.0)

    def test_get_short_packages_accept_bearer_authorization(self):
        response = self.client.get(
            "/partner/get_package_short_detail_by_partner_token/",
            {"package_type": "Hajj"},
            HTTP_AUTHORIZATION=f"Bearer {self.partner.partner_session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)
        returned_tokens = {item.get("huz_token") for item in response.data.get("results") or []}
        self.assertIn(self.package.huz_token, returned_tokens)
        self.assertNotIn("X-Auth-Deprecated", response)

    def test_get_short_packages_rejects_unauthenticated_requests(self):
        response = self.client.get(
            "/partner/get_package_short_detail_by_partner_token/",
            {"package_type": "Hajj"},
        )

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

        request = self.factory.get(
            "/partner/get_partner_overall_package_statistics/",
            {"partner_session_token": self.partner.partner_session_token},
        )
        response = GetPartnersOverallPackagesStatisticsView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("Active"), 1)
        self.assertEqual(response.data.get("Completed"), 1)
        self.assertEqual(response.data.get("Block"), 1)
        self.assertEqual(response.data.get("Pending"), 1)
        self.assertEqual(response.data.get("NotActive"), 1)


class PartnerWalletEndpointAccessTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        self.factory = APIRequestFactory()
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
        request = self.factory.get(
            "/partner/get_partner_over_transaction_amount/",
            {"partner_session_token": self.partner.partner_session_token},
        )

        response = GetPartnerTransactionOverallSummaryView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("credit_transaction_amount"), 250.0)
        self.assertEqual(response.data.get("debit_transaction_amount"), 80.0)
        self.assertEqual(response.data.get("credit_number_transactions"), 1)
        self.assertEqual(response.data.get("debit_number_transactions"), 1)

    def test_transaction_history_endpoint_works_without_admin_auth(self):
        request = self.factory.get(
            "/partner/get_partner_all_transaction_history/",
            {"partner_session_token": self.partner.partner_session_token},
        )

        response = GetPartnerAllTransactionHistoryView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_transaction_summary_requires_partner_session_token(self):
        request = self.factory.get("/partner/get_partner_over_transaction_amount/")

        response = GetPartnerTransactionOverallSummaryView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get("message"), "Missing user information.")
