from django.urls import path
from . import views

urlpatterns = [
    path("user-messages/<user_session_token>/", views.UserInbox.as_view()),
    path("partner-messages/<partner_session_token>/", views.PartnerInbox.as_view()),
    path("send-messages/", views.SendMessages.as_view()),
    path("get-messages/<user_session_token>/<partner_session_token>/", views.GetMessages.as_view()),
    path("delete-all-messages/", views.DeleteAllMessages.as_view()),
]