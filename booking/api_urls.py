from django.urls import path
from rest_framework.routers import DefaultRouter

from .views.api_v1 import BookingViewSet
from .views.bookings import CurrentUserExistingBookingView
from .views.support import CurrentUserComplaintListView, CurrentUserRequestListView
from . import manage_partner_booking


router = DefaultRouter()
router.register("bookings", BookingViewSet, basename="v1-bookings")

current_user_booking_list = BookingViewSet.as_view({"get": "list"})


urlpatterns = [
    path("users/me/bookings/", current_user_booking_list, name="v1-user-bookings"),
    path(
        "users/me/bookings/existing/",
        CurrentUserExistingBookingView.as_view(),
        name="v1-user-bookings-existing",
    ),
    path(
        "users/me/complaints/",
        CurrentUserComplaintListView.as_view(),
        name="v1-user-complaints",
    ),
    path(
        "users/me/requests/",
        CurrentUserRequestListView.as_view(),
        name="v1-user-requests",
    ),
    path("operator/bookings/", manage_partner_booking.GetBookingShortDetailForPartnersView.as_view(), name="v1-operator-bookings"),
    path("operator/bookings/detail/", manage_partner_booking.GetBookingDetailByBookingNumberForPartnerView.as_view(), name="v1-operator-booking-detail"),
    path("operator/bookings/action/", manage_partner_booking.TakeActionView.as_view(), name="v1-operator-booking-action"),
    path("operator/bookings/close/", manage_partner_booking.CloseBookingView.as_view(), name="v1-operator-booking-close"),
    path("operator/bookings/documents/", manage_partner_booking.ManageBookingDocumentsView.as_view(), name="v1-operator-booking-documents"),
    path("operator/bookings/document-delete/", manage_partner_booking.DeleteBookingDocumentsView.as_view(), name="v1-operator-booking-document-delete"),
    path("operator/bookings/airline-details/", manage_partner_booking.BookingAirlineDetailsView.as_view(), name="v1-operator-booking-airline"),
    path("operator/bookings/arrangements/", manage_partner_booking.BookingHotelAndTransportDetailsView.as_view(), name="v1-operator-booking-arrangements"),
    path("operator/bookings/issues/", manage_partner_booking.ReportBookingView.as_view(), name="v1-operator-booking-issues"),
    path("operator/bookings/ratings/summary/", manage_partner_booking.GetOverallRatingView.as_view(), name="v1-operator-rating-summary"),
    path("operator/bookings/ratings/package/", manage_partner_booking.GetRatingPackageWiseView.as_view(), name="v1-operator-rating-package"),
    path("operator/bookings/ratings/package-summary/", manage_partner_booking.GetPackageOverallRatingView.as_view(), name="v1-operator-rating-package-summary"),
    path("operator/bookings/complaints/", manage_partner_booking.GetPartnerComplaintsView.as_view(), name="v1-operator-complaints"),
    path("operator/bookings/complaints/detail/", manage_partner_booking.GetPartnerComplaintDetailView.as_view(), name="v1-operator-complaints-detail"),
    path("operator/bookings/complaints/summary/", manage_partner_booking.GetOverallPartnerComplaintsView.as_view(), name="v1-operator-complaints-summary"),
    path("operator/bookings/complaints/respond/", manage_partner_booking.GiveUpdateOnComplaintsView.as_view(), name="v1-operator-complaints-respond"),
    path("operator/bookings/statistics/", manage_partner_booking.GetPartnersOverallBookingStatisticsView.as_view(), name="v1-operator-booking-statistics"),
    path("operator/bookings/earnings/yearly/", manage_partner_booking.GetYearlyBookingStatisticsView.as_view(), name="v1-operator-booking-earnings"),
    path("operator/bookings/payments/", manage_partner_booking.PartnersBookingPaymentView.as_view(), name="v1-operator-booking-payments"),
    path("operator/dashboard/summary/", manage_partner_booking.GetOperatorDashboardSummaryView.as_view(), name="v1-operator-dashboard-summary"),
]

urlpatterns += router.urls
