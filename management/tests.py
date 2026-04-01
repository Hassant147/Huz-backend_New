from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase


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
        self.assertEqual(
            bootstrap_response.data.get("user", {}).get("username"),
            self.staff_user.username,
        )

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
