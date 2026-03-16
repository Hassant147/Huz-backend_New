from django.urls import path

from . import manage_partner_booking

urlpatterns = [
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
    path('update_booking_status_into_close/', manage_partner_booking.CloseBookingView.as_view()),
    path('manage_traveler_issues/', manage_partner_booking.ReportBookingView.as_view()),
]
