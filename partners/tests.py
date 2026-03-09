from datetime import timedelta

from django.apps import apps
from django.db import connection
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITransactionTestCase

from booking.models import BookingRatingAndReview

from .models import (
    HuzAirlineDetail,
    HuzBasicDetail,
    HuzHotelDetail,
    HuzPackageDateRange,
    PartnerProfile,
    PartnerTransactionHistory,
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
        request = self.factory.get(
            "/partner/get_package_detail_by_partner_token/",
            {
                "partner_session_token": self.partner.partner_session_token,
                "huz_token": self.package.huz_token,
            },
        )

        response = GetHuzPackageDetailByTokenView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        hotel_payload = response.data[0]["hotel_detail"][0]

        self.assertIn("images", hotel_payload)
        self.assertNotIn("hotel_images", hotel_payload)
        self.assertNotIn("hotel_detail", hotel_payload)
        self.assertNotIn("huz_hotel_id", hotel_payload)

    def test_operator_create_package_accepts_package_date_range_without_summary_dates(self):
        range_start = timezone.now() + timedelta(days=14)
        range_end = range_start + timedelta(days=10)
        request = self.factory.post(
            "/partner/enroll_package_basic_detail/",
            {
                "partner_session_token": self.partner.partner_session_token,
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
        )

        response = CreateHuzPackageView.as_view()(request)
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
        results_by_token = {
            item.get("huz_token"): item for item in response.data.get("results") or []
        }
        rating_payload = results_by_token[self.package.huz_token].get("rating_count")
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

        request = self.factory.get(
            "/partner/get_partner_overall_package_statistics/",
            {"partner_session_token": self.partner.partner_session_token},
        )
        response = GetPartnersOverallPackagesStatisticsView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("Active"), 1)
        self.assertEqual(response.data.get("Completed"), 1)


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
            "/partner/get_package_short_detail_for_web/",
            query_params,
        )
        return GetHuzShortPackageForWebsiteView.as_view()(request)

    def _request_website_search(self, **query_params):
        request = self.factory.get(
            "/partner/get_package_detail_by_city_and_date/",
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

        self.assertEqual(len(ranged_package_payload.get("package_date_range") or []), 2)
        self.assertEqual(
            ranged_package_payload["package_date_range"][0].get("group_capacity"),
            12,
        )
        self.assertEqual(ranged_package_payload.get("package_capacity"), 12)
        self.assertFalse(ranged_package_payload.get("is_landed"))
        self.assertNotIn("package_seats", ranged_package_payload)
        self.assertNotIn("start_date", ranged_package_payload)
        self.assertNotIn("end_date", ranged_package_payload)
        self.assertNotIn("package_validity", ranged_package_payload)

    def test_website_search_respects_future_package_ranges_even_with_old_package_start_date(self):
        response = self._request_website_search(
            package_type="Umrah",
            start_date=(timezone.now() + timedelta(days=20)).date().isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results") or []
        returned_tokens = {item.get("huz_token") for item in results}

        self.assertIn(self.ranged_package.huz_token, returned_tokens)

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
            "/partner/get_package_detail_by_package_id_for_web/",
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
            "/partner/get_package_detail_by_package_id_for_web/",
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
