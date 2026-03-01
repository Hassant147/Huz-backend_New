from django.urls import path
from rest_framework.routers import DefaultRouter

from .views.api_v1 import BookingViewSet


router = DefaultRouter()
router.register("bookings", BookingViewSet, basename="v1-bookings")

current_user_booking_list = BookingViewSet.as_view({"get": "list"})


urlpatterns = [
    path("users/me/bookings/", current_user_booking_list, name="v1-user-bookings"),
]

urlpatterns += router.urls
