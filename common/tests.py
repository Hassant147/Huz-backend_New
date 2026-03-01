from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate

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
