from django.urls import path, re_path
from . import user_profile, accounts_and_transactions, views

urlpatterns = [
    path('user_subscribe/', user_profile.SubscribeAPIView.as_view()),
    path('send_otp_sms/', user_profile.SendOTPSMSAPIView.as_view()),
    path('verify_otp/', user_profile.MatchOTPSMSAPIView.as_view()),
    path('send_otp_email/', user_profile.SendEmailOTPView.as_view()),
    path('verify_otp_email/', user_profile.MatchEmailOTPView.as_view()),
    path('is_user_exist/', user_profile.IsUserExistView.as_view()),
    path('manage_user_account/', user_profile.CreateMemberProfileView.as_view()),
    path('upload_user_photo/', user_profile.UploadUserImageView.as_view()),
    path('update_firebase_token/', user_profile.UpdateFirebaseTokenView.as_view()),
    path('update_user_name/', user_profile.UpdateUserNameView.as_view()),
    path('update_user_gender/', user_profile.UpdateUserGenderView.as_view()),
    path('update_user_email/', user_profile.UpdateEmailAddressView.as_view()),
    path('manage_user_address_detail/', user_profile.ManageUserAddressView.as_view()),
    # User account and Transactions
    path('manage_user_withdraw_request/', accounts_and_transactions.ManageWithdrawView.as_view()),
    path('manage_user_bank_account/', accounts_and_transactions.ManageBankAccountView.as_view()),
    path('get_user_all_transaction_history/', accounts_and_transactions.GetUserAllTransactionHistoryView.as_view()),
    path('get_user_overall_transaction_summary/', accounts_and_transactions.GetUserTransactionOverallSummaryView.as_view()),

    # path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    # path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    # re_path(r'^swagger(?P<format>\.json|\.yaml)$', schema_view.without_ui(cache_timeout=0), name='schema-json'),
]

# # Include schema view for common app
# schema_url_patterns_common = [
#     path('swagger/', views.schema_view_common.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui-common'),
# ]
#
# urlpatterns += schema_url_patterns_common  # Append schema URLs for common app