from django.urls import path, re_path
from . import (
    forgot_password,
    package_management,
    package_management_operator,
    partner_accounts_and_transactions,
    partner_profile,
    views,
)


urlpatterns = [
    path('forgot_password_request/', forgot_password.ForgotEmail.as_view()),
    path('update_forgot_password_request/', forgot_password.UpdatePassword.as_view()),

    path('partner_login/', partner_profile.PartnerLoginView.as_view()),
    path('is_user_exist/', partner_profile.IsPartnerExistView.as_view()),
    path('create_partner_profile/', partner_profile.CreatePartnerProfileView.as_view()),
    path('get_partner_profile/', partner_profile.GetPartnerProfileView.as_view()),
    path('resend_otp/', partner_profile.SendEmailOTPView.as_view()),
    path('verify_otp/', partner_profile.MatchEmailOTPView.as_view()),
    path('partner_service/', partner_profile.PartnerServicesView.as_view()),
    path('register_as_individual/', partner_profile.IndividualPartnerView.as_view()),
    path('update_individual_partner_profile/', partner_profile.UpdatePartnerIndividualProfileView.as_view()),
    path('register_as_company/', partner_profile.BusinessPartnerView.as_view()),
    path('update_partner_company_profile/', partner_profile.UpdateBusinessProfileView.as_view()),
    path('check_username_exist/', partner_profile.CheckPartnerUsernameAvailabilityView.as_view()),
    path('get_partner_address_detail/', partner_profile.GetPartnerAddressView.as_view()),
    path('update_partner_address_detail/', partner_profile.UpdatePartnerAddressView.as_view()),
    path('update_company_logo/', partner_profile.UpdateCompanyLogoView.as_view()),
    path('change_partner_password/', partner_profile.ChangePasswordView.as_view()),

    # Package Management
    path('enroll_package_basic_detail/', package_management_operator.CreateHuzPackageView.as_view()),
    path('enroll_package_airline_detail/', package_management_operator.CreateHuzAirlineView.as_view()),
    path('enroll_package_transport_detail/', package_management_operator.CreateHuzTransportView.as_view()),
    path('enroll_package_hotel_detail/', package_management_operator.CreateHuzHotelView.as_view()),
    path('enroll_package_ziyarah_detail/', package_management_operator.CreateHuzZiyarahView.as_view()),
    path('change_huz_package_status/', package_management_operator.ManageHuzPackageStatusView.as_view()),
    path('get_package_short_detail_by_partner_token/', package_management_operator.GetHuzShortPackageByTokenView.as_view()),
    path('get_package_detail_by_partner_token/', package_management_operator.GetHuzPackageDetailByTokenView.as_view()),
    path('get_partner_overall_package_statistics/', package_management_operator.GetPartnersOverallPackagesStatisticsView.as_view()),
    path('get_all_hotels_with_images/', package_management_operator.GetAllHotelsWithImagesView.as_view()),

    # For Website only
    path('get_package_short_detail_for_web/', package_management.GetHuzShortPackageForWebsiteView.as_view()),
    path('get_package_detail_by_package_id_for_web/', package_management.GetHuzPackageDetailForWebsiteView.as_view()),
    path('get_city_wise_packages_count/', package_management.GetPackageCountCitiesWiseForWebsiteView.as_view()),
    path('get_featured_packages/', package_management.GetHuzFeaturedPackageForWebsiteView.as_view()),
    path('get_package_detail_by_city_and_date/', package_management.GetSearchPackageByCityNDateView.as_view()),

    # For Partner Accounts and Bank Statement
    path('manage_partner_bank_account/', partner_accounts_and_transactions.ManagePartnerBankAccountView.as_view()),
    path('manage_partner_withdraw_request/', partner_accounts_and_transactions.ManagePartnerWithdrawView.as_view()),
    path('get_partner_all_transaction_history/', partner_accounts_and_transactions.GetPartnerAllTransactionHistoryView.as_view()),
    path('get_partner_over_transaction_amount/', partner_accounts_and_transactions.GetPartnerTransactionOverallSummaryView.as_view()),


    # path('partner_swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    # path('partner_redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    # re_path(r'^swagger(?P<format>\.json|\.yaml)$', schema_view.without_ui(cache_timeout=0), name='schema-json'),
]

# Include schema view for partner app
# schema_url_patterns_partner = [
#     path('swagger/', views.schema_view_partner.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui-partner'),
# ]
#
# urlpatterns += schema_url_patterns_partner  # Append schema URLs for partner app
