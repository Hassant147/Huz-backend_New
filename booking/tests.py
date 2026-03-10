from datetime import datetime, timedelta
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITransactionTestCase, force_authenticate

from common.models import UserProfile
from partners.models import HuzBasicDetail, HuzPackageDateRange, PartnerProfile

from .manage_partner_booking import (
    GetOverallPartnerComplaintsView,
    GetPackageOverallRatingView,
    GetOverallRatingView,
    BookingAirlineDetailsView,
    BookingHotelAndTransportDetailsView,
    CloseBookingView,
    GetPartnerComplaintsView,
    GetPartnersOverallBookingStatisticsView,
    GetBookingShortDetailForPartnersView,
    PartnersBookingPaymentView,
    GetYearlyBookingStatisticsView,
    GiveUpdateOnComplaintsView,
    ManageBookingDocumentsView,
    ReportBookingView,
    TakeActionView,
)
from .manage_bookings import (
    BookingRatingAndReviewView,
    BookingComplaintsView,
    GetAllBookingsByUserView,
    ManageBookingsView,
    ManagePassportValidityView,
    PaidAmountByTransactionNumberView,
)
from .models import (
    Booking,
    BookingAirlineDetail,
    BookingComplaints,
    BookingObjections,
    PassportValidity,
    BookingRatingAndReview,
    Payment,
    PartnersBookingPayment,
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


def aware_midnight(value):
    return timezone.make_aware(datetime.strptime(value, "%Y-%m-%d"))


class ManageBookingsUserListViewTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin_user = get_user_model().objects.create_user(
            username="booking-user-list-admin",
            password="pass123",
            is_staff=True,
            is_superuser=True,
        )
        self.customer = UserProfile.objects.create(
            session_token="booking-user-list-session-token",
            name="Booking User",
            country_code="+1",
            phone_number="9991112222",
            email="booking-user@example.com",
            user_type="user",
        )
        self.empty_customer = UserProfile.objects.create(
            session_token="booking-empty-session-token",
            name="Empty Booking User",
            country_code="+1",
            phone_number="9993334444",
            email="empty-booking-user@example.com",
            user_type="user",
        )
        self.other_customer = UserProfile.objects.create(
            session_token="booking-other-session-token",
            name="Other Booking User",
            country_code="+1",
            phone_number="9995556666",
            email="other-booking-user@example.com",
            user_type="user",
        )
        self.partner = PartnerProfile.objects.create(
            partner_session_token="booking-user-list-partner-token",
            user_name="booking-user-list-partner",
            name="Booking Partner",
            partner_type="Company",
            account_status="Active",
        )
        start_date = timezone.now() + timedelta(days=15)
        end_date = start_date + timedelta(days=5)
        self.package = HuzBasicDetail.objects.create(
            huz_token="booking-user-list-package-token",
            package_type="Hajj",
            package_name="Booking User Package",
            start_date=start_date,
            end_date=end_date,
            description="Booking list package",
            package_status="Active",
            package_provider=self.partner,
        )
        self.booking = Booking.objects.create(
            booking_number="BOOKING-USER-LIST-001",
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
            total_price=1800,
            special_request="Window seat",
            booking_status="Pending",
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
        )
        Payment.objects.create(
            transaction_number="PAY-BOOKING-USER-LIST-001",
            transaction_type="Full",
            transaction_amount=1800,
            payment_status="Pending",
            booking_token=self.booking,
        )
        self.other_booking = Booking.objects.create(
            booking_number="BOOKING-USER-LIST-002",
            adults=1,
            child=0,
            infants=0,
            sharing="No",
            quad="0",
            triple="0",
            double="0",
            single="1",
            start_date=start_date,
            end_date=end_date,
            total_price=900,
            special_request="None",
            booking_status="Pending",
            payment_type="Bank",
            order_by=self.other_customer,
            order_to=self.partner,
            package_token=self.package,
        )

    def test_get_all_bookings_by_user_returns_legacy_list_shape(self):
        response = self.client.get(
            "/bookings/get_all_booking_short_detail_by_user/",
            {"session_token": self.customer.session_token},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0].get("booking_number"), self.booking.booking_number)
        self.assertIsInstance(response.data[0].get("payment_detail"), list)

    def test_get_all_bookings_by_user_returns_404_when_user_has_no_bookings(self):
        response = self.client.get(
            "/bookings/get_all_booking_short_detail_by_user/",
            {"session_token": self.empty_customer.session_token},
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data.get("message"), "Booking detail not found.")

    def test_get_all_bookings_by_user_accepts_bearer_authorization(self):
        response = self.client.get(
            "/bookings/get_all_booking_short_detail_by_user/",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0].get("booking_number"), self.booking.booking_number)
        self.assertNotIn("X-Auth-Deprecated", response)

    def test_get_all_bookings_by_user_legacy_query_token_sets_deprecation_header(self):
        response = self.client.get(
            "/bookings/get_all_booking_short_detail_by_user/",
            {"session_token": self.customer.session_token},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["X-Auth-Deprecated"], "session_token_in_query")

    def test_get_all_bookings_by_user_header_auth_cannot_access_other_users_bookings(self):
        response = self.client.get(
            "/bookings/get_all_booking_short_detail_by_user/",
            {"session_token": self.other_customer.session_token},
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0].get("booking_number"), self.booking.booking_number)
        self.assertNotEqual(response.data[0].get("booking_number"), self.other_booking.booking_number)


class BookingWorkflowServiceValidationTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin_user = get_user_model().objects.create_user(
            username="booking-workflow-admin",
            password="pass123",
            is_staff=True,
            is_superuser=True,
        )
        self.customer = UserProfile.objects.create(
            session_token="booking-workflow-user-token",
            name="Workflow User",
            country_code="+1",
            phone_number="1012023030",
            email="workflow-user@example.com",
            user_type="user",
        )
        self.partner = PartnerProfile.objects.create(
            partner_session_token="booking-workflow-partner-token",
            user_name="booking-workflow-partner",
            name="Workflow Partner",
            partner_type="Company",
            account_status="Active",
        )
        self.start_date = timezone.now() + timedelta(days=20)
        self.end_date = self.start_date + timedelta(days=5)
        self.package = HuzBasicDetail.objects.create(
            huz_token="booking-workflow-huz-token",
            package_type="Hajj",
            package_name="Workflow Package",
            package_base_cost=1200,
            cost_for_child=300,
            cost_for_infants=100,
            start_date=self.start_date,
            end_date=self.end_date,
            description="Workflow package",
            package_status="Active",
            package_provider=self.partner,
        )
        self.existing_booking = Booking.objects.create(
            booking_number="BOOKING-WORKFLOW-001",
            adults=2,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=self.start_date,
            end_date=self.end_date,
            total_price=2400,
            special_request="Wheelchair support",
            booking_status="Initialize",
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
        )

    def _authenticated_request(self, request):
        force_authenticate(request, user=self.admin_user)
        return request

    def _booking_payload(self):
        return {
            "session_token": self.customer.session_token,
            "partner_session_token": self.partner.partner_session_token,
            "huz_token": self.package.huz_token,
            "adults": 2,
            "child": 1,
            "infants": 0,
            "sharing": "Yes",
            "quad": "0",
            "triple": "0",
            "double": "1",
            "single": "0",
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "total_price": 2700,
            "special_request": "Closer to Haram",
            "payment_type": "Bank",
        }

    def test_create_booking_returns_drf_validation_error_for_missing_required_field(self):
        payload = self._booking_payload()
        payload.pop("single")
        request = self._authenticated_request(
            self.factory.post("/bookings/manage_booking/", payload, format="json")
        )

        response = ManageBookingsView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("message", response.data)
        self.assertIn("single", response.data)

    def test_create_booking_updates_existing_initialized_booking_for_same_package_and_departure(self):
        request = self._authenticated_request(
            self.factory.post("/bookings/manage_booking/", self._booking_payload(), format="json")
        )

        response = ManageBookingsView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("booking_number"), self.existing_booking.booking_number)
        self.existing_booking.refresh_from_db()
        self.assertEqual(self.existing_booking.child, 1)
        self.assertEqual(self.existing_booking.total_price, 2700)
        self.assertEqual(self.existing_booking.special_request, "Closer to Haram")
        self.assertEqual(
            Booking.objects.filter(order_by=self.customer, package_token=self.package).count(),
            1,
        )

    def test_create_booking_allows_new_record_for_same_package_with_different_departure(self):
        payload = self._booking_payload()
        new_start_date = self.start_date + timedelta(days=14)
        payload["start_date"] = new_start_date.isoformat()
        payload["end_date"] = (new_start_date + timedelta(days=5)).isoformat()
        request = self._authenticated_request(
            self.factory.post("/bookings/manage_booking/", payload, format="json")
        )

        response = ManageBookingsView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("booking_number", response.data)
        self.assertEqual(
            Booking.objects.filter(order_by=self.customer, package_token=self.package).count(),
            2,
        )

    def test_v1_create_booking_rejects_when_requested_travellers_exceed_range_capacity(self):
        range_package = HuzBasicDetail.objects.create(
            huz_token="booking-workflow-range-capacity-token",
            package_type="Hajj",
            package_name="Capacity Limited Package",
            package_base_cost=1000,
            cost_for_child=300,
            cost_for_infants=100,
            start_date=self.start_date,
            end_date=self.end_date,
            description="Capacity limited package",
            package_status="Active",
            package_provider=self.partner,
        )
        HuzPackageDateRange.objects.create(
            date_range_for_package=range_package,
            start_date=self.start_date,
            end_date=self.end_date,
            group_capacity=5,
        )

        response = self.client.post(
            "/api/v1/bookings/",
            {
                "partner_session_token": self.partner.partner_session_token,
                "huz_token": range_package.huz_token,
                "adults": 6,
                "child": 0,
                "infants": 0,
                "sharing": "0",
                "quad": "0",
                "triple": "0",
                "double": "0",
                "single": "6",
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat(),
                "total_price": 6000,
                "special_request": "Too many travellers",
                "payment_type": "Bank",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("only allows 5 travellers", response.data.get("message", "").lower())

    def test_v1_create_booking_rejects_expired_range_by_range_id(self):
        expired_start_date = timezone.now() + timedelta(days=1)
        expired_end_date = expired_start_date + timedelta(days=5)
        range_package = HuzBasicDetail.objects.create(
            huz_token="booking-workflow-expired-range-id-token",
            package_type="Hajj",
            package_name="Expired Range Package",
            package_base_cost=1000,
            cost_for_child=300,
            cost_for_infants=100,
            start_date=expired_start_date,
            end_date=expired_end_date,
            description="Expired range package",
            package_status="Active",
            package_provider=self.partner,
        )
        expired_range = HuzPackageDateRange.objects.create(
            date_range_for_package=range_package,
            start_date=expired_start_date,
            end_date=expired_end_date,
            group_capacity=5,
            package_validity=timezone.now() - timedelta(days=1),
        )

        response = self.client.post(
            "/api/v1/bookings/",
            {
                "partner_session_token": self.partner.partner_session_token,
                "huz_token": range_package.huz_token,
                "package_date_range_id": str(expired_range.range_id),
                "adults": 2,
                "child": 0,
                "infants": 0,
                "sharing": "0",
                "quad": "0",
                "triple": "0",
                "double": "0",
                "single": "2",
                "start_date": expired_start_date.isoformat(),
                "end_date": expired_end_date.isoformat(),
                "total_price": 2000,
                "special_request": "Expired by range id",
                "payment_type": "Bank",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("no longer open for booking", response.data.get("message", "").lower())

    def test_v1_create_booking_rejects_expired_range_by_matching_dates(self):
        expired_start_date = timezone.now() + timedelta(days=1)
        expired_end_date = expired_start_date + timedelta(days=5)
        range_package = HuzBasicDetail.objects.create(
            huz_token="booking-workflow-expired-range-match-token",
            package_type="Hajj",
            package_name="Expired Match Package",
            package_base_cost=1000,
            cost_for_child=300,
            cost_for_infants=100,
            start_date=expired_start_date,
            end_date=expired_end_date,
            description="Expired match package",
            package_status="Active",
            package_provider=self.partner,
        )
        HuzPackageDateRange.objects.create(
            date_range_for_package=range_package,
            start_date=expired_start_date,
            end_date=expired_end_date,
            group_capacity=5,
            package_validity=timezone.now() - timedelta(days=1),
        )

        response = self.client.post(
            "/api/v1/bookings/",
            {
                "partner_session_token": self.partner.partner_session_token,
                "huz_token": range_package.huz_token,
                "adults": 2,
                "child": 0,
                "infants": 0,
                "sharing": "0",
                "quad": "0",
                "triple": "0",
                "double": "0",
                "single": "2",
                "start_date": expired_start_date.isoformat(),
                "end_date": expired_end_date.isoformat(),
                "total_price": 2000,
                "special_request": "Expired by matching dates",
                "payment_type": "Bank",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("no longer open for booking", response.data.get("message", "").lower())

    def test_v1_create_booking_rejects_when_other_active_bookings_exhaust_range_capacity(self):
        other_customer = UserProfile.objects.create(
            session_token="booking-range-capacity-other-user-token",
            name="Capacity Other User",
            country_code="+1",
            phone_number="6067078080",
            email="capacity-other-user@example.com",
            user_type="user",
        )
        range_package = HuzBasicDetail.objects.create(
            huz_token="booking-workflow-range-capacity-shared-token",
            package_type="Hajj",
            package_name="Shared Capacity Package",
            package_base_cost=1000,
            cost_for_child=300,
            cost_for_infants=100,
            start_date=self.start_date,
            end_date=self.end_date,
            description="Shared capacity package",
            package_status="Active",
            package_provider=self.partner,
        )
        HuzPackageDateRange.objects.create(
            date_range_for_package=range_package,
            start_date=self.start_date,
            end_date=self.end_date,
            group_capacity=5,
        )
        Booking.objects.create(
            booking_number="BOOKING-WORKFLOW-CAPACITY-001",
            adults=4,
            child=0,
            infants=0,
            sharing="0",
            quad="0",
            triple="0",
            double="2",
            single="0",
            start_date=self.start_date,
            end_date=self.end_date,
            total_price=4000,
            special_request="Occupy seats",
            booking_status="Paid",
            payment_type="Bank",
            order_by=other_customer,
            order_to=self.partner,
            package_token=range_package,
        )

        response = self.client.post(
            "/api/v1/bookings/",
            {
                "partner_session_token": self.partner.partner_session_token,
                "huz_token": range_package.huz_token,
                "adults": 2,
                "child": 0,
                "infants": 0,
                "sharing": "0",
                "quad": "0",
                "triple": "0",
                "double": "1",
                "single": "0",
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat(),
                "total_price": 2000,
                "special_request": "Need two seats",
                "payment_type": "Bank",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("only 1 travellers can still be booked", response.data.get("message", "").lower())

    def test_payment_validation_returns_400_with_useful_error_payload(self):
        payload = {
            "session_token": self.customer.session_token,
            "booking_number": self.existing_booking.booking_number,
            "transaction_number": "TRANS-001",
            "transaction_amount": 2400,
        }
        request = self._authenticated_request(
            self.factory.post(
                "/bookings/paid_amount_by_transaction_number/",
                payload,
                format="json",
            )
        )

        response = PaidAmountByTransactionNumberView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("message", response.data)
        self.assertIn("transaction_type", response.data)

    def test_passport_validation_accepts_legacy_date_only_payload(self):
        payload = {
            "session_token": self.customer.session_token,
            "booking_number": self.existing_booking.booking_number,
            "first_name": "Fatima",
            "last_name": "Noor",
            "date_of_birth": "1990-01-10",
            "passport_number": "P1234567",
            "passport_country": "US",
            "expiry_date": "2030-06-01",
        }
        request = self._authenticated_request(
            self.factory.post(
                "/bookings/manage_passport_validity/",
                payload,
                format="json",
            )
        )

        response = ManagePassportValidityView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.existing_booking.refresh_from_db()
        self.assertEqual(self.existing_booking.booking_status, "Passport_Validation")
        self.assertTrue(
            PassportValidity.objects.filter(
                passport_for_booking_number=self.existing_booking,
                passport_number="P1234567",
            ).exists()
        )

    def test_passport_validation_reuses_existing_placeholder_row(self):
        placeholder_passport = PassportValidity.objects.create(
            passport_for_booking_number=self.existing_booking,
        )
        payload = {
            "session_token": self.customer.session_token,
            "booking_number": self.existing_booking.booking_number,
            "first_name": "Zara",
            "last_name": "Ali",
            "date_of_birth": "1993-03-03",
            "passport_number": "P7651000",
            "passport_country": "PK",
            "expiry_date": "2031-04-01",
        }
        request = self._authenticated_request(
            self.factory.post(
                "/bookings/manage_passport_validity/",
                payload,
                format="json",
            )
        )

        response = ManagePassportValidityView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            PassportValidity.objects.filter(passport_for_booking_number=self.existing_booking).count(),
            1,
        )
        placeholder_passport.refresh_from_db()
        self.assertEqual(placeholder_passport.passport_number, "P7651000")

    def test_v1_passport_update_rejects_duplicate_passport_number_inside_booking(self):
        first_passport = PassportValidity.objects.create(
            first_name="Amina",
            last_name="Khan",
            date_of_birth=aware_midnight("1992-05-05"),
            passport_number="P9990001",
            passport_country="PK",
            expiry_date=aware_midnight("2031-05-05"),
            passport_for_booking_number=self.existing_booking,
        )
        second_passport = PassportValidity.objects.create(
            first_name="Sara",
            last_name="Yousaf",
            date_of_birth=aware_midnight("1991-04-04"),
            passport_number="P9990002",
            passport_country="PK",
            expiry_date=aware_midnight("2031-06-06"),
            passport_for_booking_number=self.existing_booking,
        )

        response = self.client.put(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/passports/",
            {
                "passport_id": str(second_passport.passport_id),
                "first_name": "Sara",
                "last_name": "Yousaf",
                "date_of_birth": "1991-04-04",
                "passport_number": first_passport.passport_number,
                "passport_country": "PK",
                "expiry_date": "2031-06-06",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        second_passport.refresh_from_db()
        self.assertEqual(second_passport.passport_number, "P9990002")

    def test_v1_passport_endpoint_accepts_bearer_auth_without_legacy_session_token(self):
        response = self.client.post(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/passports/",
            {
                "first_name": "Fatima",
                "last_name": "Noor",
                "date_of_birth": "1990-01-10",
                "passport_number": "P7654321",
                "passport_country": "US",
                "expiry_date": "2030-06-01",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.existing_booking.refresh_from_db()
        self.assertEqual(self.existing_booking.booking_status, "Passport_Validation")
        self.assertTrue(
            PassportValidity.objects.filter(
                passport_for_booking_number=self.existing_booking,
                passport_number="P7654321",
            ).exists()
        )

    def test_v1_passport_endpoint_accepts_files_in_single_request(self):
        self.existing_booking.adults = 1
        self.existing_booking.save(update_fields=["adults"])

        passport_file = SimpleUploadedFile(
            "traveler-passport.jpg",
            b"passport-image",
            content_type="image/jpeg",
        )
        photo_file = SimpleUploadedFile(
            "traveler-photo.jpg",
            b"photo-image",
            content_type="image/jpeg",
        )

        response = self.client.post(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/passports/",
            {
                "first_name": "Fatima",
                "last_name": "Noor",
                "date_of_birth": "1990-01-10",
                "passport_number": "P7654322",
                "passport_country": "US",
                "expiry_date": "2030-06-01",
                "user_passport": passport_file,
                "user_photo": photo_file,
            },
            format="multipart",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.existing_booking.refresh_from_db()
        self.assertEqual(self.existing_booking.booking_status, "Pending")

        traveller_passport = PassportValidity.objects.get(
            passport_for_booking_number=self.existing_booking,
            passport_number="P7654322",
        )
        self.assertTrue(bool(traveller_passport.user_passport))
        self.assertTrue(bool(traveller_passport.user_photo))

    def test_v1_passport_update_rejects_unrelated_passport_id(self):
        other_customer = UserProfile.objects.create(
            session_token="booking-workflow-other-user-token",
            name="Other Workflow User",
            country_code="+1",
            phone_number="4045056060",
            email="other-workflow-user@example.com",
            user_type="user",
        )
        other_booking = Booking.objects.create(
            booking_number="BOOKING-WORKFLOW-OTHER-001",
            adults=1,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=self.start_date,
            end_date=self.end_date,
            total_price=1200,
            special_request="N/A",
            booking_status="Passport_Validation",
            payment_type="Bank",
            order_by=other_customer,
            order_to=self.partner,
            package_token=self.package,
        )
        unrelated_passport = PassportValidity.objects.create(
            first_name="Amina",
            last_name="Khan",
            date_of_birth=aware_midnight("1992-05-05"),
            passport_number="P9990001",
            passport_country="PK",
            expiry_date=aware_midnight("2031-05-05"),
            passport_for_booking_number=other_booking,
        )

        response = self.client.put(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/passports/",
            {
                "passport_id": str(unrelated_passport.passport_id),
                "first_name": "Edited",
                "last_name": "Traveler",
                "date_of_birth": "1992-05-05",
                "passport_number": "P9990001",
                "passport_country": "PK",
                "expiry_date": "2031-05-05",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        unrelated_passport.refresh_from_db()
        self.assertEqual(unrelated_passport.first_name, "Amina")

    def test_legacy_passport_upload_endpoints_accept_bearer_auth(self):
        self.existing_booking.adults = 1
        self.existing_booking.save(update_fields=["adults"])
        traveller_passport = PassportValidity.objects.create(
            first_name="Fatima",
            last_name="Noor",
            date_of_birth=aware_midnight("1990-01-10"),
            passport_number="P1234567",
            passport_country="US",
            expiry_date=aware_midnight("2030-06-01"),
            passport_for_booking_number=self.existing_booking,
        )
        self.existing_booking.booking_status = "Passport_Validation"
        self.existing_booking.save(update_fields=["booking_status"])

        passport_file = SimpleUploadedFile(
            "traveler-passport.jpg",
            b"passport-image",
            content_type="image/jpeg",
        )
        photo_file = SimpleUploadedFile(
            "traveler-photo.jpg",
            b"photo-image",
            content_type="image/jpeg",
        )

        with patch("booking.manage_bookings.send_new_order_email"):
            passport_response = self.client.post(
                "/bookings/manage_user_passport/",
                {
                    "session_token": self.customer.session_token,
                    "booking_number": self.existing_booking.booking_number,
                    "passport_id": str(traveller_passport.passport_id),
                    "user_passport": passport_file,
                },
                format="multipart",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )
            photo_response = self.client.post(
                "/bookings/manage_user_passport_photo/",
                {
                    "session_token": self.customer.session_token,
                    "booking_number": self.existing_booking.booking_number,
                    "passport_id": str(traveller_passport.passport_id),
                    "user_photo": photo_file,
                },
                format="multipart",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )

        self.assertEqual(passport_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(photo_response.status_code, status.HTTP_201_CREATED)
        traveller_passport.refresh_from_db()
        self.existing_booking.refresh_from_db()
        self.assertTrue(bool(traveller_passport.user_passport))
        self.assertTrue(bool(traveller_passport.user_photo))
        self.assertEqual(self.existing_booking.booking_status, "Pending")

    def test_v1_users_me_bookings_accepts_bearer_auth(self):
        response = self.client.get(
            "/api/v1/users/me/bookings/",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0].get("booking_number"), self.existing_booking.booking_number)

    def test_v1_booking_detail_accepts_bearer_auth(self):
        response = self.client.get(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("booking_number"), self.existing_booking.booking_number)
        self.assertEqual(response.data.get("user_session_token"), self.customer.session_token)

    def test_v1_booking_detail_cannot_access_other_users_booking(self):
        other_customer = UserProfile.objects.create(
            session_token="booking-workflow-retrieve-other-user-token",
            name="Retrieve Other User",
            country_code="+1",
            phone_number="7878787878",
            email="retrieve-other-user@example.com",
            user_type="user",
        )
        other_booking = Booking.objects.create(
            booking_number="BOOKING-WORKFLOW-OTHER-DETAIL-001",
            adults=1,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=self.start_date + timedelta(days=3),
            end_date=self.end_date + timedelta(days=3),
            total_price=1200,
            special_request="N/A",
            booking_status="Initialize",
            payment_type="Bank",
            order_by=other_customer,
            order_to=self.partner,
            package_token=self.package,
        )

        response = self.client.get(
            f"/api/v1/bookings/{other_booking.booking_number}/",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_v1_create_booking_accepts_bearer_auth_without_legacy_session_token(self):
        payload = self._booking_payload()
        payload.pop("session_token")
        payload["start_date"] = (self.start_date + timedelta(days=21)).isoformat()
        payload["end_date"] = (self.start_date + timedelta(days=26)).isoformat()

        response = self.client.post(
            "/api/v1/bookings/",
            payload,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data.get("user_session_token"), self.customer.session_token)

    def test_v1_delete_endpoint_removes_initialized_booking_with_bearer_auth(self):
        response = self.client.delete(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/",
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data.get("deleted"))
        self.assertFalse(
            Booking.objects.filter(booking_number=self.existing_booking.booking_number).exists()
        )

    def test_legacy_delete_endpoint_cancels_paid_booking(self):
        self.existing_booking.booking_status = "Paid"
        self.existing_booking.save(update_fields=["booking_status"])

        request = self._authenticated_request(
            self.factory.delete(
                "/bookings/create_booking_view/",
                {
                    "session_token": self.customer.session_token,
                    "booking_number": self.existing_booking.booking_number,
                },
                format="json",
            )
        )

        response = ManageBookingsView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.existing_booking.refresh_from_db()
        self.assertEqual(self.existing_booking.booking_status, "Cancel")
        self.assertFalse(response.data.get("deleted"))

    def test_v1_payment_endpoint_accepts_path_booking_identifier(self):
        with patch("booking.services.user_new_booking_email"):
            response = self.client.post(
                f"/api/v1/bookings/{self.existing_booking.booking_id}/payments/",
                {
                    "transaction_number": "V1-TRANS-001",
                    "transaction_type": "Full",
                    "transaction_amount": 2400,
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.existing_booking.refresh_from_db()
        self.assertEqual(self.existing_booking.booking_status, "Paid")

    def test_v1_payment_endpoint_reuses_existing_stage_payment_record(self):
        with patch("booking.services.user_new_booking_email"):
            first_response = self.client.post(
                f"/api/v1/bookings/{self.existing_booking.booking_id}/payments/",
                {
                    "transaction_number": "V1-TRANS-001",
                    "transaction_type": "Full",
                    "transaction_amount": 2400,
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )
            second_response = self.client.post(
                f"/api/v1/bookings/{self.existing_booking.booking_id}/payments/",
                {
                    "transaction_number": "V1-TRANS-002",
                    "transaction_type": "Full",
                    "transaction_amount": 2600,
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_201_CREATED)
        full_payments = Payment.objects.filter(
            booking_token=self.existing_booking,
            transaction_type__iexact="Full",
        )
        self.assertEqual(full_payments.count(), 1)
        payment = full_payments.first()
        self.assertEqual(payment.transaction_number, "V1-TRANS-002")
        self.assertEqual(payment.transaction_amount, 2600)

    def test_v1_payment_endpoint_rejects_duplicate_approved_stage(self):
        Payment.objects.create(
            transaction_number="APPROVED-FULL-001",
            transaction_type="Full",
            transaction_amount=2400,
            payment_status="Approved",
            booking_token=self.existing_booking,
        )

        response = self.client.post(
            f"/api/v1/bookings/{self.existing_booking.booking_id}/payments/",
            {
                "transaction_number": "V1-TRANS-003",
                "transaction_type": "Full",
                "transaction_amount": 2400,
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_v1_payment_endpoint_rejects_payment_id_from_different_stage(self):
        minimum_payment = Payment.objects.create(
            transaction_number="MIN-001",
            transaction_type="Minimum",
            transaction_amount=240,
            payment_status="Pending",
            booking_token=self.existing_booking,
        )

        response = self.client.post(
            f"/api/v1/bookings/{self.existing_booking.booking_id}/payments/",
            {
                "payment_id": str(minimum_payment.payment_id),
                "transaction_number": "FULL-001",
                "transaction_type": "Full",
                "transaction_amount": 2400,
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        minimum_payment.refresh_from_db()
        self.assertEqual(minimum_payment.transaction_type, "Minimum")
        self.assertEqual(minimum_payment.transaction_number, "MIN-001")

    def test_v1_payment_endpoint_rejects_duplicate_transaction_number_system_wide(self):
        other_customer = UserProfile.objects.create(
            session_token="booking-transaction-other-user-token",
            name="Other Transaction User",
            country_code="+1",
            phone_number="9098087070",
            email="other-transaction-user@example.com",
            user_type="user",
        )
        other_booking = Booking.objects.create(
            booking_number="BOOKING-WORKFLOW-TRANS-002",
            adults=1,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=self.start_date + timedelta(days=10),
            end_date=self.end_date + timedelta(days=10),
            total_price=1200,
            special_request="N/A",
            booking_status="Paid",
            payment_type="Bank",
            order_by=other_customer,
            order_to=self.partner,
            package_token=self.package,
        )
        Payment.objects.create(
            transaction_number="GLOBAL-TRANS-001",
            transaction_type="Full",
            transaction_amount=1200,
            payment_status="Pending",
            booking_token=other_booking,
        )

        with patch("booking.services.user_new_booking_email"):
            response = self.client.post(
                f"/api/v1/bookings/{self.existing_booking.booking_id}/payments/",
                {
                    "transaction_number": "GLOBAL-TRANS-001",
                    "transaction_type": "Full",
                    "transaction_amount": 2400,
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data.get("message"),
            "This transaction number has already been used.",
        )

    def test_legacy_payment_photo_endpoint_accepts_valid_upload(self):
        self.client.force_authenticate(user=self.admin_user)
        payment_file = SimpleUploadedFile(
            "payment-receipt.pdf",
            b"legacy-payment-receipt",
            content_type="application/pdf",
        )

        with patch("booking.services.user_new_booking_email"):
            response = self.client.post(
                "/bookings/pay_booking_amount_by_transaction_photo/",
                {
                    "session_token": self.customer.session_token,
                    "booking_number": self.existing_booking.booking_number,
                    "transaction_amount": "2400",
                    "transaction_type": "Full",
                    "transaction_photo": payment_file,
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Payment.objects.filter(
                booking_token=self.existing_booking,
                transaction_photo__contains="payment_uploads/",
            ).exists()
        )

    def test_legacy_payment_photo_endpoint_accepts_bearer_auth(self):
        payment_file = SimpleUploadedFile(
            "payment-receipt-bearer.pdf",
            b"legacy-payment-receipt-bearer",
            content_type="application/pdf",
        )

        with patch("booking.services.user_new_booking_email"):
            response = self.client.post(
                "/bookings/pay_booking_amount_by_transaction_photo/",
                {
                    "session_token": self.customer.session_token,
                    "booking_number": self.existing_booking.booking_number,
                    "transaction_amount": "2400",
                    "transaction_type": "Full",
                    "transaction_photo": payment_file,
                },
                format="multipart",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Payment.objects.filter(
                booking_token=self.existing_booking,
                transaction_photo__contains="payment_uploads/",
            ).exists()
        )

    def test_v1_payment_endpoint_accepts_receipt_upload_in_single_request(self):
        payment_file = SimpleUploadedFile(
            "payment-receipt-v1.pdf",
            b"v1-payment-receipt",
            content_type="application/pdf",
        )

        with patch("booking.services.user_new_booking_email"):
            response = self.client.post(
                f"/api/v1/bookings/{self.existing_booking.booking_id}/payments/",
                {
                    "transaction_amount": "2400",
                    "transaction_type": "Full",
                    "transaction_photo": payment_file,
                },
                format="multipart",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Payment.objects.filter(
                booking_token=self.existing_booking,
                transaction_photo__contains="payment_uploads/",
            ).exists()
        )

    def test_v1_complaint_endpoint_creates_record_and_user_list_returns_it(self):
        self.existing_booking.booking_status = "Pending"
        self.existing_booking.save(update_fields=["booking_status"])

        response = self.client.post(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/complaints/",
            {
                "complaint_title": "Need support",
                "complaint_message": "Please review this booking.",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        list_response = self.client.get(
            "/api/v1/users/me/complaints/",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0].get("booking_number"), self.existing_booking.booking_number)

    def test_v1_request_endpoint_creates_record_and_user_list_returns_it(self):
        self.existing_booking.booking_status = "Completed"
        self.existing_booking.save(update_fields=["booking_status"])

        response = self.client.post(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/requests/",
            {
                "request_title": "Need concierge help",
                "request_message": "Please arrange support.",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        list_response = self.client.get(
            "/api/v1/users/me/requests/",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0].get("booking_number"), self.existing_booking.booking_number)

    def test_v1_review_endpoint_accepts_bearer_auth(self):
        self.existing_booking.booking_status = "Completed"
        self.existing_booking.save(update_fields=["booking_status"])

        response = self.client.post(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/reviews/",
            {
                "partner_total_stars": 5,
                "partner_comment": "Everything went well.",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            BookingRatingAndReview.objects.filter(rating_for_booking=self.existing_booking).exists()
        )

    def test_v1_objection_response_endpoint_accepts_bearer_auth(self):
        self.existing_booking.booking_status = "Objection"
        self.existing_booking.save(update_fields=["booking_status"])
        objection = BookingObjections.objects.create(
            remarks_or_reason="Passport photo is unclear.",
            objection_for_booking=self.existing_booking,
        )
        objection_file = SimpleUploadedFile(
            "objection-response.pdf",
            b"updated-passport-copy",
            content_type="application/pdf",
        )

        response = self.client.put(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/objections/{objection.objection_id}/response/",
            {
                "client_remarks": "Updated document attached.",
                "objection_document": objection_file,
            },
            format="multipart",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.existing_booking.refresh_from_db()
        objection.refresh_from_db()
        self.assertEqual(self.existing_booking.booking_status, "Pending")
        self.assertTrue(bool(objection.required_document_for_objection))


class ApproveBookingPaymentViewTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        self.admin_user = get_user_model().objects.create_user(
            username="approve-payment-admin",
            password="pass123",
            is_staff=True,
            is_superuser=True,
        )
        self.customer = UserProfile.objects.create(
            session_token="approve-payment-user-token",
            name="Approve Payment User",
            country_code="+1",
            phone_number="3034045050",
            email="approve-payment-user@example.com",
            user_type="user",
        )
        self.partner = PartnerProfile.objects.create(
            partner_session_token="approve-payment-partner-token",
            user_name="approve-payment-partner",
            name="Approve Payment Partner",
            partner_type="Company",
            account_status="Active",
        )
        self.package = HuzBasicDetail.objects.create(
            huz_token="approve-payment-huz-token",
            package_type="Umrah",
            package_name="Approve Payment Package",
            package_base_cost=1200,
            start_date=timezone.now() + timedelta(days=30),
            end_date=timezone.now() + timedelta(days=36),
            description="Approve payment package",
            package_status="Active",
            package_provider=self.partner,
        )

    def test_admin_can_reject_initial_payment_and_restore_booking_to_initialize(self):
        booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-001",
            adults=2,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=timezone.now() + timedelta(days=30),
            end_date=timezone.now() + timedelta(days=36),
            total_price=2400,
            special_request="Near Haram",
            booking_status="Paid",
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
        )
        payment = Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-REF-001",
            transaction_type="Full",
            transaction_amount=2400,
            payment_status="Pending",
            booking_token=booking,
        )

        self.client.force_authenticate(user=self.admin_user)
        with patch("management.approval_task.send_payment_rejection_email") as mocked_rejection_email, patch(
            "management.approval_task._notify_user_about_payment_update"
        ) as mocked_notify_user:
            response = self.client.put(
                "/management/approve_booking_payment/",
                {
                    "session_token": self.customer.session_token,
                    "booking_number": booking.booking_number,
                    "payment_id": str(payment.payment_id),
                    "decision": "reject",
                    "review_message": "The receipt image is unreadable.",
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.booking_status, "Initialize")
        self.assertFalse(booking.is_payment_received)
        self.assertEqual(payment.payment_status, "Rejected")
        self.assertEqual(payment.review_message, "The receipt image is unreadable.")
        mocked_rejection_email.assert_called_once_with(
            self.customer.email,
            self.customer.name,
            booking.booking_number,
            "The receipt image is unreadable.",
        )
        mocked_notify_user.assert_called_once()

    def test_admin_can_reject_full_payment_without_resetting_booking_after_minimum_approval(self):
        booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-002",
            adults=2,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=timezone.now() + timedelta(days=40),
            end_date=timezone.now() + timedelta(days=46),
            total_price=2400,
            special_request="Near Haram",
            booking_status="Pending",
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
            is_payment_received=True,
        )
        Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-MIN-002",
            transaction_type="Minimum",
            transaction_amount=240,
            payment_status="Approved",
            booking_token=booking,
        )
        full_payment = Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-FULL-002",
            transaction_type="Full",
            transaction_amount=2160,
            payment_status="Pending",
            booking_token=booking,
        )

        self.client.force_authenticate(user=self.admin_user)
        with patch("management.approval_task.send_payment_rejection_email"), patch(
            "management.approval_task._notify_user_about_payment_update"
        ):
            response = self.client.put(
                "/management/approve_booking_payment/",
                {
                    "session_token": self.customer.session_token,
                    "booking_number": booking.booking_number,
                    "payment_id": str(full_payment.payment_id),
                    "decision": "reject",
                    "review_message": "The transfer reference does not match the amount.",
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        full_payment.refresh_from_db()
        self.assertEqual(booking.booking_status, "Pending")
        self.assertTrue(booking.is_payment_received)
        self.assertEqual(full_payment.payment_status, "Rejected")
        self.assertEqual(
            full_payment.review_message,
            "The transfer reference does not match the amount.",
        )

    def test_admin_can_approve_pending_full_payment_without_regressing_booking_status(self):
        booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-003",
            adults=1,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=timezone.now() + timedelta(days=50),
            end_date=timezone.now() + timedelta(days=56),
            total_price=1200,
            special_request="None",
            booking_status="Pending",
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
            is_payment_received=True,
        )
        Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-MIN-003",
            transaction_type="Minimum",
            transaction_amount=120,
            payment_status="Approved",
            booking_token=booking,
        )
        full_payment = Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-FULL-003",
            transaction_type="Full",
            transaction_amount=1080,
            payment_status="Pending",
            booking_token=booking,
        )

        self.client.force_authenticate(user=self.admin_user)
        with patch("management.approval_task.send_payment_verification_email") as mocked_verified_email, patch(
            "management.approval_task._notify_user_about_payment_update"
        ) as mocked_notify_user, patch("management.approval_task.preparation_email") as mocked_preparation_email:
            response = self.client.put(
                "/management/approve_booking_payment/",
                {
                    "session_token": self.customer.session_token,
                    "booking_number": booking.booking_number,
                    "payment_id": str(full_payment.payment_id),
                    "decision": "approve",
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        full_payment.refresh_from_db()
        self.assertEqual(booking.booking_status, "Pending")
        self.assertTrue(booking.is_payment_received)
        self.assertEqual(full_payment.payment_status, "Approved")
        self.assertIsNone(full_payment.review_message)
        mocked_verified_email.assert_called_once()
        mocked_notify_user.assert_called_once()
        mocked_preparation_email.assert_not_called()

    def test_user_can_resubmit_rejected_initial_payment_and_restore_booking_to_paid(self):
        booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-004",
            adults=1,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=timezone.now() + timedelta(days=35),
            end_date=timezone.now() + timedelta(days=41),
            total_price=1200,
            special_request="Window seat",
            booking_status="Initialize",
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
            is_payment_received=False,
        )
        payment = Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-REF-004",
            transaction_type="Full",
            transaction_amount=1200,
            payment_status="Rejected",
            review_message="Old receipt",
            booking_token=booking,
        )

        response = self.client.put(
            "/bookings/pay_booking_amount_by_transaction_number/",
            {
                "session_token": self.customer.session_token,
                "booking_number": booking.booking_number,
                "payment_id": str(payment.payment_id),
                "transaction_number": "APPROVE-PAYMENT-REF-004-UPDATED",
                "transaction_type": "Full",
                "transaction_amount": 1200,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.booking_status, "Paid")
        self.assertEqual(payment.payment_status, "Pending")
        self.assertIsNone(payment.review_message)

    def test_admin_review_queue_includes_pending_full_payment_bookings(self):
        booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-005",
            adults=1,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=timezone.now() + timedelta(days=45),
            end_date=timezone.now() + timedelta(days=51),
            total_price=1200,
            special_request="Aisle seat",
            booking_status="Pending",
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
            is_payment_received=True,
        )
        Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-MIN-005",
            transaction_type="Minimum",
            transaction_amount=120,
            payment_status="Approved",
            booking_token=booking,
        )
        Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-FULL-005",
            transaction_type="Full",
            transaction_amount=1080,
            payment_status="Pending",
            booking_token=booking,
        )

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get("/management/fetch_all_paid_bookings/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking_numbers = [item["booking_number"] for item in response.data]
        self.assertIn(booking.booking_number, booking_numbers)


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

    def _create_complaint(
        self,
        *,
        partner,
        package,
        booking,
        status_value="Open",
        ticket="CMP-001",
        title="Complaint title",
        message="Complaint message",
    ):
        return BookingComplaints.objects.create(
            complaint_ticket=ticket,
            complaint_title=title,
            complaint_message=message,
            complaint_status=status_value,
            complaint_by_user=self.customer,
            complaint_for_partner=partner,
            complaint_for_package=package,
            complaint_for_booking=booking,
        )

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

    def test_booking_list_filters_by_booking_number(self):
        matching_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-FILTER-001",
            booking_status="Active",
        )
        self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-FILTER-999",
            booking_status="Active",
        )

        request = self._authenticated_request(
            self.factory.get(
                "/bookings/get_all_booking_detail_for_partner/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_status": "Active",
                    "booking_number": "001",
                    "page": 1,
                    "page_size": 10,
                },
            )
        )

        response = GetBookingShortDetailForPartnersView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 1)
        self.assertEqual(
            response.data.get("results")[0].get("booking_number"),
            matching_booking.booking_number,
        )

    def test_complaints_list_returns_paginated_empty_payload(self):
        request = self._authenticated_request(
            self.factory.get(
                "/bookings/get_all_complaints_for_partner/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "page": 1,
                    "page_size": 10,
                },
            )
        )

        response = GetPartnerComplaintsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 0)
        self.assertEqual(response.data.get("results"), [])

    def test_complaints_list_is_scoped_to_partner_without_status_filter(self):
        booking_a = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-CMP-A-001",
            booking_status="Active",
        )
        booking_b = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-CMP-B-001",
            booking_status="Active",
        )

        own_complaint = self._create_complaint(
            partner=self.partner_a,
            package=self.package_a,
            booking=booking_a,
            ticket="CMP-A-001",
            title="Own complaint",
            message="Issue for partner A",
        )
        self._create_complaint(
            partner=self.partner_b,
            package=self.package_b,
            booking=booking_b,
            ticket="CMP-B-001",
            title="Other complaint",
            message="Issue for partner B",
        )

        request = self._authenticated_request(
            self.factory.get(
                "/bookings/get_all_complaints_for_partner/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                },
            )
        )

        response = GetPartnerComplaintsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 1)
        self.assertEqual(
            response.data.get("results")[0].get("complaint_id"),
            str(own_complaint.complaint_id),
        )

    def test_complaints_list_supports_status_and_search_filters(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-CMP-FILTER-001",
            booking_status="Active",
        )
        matched_complaint = self._create_complaint(
            partner=self.partner_a,
            package=self.package_a,
            booking=booking,
            status_value="Open",
            ticket="CMP-FILTER-OPEN",
            title="Delayed transport",
            message="Transport reached late",
        )
        self._create_complaint(
            partner=self.partner_a,
            package=self.package_a,
            booking=booking,
            status_value="Solved",
            ticket="CMP-FILTER-SOLVED",
            title="Solved issue",
            message="Issue has been resolved",
        )

        request = self._authenticated_request(
            self.factory.get(
                "/bookings/get_all_complaints_for_partner/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "complaint_status": "Open",
                    "search": "transport",
                },
            )
        )

        response = GetPartnerComplaintsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 1)
        first_result = response.data.get("results")[0]
        self.assertEqual(first_result.get("complaint_id"), str(matched_complaint.complaint_id))
        self.assertEqual(first_result.get("complaint_status"), "Open")

    def test_complaint_status_update_rejects_invalid_transition(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-CMP-TRANSITION-001",
            booking_status="Active",
        )
        complaint = self._create_complaint(
            partner=self.partner_a,
            package=self.package_a,
            booking=booking,
            status_value="Open",
            ticket="CMP-TRANSITION-OPEN",
        )

        request = self._authenticated_request(
            self.factory.post(
                "/bookings/give_feedback_on_complaints/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "complaint_id": str(complaint.complaint_id),
                    "complaint_status": "Close",
                    "response_message": "Closing directly",
                },
                format="json",
            )
        )

        response = GiveUpdateOnComplaintsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        complaint.refresh_from_db()
        self.assertEqual(complaint.complaint_status, "Open")

    def test_complaint_status_update_is_scoped_to_partner(self):
        booking = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-CMP-SCOPE-001",
            booking_status="Active",
        )
        complaint = self._create_complaint(
            partner=self.partner_b,
            package=self.package_b,
            booking=booking,
            status_value="Open",
            ticket="CMP-SCOPE-OPEN",
        )

        request = self._authenticated_request(
            self.factory.post(
                "/bookings/give_feedback_on_complaints/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "complaint_id": str(complaint.complaint_id),
                    "complaint_status": "InProgress",
                    "response_message": "Attempting unauthorized update",
                },
                format="json",
            )
        )

        response = GiveUpdateOnComplaintsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        complaint.refresh_from_db()
        self.assertEqual(complaint.complaint_status, "Open")

    def test_complaint_status_update_allows_sequential_transition(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-CMP-SEQUENCE-001",
            booking_status="Active",
        )
        complaint = self._create_complaint(
            partner=self.partner_a,
            package=self.package_a,
            booking=booking,
            status_value="Open",
            ticket="CMP-SEQUENCE-OPEN",
        )

        request = self._authenticated_request(
            self.factory.post(
                "/bookings/give_feedback_on_complaints/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "complaint_id": str(complaint.complaint_id),
                    "complaint_status": "InProgress",
                    "response_message": "Complaint is now under review.",
                },
                format="json",
            )
        )

        response = GiveUpdateOnComplaintsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        complaint.refresh_from_db()
        self.assertEqual(complaint.complaint_status, "InProgress")
        self.assertEqual(complaint.response_message, "Complaint is now under review.")

    @patch("booking.manage_partner_booking.send_booking_documents_email")
    def test_manage_booking_documents_rejects_invalid_document_type(
        self, mocked_send_booking_documents
    ):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-DOC-INVALID-001",
            booking_status="Active",
        )
        upload_file = SimpleUploadedFile(
            "sample.pdf",
            b"%PDF-1.4 test payload",
            content_type="application/pdf",
        )

        request = self._authenticated_request(
            self.factory.post(
                "/bookings/manage_booking_documents/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": booking.booking_number,
                    "document_for": "passport",
                    "document_link": upload_file,
                },
                format="multipart",
            )
        )

        response = ManageBookingDocumentsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid document_for", response.data.get("message", ""))
        mocked_send_booking_documents.assert_not_called()

    def test_hotel_transport_post_requires_booking_number(self):
        request = self._authenticated_request(
            self.factory.post(
                "/bookings/manage_booking_hotel_or_transport_details/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "jeddah_name": "J Name",
                    "jeddah_number": "+123",
                    "mecca_name": "M Name",
                    "mecca_number": "+456",
                    "madinah_name": "Md Name",
                    "madinah_number": "+789",
                    "comment_1": "note 1",
                    "comment_2": "note 2",
                    "detail_for": "Hotel",
                },
                format="json",
            )
        )

        response = BookingHotelAndTransportDetailsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Booking number", response.data.get("message", ""))

    def test_hotel_transport_post_rejects_invalid_detail_for(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-DETAIL-INVALID-001",
            booking_status="Active",
        )

        request = self._authenticated_request(
            self.factory.post(
                "/bookings/manage_booking_hotel_or_transport_details/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": booking.booking_number,
                    "jeddah_name": "J Name",
                    "jeddah_number": "+123",
                    "mecca_name": "M Name",
                    "mecca_number": "+456",
                    "madinah_name": "Md Name",
                    "madinah_number": "+789",
                    "comment_1": "note 1",
                    "comment_2": "note 2",
                    "detail_for": "Bus",
                },
                format="json",
            )
        )

        response = BookingHotelAndTransportDetailsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid detail_for", response.data.get("message", ""))

    def test_airline_put_is_scoped_to_booking_airline_id(self):
        booking_a = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-AIRLINE-A-001",
            booking_status="Active",
        )
        booking_b = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-AIRLINE-B-001",
            booking_status="Active",
        )

        own_airline = BookingAirlineDetail.objects.create(
            flight_date=timezone.now(),
            flight_time="10:00:00",
            flight_from="From-A",
            flight_to="To-A",
            airline_for_booking=booking_a,
        )
        other_airline = BookingAirlineDetail.objects.create(
            flight_date=timezone.now(),
            flight_time="11:00:00",
            flight_from="From-B",
            flight_to="To-B",
            airline_for_booking=booking_b,
        )

        request = self._authenticated_request(
            self.factory.put(
                "/bookings/manage_booking_airline_details/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_airline_id": str(other_airline.booking_airline_id),
                    "booking_number": booking_a.booking_number,
                    "flight_date": timezone.now().isoformat(),
                    "flight_time": "15:30:00",
                    "flight_from": "Updated-From",
                    "flight_to": "Updated-To",
                },
                format="json",
            )
        )

        response = BookingAirlineDetailsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data.get("message"), "Airline details not found.")

        own_airline.refresh_from_db()
        self.assertEqual(own_airline.flight_from, "From-A")
        self.assertEqual(own_airline.flight_to, "To-A")

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

    def test_overall_complaints_counts_merge_legacy_close_and_closed(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-CMP-OVERALL-001",
            booking_status="Active",
        )
        self._create_complaint(
            partner=self.partner_a,
            package=self.package_a,
            booking=booking,
            status_value="Close",
            ticket="CMP-CLOSE-001",
        )
        self._create_complaint(
            partner=self.partner_a,
            package=self.package_a,
            booking=booking,
            status_value="Closed",
            ticket="CMP-CLOSED-001",
        )

        request = self._authenticated_request(
            self.factory.get(
                "/bookings/get_overall_complaints_counts/",
                {"partner_session_token": self.partner_a.partner_session_token},
            )
        )

        response = GetOverallPartnerComplaintsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("Close"), 2)
        self.assertEqual(response.data.get("Open"), 0)
        self.assertEqual(response.data.get("InProgress"), 0)
        self.assertEqual(response.data.get("Solved"), 0)

    def test_yearly_earning_statistics_rejects_invalid_year(self):
        request = self._authenticated_request(
            self.factory.get(
                "/bookings/get_yearly_earning_statistics/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "year": "not-a-year",
                },
            )
        )

        response = GetYearlyBookingStatisticsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid year", response.data.get("message", ""))

    def test_overall_booking_statistics_include_all_booking_status_choices(self):
        self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-STATS-PASSPORT-001",
            booking_status="Passport_Validation",
        )
        self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-STATS-CANCEL-001",
            booking_status="Cancel",
        )

        request = self._authenticated_request(
            self.factory.get(
                "/bookings/get_overall_booking_statistics/",
                {"partner_session_token": self.partner_a.partner_session_token},
            )
        )

        response = GetPartnersOverallBookingStatisticsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("Passport_Validation", response.data)
        self.assertIn("Cancel", response.data)
        self.assertEqual(response.data.get("Passport_Validation"), 1)
        self.assertEqual(response.data.get("Cancel"), 1)

    def test_receivable_payment_statistics_returns_paginated_empty_payload(self):
        request = self._authenticated_request(
            self.factory.get(
                "/bookings/get_receivable_payment_statistics/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "page": 1,
                    "page_size": 10,
                },
            )
        )

        response = PartnersBookingPaymentView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 0)
        self.assertEqual(response.data.get("results"), [])

    def test_receivable_payment_statistics_are_scoped_to_partner(self):
        partner_a_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-REC-A-001",
            booking_status="Completed",
        )
        partner_b_booking = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-REC-B-001",
            booking_status="Completed",
        )

        PartnersBookingPayment.objects.create(
            receivable_amount=1000.0,
            pending_amount=100.0,
            processed_amount=0.0,
            payment_status="NotPaid",
            payment_for_partner=self.partner_a,
            payment_for_package=self.package_a,
            payment_for_booking=partner_a_booking,
        )
        PartnersBookingPayment.objects.create(
            receivable_amount=500.0,
            pending_amount=50.0,
            processed_amount=0.0,
            payment_status="NotPaid",
            payment_for_partner=self.partner_b,
            payment_for_package=self.package_b,
            payment_for_booking=partner_b_booking,
        )

        request = self._authenticated_request(
            self.factory.get(
                "/bookings/get_receivable_payment_statistics/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "page": 1,
                    "page_size": 10,
                },
            )
        )

        response = PartnersBookingPaymentView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 1)

        results = response.data.get("results") or []
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("booking_number"), partner_a_booking.booking_number)
        self.assertEqual(results[0].get("partner_session_token"), self.partner_a.partner_session_token)
        self.assertEqual(float(results[0].get("receivable_amount")), 1000.0)

    def test_overall_rating_distribution_normalizes_decimal_ratings(self):
        booking_one = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-001",
            booking_status="Completed",
        )
        booking_two = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-002",
            booking_status="Completed",
        )
        booking_three = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-003",
            booking_status="Completed",
        )

        BookingRatingAndReview.objects.create(
            partner_total_stars=4.6,
            partner_comment="Great service",
            rating_for_partner=self.partner_a,
            rating_for_package=self.package_a,
            rating_for_booking=booking_one,
            rating_by_user=self.customer,
        )
        BookingRatingAndReview.objects.create(
            partner_total_stars=4.4,
            partner_comment="Good service",
            rating_for_partner=self.partner_a,
            rating_for_package=self.package_a,
            rating_for_booking=booking_two,
            rating_by_user=self.customer,
        )
        BookingRatingAndReview.objects.create(
            partner_total_stars=5.8,
            partner_comment="Invalid legacy value",
            rating_for_partner=self.partner_a,
            rating_for_package=self.package_a,
            rating_for_booking=booking_three,
            rating_by_user=self.customer,
        )

        overall_request = self._authenticated_request(
            self.factory.get(
                "/bookings/get_overall_partner_rating/",
                {"partner_session_token": self.partner_a.partner_session_token},
            )
        )
        overall_response = GetOverallRatingView.as_view()(overall_request)
        self.assertEqual(overall_response.status_code, status.HTTP_200_OK)
        self.assertEqual(overall_response.data.get("total_star_5"), 1)
        self.assertEqual(overall_response.data.get("total_star_4"), 1)
        self.assertEqual(overall_response.data.get("total_star_3"), 0)

        package_request = self._authenticated_request(
            self.factory.get(
                "/bookings/get_overall_rating_package_wise/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "huz_token": self.package_a.huz_token,
                },
            )
        )
        package_response = GetPackageOverallRatingView.as_view()(package_request)
        self.assertEqual(package_response.status_code, status.HTTP_200_OK)
        self.assertEqual(package_response.data.get("total_package_star_5"), 1)
        self.assertEqual(package_response.data.get("total_package_star_4"), 1)
        self.assertEqual(package_response.data.get("total_package_star_3"), 0)

    def test_rating_submission_supports_closed_booking_and_validates_stars(self):
        closed_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-CLOSED-001",
            booking_status="Closed",
        )

        invalid_request = self._authenticated_request(
            self.factory.post(
                "/bookings/rating_and_review/",
                {
                    "session_token": self.customer.session_token,
                    "booking_number": closed_booking.booking_number,
                    "huz_concierge": 5,
                    "huz_support": 5,
                    "huz_platform": 5,
                    "huz_service_quality": 5,
                    "huz_response_time": 5,
                    "huz_comment": "All good",
                    "partner_total_stars": 4.5,
                    "partner_comment": "Great",
                },
                format="json",
            )
        )
        invalid_response = BookingRatingAndReviewView.as_view()(invalid_request)
        self.assertEqual(invalid_response.status_code, status.HTTP_400_BAD_REQUEST)

        valid_request = self._authenticated_request(
            self.factory.post(
                "/bookings/rating_and_review/",
                {
                    "session_token": self.customer.session_token,
                    "booking_number": closed_booking.booking_number,
                    "huz_concierge": 5,
                    "huz_support": 5,
                    "huz_platform": 5,
                    "huz_service_quality": 5,
                    "huz_response_time": 5,
                    "huz_comment": "All good",
                    "partner_total_stars": 5,
                    "partner_comment": "Great",
                },
                format="json",
            )
        )
        valid_response = BookingRatingAndReviewView.as_view()(valid_request)
        self.assertEqual(valid_response.status_code, status.HTTP_201_CREATED)

    def test_complaint_submission_supports_closed_booking_status(self):
        closed_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-COMPLAINT-CLOSED-001",
            booking_status="Closed",
        )

        request = self._authenticated_request(
            self.factory.post(
                "/bookings/raise_complaint_booking_wise/",
                {
                    "session_token": self.customer.session_token,
                    "booking_number": closed_booking.booking_number,
                    "complaint_title": "Need follow-up",
                    "complaint_message": "Issue details",
                },
                format="multipart",
            )
        )

        response = BookingComplaintsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
