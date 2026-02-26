from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITransactionTestCase, force_authenticate

from common.models import UserProfile
from partners.models import HuzBasicDetail, PartnerProfile

from .manage_partner_booking import (
    CloseBookingView,
    GetBookingShortDetailForPartnersView,
    ReportBookingView,
    TakeActionView,
)
from .models import Booking, BookingObjections, PassportValidity


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


class ManagePartnerBookingViewsTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin_user = get_user_model().objects.create_user(
            username="booking-admin",
            password="pass123",
            is_staff=True,
            is_superuser=True,
        )

        self.customer = UserProfile.objects.create(
            session_token="customer-session-token",
            name="Customer",
            country_code="+1",
            phone_number="1234567890",
            email="customer@example.com",
            user_type="user",
        )

        self.partner_a = self._create_partner("partner-a")
        self.partner_b = self._create_partner("partner-b")
        self.package_a = self._create_package(self.partner_a, "huz-a-token")
        self.package_b = self._create_package(self.partner_b, "huz-b-token")

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
            package_type="Hajj",
            package_name=f"Package-{huz_token}",
            start_date=start_date,
            end_date=end_date,
            description="Test package",
            package_status="Active",
            package_provider=partner,
        )

    def _create_booking(self, *, partner, package, booking_number, booking_status):
        start_date = timezone.now() + timedelta(days=7)
        end_date = start_date + timedelta(days=5)
        return Booking.objects.create(
            booking_number=booking_number,
            adults=2,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=start_date,
            end_date=end_date,
            total_price=1500,
            special_request="N/A",
            booking_status=booking_status,
            payment_type="Bank",
            order_by=self.customer,
            order_to=partner,
            package_token=package,
        )

    def _authenticated_request(self, request):
        force_authenticate(request, user=self.admin_user)
        return request

    def test_booking_list_returns_paginated_empty_payload(self):
        self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-ACTIVE-001",
            booking_status="Active",
        )

        request = self._authenticated_request(
            self.factory.get(
                "/bookings/get_all_booking_detail_for_partner/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_status": "Pending",
                    "page": 1,
                    "page_size": 10,
                },
            )
        )

        response = GetBookingShortDetailForPartnersView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 0)
        self.assertEqual(response.data.get("results"), [])

    def test_booking_list_accepts_partner_token_without_authorization_header(self):
        self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-ACTIVE-002",
            booking_status="Active",
        )

        request = self.factory.get(
            "/bookings/get_all_booking_detail_for_partner/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "booking_status": "Active",
                "page": 1,
                "page_size": 10,
            },
        )

        response = GetBookingShortDetailForPartnersView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 1)

    def test_close_booking_is_scoped_to_partner(self):
        other_partner_booking = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-OTHER-001",
            booking_status="Completed",
        )

        request = self._authenticated_request(
            self.factory.put(
                "/bookings/update_booking_status_into_close/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": other_partner_booking.booking_number,
                },
                format="json",
            )
        )

        response = CloseBookingView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data.get("message"), "Booking detail not found.")

        other_partner_booking.refresh_from_db()
        self.assertEqual(other_partner_booking.booking_status, "Completed")

    def test_report_booking_requires_passport_for_same_booking(self):
        partner_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-A-REPORT-001",
            booking_status="Completed",
        )
        other_booking = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-B-REPORT-001",
            booking_status="Completed",
        )
        unrelated_passport = PassportValidity.objects.create(
            passport_for_booking_number=other_booking
        )

        request = self._authenticated_request(
            self.factory.put(
                "/bookings/update_booking_status_into_report_rabbit/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": partner_booking.booking_number,
                    "passport_id": str(unrelated_passport.passport_id),
                },
                format="json",
            )
        )

        response = ReportBookingView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.data.get("message"),
            "Passport not found for the provided booking.",
        )

        partner_booking.refresh_from_db()
        unrelated_passport.refresh_from_db()
        self.assertEqual(partner_booking.booking_status, "Completed")
        self.assertFalse(unrelated_passport.report_rabbit)

    @patch("booking.manage_partner_booking.send_objection_email")
    def test_take_action_sends_email_only_for_objection(self, mocked_send_objection):
        pending_booking_active = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-A-PENDING-001",
            booking_status="Pending",
        )
        pending_booking_objection = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-A-PENDING-002",
            booking_status="Pending",
        )

        active_request = self._authenticated_request(
            self.factory.put(
                "/bookings/partner_action_for_booking/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": pending_booking_active.booking_number,
                    "partner_remarks": "All good",
                    "booking_status": "Active",
                },
                format="json",
            )
        )
        active_response = TakeActionView.as_view()(active_request)
        self.assertEqual(active_response.status_code, status.HTTP_201_CREATED)
        mocked_send_objection.assert_not_called()

        objection_request = self._authenticated_request(
            self.factory.put(
                "/bookings/partner_action_for_booking/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": pending_booking_objection.booking_number,
                    "partner_remarks": "Missing docs",
                    "booking_status": "Objection",
                },
                format="json",
            )
        )
        objection_response = TakeActionView.as_view()(objection_request)
        self.assertEqual(objection_response.status_code, status.HTTP_201_CREATED)
        mocked_send_objection.assert_called_once()
        self.assertTrue(
            BookingObjections.objects.filter(
                objection_for_booking=pending_booking_objection
            ).exists()
        )
