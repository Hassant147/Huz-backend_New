from django.urls import path

from . import approval_task, auth_views


urlpatterns = [
    path('auth/login/', auth_views.AdminLoginView.as_view()),
    path('auth/logout/', auth_views.AdminLogoutView.as_view()),
    path('auth/me/', auth_views.AdminSessionMeView.as_view()),
    path('approved_or_reject_company/', approval_task.ApprovedORRejectCompanyView.as_view()),
    path('fetch_all_pending_companies/', approval_task.GetAllPendingApprovalsView.as_view()),
    path('fetch_all_sale_directors/', approval_task.GetAllSaleDirectorsView.as_view()),
    path('approve_booking_payment/', approval_task.ApproveBookingPaymentView.as_view()),
    path('fetch_all_paid_bookings/', approval_task.FetchPaidBookingView.as_view()),
    path('fetch_all_partner_receive_able_payments_details/', approval_task.GetPartnerReceiveAblePaymentsView.as_view()),
    path('transfer_partner_receive_able_payments/', approval_task.ManagePartnerReceiveAblePaymentView.as_view()),
    path('manage_master_hotels/', approval_task.ManageMasterHotelsCatalogView.as_view()),
]
