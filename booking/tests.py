from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITransactionTestCase, force_authenticate

from common.models import MailingDetail, UserProfile
from management.approval_task import FetchPaidBookingView
from partners.models import BusinessProfile, HuzAirlineDetail, HuzBasicDetail, HuzHotelDetail, HuzPackageDateRange, HuzTransportDetail, PartnerProfile, Wallet

from .manage_partner_booking import (
    GetOverallPartnerComplaintsView,
    GetRatingPackageWiseView,
    GetPackageOverallRatingView,
    GetOverallRatingView,
    BookingAirlineDetailsView,
    BookingHotelAndTransportDetailsView,
    CloseBookingView,
    DeleteBookingDocumentsView,
    GetBookingDetailByBookingNumberForPartnerView,
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
from .models import (
    Booking,
    BookingAirlineDetail,
    BookingComplaints,
    BookingDocuments,
    BookingHotelFulfillment,
    BookingObjections,
    BookingRequest,
    BookingTransportFulfillment,
    DocumentsStatus,
    PassportValidity,
    BookingRatingAndReview,
    Payment,
    PartnersBookingPayment,
    TravelerIssue,
)
from .querysets import annotate_effective_booking_status
from .serializers import (
    BookingComplaintsSerializer,
    BookingMutationSerializer,
    BookingRequestSerializer,
    DetailBookingSerializer,
    PartnerRatingSerializer,
)
from .services import (
    get_booking_by_identifier_for_user,
    record_booking_payment,
    validate_passport,
)
from .statuses import (
    BOOKING_STATUS_AWAITING_FINAL_PAYMENT,
    BOOKING_STATUS_CANCELLED,
    BOOKING_STATUS_COMPLETED,
    BOOKING_STATUS_HOLD,
    BOOKING_STATUS_IN_FULFILLMENT,
    BOOKING_STATUS_READY_FOR_OPERATOR,
    BOOKING_STATUS_READY_FOR_TRAVEL,
    BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
    ISSUE_STATUS_OPERATOR_OBJECTION,
    ISSUE_STATUS_REPORTED,
    PAYMENT_STATUS_APPROVED,
    PAYMENT_STATUS_REJECTED,
    PAYMENT_STATUS_UNDER_REVIEW,
    WORKFLOW_BUCKET_HISTORY,
    WORKFLOW_BUCKET_ISSUES,
    WORKFLOW_BUCKET_REPORTED,
    WORKFLOW_BUCKET_VIEW_ONLY,
)
from .workflow import (
    booking_hotel_fulfillments_are_complete,
    booking_transport_fulfillment_is_complete,
    sync_booking_state,
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


class BookingWorkflowServiceValidationTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        cache.clear()
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
        BusinessProfile.objects.create(
            company_name="Workflow Travel",
            contact_name="Workflow Partner",
            contact_number="03001230000",
            company_of_partner=self.partner,
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
            cost_for_sharing=900,
            cost_for_quad=1000,
            cost_for_triple=1100,
            cost_for_double=1200,
            cost_for_single=1400,
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
            booking_status=BOOKING_STATUS_HOLD,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
        )

    def _authenticated_request(self, request):
        force_authenticate(request, user=self.admin_user)
        return request

    def _partner_auth_headers(self, partner):
        return {"HTTP_AUTHORIZATION": f"Bearer {partner.partner_session_token}"}

    def _build_traveler_breakdown(self, travelers):
        return [
            {
                "traveler_type": traveler_type,
                "room_type": room_type,
            }
            for traveler_type, room_type in travelers
        ]

    def _create_payment(self, booking, *, stage, amount, status_value, suffix):
        return Payment.objects.create(
            transaction_number=f"{booking.booking_number}-{stage}-{suffix}",
            transaction_type=stage,
            transaction_amount=amount,
            payment_status=status_value,
            booking_token=booking,
        )

    def _approve_minimum(self, booking=None):
        booking = booking or self.existing_booking
        payment = self._create_payment(
            booking,
            stage="Minimum",
            amount=max(float(booking.total_price) * 0.1, 1),
            status_value=PAYMENT_STATUS_APPROVED,
            suffix="approved-minimum",
        )
        sync_booking_state(booking, save=True)
        return payment

    def _approve_full(self, booking=None):
        booking = booking or self.existing_booking
        payment = self._create_payment(
            booking,
            stage="Full",
            amount=float(booking.total_price),
            status_value=PAYMENT_STATUS_APPROVED,
            suffix="approved-full",
        )
        sync_booking_state(booking, save=True)
        return payment

    def _add_complete_passports(self, booking=None):
        booking = booking or self.existing_booking
        traveller_count = booking.adults + booking.child + booking.infants
        for index in range(traveller_count):
            PassportValidity.objects.create(
                first_name=f"Traveler{index + 1}",
                last_name="Test",
                date_of_birth=aware_midnight("1990-01-10"),
                passport_number=f"{booking.booking_number}-{index + 1:02d}",
                passport_country="PK",
                expiry_date=aware_midnight("2031-06-01"),
                user_passport=SimpleUploadedFile(
                    f"passport-{index + 1}.jpg",
                    b"passport-image",
                    content_type="image/jpeg",
                ),
                user_photo=SimpleUploadedFile(
                    f"photo-{index + 1}.jpg",
                    b"photo-image",
                    content_type="image/jpeg",
                ),
                passport_for_booking_number=booking,
            )
        sync_booking_state(booking, save=True)

    def _mark_ready_for_operator(self, booking=None):
        booking = booking or self.existing_booking
        self._approve_minimum(booking)
        self._add_complete_passports(booking)
        self._approve_full(booking)
        booking.refresh_from_db()
        sync_booking_state(booking, save=True)
        return booking

    def _mark_in_fulfillment(self, booking=None):
        booking = self._mark_ready_for_operator(booking)
        booking.booking_status = BOOKING_STATUS_IN_FULFILLMENT
        booking.save(update_fields=["booking_status"])
        booking.refresh_from_db()
        sync_booking_state(booking, save=True)
        return booking

    def _mark_ready_for_travel(self, booking=None):
        booking = self._mark_in_fulfillment(booking)
        BookingAirlineDetail.objects.update_or_create(
            airline_for_booking=booking,
            flight_direction="outbound",
            defaults={
                "flight_date": booking.start_date,
                "flight_time": booking.start_date.time(),
                "flight_from": "Karachi",
                "flight_to": "Jeddah",
            },
        )
        BookingDocuments.objects.get_or_create(
            document_for="eVisa",
            document_category="evisa",
            document_scope=BookingDocuments.DOCUMENT_SCOPE_BOOKING,
            document_title="Visa document",
            document_for_booking_token=booking,
        )
        BookingDocuments.objects.get_or_create(
            document_for="airline",
            document_category="airline",
            document_scope=BookingDocuments.DOCUMENT_SCOPE_BOOKING,
            document_title="Airline ticket",
            document_for_booking_token=booking,
        )
        DocumentsStatus.objects.update_or_create(
            status_for_booking=booking,
            defaults={
                "is_visa_completed": True,
                "is_airline_completed": True,
                "is_airline_detail_completed": True,
                "is_hotel_completed": True,
                "is_transport_completed": True,
            },
        )
        booking.refresh_from_db()
        sync_booking_state(booking, save=True)
        return booking

    def _booking_payload(self):
        return {
            "session_token": self.customer.session_token,
            "partner_session_token": self.partner.partner_session_token,
            "huz_token": self.package.huz_token,
            "adults": 2,
            "child": 1,
            "infants": 0,
            "sharing": "0",
            "quad": "0",
            "triple": "0",
            "double": "2",
            "single": "0",
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "total_price": 2700,
            "traveler_breakdown": self._build_traveler_breakdown(
                [
                    ("Adult", "Double(2 bed)"),
                    ("Adult", "Double(2 bed)"),
                    ("Child (2-5)", ""),
                ]
            ),
            "special_request": "Closer to Haram",
            "payment_type": "Bank",
        }

    def test_v1_create_booking_rejects_when_requested_travellers_exceed_range_capacity(self):
        range_package = HuzBasicDetail.objects.create(
            huz_token="booking-workflow-range-capacity-token",
            package_type="Hajj",
            package_name="Capacity Limited Package",
            package_base_cost=1000,
            cost_for_child=300,
            cost_for_infants=100,
            cost_for_sharing=800,
            cost_for_quad=900,
            cost_for_triple=950,
            cost_for_double=1000,
            cost_for_single=1000,
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
                "traveler_breakdown": self._build_traveler_breakdown(
                    [("Adult", "Single(1 bed)")] * 6
                ),
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
            cost_for_sharing=800,
            cost_for_quad=900,
            cost_for_triple=950,
            cost_for_double=1000,
            cost_for_single=1000,
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
                "traveler_breakdown": self._build_traveler_breakdown(
                    [("Adult", "Single(1 bed)")] * 2
                ),
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
            cost_for_sharing=800,
            cost_for_quad=900,
            cost_for_triple=950,
            cost_for_double=1000,
            cost_for_single=1000,
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
                "traveler_breakdown": self._build_traveler_breakdown(
                    [("Adult", "Single(1 bed)")] * 2
                ),
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
            cost_for_sharing=800,
            cost_for_quad=900,
            cost_for_triple=950,
            cost_for_double=1000,
            cost_for_single=1000,
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
            booking_status=BOOKING_STATUS_HOLD,
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
                "double": "2",
                "single": "0",
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat(),
                "total_price": 2000,
                "traveler_breakdown": self._build_traveler_breakdown(
                    [("Adult", "Double(2 bed)")] * 2
                ),
                "special_request": "Need two seats",
                "payment_type": "Bank",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("only 1 travellers can still be booked", response.data.get("message", "").lower())


    def test_v1_passport_update_rejects_duplicate_passport_number_inside_booking(self):
        self._approve_minimum()
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

    def test_v1_passport_update_rejects_expiry_on_or_before_booking_return_date(self):
        self._approve_minimum()
        passport = PassportValidity.objects.create(
            first_name="Amina",
            last_name="Khan",
            date_of_birth=aware_midnight("1992-05-05"),
            passport_number="P9990010",
            passport_country="PK",
            expiry_date=aware_midnight("2031-05-05"),
            passport_for_booking_number=self.existing_booking,
        )

        response = self.client.put(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/passports/",
            {
                "passport_id": str(passport.passport_id),
                "first_name": "Amina",
                "last_name": "Khan",
                "date_of_birth": "1992-05-05",
                "passport_number": "P9990010",
                "passport_country": "PK",
                "expiry_date": self.end_date.date().isoformat(),
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        expected_message = (
            "Passport expiry must be later than the package return date. "
            "Please renew your passport before continuing with this booking."
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get("message"), expected_message)
        self.assertEqual(response.data.get("expiry_date"), [expected_message])
        passport.refresh_from_db()
        self.assertEqual(passport.expiry_date.date().isoformat(), "2031-05-05")

    def test_v1_passport_endpoint_accepts_bearer_auth_without_legacy_session_token(self):
        self._approve_minimum()
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
        self.assertEqual(self.existing_booking.booking_status, BOOKING_STATUS_TRAVELER_DETAILS_PENDING)
        self.assertTrue(
            PassportValidity.objects.filter(
                passport_for_booking_number=self.existing_booking,
                passport_number="P7654321",
            ).exists()
        )

    def test_v1_passport_endpoint_rejects_expiry_on_or_before_booking_return_date(self):
        self._approve_minimum()
        invalid_expiry_date = self.end_date.date().isoformat()

        response = self.client.post(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/passports/",
            {
                "first_name": "Fatima",
                "last_name": "Noor",
                "date_of_birth": "1990-01-10",
                "passport_number": "P7654329",
                "passport_country": "US",
                "expiry_date": invalid_expiry_date,
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        expected_message = (
            "Passport expiry must be later than the package return date. "
            "Please renew your passport before continuing with this booking."
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get("message"), expected_message)
        self.assertEqual(response.data.get("expiry_date"), [expected_message])
        self.assertFalse(
            PassportValidity.objects.filter(
                passport_for_booking_number=self.existing_booking,
                passport_number="P7654329",
            ).exists()
        )

    def test_v1_passport_endpoint_accepts_files_in_single_request(self):
        self.existing_booking.adults = 1
        self.existing_booking.save(update_fields=["adults"])
        self._approve_minimum()

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
        self.assertEqual(self.existing_booking.booking_status, BOOKING_STATUS_AWAITING_FINAL_PAYMENT)

        traveller_passport = PassportValidity.objects.get(
            passport_for_booking_number=self.existing_booking,
            passport_number="P7654322",
        )
        self.assertTrue(bool(traveller_passport.user_passport))
        self.assertTrue(bool(traveller_passport.user_photo))

    def test_v1_passport_update_rejects_unrelated_passport_id(self):
        self._approve_minimum()
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
            booking_status=BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
            payment_type="Bank",
            order_by=other_customer,
            order_to=self.partner,
            package_token=self.package,
        )
        self._create_payment(
            other_booking,
            stage="Minimum",
            amount=120,
            status_value=PAYMENT_STATUS_APPROVED,
            suffix="other-approved-minimum",
        )
        sync_booking_state(other_booking, save=True)
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


    def test_v1_users_me_bookings_accepts_bearer_auth(self):
        response = self.client.get(
            "/api/v1/users/me/bookings/",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 1)
        self.assertEqual(len(response.data.get("results", [])), 1)
        self.assertEqual(
            response.data["results"][0].get("booking_number"),
            self.existing_booking.booking_number,
        )
        self.assertEqual(
            response.data["results"][0].get("company_detail", {}).get("company_name"),
            "Workflow Travel",
        )
        self.assertFalse(response.data["results"][0].get("has_airline_detail"))
        self.assertFalse(response.data["results"][0].get("has_transport_detail"))

    def test_v1_users_me_bookings_filters_status_bucket_server_side(self):
        self._create_payment(
            self.existing_booking,
            stage="Minimum",
            amount=max(float(self.existing_booking.total_price) * 0.1, 1),
            status_value=PAYMENT_STATUS_UNDER_REVIEW,
            suffix="users-me-under-review",
        )
        other_booking = Booking.objects.create(
            booking_number="BOOKING-WORKFLOW-COMPLETED-001",
            adults=1,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=self.start_date + timedelta(days=7),
            end_date=self.end_date + timedelta(days=7),
            total_price=1200,
            special_request="N/A",
            booking_status=BOOKING_STATUS_COMPLETED,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
        )

        response = self.client.get(
            "/api/v1/users/me/bookings/",
            {"status_bucket": "under_review"},
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 1)
        self.assertEqual(
            response.data["results"][0].get("booking_number"),
            self.existing_booking.booking_number,
        )
        self.assertNotEqual(
            response.data["results"][0].get("booking_number"),
            other_booking.booking_number,
        )

    def test_v1_existing_booking_lookup_returns_matching_active_booking(self):
        response = self.client.get(
            "/api/v1/users/me/bookings/existing/",
            {
                "huz_token": self.package.huz_token,
                "start_date": self.start_date.date().isoformat(),
                "end_date": self.end_date.date().isoformat(),
            },
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data.get("exists"))
        self.assertEqual(
            response.data.get("booking", {}).get("booking_number"),
            self.existing_booking.booking_number,
        )

    def test_detail_booking_serializer_does_not_save_during_read_serialization(self):
        with patch.object(Booking, "save", autospec=True) as save_mock:
            serializer = DetailBookingSerializer(self.existing_booking)
            payload = serializer.data

        self.assertEqual(payload.get("booking_number"), self.existing_booking.booking_number)
        save_mock.assert_not_called()

    def test_detail_booking_serializer_exposes_only_typed_traveler_contract(self):
        payload = DetailBookingSerializer(self.existing_booking).data

        self.assertNotIn("traveller_detail", payload)
        self.assertNotIn("passport_validity_detail", payload)
        self.assertIn("traveler_groups", payload)
        self.assertIn("traveler_issues", payload)

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
            booking_status=BOOKING_STATUS_HOLD,
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

    def test_v1_create_booking_returns_mutation_summary_without_heavy_relations(self):
        payload = self._booking_payload()
        payload["start_date"] = (self.start_date + timedelta(days=23)).isoformat()
        payload["end_date"] = (self.start_date + timedelta(days=28)).isoformat()

        response = self.client.post(
            "/api/v1/bookings/",
            payload,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data.get("response_mode"), "mutation_summary")
        self.assertEqual(response.data.get("user_session_token"), self.customer.session_token)
        self.assertEqual(response.data.get("payment_detail"), [])
        self.assertNotIn("booking_documents", response.data)
        self.assertNotIn("booking_airline_details", response.data)
        self.assertNotIn("booking_hotel_and_transport_details", response.data)
        self.assertNotIn("passport_validity_detail", response.data)

    def test_v1_create_booking_retries_when_booking_number_collides(self):
        payload = self._booking_payload()
        payload["start_date"] = (self.start_date + timedelta(days=31)).isoformat()
        payload["end_date"] = (self.start_date + timedelta(days=36)).isoformat()

        with patch(
            "booking.services.generate_unique_booking_number",
            side_effect=[self.existing_booking.booking_number, "BOOKING-WORKFLOW-RETRY-001"],
        ):
            response = self.client.post(
                "/api/v1/bookings/",
                payload,
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data.get("booking_number"), "BOOKING-WORKFLOW-RETRY-001")
        self.assertTrue(
            Booking.objects.filter(booking_number="BOOKING-WORKFLOW-RETRY-001").exists()
        )

    def test_v1_create_booking_rejects_tampered_total_price(self):
        payload = self._booking_payload()
        payload["start_date"] = (self.start_date + timedelta(days=41)).isoformat()
        payload["end_date"] = (self.start_date + timedelta(days=46)).isoformat()
        payload["total_price"] = 99

        response = self.client.post(
            "/api/v1/bookings/",
            payload,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data.get("message"),
            "Submitted total_price does not match the server-calculated booking total.",
        )

    def test_v1_delete_endpoint_removes_hold_booking_with_bearer_auth(self):
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

    def test_v1_delete_endpoint_rejects_hold_booking_with_submitted_payment(self):
        self._create_payment(
            self.existing_booking,
            stage="Full",
            amount=2400,
            status_value=PAYMENT_STATUS_UNDER_REVIEW,
            suffix="hold-delete-blocked",
        )
        sync_booking_state(self.existing_booking, save=True)

        response = self.client.delete(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/",
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.existing_booking.refresh_from_db()
        self.assertEqual(self.existing_booking.booking_status, BOOKING_STATUS_HOLD)
        self.assertEqual(
            response.data.get("message"),
            "Bookings with submitted payments cannot be removed or cancelled from self-service.",
        )

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
        self.assertEqual(self.existing_booking.booking_status, BOOKING_STATUS_HOLD)

    def test_v1_payment_endpoint_returns_mutation_summary_without_heavy_relations(self):
        with patch("booking.services.user_new_booking_email"):
            response = self.client.post(
                f"/api/v1/bookings/{self.existing_booking.booking_id}/payments/",
                {
                    "transaction_number": "V1-TRANS-MUTATION-SUMMARY-001",
                    "transaction_type": "Full",
                    "transaction_amount": 2400,
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data.get("response_mode"), "mutation_summary")
        self.assertEqual(len(response.data.get("payment_detail") or []), 1)
        self.assertNotIn("booking_documents", response.data)
        self.assertNotIn("booking_airline_details", response.data)
        self.assertNotIn("booking_hotel_and_transport_details", response.data)
        self.assertNotIn("passport_validity_detail", response.data)

    def test_v1_booking_detail_locks_actions_for_full_payment_under_review(self):
        with patch("booking.services.user_new_booking_email"):
            response = self.client.post(
                f"/api/v1/bookings/{self.existing_booking.booking_id}/payments/",
                {
                    "transaction_number": "V1-TRANS-UNDER-REVIEW-001",
                    "transaction_type": "Full",
                    "transaction_amount": 2400,
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.existing_booking.refresh_from_db()
        self.assertEqual(self.existing_booking.booking_status, BOOKING_STATUS_HOLD)
        self.assertIsNone(self.existing_booking.hold_expires_at)

        detail_response = self.client.get(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data.get("minimum_payment_status"), "NOT_SUBMITTED")
        self.assertEqual(detail_response.data.get("full_payment_status"), "UNDER_REVIEW")
        self.assertEqual(detail_response.data.get("initial_payment_status"), "UNDER_REVIEW")
        self.assertEqual(detail_response.data.get("client_workflow_stage"), "initial_payment_review")
        self.assertEqual(detail_response.data.get("client_workflow_step"), 2)
        self.assertFalse(detail_response.data.get("client_can_submit_initial_payment"))
        self.assertFalse(detail_response.data.get("client_can_submit_minimum_payment"))
        self.assertFalse(detail_response.data.get("client_can_submit_full_payment"))
        self.assertFalse(detail_response.data.get("client_can_edit_travellers"))
        self.assertIsNone(detail_response.data.get("hold_expires_at"))

    def test_v1_booking_detail_reports_final_payment_review_stage(self):
        self._approve_minimum()
        self._add_complete_passports()

        with patch("booking.services.user_new_booking_email"):
            response = self.client.post(
                f"/api/v1/bookings/{self.existing_booking.booking_number}/payments/",
                {
                    "transaction_number": "V1-TRANS-FULL-REVIEW-001",
                    "transaction_type": "Full",
                    "transaction_amount": 2160,
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        detail_response = self.client.get(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data.get("client_workflow_stage"), "full_payment_review")
        self.assertEqual(detail_response.data.get("client_workflow_step"), 4)

    def test_v1_booking_detail_reports_initial_payment_status_from_full_approval(self):
        with patch("booking.services.user_new_booking_email"):
            response = self.client.post(
                f"/api/v1/bookings/{self.existing_booking.booking_id}/payments/",
                {
                    "transaction_number": "V1-TRANS-FULL-INITIAL-001",
                    "transaction_type": "Full",
                    "transaction_amount": 2400,
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        payment = Payment.objects.get(transaction_number="V1-TRANS-FULL-INITIAL-001")
        payment.payment_status = "APPROVED"
        payment.save(update_fields=["payment_status"])

        detail_response = self.client.get(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )

        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data.get("minimum_payment_status"), "NOT_SUBMITTED")
        self.assertEqual(detail_response.data.get("full_payment_status"), "APPROVED")
        self.assertEqual(detail_response.data.get("initial_payment_status"), "APPROVED")

    def test_v1_payment_endpoint_accepts_near_term_booking_inside_10_day_window(self):
        near_term_start_date = timezone.now() + timedelta(days=2)
        near_term_end_date = near_term_start_date + timedelta(days=5)
        near_term_booking = Booking.objects.create(
            booking_number="BOOKING-WORKFLOW-NEAR-TERM-001",
            adults=1,
            child=0,
            infants=0,
            sharing="0",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=near_term_start_date,
            end_date=near_term_end_date,
            total_price=1200,
            special_request="Near-term booking",
            booking_status=BOOKING_STATUS_HOLD,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
        )

        with patch("booking.services.user_new_booking_email"):
            response = self.client.post(
                f"/api/v1/bookings/{near_term_booking.booking_number}/payments/",
                {
                    "transaction_number": "V1-TRANS-NEAR-TERM-001",
                    "transaction_type": "Minimum",
                    "transaction_amount": 120,
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Payment.objects.filter(
                booking_token=near_term_booking,
                transaction_number="V1-TRANS-NEAR-TERM-001",
            ).exists()
        )

    def test_v1_payment_endpoint_rejects_underpaid_full_amount(self):
        with patch("booking.services.user_new_booking_email"):
            response = self.client.post(
                f"/api/v1/bookings/{self.existing_booking.booking_id}/payments/",
                {
                    "transaction_number": "V1-TRANS-UNDERPAID-FULL-001",
                    "transaction_type": "Full",
                    "transaction_amount": 1200,
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
            )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data.get("message"),
            "Full payment amount must be 2400 for this booking.",
        )
        self.assertFalse(
            Payment.objects.filter(
                booking_token=self.existing_booking,
                transaction_number="V1-TRANS-UNDERPAID-FULL-001",
            ).exists()
        )

    def test_v1_payment_endpoint_rejects_resubmission_while_stage_is_under_review(self):
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
        self.assertEqual(second_response.status_code, status.HTTP_409_CONFLICT)
        full_payments = Payment.objects.filter(
            booking_token=self.existing_booking,
            transaction_type__iexact="Full",
        )
        self.assertEqual(full_payments.count(), 1)
        payment = full_payments.first()
        self.assertEqual(payment.transaction_number, "V1-TRANS-001")
        self.assertEqual(payment.transaction_amount, 2400)

    def test_v1_payment_endpoint_rejects_duplicate_approved_stage(self):
        Payment.objects.create(
            transaction_number="APPROVED-FULL-001",
            transaction_type="Full",
            transaction_amount=2400,
            payment_status=PAYMENT_STATUS_APPROVED,
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
            payment_status=PAYMENT_STATUS_UNDER_REVIEW,
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
            booking_status=BOOKING_STATUS_HOLD,
            payment_type="Bank",
            order_by=other_customer,
            order_to=self.partner,
            package_token=self.package,
        )
        Payment.objects.create(
            transaction_number="GLOBAL-TRANS-001",
            transaction_type="Full",
            transaction_amount=1200,
            payment_status=PAYMENT_STATUS_UNDER_REVIEW,
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
        self._mark_ready_for_operator()

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
        self._mark_in_fulfillment()

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
        self._mark_ready_for_travel()

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
        self._mark_ready_for_operator()
        self.existing_booking.issue_status = ISSUE_STATUS_OPERATOR_OBJECTION
        self.existing_booking.save(update_fields=["issue_status"])
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
        self.assertEqual(self.existing_booking.booking_status, BOOKING_STATUS_READY_FOR_OPERATOR)
        self.assertTrue(bool(objection.required_document_for_objection))


class ApproveBookingPaymentViewTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
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
        self.existing_booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-BASE-001",
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
            special_request="Approve payment baseline",
            booking_status=BOOKING_STATUS_HOLD,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
        )

    def _create_payment(self, booking, *, stage, amount, status_value, suffix):
        return Payment.objects.create(
            transaction_number=f"{booking.booking_number}-{stage}-{suffix}",
            transaction_type=stage,
            transaction_amount=amount,
            payment_status=status_value,
            booking_token=booking,
        )

    def _authenticated_request(self, request):
        force_authenticate(request, user=self.admin_user)
        return request

    def _approve_full(self, booking=None):
        booking = booking or self.existing_booking
        payment = self._create_payment(
            booking,
            stage="Full",
            amount=float(booking.total_price),
            status_value=PAYMENT_STATUS_APPROVED,
            suffix="approved-full",
        )
        sync_booking_state(booking, save=True)
        return payment

    def _add_complete_passports(self, booking=None):
        booking = booking or self.existing_booking
        traveller_count = booking.adults + booking.child + booking.infants
        booking_token = str(booking.pk).replace("-", "")[:8]
        for index in range(traveller_count):
            PassportValidity.objects.create(
                first_name=f"Approve{index + 1}",
                last_name="Traveler",
                date_of_birth=aware_midnight("1990-01-10"),
                passport_number=f"AP{booking_token}{index + 1:02d}",
                passport_country="PK",
                expiry_date=aware_midnight("2031-06-01"),
                user_passport=SimpleUploadedFile(
                    f"approve-passport-{index + 1}.jpg",
                    b"passport-image",
                    content_type="image/jpeg",
                ),
                user_photo=SimpleUploadedFile(
                    f"approve-photo-{index + 1}.jpg",
                    b"photo-image",
                    content_type="image/jpeg",
                ),
                passport_for_booking_number=booking,
            )
        sync_booking_state(booking, save=True)

    def _mark_ready_for_operator(self, booking=None):
        booking = booking or self.existing_booking
        self._create_payment(
            booking,
            stage="Minimum",
            amount=max(float(booking.total_price) * 0.1, 1),
            status_value=PAYMENT_STATUS_APPROVED,
            suffix="approve-ready-minimum",
        )
        self._add_complete_passports(booking)
        self._create_payment(
            booking,
            stage="Full",
            amount=float(booking.total_price),
            status_value=PAYMENT_STATUS_APPROVED,
            suffix="approve-ready-full",
        )
        sync_booking_state(booking, save=True)
        booking.refresh_from_db()
        return booking

    def _mark_in_fulfillment(self, booking=None):
        booking = self._mark_ready_for_operator(booking)
        booking.booking_status = BOOKING_STATUS_IN_FULFILLMENT
        booking.save(update_fields=["booking_status"])
        sync_booking_state(booking, save=True)
        booking.refresh_from_db()
        return booking

    def _mark_ready_for_travel(self, booking=None):
        booking = self._mark_in_fulfillment(booking)
        BookingAirlineDetail.objects.update_or_create(
            airline_for_booking=booking,
            flight_direction="outbound",
            defaults={
                "flight_date": booking.start_date,
                "flight_time": booking.start_date.time(),
                "flight_from": "Karachi",
                "flight_to": "Jeddah",
            },
        )
        BookingDocuments.objects.get_or_create(
            document_for="eVisa",
            document_category="evisa",
            document_scope=BookingDocuments.DOCUMENT_SCOPE_BOOKING,
            document_title="Visa document",
            document_for_booking_token=booking,
        )
        BookingDocuments.objects.get_or_create(
            document_for="airline",
            document_category="airline",
            document_scope=BookingDocuments.DOCUMENT_SCOPE_BOOKING,
            document_title="Airline ticket",
            document_for_booking_token=booking,
        )
        DocumentsStatus.objects.update_or_create(
            status_for_booking=booking,
            defaults={
                "is_visa_completed": True,
                "is_airline_completed": True,
                "is_airline_detail_completed": True,
                "is_hotel_completed": True,
                "is_transport_completed": True,
            },
        )
        sync_booking_state(booking, save=True)
        booking.refresh_from_db()
        return booking

    def test_admin_can_reject_initial_payment_and_keep_booking_on_hold(self):
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
            booking_status=BOOKING_STATUS_HOLD,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
        )
        payment = Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-REF-001",
            transaction_type="Full",
            transaction_amount=2400,
            payment_status=PAYMENT_STATUS_UNDER_REVIEW,
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
        self.assertEqual(booking.booking_status, BOOKING_STATUS_HOLD)
        self.assertFalse(booking.is_payment_received)
        self.assertEqual(payment.payment_status, PAYMENT_STATUS_REJECTED)
        self.assertEqual(payment.review_message, "The receipt image is unreadable.")
        self.assertIsNotNone(booking.payment_correction_expires_at)
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
            booking_status=BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
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
            payment_status=PAYMENT_STATUS_APPROVED,
            booking_token=booking,
        )
        self._add_complete_passports(booking)
        full_payment = Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-FULL-002",
            transaction_type="Full",
            transaction_amount=2160,
            payment_status=PAYMENT_STATUS_UNDER_REVIEW,
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
        self.assertEqual(booking.booking_status, BOOKING_STATUS_AWAITING_FINAL_PAYMENT)
        self.assertTrue(booking.is_payment_received)
        self.assertEqual(full_payment.payment_status, PAYMENT_STATUS_REJECTED)
        self.assertEqual(
            full_payment.review_message,
            "The transfer reference does not match the amount.",
        )
        self.assertIsNotNone(booking.payment_correction_expires_at)

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
            booking_status=BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
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
            payment_status=PAYMENT_STATUS_APPROVED,
            booking_token=booking,
        )
        self._add_complete_passports(booking)
        full_payment = Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-FULL-003",
            transaction_type="Full",
            transaction_amount=1080,
            payment_status=PAYMENT_STATUS_UNDER_REVIEW,
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
        self.assertEqual(booking.booking_status, BOOKING_STATUS_READY_FOR_OPERATOR)
        self.assertTrue(booking.is_payment_received)
        self.assertEqual(full_payment.payment_status, PAYMENT_STATUS_APPROVED)
        self.assertIsNone(full_payment.review_message)
        mocked_verified_email.assert_called_once()
        mocked_notify_user.assert_called_once()
        mocked_preparation_email.assert_not_called()

    def test_admin_cannot_approve_underpaid_full_payment(self):
        booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-UNDERPAID-003",
            adults=2,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=timezone.now() + timedelta(days=52),
            end_date=timezone.now() + timedelta(days=58),
            total_price=2400,
            special_request="None",
            booking_status=BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
            is_payment_received=True,
        )
        Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-MIN-UNDERPAID-003",
            transaction_type="Minimum",
            transaction_amount=240,
            payment_status=PAYMENT_STATUS_APPROVED,
            booking_token=booking,
        )
        self._add_complete_passports(booking)
        full_payment = Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-FULL-UNDERPAID-003",
            transaction_type="Full",
            transaction_amount=1000,
            payment_status=PAYMENT_STATUS_UNDER_REVIEW,
            booking_token=booking,
        )
        booking.refresh_from_db()
        original_booking_status = booking.booking_status

        self.client.force_authenticate(user=self.admin_user)
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

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data.get("message"),
            "Full payment amount must be 2160 for this booking.",
        )
        full_payment.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(full_payment.payment_status, PAYMENT_STATUS_UNDER_REVIEW)
        self.assertEqual(booking.booking_status, original_booking_status)

    def test_full_payment_approval_without_minimum_unlocks_the_correct_lifecycle_states(self):
        self._approve_full()
        self.existing_booking.refresh_from_db()
        self.assertEqual(
            self.existing_booking.booking_status,
            BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
        )

        detail_response = self.client.get(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertTrue(detail_response.data.get("operator_visible"))
        self.assertFalse(detail_response.data.get("client_can_submit_initial_payment"))
        self.assertFalse(detail_response.data.get("client_can_submit_minimum_payment"))
        self.assertFalse(detail_response.data.get("client_can_submit_full_payment"))
        self.assertTrue(detail_response.data.get("client_can_edit_travellers"))
        self.assertEqual(detail_response.data.get("client_workflow_stage"), "traveler_details")
        self.assertEqual(detail_response.data.get("client_workflow_step"), 3)

        self._add_complete_passports()
        self.existing_booking.refresh_from_db()
        self.assertEqual(self.existing_booking.booking_status, BOOKING_STATUS_READY_FOR_OPERATOR)

        detail_response = self.client.get(
            f"/api/v1/bookings/{self.existing_booking.booking_number}/",
            HTTP_AUTHORIZATION=f"Bearer {self.customer.session_token}",
        )
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertTrue(detail_response.data.get("operator_visible"))
        self.assertTrue(detail_response.data.get("operator_can_act"))
        self.assertFalse(detail_response.data.get("client_can_edit_travellers"))
        self.assertFalse(detail_response.data.get("client_can_submit_full_payment"))
        self.assertEqual(detail_response.data.get("client_workflow_stage"), "booking_status")
        self.assertEqual(detail_response.data.get("client_workflow_step"), 5)

    def test_ready_for_travel_auto_completes_after_end_date_without_document_recheck(self):
        self._mark_ready_for_travel(self.existing_booking)
        self.existing_booking.booking_status = BOOKING_STATUS_READY_FOR_TRAVEL
        self.existing_booking.start_date = timezone.now() - timedelta(days=7)
        self.existing_booking.end_date = timezone.now() - timedelta(days=1)
        self.existing_booking.save(update_fields=["booking_status", "start_date", "end_date"])

        sync_booking_state(self.existing_booking, save=True)
        self.existing_booking.refresh_from_db()

        self.assertEqual(self.existing_booking.booking_status, BOOKING_STATUS_COMPLETED)

    def test_ready_for_travel_auto_complete_keeps_booking_open_when_traveler_issue_exists(self):
        self._mark_ready_for_travel(self.existing_booking)
        passport = PassportValidity.objects.filter(
            passport_for_booking_number=self.existing_booking
        ).first()
        self.assertIsNotNone(passport)
        TravelerIssue.objects.create(
            booking=self.existing_booking,
            traveler=passport,
            status=TravelerIssue.STATUS_OPEN,
            created_by=self.partner,
        )
        self.existing_booking.start_date = timezone.now() - timedelta(days=7)
        self.existing_booking.end_date = timezone.now() - timedelta(days=1)
        self.existing_booking.save(update_fields=["start_date", "end_date"])

        sync_booking_state(self.existing_booking, save=True)
        self.existing_booking.refresh_from_db()

        self.assertEqual(self.existing_booking.booking_status, BOOKING_STATUS_READY_FOR_TRAVEL)
        self.assertEqual(self.existing_booking.issue_status, ISSUE_STATUS_REPORTED)

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
            booking_status=BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
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
            payment_status=PAYMENT_STATUS_APPROVED,
            booking_token=booking,
        )
        self._add_complete_passports(booking)
        Payment.objects.create(
            transaction_number="APPROVE-PAYMENT-FULL-005",
            transaction_type="Full",
            transaction_amount=1080,
            payment_status=PAYMENT_STATUS_UNDER_REVIEW,
            booking_token=booking,
        )

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get("/management/fetch_all_paid_bookings/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking_numbers = [item["booking_number"] for item in response.data.get("results", [])]
        self.assertIn(booking.booking_number, booking_numbers)

    def test_admin_review_queue_supports_server_side_queue_and_date_filters(self):
        target_order_time = timezone.now() - timedelta(days=2)

        minimum_booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-QUEUE-MIN",
            adults=1,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=timezone.now() + timedelta(days=25),
            end_date=timezone.now() + timedelta(days=31),
            total_price=900,
            special_request="Minimum queue",
            booking_status=BOOKING_STATUS_HOLD,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
        )
        self._create_payment(
            minimum_booking,
            stage="Minimum",
            amount=90,
            status_value=PAYMENT_STATUS_UNDER_REVIEW,
            suffix="queue-minimum",
        )

        full_booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-QUEUE-FULL",
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
            total_price=1500,
            special_request="Full queue",
            booking_status=BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
            is_payment_received=True,
        )
        full_booking.order_time = target_order_time
        full_booking.save(update_fields=["order_time"])
        self._create_payment(
            full_booking,
            stage="Minimum",
            amount=150,
            status_value=PAYMENT_STATUS_APPROVED,
            suffix="queue-full-minimum",
        )
        self._create_payment(
            full_booking,
            stage="Full",
            amount=1350,
            status_value=PAYMENT_STATUS_UNDER_REVIEW,
            suffix="queue-full-review",
        )

        approved_booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-QUEUE-APPROVED",
            adults=1,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=timezone.now() + timedelta(days=55),
            end_date=timezone.now() + timedelta(days=61),
            total_price=1100,
            special_request="Approved queue",
            booking_status=BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
            is_payment_received=True,
        )
        self._create_payment(
            approved_booking,
            stage="Full",
            amount=1100,
            status_value=PAYMENT_STATUS_APPROVED,
            suffix="queue-approved",
        )

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(
            "/management/fetch_all_paid_bookings/",
            {
                "payment_queue": "full_under_review",
                "order_date": target_order_time.date().isoformat(),
                "page": 1,
                "page_size": 1,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 1)
        self.assertEqual(len(response.data.get("results") or []), 1)
        self.assertEqual(
            response.data.get("results")[0].get("booking_number"),
            full_booking.booking_number,
        )
        self.assertEqual(
            response.data.get("meta", {}).get("queue_counts", {}).get("full_under_review"),
            1,
        )
        self.assertEqual(response.data.get("meta", {}).get("total_requests"), 1)
        self.assertEqual(
            float(response.data.get("meta", {}).get("total_amount")),
            float(full_booking.total_price),
        )

    def test_admin_review_queue_supports_exact_booking_lookup_for_settlement_detail(self):
        booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-QUEUE-LOOKUP",
            adults=1,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=timezone.now() + timedelta(days=65),
            end_date=timezone.now() + timedelta(days=71),
            total_price=1300,
            special_request="Lookup queue",
            booking_status=BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
            is_payment_received=True,
        )
        self._create_payment(
            booking,
            stage="Full",
            amount=1300,
            status_value=PAYMENT_STATUS_APPROVED,
            suffix="queue-lookup-approved",
        )

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(
            "/management/fetch_all_paid_bookings/",
            {
                "booking_number": booking.booking_number,
                "page": 1,
                "page_size": 1,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 1)
        self.assertEqual(
            response.data.get("results")[0].get("booking_number"),
            booking.booking_number,
        )

    def test_admin_review_queue_uses_bounded_summary_query_count(self):
        minimum_booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-QUERY-MIN",
            adults=1,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=timezone.now() + timedelta(days=25),
            end_date=timezone.now() + timedelta(days=31),
            total_price=900,
            special_request="Minimum queue",
            booking_status=BOOKING_STATUS_HOLD,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
        )
        self._create_payment(
            minimum_booking,
            stage="Minimum",
            amount=90,
            status_value=PAYMENT_STATUS_UNDER_REVIEW,
            suffix="query-minimum",
        )

        full_booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-QUERY-FULL",
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
            total_price=1500,
            special_request="Full queue",
            booking_status=BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
            is_payment_received=True,
        )
        self._create_payment(
            full_booking,
            stage="Minimum",
            amount=150,
            status_value=PAYMENT_STATUS_APPROVED,
            suffix="query-full-minimum",
        )
        self._create_payment(
            full_booking,
            stage="Full",
            amount=1350,
            status_value=PAYMENT_STATUS_UNDER_REVIEW,
            suffix="query-full-review",
        )

        request = self._authenticated_request(
            self.factory.get(
                "/management/fetch_all_paid_bookings/",
                {
                    "payment_queue": "full_under_review",
                    "page": 1,
                    "page_size": 1,
                },
            )
        )

        with CaptureQueriesContext(connection) as queries:
            response = FetchPaidBookingView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(queries), 9)
        self.assertEqual(response.data.get("meta", {}).get("total_requests"), 2)
        self.assertEqual(
            response.data.get("meta", {}).get("queue_counts", {}).get("full_under_review"),
            1,
        )
        self.assertEqual(
            response.data.get("results", [{}])[0].get("booking_number"),
            full_booking.booking_number,
        )

    def test_admin_review_queue_reuses_cached_payload_for_identical_filters(self):
        booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-CACHE-001",
            adults=1,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=timezone.now() + timedelta(days=25),
            end_date=timezone.now() + timedelta(days=31),
            total_price=900,
            special_request="Cache queue",
            booking_status=BOOKING_STATUS_HOLD,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
        )
        self._create_payment(
            booking,
            stage="Minimum",
            amount=90,
            status_value=PAYMENT_STATUS_UNDER_REVIEW,
            suffix="cache-minimum",
        )

        params = {
            "payment_queue": "minimum_under_review",
            "page": 1,
            "page_size": 10,
        }
        self.client.force_authenticate(user=self.admin_user)
        first_response = self.client.get("/management/fetch_all_paid_bookings/", params)
        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(first_response.data.get("count"), 1)

        Payment.objects.filter(pk=booking.booking_token.first().pk).update(
            payment_status=PAYMENT_STATUS_APPROVED
        )

        second_response = self.client.get("/management/fetch_all_paid_bookings/", params)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.data.get("count"), 1)
        self.assertEqual(
            second_response.data.get("results", [{}])[0].get("booking_number"),
            booking.booking_number,
        )

    def test_admin_review_queue_cache_invalidates_after_review_decision(self):
        booking = Booking.objects.create(
            booking_number="APPROVE-PAYMENT-CACHE-INVALIDATE-001",
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
            special_request="Invalidate queue",
            booking_status=BOOKING_STATUS_HOLD,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
        )
        payment = self._create_payment(
            booking,
            stage="Minimum",
            amount=120,
            status_value=PAYMENT_STATUS_UNDER_REVIEW,
            suffix="cache-invalidate-minimum",
        )

        self.client.force_authenticate(user=self.admin_user)
        initial_response = self.client.get(
            "/management/fetch_all_paid_bookings/",
            {
                "payment_queue": "minimum_under_review",
                "page": 1,
                "page_size": 10,
            },
        )
        self.assertEqual(initial_response.status_code, status.HTTP_200_OK)
        self.assertEqual(initial_response.data.get("count"), 1)

        with patch("management.approval_task.send_payment_verification_email"), patch(
            "management.approval_task._notify_user_about_payment_update"
        ), patch("management.approval_task.preparation_email"):
            update_response = self.client.put(
                "/management/approve_booking_payment/",
                {
                    "session_token": self.customer.session_token,
                    "booking_number": booking.booking_number,
                    "payment_id": str(payment.payment_id),
                    "decision": "approve",
                },
                format="json",
            )

        self.assertEqual(update_response.status_code, status.HTTP_200_OK)

        refreshed_response = self.client.get(
            "/management/fetch_all_paid_bookings/",
            {
                "payment_queue": "minimum_under_review",
                "page": 1,
                "page_size": 10,
            },
        )
        self.assertEqual(refreshed_response.status_code, status.HTTP_200_OK)
        self.assertEqual(refreshed_response.data.get("count"), 0)
        self.assertEqual(refreshed_response.data.get("results"), [])


class ManagePartnerBookingViewsTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        cache.clear()
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

    def _partner_auth_headers(self, partner):
        return {"HTTP_AUTHORIZATION": f"Bearer {partner.partner_session_token}"}

    def _request_package_reviews(self, **query_params):
        request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/ratings/package/",
                query_params,
            )
        )
        return GetRatingPackageWiseView.as_view()(request)

    def _create_stage_payment(self, booking, *, stage, amount, status_value, suffix):
        return Payment.objects.create(
            transaction_number=f"{booking.booking_number}-{stage}-{suffix}",
            transaction_type=stage,
            transaction_amount=amount,
            payment_status=status_value,
            booking_token=booking,
        )

    def _mark_ready_for_operator(self, booking):
        self._create_stage_payment(
            booking,
            stage="Minimum",
            amount=max(float(booking.total_price) * 0.1, 1),
            status_value=PAYMENT_STATUS_APPROVED,
            suffix="partner-ready-minimum",
        )
        traveller_count = booking.adults + booking.child + booking.infants
        booking_token = str(booking.pk).replace("-", "")[:8]
        for index in range(traveller_count):
            PassportValidity.objects.create(
                first_name=f"Partner{index + 1}",
                last_name="Traveler",
                date_of_birth=aware_midnight("1990-01-10"),
                passport_number=f"PT{booking_token}{index + 1:02d}",
                passport_country="PK",
                expiry_date=aware_midnight("2031-06-01"),
                user_passport=SimpleUploadedFile(
                    f"partner-passport-{index + 1}.jpg",
                    b"passport-image",
                    content_type="image/jpeg",
                ),
                user_photo=SimpleUploadedFile(
                    f"partner-photo-{index + 1}.jpg",
                    b"photo-image",
                    content_type="image/jpeg",
                ),
                passport_for_booking_number=booking,
            )
        self._create_stage_payment(
            booking,
            stage="Full",
            amount=float(booking.total_price),
            status_value=PAYMENT_STATUS_APPROVED,
            suffix="partner-ready-full",
        )
        sync_booking_state(booking, save=True)
        booking.refresh_from_db()
        return booking

    def _mark_in_fulfillment(self, booking):
        booking = self._mark_ready_for_operator(booking)
        booking.booking_status = BOOKING_STATUS_IN_FULFILLMENT
        booking.save(update_fields=["booking_status"])
        sync_booking_state(booking, save=True)
        booking.refresh_from_db()
        return booking

    def _mark_ready_for_travel(self, booking):
        booking = self._mark_in_fulfillment(booking)
        BookingAirlineDetail.objects.update_or_create(
            airline_for_booking=booking,
            flight_direction="outbound",
            defaults={
                "flight_date": booking.start_date,
                "flight_time": booking.start_date.time(),
                "flight_from": "Karachi",
                "flight_to": "Jeddah",
            },
        )
        BookingDocuments.objects.get_or_create(
            document_for="eVisa",
            document_category="evisa",
            document_scope=BookingDocuments.DOCUMENT_SCOPE_BOOKING,
            document_title="Visa document",
            document_for_booking_token=booking,
        )
        BookingDocuments.objects.get_or_create(
            document_for="airline",
            document_category="airline",
            document_scope=BookingDocuments.DOCUMENT_SCOPE_BOOKING,
            document_title="Airline ticket",
            document_for_booking_token=booking,
        )
        DocumentsStatus.objects.update_or_create(
            status_for_booking=booking,
            defaults={
                "is_visa_completed": True,
                "is_airline_completed": True,
                "is_airline_detail_completed": True,
                "is_hotel_completed": True,
                "is_transport_completed": True,
            },
        )
        sync_booking_state(booking, save=True)
        booking.refresh_from_db()
        return booking

    def _report_open_traveler_issue(self, booking):
        traveler = PassportValidity.objects.filter(
            passport_for_booking_number=booking,
        ).order_by("pk").first()
        if traveler is None:
            raise AssertionError("Booking must have at least one traveler before reporting an issue.")

        TravelerIssue.objects.create(
            booking=booking,
            traveler=traveler,
            status=TravelerIssue.STATUS_OPEN,
            created_by=booking.order_to,
        )
        sync_booking_state(booking, save=True)
        booking.refresh_from_db()
        return booking

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
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-ACTIVE-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(booking)

        request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "workflow_bucket": "READY",
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
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-ACTIVE-002",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(booking)

        request = self.factory.get(
            "/api/v1/operator/bookings/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "workflow_bucket": "FULFILLMENT",
                "page": 1,
                "page_size": 10,
            },
        )

        response = GetBookingShortDetailForPartnersView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_booking_list_filters_by_booking_number_prefix(self):
        matching_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-FILTER-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(matching_booking)
        other_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-FILTER-999",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(other_booking)

        request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "workflow_bucket": "FULFILLMENT",
                    "booking_number": "BK-FILTER-0",
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

    def test_booking_list_issues_bucket_includes_reported_and_operator_objection_bookings(self):
        reported_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-ISSUES-REPORTED-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(reported_booking)
        self._report_open_traveler_issue(reported_booking)

        objection_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-ISSUES-OBJECTION-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(objection_booking)
        objection_booking.issue_status = ISSUE_STATUS_OPERATOR_OBJECTION
        objection_booking.save(update_fields=["issue_status"])
        sync_booking_state(objection_booking, save=True)
        objection_booking.refresh_from_db()

        request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "workflow_bucket": WORKFLOW_BUCKET_ISSUES,
                    "page": 1,
                    "page_size": 10,
                },
            )
        )

        response = GetBookingShortDetailForPartnersView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 2)
        self.assertEqual(
            {
                result.get("booking_number")
                for result in response.data.get("results", [])
            },
            {
                reported_booking.booking_number,
                objection_booking.booking_number,
            },
        )

    def test_booking_list_reported_bucket_alias_returns_only_reported_issue_bookings(self):
        reported_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-REPORTED-ALIAS-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(reported_booking)
        self._report_open_traveler_issue(reported_booking)

        objection_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-REPORTED-ALIAS-002",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(objection_booking)
        objection_booking.issue_status = ISSUE_STATUS_OPERATOR_OBJECTION
        objection_booking.save(update_fields=["issue_status"])
        sync_booking_state(objection_booking, save=True)
        objection_booking.refresh_from_db()

        request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "workflow_bucket": WORKFLOW_BUCKET_REPORTED,
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
            reported_booking.booking_number,
        )

    def test_booking_list_raw_status_filter_hides_non_visible_hold_bookings_for_partner(self):
        self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-HIDDEN-HOLD-001",
            booking_status=BOOKING_STATUS_HOLD,
        )

        request = self.factory.get(
            "/api/v1/operator/bookings/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "booking_status": BOOKING_STATUS_HOLD,
                "page": 1,
                "page_size": 10,
            },
        )

        response = GetBookingShortDetailForPartnersView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_booking_list_raw_status_filter_allows_staff_override_for_hidden_bookings(self):
        hidden_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-HIDDEN-HOLD-STAFF-001",
            booking_status=BOOKING_STATUS_HOLD,
        )

        request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_status": BOOKING_STATUS_HOLD,
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
            hidden_booking.booking_number,
        )

    def test_partner_detail_hides_non_visible_booking_for_partner(self):
        hidden_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-DETAIL-HIDDEN-001",
            booking_status=BOOKING_STATUS_HOLD,
        )

        request = self.factory.get(
            "/api/v1/operator/bookings/detail/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "booking_number": hidden_booking.booking_number,
            },
        )

        response = GetBookingDetailByBookingNumberForPartnerView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_canonical_operator_booking_list_accepts_bearer_auth_without_partner_session_token(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-CANONICAL-LIST-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(booking)

        response = self.client.get(
            "/api/v1/operator/bookings/",
            {
                "workflow_bucket": "FULFILLMENT",
                "page": 1,
                "page_size": 10,
            },
            HTTP_AUTHORIZATION=f"Bearer {self.partner_a.partner_session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 1)
        self.assertEqual(
            response.data.get("results")[0].get("booking_number"),
            booking.booking_number,
        )

    def test_staff_can_fetch_non_visible_partner_booking_detail(self):
        hidden_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-DETAIL-HIDDEN-STAFF-001",
            booking_status=BOOKING_STATUS_HOLD,
        )

        request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/detail/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": hidden_booking.booking_number,
                },
            )
        )

        response = GetBookingDetailByBookingNumberForPartnerView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("booking_number"), hidden_booking.booking_number)
        self.assertFalse(response.data.get("operator_visible"))

    def test_staff_can_fetch_non_visible_partner_booking_detail_without_partner_token(self):
        hidden_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-DETAIL-HIDDEN-STAFF-002",
            booking_status=BOOKING_STATUS_HOLD,
        )

        request = self._authenticated_request(
            self.factory.get(
                "/api/v1/admin/operators/bookings/detail/",
                {
                    "booking_number": hidden_booking.booking_number,
                },
            )
        )

        response = GetBookingDetailByBookingNumberForPartnerView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("booking_number"), hidden_booking.booking_number)
        self.assertFalse(response.data.get("operator_visible"))

    def test_partner_detail_includes_package_transport_and_hotel_defaults(self):
        self.package_a.jeddah_nights = 1
        self.package_a.mecca_nights = 6
        self.package_a.madinah_nights = 3
        self.package_a.taif_nights = 2
        self.package_a.riyadah_nights = 1
        self.package_a.save(
            update_fields=[
                "jeddah_nights",
                "mecca_nights",
                "madinah_nights",
                "taif_nights",
                "riyadah_nights",
            ]
        )
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-DETAIL-PACKAGE-DEFAULTS-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(booking)
        HuzTransportDetail.objects.create(
            transport_name="Coaster",
            transport_type="Shared",
            routes="JED_MKK,MKK_MDN,MDN_JED",
            transport_for_package=self.package_a,
        )
        HuzHotelDetail.objects.create(
            hotel_city="Makkah",
            hotel_name="Premium Makkah Hotel",
            hotel_rating="5 Star",
            room_sharing_type="Quad",
            hotel_distance="900",
            distance_type="Meters",
            hotel_for_package=self.package_a,
        )

        request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/detail/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": booking.booking_number,
                },
            )
        )

        response = GetBookingDetailByBookingNumberForPartnerView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data.get("package_defaults", {}).get("transport", {}).get("transport_name"),
            "Coaster",
        )
        self.assertEqual(
            response.data.get("package_defaults", {}).get("transport", {}).get("routes"),
            "JED_MKK,MKK_MDN,MDN_JED",
        )
        self.assertEqual(
            response.data.get("package_defaults", {}).get("hotels", [])[0].get("hotel_name"),
            "Premium Makkah Hotel",
        )
        self.assertEqual(
            response.data.get("package_defaults", {}).get("hotels", [])[0].get("hotel_city"),
            "Makkah",
        )
        self.assertEqual(response.data.get("jeddah_nights"), "1")
        self.assertEqual(response.data.get("taif_nights"), "2")
        self.assertEqual(response.data.get("riyadah_nights"), "1")

    def test_transport_post_persists_typed_transport_fulfillment(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-DETAIL-ARRANGEMENT-CITIES-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(booking)

        request = self._authenticated_request(
            self.factory.post(
                "/api/v1/operator/bookings/arrangements/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": booking.booking_number,
                    "detail_for": "Transport",
                    "transport_mode": "details_only",
                    "transport_name": "Coaster",
                    "transport_type": "Shared",
                    "route_summary": "Jeddah -> Makkah -> Madinah",
                    "contact_name": "Transport desk",
                    "note": "Primary arrangement note",
                },
                format="json",
            )
        )

        response = BookingHotelAndTransportDetailsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            response.data.get("booking_fulfillment", {}).get("transport", {}).get("transport_name"),
            "Coaster",
        )
        self.assertEqual(
            response.data.get("booking_fulfillment", {}).get("transport", {}).get("contact_name"),
            "Transport desk",
        )

        detail = BookingTransportFulfillment.objects.get(
            transport_for_booking=booking,
        )
        self.assertEqual(detail.transport_mode, "details_only")
        self.assertEqual(detail.route_summary, "Jeddah -> Makkah -> Madinah")
        self.assertEqual(detail.contact_name, "Transport desk")

    def test_package_transport_requires_ticket_or_shared_details(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-TRANSPORT-REQUIRED-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        HuzTransportDetail.objects.create(
            transport_name="Train",
            transport_type="Rail",
            routes="JED_MKK",
            transport_for_package=self.package_a,
        )

        self.assertFalse(booking_transport_fulfillment_is_complete(booking))

    def test_transport_documents_complete_transport_without_contact_details(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-TRANSPORT-DOC-ONLY-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        HuzTransportDetail.objects.create(
            transport_name="Train",
            transport_type="Rail",
            routes="JED_MKK",
            transport_for_package=self.package_a,
        )
        BookingDocuments.objects.create(
            document_for="transport",
            document_category="transport",
            document_link=SimpleUploadedFile(
                "transport-ticket.pdf",
                b"%PDF-1.4 ticket",
                content_type="application/pdf",
            ),
            document_for_booking_token=booking,
        )

        self.assertTrue(booking_transport_fulfillment_is_complete(booking))

    def test_hotel_completion_ignores_package_default_without_shared_details(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-HOTEL-SHARE-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        package_hotel = HuzHotelDetail.objects.create(
            hotel_city="Makkah",
            hotel_name="Premium Makkah Hotel",
            hotel_rating="5 Star",
            room_sharing_type="Quad",
            hotel_distance="900",
            distance_type="Meters",
            hotel_for_package=self.package_a,
        )
        BookingHotelFulfillment.objects.create(
            city="makkah",
            package_hotel=package_hotel,
            hotel_for_booking=booking,
        )

        self.assertFalse(booking_hotel_fulfillments_are_complete(booking))

    def test_hotel_documents_complete_hotel_step_without_shared_details(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-HOTEL-DOC-ONLY-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        HuzHotelDetail.objects.create(
            hotel_city="Makkah",
            hotel_name="Premium Makkah Hotel",
            hotel_rating="5 Star",
            room_sharing_type="Quad",
            hotel_distance="900",
            distance_type="Meters",
            hotel_for_package=self.package_a,
        )
        BookingDocuments.objects.create(
            document_for="hotel",
            document_category="hotel",
            document_link=SimpleUploadedFile(
                "hotel-voucher.pdf",
                b"%PDF-1.4 hotel",
                content_type="application/pdf",
            ),
            document_for_booking_token=booking,
        )

        self.assertTrue(booking_hotel_fulfillments_are_complete(booking))

    @patch("booking.manage_partner_booking.send_objection_email")
    def test_covered_booking_mutations_reject_unauthenticated_legacy_partner_tokens(
        self,
        mocked_send_objection,
    ):
        action_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-ACTION-LEGACY-401-001",
            booking_status=BOOKING_STATUS_READY_FOR_OPERATOR,
        )
        self._mark_ready_for_operator(action_booking)

        mutable_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-MUTATION-LEGACY-401-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(mutable_booking)

        action_response = self.client.put(
            "/api/v1/operator/bookings/action/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "booking_number": action_booking.booking_number,
                "partner_remarks": "Legacy token only.",
                "booking_status": "IN_FULFILLMENT",
            },
            format="json",
        )
        self.assertEqual(action_response.status_code, status.HTTP_401_UNAUTHORIZED)

        upload_response = self.client.post(
            "/api/v1/operator/bookings/documents/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "booking_number": mutable_booking.booking_number,
                "document_for": "eVisa",
                "document_link": SimpleUploadedFile(
                    "legacy-visa.pdf",
                    b"%PDF-1.4 legacy visa",
                    content_type="application/pdf",
                ),
            },
            format="multipart",
        )
        self.assertEqual(upload_response.status_code, status.HTTP_401_UNAUTHORIZED)

        delete_response = self.client.delete(
            "/api/v1/operator/bookings/document-delete/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "booking_number": mutable_booking.booking_number,
                "document_id": str(uuid4()),
            },
            format="json",
        )
        self.assertEqual(delete_response.status_code, status.HTTP_401_UNAUTHORIZED)

        airline_post_response = self.client.post(
            "/api/v1/operator/bookings/airline-details/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "booking_number": mutable_booking.booking_number,
                "flight_direction": "outbound",
                "flight_date": timezone.now().isoformat(),
                "flight_time": "10:00:00",
                "flight_from": "Karachi",
                "flight_to": "Jeddah",
            },
            format="json",
        )
        self.assertEqual(
            airline_post_response.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        airline_put_response = self.client.put(
            "/api/v1/operator/bookings/airline-details/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "booking_airline_id": str(uuid4()),
                "booking_number": mutable_booking.booking_number,
                "flight_direction": "outbound",
                "flight_date": timezone.now().isoformat(),
                "flight_time": "10:00:00",
                "flight_from": "Karachi",
                "flight_to": "Jeddah",
            },
            format="json",
        )
        self.assertEqual(
            airline_put_response.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        arrangement_post_response = self.client.post(
            "/api/v1/operator/bookings/arrangements/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "booking_number": mutable_booking.booking_number,
                "detail_for": "Transport",
                "transport_mode": "details_only",
                "transport_name": "Legacy Coaster",
                "transport_type": "Shared",
                "route_summary": "Karachi -> Jeddah",
            },
            format="json",
        )
        self.assertEqual(
            arrangement_post_response.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        arrangement_put_response = self.client.put(
            "/api/v1/operator/bookings/arrangements/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "booking_number": mutable_booking.booking_number,
                "detail_for": "Transport",
                "transport_mode": "details_only",
                "transport_name": "Legacy Coaster",
                "transport_type": "Shared",
                "route_summary": "Karachi -> Jeddah",
            },
            format="json",
        )
        self.assertEqual(
            arrangement_put_response.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        mocked_send_objection.assert_not_called()

    @patch("booking.manage_partner_booking.send_objection_email")
    def test_covered_booking_mutations_scope_bearer_partner_to_authenticated_principal(
        self,
        mocked_send_objection,
    ):
        action_booking = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-ACTION-SCOPE-001",
            booking_status=BOOKING_STATUS_READY_FOR_OPERATOR,
        )
        self._mark_ready_for_operator(action_booking)

        document_booking = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-DOC-SCOPE-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(document_booking)
        existing_document = BookingDocuments.objects.create(
            document_link=SimpleUploadedFile(
                "existing-visa.pdf",
                b"%PDF-1.4 existing visa",
                content_type="application/pdf",
            ),
            document_for_booking_token=document_booking,
            document_for="eVisa",
            document_category="eVisa",
        )

        airline_post_booking = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-AIRLINE-POST-SCOPE-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(airline_post_booking)

        airline_put_booking = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-AIRLINE-PUT-SCOPE-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(airline_put_booking)
        existing_airline = BookingAirlineDetail.objects.create(
            flight_date=timezone.now(),
            flight_time="10:00:00",
            flight_from="Lahore",
            flight_to="Jeddah",
            airline_for_booking=airline_put_booking,
        )

        arrangement_post_booking = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-ARRANGEMENT-POST-SCOPE-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(arrangement_post_booking)

        arrangement_put_booking = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-ARRANGEMENT-PUT-SCOPE-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(arrangement_put_booking)
        existing_transport = BookingTransportFulfillment.objects.create(
            transport_for_booking=arrangement_put_booking,
            transport_mode="details_only",
            transport_name="Scoped Bus",
            transport_type="Shared",
            route_summary="Lahore -> Jeddah",
        )

        action_response = self.client.put(
            "/api/v1/operator/bookings/action/",
            {
                "partner_session_token": self.partner_b.partner_session_token,
                "booking_number": action_booking.booking_number,
                "partner_remarks": "Attempted cross-partner action.",
                "booking_status": "IN_FULFILLMENT",
            },
            format="json",
            **self._partner_auth_headers(self.partner_a),
        )
        self.assertEqual(action_response.status_code, status.HTTP_404_NOT_FOUND)
        action_booking.refresh_from_db()
        self.assertEqual(action_booking.booking_status, BOOKING_STATUS_READY_FOR_OPERATOR)

        upload_response = self.client.post(
            "/api/v1/operator/bookings/documents/",
            {
                "partner_session_token": self.partner_b.partner_session_token,
                "booking_number": document_booking.booking_number,
                "document_for": "eVisa",
                "document_link": SimpleUploadedFile(
                    "scope-visa.pdf",
                    b"%PDF-1.4 scope visa",
                    content_type="application/pdf",
                ),
            },
            format="multipart",
            **self._partner_auth_headers(self.partner_a),
        )
        self.assertEqual(upload_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            BookingDocuments.objects.filter(
                document_for_booking_token=document_booking,
            ).count(),
            1,
        )

        delete_response = self.client.delete(
            "/api/v1/operator/bookings/document-delete/",
            {
                "partner_session_token": self.partner_b.partner_session_token,
                "booking_number": document_booking.booking_number,
                "document_id": str(existing_document.document_id),
            },
            format="json",
            **self._partner_auth_headers(self.partner_a),
        )
        self.assertEqual(delete_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(
            BookingDocuments.objects.filter(
                document_id=existing_document.document_id,
            ).exists()
        )

        airline_post_response = self.client.post(
            "/api/v1/operator/bookings/airline-details/",
            {
                "partner_session_token": self.partner_b.partner_session_token,
                "booking_number": airline_post_booking.booking_number,
                "flight_direction": "outbound",
                "flight_date": timezone.now().isoformat(),
                "flight_time": "10:00:00",
                "flight_from": "Karachi",
                "flight_to": "Jeddah",
            },
            format="json",
            **self._partner_auth_headers(self.partner_a),
        )
        self.assertEqual(airline_post_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            BookingAirlineDetail.objects.filter(
                airline_for_booking=airline_post_booking,
            ).exists()
        )

        airline_put_response = self.client.put(
            "/api/v1/operator/bookings/airline-details/",
            {
                "partner_session_token": self.partner_b.partner_session_token,
                "booking_airline_id": str(existing_airline.booking_airline_id),
                "booking_number": airline_put_booking.booking_number,
                "flight_direction": "outbound",
                "flight_date": timezone.now().isoformat(),
                "flight_time": "15:30:00",
                "flight_from": "Karachi",
                "flight_to": "Madinah",
            },
            format="json",
            **self._partner_auth_headers(self.partner_a),
        )
        self.assertEqual(airline_put_response.status_code, status.HTTP_404_NOT_FOUND)
        existing_airline.refresh_from_db()
        self.assertEqual(existing_airline.flight_from, "Lahore")
        self.assertEqual(existing_airline.flight_to, "Jeddah")

        arrangement_post_response = self.client.post(
            "/api/v1/operator/bookings/arrangements/",
            {
                "partner_session_token": self.partner_b.partner_session_token,
                "booking_number": arrangement_post_booking.booking_number,
                "detail_for": "Transport",
                "transport_mode": "details_only",
                "transport_name": "Scoped Coaster",
                "transport_type": "Shared",
                "route_summary": "Karachi -> Jeddah",
            },
            format="json",
            **self._partner_auth_headers(self.partner_a),
        )
        self.assertEqual(
            arrangement_post_response.status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertFalse(
            BookingTransportFulfillment.objects.filter(
                transport_for_booking=arrangement_post_booking,
            ).exists()
        )

        arrangement_put_response = self.client.put(
            "/api/v1/operator/bookings/arrangements/",
            {
                "partner_session_token": self.partner_b.partner_session_token,
                "booking_number": arrangement_put_booking.booking_number,
                "detail_for": "Transport",
                "transport_mode": "details_only",
                "transport_name": "Scoped Coaster",
                "transport_type": "Shared",
                "route_summary": "Karachi -> Jeddah",
            },
            format="json",
            **self._partner_auth_headers(self.partner_a),
        )
        self.assertEqual(
            arrangement_put_response.status_code,
            status.HTTP_404_NOT_FOUND,
        )
        existing_transport.refresh_from_db()
        self.assertEqual(existing_transport.transport_name, "Scoped Bus")
        self.assertEqual(existing_transport.route_summary, "Lahore -> Jeddah")

        mocked_send_objection.assert_not_called()

    @patch("booking.manage_partner_booking.send_objection_email")
    def test_take_action_accepts_partner_bearer_auth_without_body_token(
        self,
        mocked_send_objection,
    ):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-ACTION-HEADER-001",
            booking_status=BOOKING_STATUS_READY_FOR_OPERATOR,
        )
        self._mark_ready_for_operator(booking)

        response = self.client.put(
            "/api/v1/operator/bookings/action/",
            {
                "booking_number": booking.booking_number,
                "partner_remarks": "Proceed with fulfillment",
                "booking_status": "IN_FULFILLMENT",
            },
            format="json",
            **self._partner_auth_headers(self.partner_a),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mocked_send_objection.assert_not_called()
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, BOOKING_STATUS_IN_FULFILLMENT)

    def test_manage_booking_documents_and_delete_accept_partner_bearer_auth_without_body_token(
        self,
    ):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-DOC-HEADER-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(booking)

        upload_response = self.client.post(
            "/api/v1/operator/bookings/documents/",
            {
                "booking_number": booking.booking_number,
                "document_for": "eVisa",
                "document_link": SimpleUploadedFile(
                    "visa.pdf",
                    b"%PDF-1.4 visa",
                    content_type="application/pdf",
                ),
            },
            format="multipart",
            **self._partner_auth_headers(self.partner_a),
        )

        self.assertEqual(upload_response.status_code, status.HTTP_201_CREATED)
        document = BookingDocuments.objects.filter(
            document_for_booking_token=booking,
            document_for="eVisa",
        ).first()
        self.assertIsNotNone(document)

        delete_response = self.client.delete(
            "/api/v1/operator/bookings/document-delete/",
            {
                "booking_number": booking.booking_number,
                "document_id": str(document.document_id),
            },
            format="json",
            **self._partner_auth_headers(self.partner_a),
        )

        self.assertEqual(delete_response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            BookingDocuments.objects.filter(document_id=document.document_id).exists()
        )

    def test_airline_post_accepts_partner_bearer_auth_without_body_token(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-AIRLINE-HEADER-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(booking)

        response = self.client.post(
            "/api/v1/operator/bookings/airline-details/",
            {
                "booking_number": booking.booking_number,
                "flight_direction": "outbound",
                "flight_date": timezone.now().isoformat(),
                "flight_time": "10:00:00",
                "flight_from": "Karachi",
                "flight_to": "Jeddah",
            },
            format="json",
            **self._partner_auth_headers(self.partner_a),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            BookingAirlineDetail.objects.filter(
                airline_for_booking=booking,
                flight_direction="outbound",
            ).exists()
        )

    def test_transport_post_accepts_partner_bearer_auth_without_body_token(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-TRANSPORT-HEADER-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(booking)

        response = self.client.post(
            "/api/v1/operator/bookings/arrangements/",
            {
                "booking_number": booking.booking_number,
                "detail_for": "Transport",
                "transport_mode": "details_only",
                "transport_name": "Coaster",
                "transport_type": "Shared",
                "route_summary": "Jeddah -> Makkah",
                "contact_name": "Transport desk",
            },
            format="json",
            **self._partner_auth_headers(self.partner_a),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            BookingTransportFulfillment.objects.filter(
                transport_for_booking=booking
            ).exists()
        )

    def test_close_booking_accepts_partner_bearer_auth_without_body_token(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-CLOSE-HEADER-001",
            booking_status=BOOKING_STATUS_READY_FOR_TRAVEL,
        )
        self._mark_ready_for_travel(booking)

        response = self.client.put(
            "/api/v1/operator/bookings/close/",
            {
                "booking_number": booking.booking_number,
            },
            format="json",
            **self._partner_auth_headers(self.partner_a),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, BOOKING_STATUS_COMPLETED)

    def test_report_booking_accepts_partner_bearer_auth_without_body_token(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-REPORT-HEADER-001",
            booking_status=BOOKING_STATUS_READY_FOR_TRAVEL,
        )
        self._mark_ready_for_travel(booking)
        passport = PassportValidity.objects.filter(
            passport_for_booking_number=booking
        ).first()
        self.assertIsNotNone(passport)

        response = self.client.put(
            "/api/v1/operator/bookings/issues/",
            {
                "booking_number": booking.booking_number,
                "passport_id": str(passport.passport_id),
            },
            format="json",
            **self._partner_auth_headers(self.partner_a),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            TravelerIssue.objects.filter(
                booking=booking,
                traveler=passport,
                status=TravelerIssue.STATUS_OPEN,
            ).exists()
        )

    def test_complaints_list_returns_paginated_empty_payload(self):
        request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/complaints/",
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
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        booking_b = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-CMP-B-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
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
                "/api/v1/operator/bookings/complaints/",
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
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
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
                "/api/v1/operator/bookings/complaints/",
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
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
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
                "/api/v1/operator/bookings/complaints/respond/",
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
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
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
                "/api/v1/operator/bookings/complaints/respond/",
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
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
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
                "/api/v1/operator/bookings/complaints/respond/",
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
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        upload_file = SimpleUploadedFile(
            "sample.pdf",
            b"%PDF-1.4 test payload",
            content_type="application/pdf",
        )

        request = self._authenticated_request(
            self.factory.post(
                "/api/v1/operator/bookings/documents/",
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

    def test_delete_booking_documents_rejects_completed_booking(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-DOC-COMPLETE-001",
            booking_status=BOOKING_STATUS_READY_FOR_TRAVEL,
        )
        booking = self._mark_ready_for_travel(booking)
        booking.booking_status = BOOKING_STATUS_COMPLETED
        booking.save(update_fields=["booking_status"])
        booking.refresh_from_db()

        document = BookingDocuments.objects.create(
            document_link=SimpleUploadedFile(
                "completed-visa.pdf",
                b"%PDF-1.4 completed",
                content_type="application/pdf",
            ),
            document_for_booking_token=booking,
            document_for="eVisa",
        )

        request = self._authenticated_request(
            self.factory.delete(
                "/api/v1/operator/bookings/document-delete/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": booking.booking_number,
                    "document_id": str(document.document_id),
                },
                format="json",
            )
        )

        response = DeleteBookingDocumentsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn(
            "Only bookings that are in fulfillment or ready for travel can perform this task.",
            response.data.get("message", ""),
        )
        self.assertTrue(
            BookingDocuments.objects.filter(document_id=document.document_id).exists()
        )

    def test_hotel_transport_post_requires_booking_number(self):
        request = self._authenticated_request(
            self.factory.post(
                "/api/v1/operator/bookings/arrangements/",
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
        self.assertIn("Missing required data fields", response.data.get("message", ""))

    def test_hotel_transport_post_rejects_invalid_detail_for(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-DETAIL-INVALID-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )

        request = self._authenticated_request(
            self.factory.post(
                "/api/v1/operator/bookings/arrangements/",
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

    def test_airline_post_requires_both_legs_for_round_trip_booking(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-AIRLINE-ROUNDTRIP-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        DocumentsStatus.objects.update_or_create(
            status_for_booking=booking,
            defaults={
                "is_visa_completed": True,
                "is_airline_completed": True,
                "is_airline_detail_completed": False,
                "is_hotel_completed": True,
                "is_transport_completed": True,
            },
        )
        HuzAirlineDetail.objects.create(
            airline_name="Flynas",
            ticket_type="economy",
            flight_from="Karachi",
            flight_to="Jeddah",
            return_flight_from="Jeddah",
            return_flight_to="Karachi",
            is_return_flight_included=True,
            airline_for_package=self.package_a,
        )

        outbound_request = self._authenticated_request(
            self.factory.post(
                "/api/v1/operator/bookings/airline-details/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": booking.booking_number,
                    "flight_direction": "outbound",
                    "flight_date": timezone.now().isoformat(),
                    "flight_time": "10:00:00",
                    "flight_from": "Karachi",
                    "flight_to": "Jeddah",
                },
                format="json",
            )
        )

        outbound_response = BookingAirlineDetailsView.as_view()(outbound_request)
        self.assertEqual(outbound_response.status_code, status.HTTP_201_CREATED)

        booking.refresh_from_db()
        document_status = DocumentsStatus.objects.get(status_for_booking=booking)
        self.assertFalse(document_status.is_airline_detail_completed)
        self.assertEqual(booking.booking_status, BOOKING_STATUS_IN_FULFILLMENT)

        return_request = self._authenticated_request(
            self.factory.post(
                "/api/v1/operator/bookings/airline-details/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": booking.booking_number,
                    "flight_direction": "return",
                    "flight_date": (timezone.now() + timedelta(days=10)).isoformat(),
                    "flight_time": "18:30:00",
                    "flight_from": "Jeddah",
                    "flight_to": "Karachi",
                },
                format="json",
            )
        )

        return_response = BookingAirlineDetailsView.as_view()(return_request)
        self.assertEqual(return_response.status_code, status.HTTP_201_CREATED)

        booking.refresh_from_db()
        document_status.refresh_from_db()
        self.assertTrue(document_status.is_airline_detail_completed)
        self.assertFalse(document_status.is_airline_completed)
        self.assertEqual(booking.booking_status, BOOKING_STATUS_IN_FULFILLMENT)
        self.assertEqual(
            list(
                BookingAirlineDetail.objects.filter(airline_for_booking=booking).order_by("flight_direction").values_list(
                    "flight_direction", flat=True
                )
            ),
            ["outbound", "return"],
        )

    def test_airline_post_rejects_return_leg_for_one_way_booking(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-AIRLINE-ONEWAY-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )

        request = self._authenticated_request(
            self.factory.post(
                "/api/v1/operator/bookings/airline-details/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": booking.booking_number,
                    "flight_direction": "return",
                    "flight_date": timezone.now().isoformat(),
                    "flight_time": "18:30:00",
                    "flight_from": "Jeddah",
                    "flight_to": "Karachi",
                },
                format="json",
            )
        )

        response = BookingAirlineDetailsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data.get("message"),
            "Return airline details are not enabled for this booking.",
        )
        self.assertFalse(BookingAirlineDetail.objects.filter(airline_for_booking=booking).exists())

    def test_airline_put_is_scoped_to_booking_airline_id(self):
        booking_a = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-AIRLINE-A-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        booking_b = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-AIRLINE-B-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
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
                "/api/v1/operator/bookings/airline-details/",
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
            booking_status=BOOKING_STATUS_READY_FOR_TRAVEL,
        )
        self._mark_ready_for_travel(other_partner_booking)

        request = self._authenticated_request(
            self.factory.put(
                "/api/v1/operator/bookings/close/",
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
        self.assertEqual(other_partner_booking.booking_status, BOOKING_STATUS_READY_FOR_TRAVEL)

    def test_close_booking_rejects_open_traveler_issues(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-CLOSE-ISSUE-001",
            booking_status=BOOKING_STATUS_READY_FOR_TRAVEL,
        )
        booking = self._mark_ready_for_travel(booking)
        passport = PassportValidity.objects.filter(
            passport_for_booking_number=booking
        ).first()
        self.assertIsNotNone(passport)
        TravelerIssue.objects.create(
            booking=booking,
            traveler=passport,
            status=TravelerIssue.STATUS_OPEN,
            created_by=self.partner_a,
        )
        sync_booking_state(booking, save=True)

        request = self._authenticated_request(
            self.factory.put(
                "/api/v1/operator/bookings/close/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": booking.booking_number,
                },
                format="json",
            )
        )

        response = CloseBookingView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data.get("message"),
            "Booking cannot be completed while traveler issues are still open.",
        )

        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, BOOKING_STATUS_READY_FOR_TRAVEL)
        self.assertEqual(booking.issue_status, ISSUE_STATUS_REPORTED)

    def test_report_booking_requires_passport_for_same_booking(self):
        partner_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-A-REPORT-001",
            booking_status=BOOKING_STATUS_READY_FOR_TRAVEL,
        )
        other_booking = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-B-REPORT-001",
            booking_status=BOOKING_STATUS_READY_FOR_TRAVEL,
        )
        self._mark_ready_for_travel(partner_booking)
        self._mark_ready_for_travel(other_booking)
        unrelated_passport = PassportValidity.objects.create(
            passport_for_booking_number=other_booking
        )

        request = self._authenticated_request(
            self.factory.put(
                "/api/v1/operator/bookings/issues/",
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
        self.assertEqual(partner_booking.booking_status, BOOKING_STATUS_READY_FOR_TRAVEL)
        self.assertFalse(
            TravelerIssue.objects.filter(
                traveler=unrelated_passport,
                status=TravelerIssue.STATUS_OPEN,
            ).exists()
        )

    def test_report_booking_marks_issue_status_and_serializes_reported_traveler(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-REPORT-SERIALIZED-001",
            booking_status=BOOKING_STATUS_READY_FOR_TRAVEL,
        )
        booking = self._mark_ready_for_travel(booking)
        passport = PassportValidity.objects.filter(
            passport_for_booking_number=booking
        ).first()
        self.assertIsNotNone(passport)

        request = self._authenticated_request(
            self.factory.put(
                "/api/v1/operator/bookings/issues/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": booking.booking_number,
                    "passport_id": str(passport.passport_id),
                },
                format="json",
            )
        )

        response = ReportBookingView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("issue_status"), ISSUE_STATUS_REPORTED)
        self.assertTrue(
            any(
                str(issue.get("traveler_id")) == str(passport.passport_id)
                and str(issue.get("status")).lower() == TravelerIssue.STATUS_OPEN
                for issue in (response.data.get("traveler_issues") or [])
            )
        )

        booking.refresh_from_db()
        passport.refresh_from_db()
        self.assertEqual(booking.issue_status, ISSUE_STATUS_REPORTED)
        self.assertTrue(
            TravelerIssue.objects.filter(
                traveler=passport,
                status=TravelerIssue.STATUS_OPEN,
            ).exists()
        )

    @patch("booking.manage_partner_booking.send_objection_email")
    def test_take_action_sends_email_only_for_objection(self, mocked_send_objection):
        pending_booking_active = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-A-PENDING-001",
            booking_status=BOOKING_STATUS_READY_FOR_OPERATOR,
        )
        pending_booking_objection = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-A-PENDING-002",
            booking_status=BOOKING_STATUS_READY_FOR_OPERATOR,
        )
        self._mark_ready_for_operator(pending_booking_active)
        self._mark_ready_for_operator(pending_booking_objection)

        active_request = self._authenticated_request(
            self.factory.put(
                "/api/v1/operator/bookings/action/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": pending_booking_active.booking_number,
                    "partner_remarks": "All good",
                    "booking_status": "IN_FULFILLMENT",
                },
                format="json",
            )
        )
        active_response = TakeActionView.as_view()(active_request)
        self.assertEqual(active_response.status_code, status.HTTP_201_CREATED)
        mocked_send_objection.assert_not_called()

        objection_request = self._authenticated_request(
            self.factory.put(
                "/api/v1/operator/bookings/action/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "booking_number": pending_booking_objection.booking_number,
                    "partner_remarks": "Missing docs",
                    "booking_status": "OPERATOR_OBJECTION",
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
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
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
                "/api/v1/operator/bookings/complaints/summary/",
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
                "/api/v1/operator/bookings/earnings/yearly/",
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
        visible_view_only_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-STATS-PASSPORT-001",
            booking_status=BOOKING_STATUS_HOLD,
        )
        self._create_stage_payment(
            visible_view_only_booking,
            stage="Minimum",
            amount=max(float(visible_view_only_booking.total_price) * 0.1, 1),
            status_value=PAYMENT_STATUS_APPROVED,
            suffix="stats-visible-view-only",
        )
        sync_booking_state(visible_view_only_booking, save=True)

        visible_history_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-STATS-CANCEL-001",
            booking_status=BOOKING_STATUS_HOLD,
        )
        self._mark_ready_for_operator(visible_history_booking)
        visible_history_booking.booking_status = BOOKING_STATUS_CANCELLED
        visible_history_booking.save(update_fields=["booking_status"])
        sync_booking_state(visible_history_booking, save=True)

        visible_issue_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-STATS-ISSUE-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(visible_issue_booking)
        visible_issue_booking.issue_status = ISSUE_STATUS_OPERATOR_OBJECTION
        visible_issue_booking.save(update_fields=["issue_status"])
        sync_booking_state(visible_issue_booking, save=True)

        visible_reported_issue_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-STATS-REPORTED-001",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
        )
        self._mark_in_fulfillment(visible_reported_issue_booking)
        self._report_open_traveler_issue(visible_reported_issue_booking)

        request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/statistics/",
                {"partner_session_token": self.partner_a.partner_session_token},
            )
        )

        response = GetPartnersOverallBookingStatisticsView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(BOOKING_STATUS_TRAVELER_DETAILS_PENDING, response.data)
        self.assertIn(BOOKING_STATUS_CANCELLED, response.data)
        self.assertEqual(response.data.get(BOOKING_STATUS_TRAVELER_DETAILS_PENDING), 1)
        self.assertEqual(response.data.get(BOOKING_STATUS_CANCELLED), 1)
        self.assertIn(WORKFLOW_BUCKET_VIEW_ONLY, response.data)
        self.assertIn(WORKFLOW_BUCKET_ISSUES, response.data)
        self.assertIn(WORKFLOW_BUCKET_HISTORY, response.data)
        self.assertNotIn(WORKFLOW_BUCKET_REPORTED, response.data)
        self.assertEqual(response.data.get(WORKFLOW_BUCKET_VIEW_ONLY), 1)
        self.assertEqual(response.data.get(WORKFLOW_BUCKET_ISSUES), 2)
        self.assertEqual(response.data.get(WORKFLOW_BUCKET_HISTORY), 1)

    def test_booking_statistics_uses_bounded_query_count(self):
        self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-STATS-QUERY-001",
            booking_status=BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
        )

        request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/statistics/",
                {"partner_session_token": self.partner_a.partner_session_token},
            )
        )

        with CaptureQueriesContext(connection) as queries:
            response = GetPartnersOverallBookingStatisticsView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(queries), 2)

    def test_receivable_payment_statistics_returns_paginated_empty_payload(self):
        request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/payments/",
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
            booking_status=BOOKING_STATUS_COMPLETED,
        )
        partner_b_booking = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-REC-B-001",
            booking_status=BOOKING_STATUS_COMPLETED,
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
                "/api/v1/operator/bookings/payments/",
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

    def test_admin_partner_receivables_endpoint_paginates_and_returns_summary_totals(self):
        partner_a_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-REC-ADMIN-A-001",
            booking_status=BOOKING_STATUS_COMPLETED,
        )
        partner_b_booking = self._create_booking(
            partner=self.partner_b,
            package=self.package_b,
            booking_number="BK-REC-ADMIN-B-001",
            booking_status=BOOKING_STATUS_COMPLETED,
        )

        PartnersBookingPayment.objects.create(
            receivable_amount=1000.0,
            pending_amount=100.0,
            processed_amount=10.0,
            payment_status="NotPaid",
            payment_for_partner=self.partner_a,
            payment_for_package=self.package_a,
            payment_for_booking=partner_a_booking,
        )
        PartnersBookingPayment.objects.create(
            receivable_amount=500.0,
            pending_amount=50.0,
            processed_amount=20.0,
            payment_status="NotPaid",
            payment_for_partner=self.partner_b,
            payment_for_package=self.package_b,
            payment_for_booking=partner_b_booking,
        )

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(
            "/management/fetch_all_partner_receive_able_payments_details/",
            {
                "page": 1,
                "page_size": 1,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 2)
        self.assertEqual(len(response.data.get("results") or []), 1)
        self.assertEqual(
            float(response.data.get("meta", {}).get("total_receivable")),
            1500.0,
        )
        self.assertEqual(
            float(response.data.get("meta", {}).get("total_pending")),
            150.0,
        )
        self.assertEqual(
            float(response.data.get("meta", {}).get("total_processed")),
            30.0,
        )

    def test_admin_partner_receivables_endpoint_reuses_cached_payload(self):
        partner_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-REC-ADMIN-CACHE-001",
            booking_status=BOOKING_STATUS_COMPLETED,
        )
        PartnersBookingPayment.objects.create(
            receivable_amount=750.0,
            pending_amount=75.0,
            processed_amount=25.0,
            payment_status="NotPaid",
            payment_for_partner=self.partner_a,
            payment_for_package=self.package_a,
            payment_for_booking=partner_booking,
        )

        params = {
            "page": 1,
            "page_size": 10,
        }
        self.client.force_authenticate(user=self.admin_user)
        first_response = self.client.get(
            "/management/fetch_all_partner_receive_able_payments_details/",
            params,
        )
        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(first_response.data.get("count"), 1)

        PartnersBookingPayment.objects.filter(
            payment_for_booking=partner_booking
        ).update(payment_status="FirstPayment")

        second_response = self.client.get(
            "/management/fetch_all_partner_receive_able_payments_details/",
            params,
        )
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.data.get("count"), 1)
        self.assertEqual(
            second_response.data.get("results", [{}])[0].get("booking_number"),
            partner_booking.booking_number,
        )

    def test_admin_partner_receivables_cache_invalidates_after_transfer(self):
        partner_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-REC-ADMIN-CACHE-INVALIDATE-001",
            booking_status=BOOKING_STATUS_COMPLETED,
        )
        PartnersBookingPayment.objects.create(
            receivable_amount=820.0,
            pending_amount=80.0,
            processed_amount=20.0,
            payment_status="NotPaid",
            payment_for_partner=self.partner_a,
            payment_for_package=self.package_a,
            payment_for_booking=partner_booking,
        )
        Wallet.objects.create(
            wallet_code="wallet-cache-invalidate-001",
            wallet_amount=0.0,
            wallet_session=self.partner_a,
        )

        self.client.force_authenticate(user=self.admin_user)
        initial_response = self.client.get(
            "/management/fetch_all_partner_receive_able_payments_details/",
            {
                "page": 1,
                "page_size": 10,
            },
        )
        self.assertEqual(initial_response.status_code, status.HTTP_200_OK)
        self.assertEqual(initial_response.data.get("count"), 1)

        update_response = self.client.put(
            "/management/transfer_partner_receive_able_payments/",
            {
                "partner_session_token": self.partner_a.partner_session_token,
                "booking_number": partner_booking.booking_number,
            },
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)

        refreshed_response = self.client.get(
            "/management/fetch_all_partner_receive_able_payments_details/",
            {
                "page": 1,
                "page_size": 10,
            },
        )
        self.assertEqual(refreshed_response.status_code, status.HTTP_200_OK)
        self.assertEqual(refreshed_response.data.get("count"), 0)
        self.assertEqual(refreshed_response.data.get("results"), [])

    def test_overall_rating_distribution_normalizes_decimal_ratings(self):
        booking_one = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-001",
            booking_status=BOOKING_STATUS_COMPLETED,
        )
        booking_two = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-002",
            booking_status=BOOKING_STATUS_COMPLETED,
        )
        booking_three = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-003",
            booking_status=BOOKING_STATUS_COMPLETED,
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
                "/api/v1/operator/bookings/ratings/summary/",
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
                "/api/v1/operator/bookings/ratings/package-summary/",
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

    def test_rating_summary_views_use_bounded_query_counts(self):
        booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-QUERY-001",
            booking_status=BOOKING_STATUS_COMPLETED,
        )
        BookingRatingAndReview.objects.create(
            partner_total_stars=4.6,
            partner_comment="Great service",
            rating_for_partner=self.partner_a,
            rating_for_package=self.package_a,
            rating_for_booking=booking,
            rating_by_user=self.customer,
        )

        overall_request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/ratings/summary/",
                {"partner_session_token": self.partner_a.partner_session_token},
            )
        )
        with CaptureQueriesContext(connection) as overall_queries:
            overall_response = GetOverallRatingView.as_view()(overall_request)

        package_request = self._authenticated_request(
            self.factory.get(
                "/api/v1/operator/bookings/ratings/package-summary/",
                {
                    "partner_session_token": self.partner_a.partner_session_token,
                    "huz_token": self.package_a.huz_token,
                },
            )
        )
        with CaptureQueriesContext(connection) as package_queries:
            package_response = GetPackageOverallRatingView.as_view()(package_request)

        self.assertEqual(overall_response.status_code, status.HTTP_200_OK)
        self.assertEqual(package_response.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(overall_queries), 2)
        self.assertLessEqual(len(package_queries), 3)

    def test_package_reviews_endpoint_returns_paginated_results(self):
        older_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-PAGE-001",
            booking_status=BOOKING_STATUS_COMPLETED,
        )
        newer_booking = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-PAGE-002",
            booking_status=BOOKING_STATUS_COMPLETED,
        )

        BookingRatingAndReview.objects.create(
            partner_total_stars=4.0,
            partner_comment="Older review",
            rating_for_partner=self.partner_a,
            rating_for_package=self.package_a,
            rating_for_booking=older_booking,
            rating_by_user=self.customer,
            rating_time=timezone.now() - timedelta(days=2),
        )
        BookingRatingAndReview.objects.create(
            partner_total_stars=5.0,
            partner_comment="Newest review",
            rating_for_partner=self.partner_a,
            rating_for_package=self.package_a,
            rating_for_booking=newer_booking,
            rating_by_user=self.customer,
            rating_time=timezone.now() - timedelta(hours=1),
        )

        response = self._request_package_reviews(
            partner_session_token=self.partner_a.partner_session_token,
            huz_token=self.package_a.huz_token,
            page=1,
            page_size=1,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 2)
        self.assertEqual(len(response.data.get("results", [])), 1)
        self.assertEqual(response.data["results"][0]["partner_comment"], "Newest review")

    def test_package_reviews_endpoint_filters_by_search_and_date_range(self):
        booking_one = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-FILTER-001",
            booking_status=BOOKING_STATUS_COMPLETED,
        )
        booking_two = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-FILTER-002",
            booking_status=BOOKING_STATUS_COMPLETED,
        )
        dated_customer = UserProfile.objects.create(
            session_token="dated-review-customer-token",
            name="Pilgrim Search",
            country_code="+1",
            phone_number="5551112222",
            email="pilgrim-search@example.com",
            user_type="user",
        )

        BookingRatingAndReview.objects.create(
            partner_total_stars=4.2,
            partner_comment="Helpful guide",
            rating_for_partner=self.partner_a,
            rating_for_package=self.package_a,
            rating_for_booking=booking_one,
            rating_by_user=dated_customer,
            rating_time=timezone.now() - timedelta(days=4),
        )
        BookingRatingAndReview.objects.create(
            partner_total_stars=3.5,
            partner_comment="Needs follow up",
            rating_for_partner=self.partner_a,
            rating_for_package=self.package_a,
            rating_for_booking=booking_two,
            rating_by_user=self.customer,
            rating_time=timezone.now() - timedelta(days=1),
        )

        target_day = (timezone.now() - timedelta(days=4)).date().isoformat()
        response = self._request_package_reviews(
            partner_session_token=self.partner_a.partner_session_token,
            huz_token=self.package_a.huz_token,
            search="pilgrim",
            from_date=target_day,
            to_date=target_day,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("count"), 1)
        self.assertEqual(response.data["results"][0]["partner_comment"], "Helpful guide")
        self.assertEqual(response.data["results"][0]["user_fullName"], "Pilgrim Search")

    def test_package_reviews_endpoint_supports_requested_sort_orders(self):
        booking_high = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-SORT-001",
            booking_status=BOOKING_STATUS_COMPLETED,
        )
        booking_mid = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-SORT-002",
            booking_status=BOOKING_STATUS_COMPLETED,
        )
        booking_low = self._create_booking(
            partner=self.partner_a,
            package=self.package_a,
            booking_number="BK-RATING-SORT-003",
            booking_status=BOOKING_STATUS_COMPLETED,
        )

        BookingRatingAndReview.objects.create(
            partner_total_stars=5.0,
            partner_comment="Highest score",
            rating_for_partner=self.partner_a,
            rating_for_package=self.package_a,
            rating_for_booking=booking_high,
            rating_by_user=self.customer,
            rating_time=timezone.now() - timedelta(days=2),
        )
        BookingRatingAndReview.objects.create(
            partner_total_stars=3.5,
            partner_comment="Middle score",
            rating_for_partner=self.partner_a,
            rating_for_package=self.package_a,
            rating_for_booking=booking_mid,
            rating_by_user=self.customer,
            rating_time=timezone.now() - timedelta(days=3),
        )
        BookingRatingAndReview.objects.create(
            partner_total_stars=1.5,
            partner_comment="Lowest score",
            rating_for_partner=self.partner_a,
            rating_for_package=self.package_a,
            rating_for_booking=booking_low,
            rating_by_user=self.customer,
            rating_time=timezone.now() - timedelta(hours=2),
        )

        expected_first_comment = {
            "highest": "Highest score",
            "lowest": "Lowest score",
            "oldest": "Middle score",
            "newest": "Lowest score",
        }

        for sort_key, comment in expected_first_comment.items():
            with self.subTest(sort=sort_key):
                response = self._request_package_reviews(
                    partner_session_token=self.partner_a.partner_session_token,
                    huz_token=self.package_a.huz_token,
                    sort=sort_key,
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data["results"][0]["partner_comment"], comment)


class BookingSerializerQueryTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        self.customer = UserProfile.objects.create(
            session_token=f"serializer-customer-{uuid4().hex[:8]}",
            name="Serializer Customer",
            country_code="+1",
            phone_number="3332221111",
            email="serializer-customer@example.com",
            user_type="user",
        )
        self.partner = PartnerProfile.objects.create(
            partner_session_token=f"serializer-partner-{uuid4().hex[:8]}",
            user_name=f"serializer-partner-{uuid4().hex[:8]}",
            name="Serializer Partner",
            partner_type="Company",
            account_status="Active",
        )
        self.package = HuzBasicDetail.objects.create(
            huz_token=f"serializer-package-{uuid4().hex[:8]}",
            package_type="Hajj",
            package_name="Serializer Package",
            start_date=timezone.now() + timedelta(days=20),
            end_date=timezone.now() + timedelta(days=25),
            description="Serializer package",
            package_status="Active",
            package_provider=self.partner,
        )
        self.booking = Booking.objects.create(
            booking_number=f"BK-SERIALIZER-{uuid4().hex[:8]}",
            adults=2,
            child=0,
            infants=0,
            sharing="Yes",
            quad="0",
            triple="0",
            double="1",
            single="0",
            start_date=timezone.now() + timedelta(days=21),
            end_date=timezone.now() + timedelta(days=26),
            total_price=2000,
            special_request="N/A",
            booking_status=BOOKING_STATUS_IN_FULFILLMENT,
            payment_type="Bank",
            order_by=self.customer,
            order_to=self.partner,
            package_token=self.package,
        )
        MailingDetail.objects.create(
            street_address="123 Query Street",
            address_line2="Suite 1",
            city="Karachi",
            state="Sindh",
            country="Pakistan",
            postal_code="75500",
            lat="0",
            long="0",
            mailing_session=self.customer,
        )
        BusinessProfile.objects.create(
            company_name="Serializer Travel",
            contact_name="Partner Contact",
            contact_number="03001234567",
            company_of_partner=self.partner,
        )
        self.rating = BookingRatingAndReview.objects.create(
            partner_total_stars=4.5,
            partner_comment="Helpful staff",
            rating_by_user=self.customer,
            rating_for_partner=self.partner,
            rating_for_booking=self.booking,
            rating_for_package=self.package,
        )
        self.complaint = BookingComplaints.objects.create(
            complaint_ticket="CMP-SERIALIZER-001",
            complaint_title="Transport delay",
            complaint_message="Driver was late",
            complaint_status="Open",
            complaint_by_user=self.customer,
            complaint_for_partner=self.partner,
            complaint_for_package=self.package,
            complaint_for_booking=self.booking,
        )
        self.booking_request = BookingRequest.objects.create(
            request_ticket="REQ-SERIALIZER-001",
            request_title="Need update",
            request_message="Share the latest itinerary",
            request_status="Open",
            request_by_user=self.customer,
            request_for_package=self.package,
            request_for_partner=self.partner,
            request_for_booking=self.booking,
        )

    def _create_fulfillment_artifacts(self, booking=None):
        booking = booking or self.booking
        BookingAirlineDetail.objects.update_or_create(
            airline_for_booking=booking,
            flight_direction="outbound",
            defaults={
                "flight_date": booking.start_date,
                "flight_time": booking.start_date.time(),
                "flight_from": "Karachi",
                "flight_to": "Jeddah",
            },
        )
        BookingDocuments.objects.get_or_create(
            document_for="eVisa",
            document_category="evisa",
            document_scope=BookingDocuments.DOCUMENT_SCOPE_BOOKING,
            document_title="Visa document",
            document_for_booking_token=booking,
        )
        BookingDocuments.objects.get_or_create(
            document_for="airline",
            document_category="airline",
            document_scope=BookingDocuments.DOCUMENT_SCOPE_BOOKING,
            document_title="Airline ticket",
            document_for_booking_token=booking,
        )
        sync_booking_state(booking, save=True)
        booking.refresh_from_db()
        return booking

    def test_partner_rating_serializer_uses_prefetched_user_address(self):
        rating = BookingRatingAndReview.objects.select_related("rating_by_user").prefetch_related(
            "rating_by_user__mailing_session"
        ).get(pk=self.rating.pk)

        with CaptureQueriesContext(connection) as queries:
            data = PartnerRatingSerializer(rating).data

        self.assertLessEqual(len(queries), 1)
        self.assertEqual(data.get("user_address_detail", {}).get("city"), "Karachi")

    def test_booking_complaints_serializer_uses_prefetched_related_details(self):
        complaint = BookingComplaints.objects.select_related(
            "complaint_by_user",
            "complaint_for_partner",
            "complaint_for_package",
            "complaint_for_booking",
        ).prefetch_related(
            "complaint_by_user__mailing_session",
            "complaint_for_partner__company_of_partner",
        ).get(pk=self.complaint.pk)

        with CaptureQueriesContext(connection) as queries:
            data = BookingComplaintsSerializer(complaint).data

        self.assertLessEqual(len(queries), 1)
        self.assertEqual(data.get("user_address_detail", {}).get("city"), "Karachi")
        self.assertEqual(
            data.get("partner_contact_detail", {}).get("company_name"),
            "Serializer Travel",
        )

    def test_booking_request_serializer_uses_prefetched_related_details(self):
        booking_request = BookingRequest.objects.select_related(
            "request_by_user",
            "request_for_partner",
            "request_for_package",
            "request_for_booking",
        ).prefetch_related(
            "request_by_user__mailing_session",
            "request_for_partner__company_of_partner",
        ).get(pk=self.booking_request.pk)

        with CaptureQueriesContext(connection) as queries:
            data = BookingRequestSerializer(booking_request).data

        self.assertLessEqual(len(queries), 1)
        self.assertEqual(data.get("user_address_detail", {}).get("city"), "Karachi")
        self.assertEqual(
            data.get("partner_contact_detail", {}).get("company_name"),
            "Serializer Travel",
        )

    def test_get_booking_by_identifier_for_user_can_preload_customer_detail_relations(self):
        detail_booking = get_booking_by_identifier_for_user(
            self.customer,
            self.booking.booking_number,
            include_detail_relations=True,
        )

        with CaptureQueriesContext(connection) as queries:
            data = DetailBookingSerializer(detail_booking).data

        self.assertEqual(len(queries), 0)
        self.assertEqual(data.get("company_detail", {}).get("company_name"), "Serializer Travel")

    def test_detail_serializer_exposes_backend_action_flags_for_issue_state(self):
        self._create_fulfillment_artifacts(self.booking)
        passport = PassportValidity.objects.create(
            first_name="Serializer",
            last_name="Traveler",
            date_of_birth=aware_midnight("1990-01-10"),
            passport_number=f"{self.booking.booking_number}-ISSUE-01",
            passport_country="PK",
            expiry_date=aware_midnight("2031-06-01"),
            user_passport=SimpleUploadedFile(
                "serializer-passport.jpg",
                b"passport-image",
                content_type="image/jpeg",
            ),
            user_photo=SimpleUploadedFile(
                "serializer-photo.jpg",
                b"photo-image",
                content_type="image/jpeg",
            ),
            passport_for_booking_number=self.booking,
        )
        TravelerIssue.objects.create(
            booking=self.booking,
            traveler=passport,
            status=TravelerIssue.STATUS_OPEN,
            created_by=self.partner,
        )
        sync_booking_state(self.booking, save=True)

        detail_booking = get_booking_by_identifier_for_user(
            self.customer,
            self.booking.booking_number,
            include_detail_relations=True,
        )
        data = DetailBookingSerializer(detail_booking).data

        self.assertEqual(data.get("issue_status"), ISSUE_STATUS_REPORTED)
        self.assertEqual(data.get("workflow_bucket"), WORKFLOW_BUCKET_ISSUES)
        self.assertFalse(data.get("can_take_decision"))
        self.assertTrue(data.get("can_edit_fulfillment"))
        self.assertTrue(data.get("can_manage_traveler_issues"))
        self.assertFalse(data.get("can_complete_booking"))

    def test_detail_serializer_fulfillment_summary_ignores_stale_legacy_status_flags(self):
        self._create_fulfillment_artifacts(self.booking)
        DocumentsStatus.objects.update_or_create(
            status_for_booking=self.booking,
            defaults={
                "is_visa_completed": False,
                "is_airline_completed": False,
                "is_airline_detail_completed": False,
                "is_hotel_completed": False,
                "is_transport_completed": False,
            },
        )

        detail_booking = get_booking_by_identifier_for_user(
            self.customer,
            self.booking.booking_number,
            include_detail_relations=True,
        )
        data = DetailBookingSerializer(detail_booking).data
        fulfillment_summary = data.get("booking_fulfillment", {}).get("summary", {})

        self.assertTrue(fulfillment_summary.get("visa_completed"))
        self.assertTrue(fulfillment_summary.get("airline_documents_completed"))
        self.assertTrue(fulfillment_summary.get("airline_details_completed"))
        self.assertTrue(fulfillment_summary.get("hotel_completed"))
        self.assertTrue(fulfillment_summary.get("transport_completed"))

    def test_effective_booking_status_does_not_complete_ready_for_travel_with_open_issues(self):
        self._create_fulfillment_artifacts(self.booking)
        passport = PassportValidity.objects.create(
            first_name="Serializer",
            last_name="Queue",
            date_of_birth=aware_midnight("1990-01-10"),
            passport_number=f"{self.booking.booking_number}-QUEUE-01",
            passport_country="PK",
            expiry_date=aware_midnight("2031-06-01"),
            user_passport=SimpleUploadedFile(
                "serializer-queue-passport.jpg",
                b"passport-image",
                content_type="image/jpeg",
            ),
            user_photo=SimpleUploadedFile(
                "serializer-queue-photo.jpg",
                b"photo-image",
                content_type="image/jpeg",
            ),
            passport_for_booking_number=self.booking,
        )
        TravelerIssue.objects.create(
            booking=self.booking,
            traveler=passport,
            status=TravelerIssue.STATUS_OPEN,
            created_by=self.partner,
        )
        self.booking.end_date = timezone.now() - timedelta(days=1)
        self.booking.save(update_fields=["end_date"])
        sync_booking_state(self.booking, save=True)

        effective_booking = annotate_effective_booking_status(
            Booking.objects.filter(pk=self.booking.pk),
            today=timezone.localdate(),
        ).get(pk=self.booking.pk)

        self.assertEqual(effective_booking.effective_booking_status, BOOKING_STATUS_READY_FOR_TRAVEL)

    @patch("booking.services.user_new_booking_email")
    def test_record_booking_payment_returns_prefetched_mutation_booking(self, mocked_new_booking_email):
        self.booking.booking_status = BOOKING_STATUS_HOLD
        self.booking.hold_expires_at = timezone.now() + timedelta(minutes=15)
        self.booking.save(update_fields=["booking_status", "hold_expires_at"])

        updated_booking = record_booking_payment(
            {
                "session_token": self.customer.session_token,
                "booking_number": self.booking.booking_number,
                "transaction_type": "Minimum",
                "transaction_amount": 200,
                "transaction_number": "SERIALIZER-MIN-PAYMENT",
            }
        )

        with CaptureQueriesContext(connection) as queries:
            data = BookingMutationSerializer(updated_booking).data

        self.assertLessEqual(len(queries), 1)
        self.assertEqual(data.get("company_detail", {}).get("company_name"), "Serializer Travel")
        self.assertEqual(len(data.get("payment_detail") or []), 1)
        self.assertEqual(data.get("response_mode"), "mutation_summary")
        self.assertNotIn("booking_documents", data)
        self.assertEqual(
            data.get("payment_detail")[0].get("transaction_number"),
            "SERIALIZER-MIN-PAYMENT",
        )
        self.assertEqual(mocked_new_booking_email.call_count, 1)

    def test_validate_passport_returns_prefetched_detail_booking(self):
        self.booking.booking_status = BOOKING_STATUS_HOLD
        self.booking.hold_expires_at = timezone.now() + timedelta(minutes=15)
        self.booking.save(update_fields=["booking_status", "hold_expires_at"])
        Payment.objects.create(
            transaction_number="SERIALIZER-PASSPORT-MINIMUM",
            transaction_type="Minimum",
            transaction_amount=200,
            payment_status=PAYMENT_STATUS_APPROVED,
            booking_token=self.booking,
        )
        sync_booking_state(self.booking, save=True)

        updated_booking = validate_passport(
            {
                "session_token": self.customer.session_token,
                "booking_number": self.booking.booking_number,
                "first_name": "Fatima",
                "middle_name": "",
                "last_name": "Noor",
                "date_of_birth": aware_midnight("1990-01-10"),
                "passport_number": "P1234567",
                "passport_country": "PK",
                "expiry_date": aware_midnight("2031-06-01"),
            }
        )

        with CaptureQueriesContext(connection) as queries:
            data = DetailBookingSerializer(updated_booking).data

        self.assertEqual(len(queries), 0)
        self.assertEqual(data.get("user_address_detail", {}).get("city"), "Karachi")
        self.assertEqual(len(data.get("traveler_groups") or []), 1)
        self.assertEqual(
            data.get("traveler_groups")[0].get("travelers")[0].get("passport_number"),
            "P1234567",
        )
