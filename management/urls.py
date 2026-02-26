from django.urls import path, re_path
from . import approval_task, admin_reports


urlpatterns = [
    path('approved_or_reject_company/', approval_task.ApprovedORRejectCompanyView.as_view()),
    path('fetch_all_pending_companies/', approval_task.GetAllPendingApprovalsView.as_view()),
    path('fetch_all_approved_companies/', approval_task.GetAllApprovedCompaniesView.as_view()),
    path('fetch_all_sale_directors/', approval_task.GetAllSaleDirectorsView.as_view()),
    path('approve_booking_payment/', approval_task.ApproveBookingPaymentView.as_view()),
    path('fetch_all_paid_bookings/', approval_task.FetchPaidBookingView.as_view()),
    path('manage_featured_package/', approval_task.ManageFeaturedPackageView.as_view()),
    path('fetch_all_partner_receive_able_payments_details/', approval_task.GetPartnerReceiveAblePaymentsView.as_view()),
    path('transfer_partner_receive_able_payments/', approval_task.ManagePartnerReceiveAblePaymentView.as_view()),
    path('manage_master_hotels/', approval_task.ManageMasterHotelsCatalogView.as_view()),

    path('partner_status_count/', admin_reports.PartnerStatusCountView.as_view()),
    path('top-five-partners-rating/', admin_reports.TopPartnersRatingAPIView.as_view()),
    path('top-five-partners-traveller/', admin_reports.TopOperatorsWithTravelerAPIView.as_view()),
    path('top-five-partners-bookings/', admin_reports.TopOperatorsWithBookingAPIView.as_view()),
    path('top-five-partners-business/', admin_reports.TopOperatorsWithBusinessAPIView.as_view()),
    path('top-five-partners-complaints/', admin_reports.TopPartnersComplaintsAPIView.as_view()),
    path('distinct-complaints-counts/', admin_reports.DistinctComplaintTitlesAPIView.as_view()),
    path('complaint-status-count/', admin_reports.ComplaintStatusCountAPIView.as_view()),
    path('travellers-with-each-airlines/', admin_reports.BookingWithEachAirlineAPIView.as_view()),
    path('all-booking-status/', admin_reports.BookingStatusCountAPIView.as_view()),
    path('all-packages-status/', admin_reports.PackageStatusCountAPIView.as_view()),
    path('register-users-count/', admin_reports.UserRegistrationCountAPIView.as_view()),
    path('count-of-bookings-with-their-prices/', admin_reports.BookingTypeStatusCountWithPriceAPIView.as_view()),
    path('total-booking-with-traveller-and-finance/', admin_reports.BookingStatsByPackageAPIView.as_view()),

]
