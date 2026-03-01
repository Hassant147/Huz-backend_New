from django.urls import path
from . import manage_bookings, manage_partner_booking, custom_request
from .views import bookings as booking_views, passports as passport_views, payments as payment_views

urlpatterns = [
    # User Section
    path('create_booking_view/', booking_views.ManageBookingsView.as_view()),
    path('check_passport_validity/', passport_views.ManagePassportValidityView.as_view()),
    path('get_all_booking_short_detail_by_user/', booking_views.GetAllBookingsByUserView.as_view()),
    path('pay_booking_amount_by_transaction_number/', payment_views.PaidAmountByTransactionNumberView.as_view()),
    path('pay_booking_amount_by_transaction_photo/', payment_views.PaidAmountTransactionPhotoView.as_view()),
    path('delete_payment_record/', payment_views.DeleteAmountTransactionPhotoView.as_view()),
    # path('manage_passport_and_photo/', manage_bookings.ManageUserRequiredDocumentsView.as_view()),
    path('manage_user_passport/', manage_bookings.ManageUserPassportView.as_view()),
    path('manage_user_passport_photo/', manage_bookings.ManageUserPassportPhotoView.as_view()),
    # path('delete_passport_or_photo/', manage_bookings.DeleteUserRequiredDocumentsView.as_view()),
    path('rating_and_review/', manage_bookings.BookingRatingAndReviewView.as_view()),
    path('raise_complaint_booking_wise/', manage_bookings.BookingComplaintsView.as_view()),
    path('get_all_complaints_by_user/', manage_bookings.GetUserComplaintsView.as_view()),
    path('objection_response_by_user/', manage_bookings.ObjectionResponseView.as_view()),
    path('raise_a_request/', manage_bookings.BookingRequestView.as_view()),
    path('get_user_all_request/', manage_bookings.GetUserRequestsView.as_view()),

    # Partner Section
    path('get_all_booking_detail_for_partner/', manage_partner_booking.GetBookingShortDetailForPartnersView.as_view()),
    path('get_booking_detail_by_booking_number/', manage_partner_booking.GetBookingDetailByBookingNumberForPartnerView.as_view()),
    path('partner_action_for_booking/', manage_partner_booking.TakeActionView.as_view()),
    path('manage_booking_documents/', manage_partner_booking.ManageBookingDocumentsView.as_view()),
    path('delete_booking_documents/', manage_partner_booking.DeleteBookingDocumentsView.as_view()),
    path('manage_booking_airline_details/', manage_partner_booking.BookingAirlineDetailsView.as_view()),
    path('manage_booking_hotel_or_transport_details/', manage_partner_booking.BookingHotelAndTransportDetailsView.as_view()),
    path('get_overall_partner_rating/', manage_partner_booking.GetOverallRatingView.as_view()),
    path('get_rating_and_review_package_wise/', manage_partner_booking.GetRatingPackageWiseView.as_view()),
    path('get_overall_rating_package_wise/', manage_partner_booking.GetPackageOverallRatingView.as_view()),
    path('get_overall_complaints_counts/', manage_partner_booking.GetOverallPartnerComplaintsView.as_view()),
    path('get_all_complaints_for_partner/', manage_partner_booking.GetPartnerComplaintsView.as_view()),
    path('give_feedback_on_complaints/', manage_partner_booking.GiveUpdateOnComplaintsView.as_view()),
    path('get_overall_booking_statistics/', manage_partner_booking.GetPartnersOverallBookingStatisticsView.as_view()),
    path('get_yearly_earning_statistics/', manage_partner_booking.GetYearlyBookingStatisticsView.as_view()),
    path('get_receivable_payment_statistics/', manage_partner_booking.PartnersBookingPaymentView.as_view()),
    path('manage_user_check_in/', manage_bookings.ManageHotelCheckIn.as_view()),
    path('update_booking_status_into_close/', manage_partner_booking.CloseBookingView.as_view()),
    path('update_booking_status_into_report_rabbit/', manage_partner_booking.ReportBookingView.as_view()),

    path('manage_custom_package/', custom_request.CustomPackageAPIView.as_view(), name='manage_custom_package')
]
