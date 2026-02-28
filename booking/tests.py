from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITransactionTestCase, force_authenticate

from common.models import UserProfile
from partners.models import HuzBasicDetail, PartnerProfile

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
from .manage_bookings import BookingRatingAndReviewView, BookingComplaintsView
from .models import (
    Booking,
    BookingAirlineDetail,
    BookingComplaints,
    BookingObjections,
    PassportValidity,
    BookingRatingAndReview,
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
