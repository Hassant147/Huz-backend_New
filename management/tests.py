from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from booking.models import Booking, PartnersBookingPayment
from booking.statuses import BOOKING_STATUS_READY_FOR_TRAVEL
from partners.models import BusinessProfile, HuzBasicDetail, PartnerProfile, Wallet


class AdminAuthSessionApiTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            username="ops-admin",
            password="StrongAdminPassword123!",
            email="ops-admin@example.com",
            first_name="Ops",
            last_name="Admin",
            is_staff=True,
        )
        self.regular_user = user_model.objects.create_user(
            username="plain-user",
            password="PlainUserPassword123!",
            email="plain-user@example.com",
            first_name="Plain",
            last_name="User",
            is_staff=False,
        )

    def test_admin_login_returns_bootstrap_payload_and_persists_session(self):
        response = self.client.post(
            "/management/auth/login/",
            {
                "username": self.staff_user.username,
                "password": "StrongAdminPassword123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("authenticated"), True)
        self.assertIn("csrftoken", response.cookies)
        self.assertTrue(response.cookies["csrftoken"].value)
        self.assertTrue(response.data.get("csrf_token"))
        self.assertEqual(
            response.data.get("user"),
            {
                "id": self.staff_user.id,
                "username": self.staff_user.username,
                "email": self.staff_user.email,
                "name": "Ops Admin",
                "role": "admin",
            },
        )

        bootstrap_response = self.client.get("/management/auth/me/")
        self.assertEqual(bootstrap_response.status_code, status.HTTP_200_OK)
        self.assertEqual(bootstrap_response.data.get("authenticated"), True)
        self.assertTrue(bootstrap_response.data.get("csrf_token"))
        self.assertEqual(
            bootstrap_response.data.get("user", {}).get("username"),
            self.staff_user.username,
        )

    def test_admin_me_sets_csrf_cookie_for_authenticated_session(self):
        self.client.force_login(self.staff_user)

        response = self.client.get("/management/auth/me/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("csrftoken", response.cookies)
        self.assertTrue(response.cookies["csrftoken"].value)
        self.assertTrue(response.data.get("csrf_token"))

    def test_admin_login_rejects_invalid_credentials(self):
        response = self.client.post(
            "/management/auth/login/",
            {
                "username": self.staff_user.username,
                "password": "wrong-password",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data.get("authenticated"), False)
        self.assertIsNone(response.data.get("user"))
        self.assertEqual(response.data.get("message"), "Invalid credentials.")

    def test_admin_login_rejects_non_staff_user(self):
        response = self.client.post(
            "/management/auth/login/",
            {
                "username": self.regular_user.username,
                "password": "PlainUserPassword123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data.get("authenticated"), False)
        self.assertIsNone(response.data.get("user"))
        self.assertEqual(response.data.get("message"), "Admin access required.")

    def test_admin_me_requires_authenticated_session(self):
        response = self.client.get("/management/auth/me/")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data.get("authenticated"), False)
        self.assertIsNone(response.data.get("user"))
        self.assertEqual(response.data.get("message"), "Not authenticated.")

    def test_admin_me_rejects_authenticated_non_staff_session(self):
        self.client.force_login(self.regular_user)

        response = self.client.get("/management/auth/me/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data.get("authenticated"), False)
        self.assertIsNone(response.data.get("user"))
        self.assertEqual(response.data.get("message"), "Admin access required.")

    def test_admin_logout_clears_session_and_is_idempotent(self):
        self.client.force_login(self.staff_user)

        first_response = self.client.post("/management/auth/logout/", format="json")
        second_response = self.client.post("/management/auth/logout/", format="json")
        bootstrap_response = self.client.get("/management/auth/me/")

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(first_response.data.get("authenticated"), False)
        self.assertIsNone(first_response.data.get("user"))
        self.assertEqual(first_response.data.get("message"), "Logged out.")
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(bootstrap_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_existing_management_route_distinguishes_unauthorized_and_forbidden_sessions(self):
        anonymous_response = self.client.get("/management/fetch_all_pending_companies/")
        self.client.force_login(self.regular_user)
        forbidden_response = self.client.get("/management/fetch_all_pending_companies/")

        self.assertEqual(anonymous_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(forbidden_response.status_code, status.HTTP_403_FORBIDDEN)


class AdminLegacyApiCleanupTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            username="cleanup-admin",
            password="CleanupAdminPassword123!",
            email="cleanup-admin@example.com",
            is_staff=True,
        )
        self.client.force_login(self.staff_user)

        self.partner = PartnerProfile.objects.create(
            partner_session_token="cleanup-company-session-token",
            email="company@example.com",
            name="Cleanup Company",
            country_code="+92",
            phone_number="3001234000",
            partner_type="Company",
            account_status="Pending",
            is_email_verified=True,
            is_address_exist=True,
        )
        self.company = BusinessProfile.objects.create(
            company_of_partner=self.partner,
            company_name="Cleanup Travels",
            contact_name="Cleanup Contact",
            contact_number="03001234000",
            total_experience="5",
            company_bio="Cleanup company bio",
            license_type="Agency",
            license_number="LIC-123",
            license_certificate="license.pdf",
            company_logo="logo.png",
        )
        self.wallet = Wallet.objects.create(wallet_session=self.partner, wallet_amount=0)

        start_date = timezone.now() + timedelta(days=30)
        end_date = start_date + timedelta(days=7)
        self.package = HuzBasicDetail.objects.create(
            huz_token="cleanup-package-token",
            package_type="Umrah",
            package_name="Cleanup Package",
            package_base_cost=1200,
            cost_for_child=300,
            cost_for_infants=100,
            cost_for_sharing=900,
            cost_for_quad=1000,
            cost_for_triple=1100,
            cost_for_double=1200,
            cost_for_single=1400,
            start_date=start_date,
            end_date=end_date,
            description="Cleanup package",
            package_status="Active",
            package_provider=self.partner,
        )
        self.booking = Booking.objects.create(
            booking_number="CLEANUP-BOOKING-001",
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
            total_price=2400,
            special_request="",
            booking_status=BOOKING_STATUS_READY_FOR_TRAVEL,
            payment_type="Bank",
            order_to=self.partner,
            package_token=self.package,
        )
        self.receivable = PartnersBookingPayment.objects.create(
            payment_for_partner=self.partner,
            payment_for_booking=self.booking,
            payment_for_package=self.package,
            payment_status="NotPaid",
            receivable_amount=500,
            pending_amount=250,
            processed_amount=0,
        )

    def test_company_status_update_accepts_company_id(self):
        response = self.client.put(
            "/api/v1/admin/companies/status/",
            {
                "company_id": str(self.company.company_id),
                "account_status": "Active",
            },
            format="json",
        )

        self.partner.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.partner.account_status, "Active")

    def test_receivables_transfer_accepts_booking_number_without_partner_token(self):
        self.partner.account_status = "Active"
        self.partner.save(update_fields=["account_status"])

        response = self.client.put(
            "/api/v1/admin/receivables/transfer/",
            {
                "booking_number": self.booking.booking_number,
            },
            format="json",
        )

        self.receivable.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.receivable.payment_status, "FirstPayment")
        self.assertEqual(float(self.wallet.wallet_amount), float(self.receivable.receivable_amount))

    def test_featured_package_list_defaults_to_active_packages(self):
        response = self.client.get("/api/v1/admin/packages/featured/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data.get("count", 0), 1)
        first_item = (response.data.get("results") or [])[0]
        self.assertIn("huz_token", first_item)
        self.assertIn("is_featured", first_item)
        self.assertEqual(first_item.get("package_status"), "Active")

    def test_featured_package_toggle_updates_status_without_partner_token(self):
        response = self.client.put(
            "/api/v1/admin/packages/featured/",
            {
                "huz_token": self.package.huz_token,
                "is_featured": True,
            },
            format="json",
        )

        self.package.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(self.package.is_featured)
        self.assertEqual(response.data.get("huz_token"), self.package.huz_token)
        self.assertEqual(response.data.get("is_featured"), True)
