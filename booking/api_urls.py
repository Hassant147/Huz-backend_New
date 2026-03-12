from django.urls import path
from rest_framework.routers import DefaultRouter

from .views.api_v1 import BookingViewSet
from .views.bookings import CurrentUserExistingBookingView
from .views.support import CurrentUserComplaintListView, CurrentUserRequestListView


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
]

urlpatterns += router.urls
