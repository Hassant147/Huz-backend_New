from django.urls import path

from .api_v1 import (
    CurrentUserAddressView,
    CurrentUserProfileView,
    CurrentUserWalletBankAccountsView,
    CurrentUserWalletTransactionsView,
    CurrentUserWalletWithdrawalsView,
)


urlpatterns = [
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
