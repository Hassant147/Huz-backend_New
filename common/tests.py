from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from uuid import uuid4
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate

from .models import MailingDetail, UserBankAccount, UserProfile, UserTransactionHistory, Wallet
from .user_profile import SendOTPSMSAPIView


class SendOTPSMSAPIViewThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.admin_user = get_user_model().objects.create_user(
            username="otp-throttle-admin",
            password="pass123",
            is_staff=True,
            is_superuser=True,
        )

    def tearDown(self):
        cache.clear()
        super().tearDown()

    @patch("common.user_profile.config", return_value="test-api-key")
    @patch("common.user_profile.requests.post")
    def test_send_otp_sms_is_rate_limited(self, mocked_post, _mocked_config):
        mocked_post.return_value = Mock(status_code=200)
        view = SendOTPSMSAPIView.as_view()

        for _ in range(10):
            request = self.factory.post(
                "/common/send_otp_sms/",
                {"phone_number": "+921234567890"},
                format="json",
            )
            force_authenticate(request, user=self.admin_user)
            response = view(request)
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        throttled_request = self.factory.post(
            "/common/send_otp_sms/",
            {"phone_number": "+921234567890"},
            format="json",
        )
        force_authenticate(throttled_request, user=self.admin_user)
        throttled_response = view(throttled_request)

        self.assertEqual(throttled_response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class CurrentUserApiV1Tests(APITestCase):
    def setUp(self):
        token_suffix = uuid4().hex[:8]
        self.user = UserProfile.objects.create(
            session_token=f"current-user-api-session-token-{token_suffix}",
            name="Current User",
            country_code="+1",
            phone_number="1112223333",
            email="current-user@example.com",
            user_gender="male",
            user_type="user",
        )
        self.wallet = Wallet.objects.create(
            wallet_code=f"wallet-current-user-api-{token_suffix}",
            wallet_amount=5000,
            wallet_session=self.user,
        )
        self.transaction = UserTransactionHistory.objects.create(
            transaction_code=f"TX-{token_suffix}",
            transaction_amount=750,
            transaction_type="Credit",
            transaction_for_user=self.user,
            transaction_wallet_token=self.wallet,
            transaction_description="Wallet top-up",
        )

    def test_v1_profile_endpoint_returns_authenticated_profile(self):
        response = self.client.get(
            "/api/v1/users/me/profile/",
            HTTP_AUTHORIZATION=f"Bearer {self.user.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("session_token"), self.user.session_token)
        self.assertEqual(response.data.get("wallet_amount"), self.wallet.wallet_amount)

    def test_v1_profile_endpoint_updates_profile_fields(self):
        response = self.client.put(
            "/api/v1/users/me/profile/",
            {
                "name": "Updated User",
                "email": "updated-user@example.com",
                "user_gender": "other",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.user.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.name, "Updated User")
        self.assertEqual(self.user.email, "updated-user@example.com")
        self.assertEqual(self.user.user_gender, "other")

    def test_v1_address_endpoints_create_and_fetch_address(self):
        create_response = self.client.post(
            "/api/v1/users/me/address/",
            {
                "street_address": "123 Main Street",
                "address_line2": "Suite 5",
                "city": "Karachi",
                "state": "Sindh",
                "country": "Pakistan",
                "postal_code": "75500",
                "lat": "0",
                "long": "0",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.user.session_token}",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_address_exist)

        fetch_response = self.client.get(
            "/api/v1/users/me/address/",
            HTTP_AUTHORIZATION=f"Bearer {self.user.session_token}",
        )

        self.assertEqual(fetch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(fetch_response.data.get("city"), "Karachi")
        self.assertEqual(fetch_response.data.get("postal_code"), "75500")

    def test_v1_wallet_bank_create_and_transaction_list(self):
        create_response = self.client.post(
            "/api/v1/users/me/wallet/banks/",
            {
                "account_title": "Current User",
                "account_number": "1234567890",
                "bank_name": "Test Bank",
                "branch_code": "001",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.user.session_token}",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            UserBankAccount.objects.filter(bank_account_for_user=self.user).exists()
        )

        list_response = self.client.get(
            "/api/v1/users/me/wallet/transactions/",
            HTTP_AUTHORIZATION=f"Bearer {self.user.session_token}",
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0].get("transaction_code"), self.transaction.transaction_code)

    def test_v1_wallet_withdrawal_endpoint_reduces_wallet_balance(self):
        bank_account = UserBankAccount.objects.create(
            account_title="Current User",
            account_number="555444333",
            bank_name="Test Bank",
            branch_code="002",
            bank_account_for_user=self.user,
        )

        response = self.client.post(
            "/api/v1/users/me/wallet/withdrawals/",
            {
                "account_id": str(bank_account.account_id),
                "withdraw_amount": 1200,
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.user.session_token}",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_amount, 3800)
