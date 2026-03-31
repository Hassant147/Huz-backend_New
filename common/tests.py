import importlib
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import clear_url_caches
from uuid import uuid4
from rest_framework import serializers, status
from rest_framework.test import APIRequestFactory, APITestCase

from .models import MailingDetail, UserBankAccount, UserOTP, UserProfile, UserTransactionHistory, Wallet
from .phone_utils import resolve_phone_identity
from .serializers import UserProfileSerializer
from .utility import _send_email
from .user_profile import CreateMemberProfileView, SendOTPSMSAPIView, send_otp_via_sms_gateway
from huz import urls as huz_urls


class PhoneIdentityTests(SimpleTestCase):
    def test_resolve_phone_identity_normalizes_countries_with_national_trunk_prefixes(self):
        gb_identity = resolve_phone_identity(
            phone_number="+447400123456",
            country_code="+44",
            country_iso_code="GB",
            local_phone_number="07400123456",
        )
        it_identity = resolve_phone_identity(
            phone_number="+3903123456789",
            country_code="+39",
            country_iso_code="IT",
            local_phone_number="03123456789",
        )

        self.assertEqual(gb_identity["local_phone_number"], "7400123456")
        self.assertEqual(gb_identity["full_phone_number"], "+447400123456")
        self.assertEqual(it_identity["local_phone_number"], "03123456789")
        self.assertEqual(it_identity["full_phone_number"], "+3903123456789")

    def test_resolve_phone_identity_rejects_numbers_outside_selected_country(self):
        with self.assertRaisesMessage(
            serializers.ValidationError,
            "Phone number does not match the selected country.",
        ):
            resolve_phone_identity(
                phone_number="+15062345678",
                country_code="+1",
                country_iso_code="US",
                local_phone_number="5062345678",
            )


class SendOTPSMSAPIViewThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()

    def tearDown(self):
        cache.clear()
        super().tearDown()

    @patch("common.user_profile.config", return_value="test-api-key")
    @patch("common.user_profile.requests.post")
    def test_send_otp_sms_is_rate_limited(self, mocked_post, _mocked_config):
        mocked_post.return_value = Mock(status_code=200)
        view = SendOTPSMSAPIView.as_view()

        for _ in range(3):
            request = self.factory.post(
                "/common/send_otp_sms/",
                {"phone_number": "+14155552671"},
                format="json",
            )
            response = view(request)
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        throttled_request = self.factory.post(
            "/common/send_otp_sms/",
            {"phone_number": "+14155552671"},
            format="json",
        )
        throttled_response = view(throttled_request)

        self.assertEqual(throttled_response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class OTPGatewayRequestTests(APITestCase):
    @patch("common.user_profile.upsert_user_otp")
    @patch("common.user_profile.requests.post")
    @patch("common.user_profile.random_six_digits", return_value="123456")
    @patch(
        "common.user_profile.config",
        side_effect=lambda key, default='': {
            "SMS_GATEWAY_API_KEY": "",
            "APIKey": "legacy-api-key",
        }.get(key, default),
    )
    def test_send_otp_uses_encoded_query_params_and_legacy_api_key_fallback(
        self,
        _mocked_config,
        _mocked_random_six_digits,
        mocked_post,
        mocked_upsert_user_otp,
    ):
        mocked_post.return_value = Mock(status_code=200, text="ok")

        send_otp_via_sms_gateway("+923395690614")

        mocked_post.assert_called_once()
        call_args, call_kwargs = mocked_post.call_args
        self.assertEqual(
            call_args[0],
            "https://api.veevotech.com/v3/sendsms",
        )
        self.assertEqual(call_kwargs["params"]["hash"], "legacy-api-key")
        self.assertEqual(call_kwargs["params"]["receivernum"], "+923395690614")
        self.assertIn("123456", call_kwargs["params"]["textmessage"])
        self.assertEqual(call_kwargs["timeout"], 6)
        mocked_upsert_user_otp.assert_called_once_with("+923395690614", "123456")


class EmailUtilityTests(SimpleTestCase):
    @override_settings(
        EMAIL_DELIVERY_BACKEND="smtp",
        EMAIL_ADDRESS="HajjUmrah.co <no-reply@example.com>",
        EMAIL_ENVELOPE_SENDER="",
        EMAIL_HOST="smtp.hostinger.com",
        EMAIL_PORT=465,
        SERVER_EMAIL="no-reply@example.com",
        SERVER_EMAIL_PASSWORD="secret",
        EMAIL_SEND_TIMEOUT_SECONDS=20,
        EMAIL_USE_SSL=True,
        EMAIL_USE_TLS=False,
        EMAIL_STARTTLS_PORT=587,
        EMAIL_ALLOW_STARTTLS_FALLBACK=True,
        EMAIL_LOCAL_HOSTNAME="",
    )
    @patch("common.utility.smtplib.SMTP")
    @patch("common.utility.smtplib.SMTP_SSL", side_effect=TimeoutError("ssl timeout"))
    def test_send_email_retries_with_starttls_when_ssl_transport_times_out(
        self,
        mocked_smtp_ssl,
        mocked_smtp,
    ):
        smtp_connection = mocked_smtp.return_value
        smtp_instance = smtp_connection.__enter__.return_value

        result = _send_email("recipient@example.com", "OTP test", "<p>otp</p>")

        self.assertTrue(result)
        mocked_smtp_ssl.assert_called_once()
        mocked_smtp.assert_called_once_with(
            host="smtp.hostinger.com",
            port=587,
            timeout=20,
        )
        smtp_connection.starttls.assert_called_once()
        smtp_instance.login.assert_called_once_with("no-reply@example.com", "secret")
        smtp_instance.sendmail.assert_called_once()
        sendmail_args = smtp_instance.sendmail.call_args[0]
        self.assertEqual(sendmail_args[0], "no-reply@example.com")
        self.assertEqual(sendmail_args[1], ["recipient@example.com"])

    @override_settings(
        EMAIL_DELIVERY_BACKEND="smtp",
        EMAIL_ADDRESS="HajjUmrah.co <no-reply@example.com>",
        EMAIL_ENVELOPE_SENDER="",
        EMAIL_HOST="smtp.hostinger.com",
        EMAIL_PORT=465,
        SERVER_EMAIL="no-reply@example.com",
        SERVER_EMAIL_PASSWORD="secret",
        EMAIL_SEND_TIMEOUT_SECONDS=20,
        EMAIL_USE_SSL=True,
        EMAIL_USE_TLS=False,
        EMAIL_STARTTLS_PORT=587,
        EMAIL_ALLOW_STARTTLS_FALLBACK=True,
        EMAIL_LOCAL_HOSTNAME="",
    )
    @patch("common.utility.smtplib.SMTP")
    @patch("common.utility.smtplib.SMTP_SSL")
    def test_send_email_rejects_blank_recipient_before_opening_smtp_connection(
        self,
        mocked_smtp_ssl,
        mocked_smtp,
    ):
        result = _send_email(None, "OTP test", "<p>otp</p>")

        self.assertFalse(result)
        mocked_smtp_ssl.assert_not_called()
        mocked_smtp.assert_not_called()


class CreateMemberProfileViewTransactionTests(APITestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    @patch("common.user_profile.save_notification", return_value="Success")
    @patch("common.user_profile.send_sms_gateway_request")
    def test_create_member_profile_normalizes_pakistan_number_with_leading_zero(
        self,
        mocked_send_sms_gateway_request,
        _mocked_save_notification,
    ):
        mocked_send_sms_gateway_request.return_value = Mock(status_code=200)
        request = self.factory.post(
            "/common/manage_user_account/",
            {
                "phone_number": "+923395690614",
                "country_code": "+92",
                "local_phone_number": "03395690614",
                "name": "Pakistan User",
                "email": "pakistan-user@example.com",
                "user_type": "user",
            },
            format="json",
        )

        response = CreateMemberProfileView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created_user = UserProfile.objects.get(email="pakistan-user@example.com")
        self.assertEqual(created_user.country_code, "+92")
        self.assertEqual(created_user.phone_number, "3395690614")

    @patch("common.user_profile.save_notification", return_value="Success")
    @patch("common.user_profile.send_sms_gateway_request")
    def test_create_member_profile_accepts_non_pakistani_country_codes(
        self,
        mocked_send_sms_gateway_request,
        _mocked_save_notification,
    ):
        mocked_send_sms_gateway_request.return_value = Mock(status_code=200)
        request = self.factory.post(
            "/common/manage_user_account/",
            {
                "phone_number": "+14155552671",
                "country_code": "+1",
                "local_phone_number": "4155552671",
                "name": "Test User",
                "email": "global-user@example.com",
                "user_type": "user",
            },
            format="json",
        )

        response = CreateMemberProfileView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created_user = UserProfile.objects.get(email="global-user@example.com")
        self.assertEqual(created_user.country_code, "+1")
        self.assertEqual(created_user.phone_number, "4155552671")
        self.assertNotIn("session_token", response.data.get("data", {}))
        self.assertEqual(Wallet.objects.count(), 1)

    @patch("common.user_profile.save_notification", return_value="Success")
    @patch("common.user_profile.send_sms_gateway_request")
    def test_create_member_profile_rolls_back_when_sms_delivery_fails(
        self,
        mocked_send_sms_gateway_request,
        _mocked_save_notification,
    ):
        mocked_send_sms_gateway_request.return_value = Mock(status_code=500)
        request = self.factory.post(
            "/common/manage_user_account/",
            {
                "phone_number": "+14155552671",
                "country_code": "+1",
                "local_phone_number": "4155552671",
                "name": "Test User",
                "email": "sms-failure@example.com",
                "user_type": "user",
            },
            format="json",
        )

        response = CreateMemberProfileView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(
            response.data.get("message"),
            "Failed to send OTP. Please try again later.",
        )
        self.assertFalse(UserProfile.objects.filter(email="sms-failure@example.com").exists())
        self.assertEqual(Wallet.objects.count(), 0)


class PublicAuthContractTests(APITestCase):
    def setUp(self):
        self.user = UserProfile.objects.create(
            session_token=f"auth-contract-session-{uuid4().hex[:8]}",
            name="Contract User",
            country_code="+1",
            phone_number="4155552671",
            email="contract-user@example.com",
            user_type="user",
        )
        Wallet.objects.create(
            wallet_code=f"auth-contract-wallet-{uuid4().hex[:8]}",
            wallet_amount=0,
            wallet_session=self.user,
        )

    def test_is_user_exist_does_not_leak_session_token(self):
        response = self.client.post(
            "/common/is_user_exist/",
            {
                "phone_number": "+14155552671",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("exists"), True)
        self.assertNotIn("session_token", response.data)

    def test_verify_otp_returns_authenticated_profile_after_match(self):
        UserOTP.objects.create(phone_number="+14155552671", otp_password="654321")

        response = self.client.put(
            "/common/verify_otp/",
            {
                "phone_number": "+14155552671",
                "otp_password": "654321",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data.get("data", {}).get("session_token"),
            self.user.session_token,
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_phone_verified)

    def test_verify_otp_normalizes_pakistan_number_with_trunk_zero(self):
        pakistan_user = UserProfile.objects.create(
            session_token=f"auth-contract-pk-session-{uuid4().hex[:8]}",
            name="Pakistan User",
            country_code="+92",
            phone_number="3395690614",
            email="pakistan-contract-user@example.com",
            user_type="user",
        )
        Wallet.objects.create(
            wallet_code=f"auth-contract-pk-wallet-{uuid4().hex[:8]}",
            wallet_amount=0,
            wallet_session=pakistan_user,
        )
        UserOTP.objects.create(phone_number="+923395690614", otp_password="123456")

        response = self.client.put(
            "/common/verify_otp/",
            {
                "phone_number": "+9203395690614",
                "otp_password": "123456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data.get("data", {}).get("session_token"),
            pakistan_user.session_token,
        )

    @override_settings(DEBUG=True)
    def test_verify_otp_does_not_accept_the_old_debug_bypass_code(self):
        response = self.client.put(
            "/common/verify_otp/",
            {
                "phone_number": "+14155552671",
                "otp_password": "123456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data.get("message"),
            "OTP not found for this phone number.",
        )


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


class PublicUrlExposureTests(SimpleTestCase):
    def _reload_urlconf(self):
        clear_url_caches()
        return importlib.reload(huz_urls)

    @override_settings(ENABLE_API_DOCS=False, SERVE_MEDIA_AND_STATIC_FROM_DJANGO=False)
    def test_public_docs_and_asset_routes_are_disabled_by_default(self):
        self.addCleanup(self._reload_urlconf)
        module = self._reload_urlconf()
        routes = {str(pattern.pattern) for pattern in module.urlpatterns}

        self.assertNotIn("huz_swagger/", routes)
        self.assertNotIn("huz_redoc/", routes)
        self.assertFalse(any(route.startswith("^media/") for route in routes))
        self.assertFalse(any(route.startswith("^static/") for route in routes))

    @override_settings(ENABLE_API_DOCS=True, SERVE_MEDIA_AND_STATIC_FROM_DJANGO=True)
    def test_public_docs_and_asset_routes_can_be_enabled_explicitly(self):
        self.addCleanup(self._reload_urlconf)
        module = self._reload_urlconf()
        routes = {str(pattern.pattern) for pattern in module.urlpatterns}

        self.assertIn("huz_swagger/", routes)
        self.assertIn("huz_redoc/", routes)
        self.assertTrue(any(route.startswith("^media/") for route in routes))
        self.assertTrue(any(route.startswith("^static/") for route in routes))


class UserProfileSerializerQueryTests(APITestCase):
    def test_wallet_amount_uses_prefetched_wallet_relation(self):
        user = UserProfile.objects.create(
            session_token=f"wallet-prefetch-session-{uuid4().hex[:8]}",
            name="Wallet Prefetch User",
            country_code="+1",
            phone_number="9998887777",
            email="wallet-prefetch@example.com",
            user_type="user",
        )
        wallet = Wallet.objects.create(
            wallet_code=f"wallet-prefetch-{uuid4().hex[:8]}",
            wallet_amount=3210,
            wallet_session=user,
        )
        prefetched_user = UserProfile.objects.prefetch_related("wallet_session").get(pk=user.pk)

        with CaptureQueriesContext(connection) as queries:
            data = UserProfileSerializer(prefetched_user).data

        self.assertEqual(len(queries), 0)
        self.assertEqual(data.get("wallet_amount"), wallet.wallet_amount)
