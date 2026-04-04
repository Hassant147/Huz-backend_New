from django.urls import path

from .api_v1 import (
    CurrentUserAddressView,
    CurrentUserProfileView,
    CurrentUserWalletBankAccountsView,
    CurrentUserWalletTransactionsView,
    CurrentUserWalletWithdrawalsView,
)
from .user_profile import (
    CreateMemberProfileView,
    IsUserExistView,
    MatchOTPSMSAPIView,
    SendOTPSMSAPIView,
)


urlpatterns = [
    path("auth/users/exists/", IsUserExistView.as_view(), name="v1-auth-user-exists"),
    path("auth/otp/send/", SendOTPSMSAPIView.as_view(), name="v1-auth-otp-send"),
    path("auth/otp/verify/", MatchOTPSMSAPIView.as_view(), name="v1-auth-otp-verify"),
    path("auth/accounts/", CreateMemberProfileView.as_view(), name="v1-auth-accounts"),
    path("users/me/profile/", CurrentUserProfileView.as_view(), name="v1-user-profile"),
    path("users/me/address/", CurrentUserAddressView.as_view(), name="v1-user-address"),
    path(
        "users/me/wallet/banks/",
        CurrentUserWalletBankAccountsView.as_view(),
        name="v1-user-wallet-banks",
    ),
    path(
        "users/me/wallet/withdrawals/",
        CurrentUserWalletWithdrawalsView.as_view(),
        name="v1-user-wallet-withdrawals",
    ),
    path(
        "users/me/wallet/transactions/",
        CurrentUserWalletTransactionsView.as_view(),
        name="v1-user-wallet-transactions",
    ),
]
