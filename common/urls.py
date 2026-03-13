from django.urls import path

from . import user_profile

urlpatterns = [
    path('send_otp_sms/', user_profile.SendOTPSMSAPIView.as_view()),
    path('verify_otp/', user_profile.MatchOTPSMSAPIView.as_view()),
    path('is_user_exist/', user_profile.IsUserExistView.as_view()),
    path('manage_user_account/', user_profile.CreateMemberProfileView.as_view()),
    path('upload_user_photo/', user_profile.UploadUserImageView.as_view()),
    path('update_firebase_token/', user_profile.UpdateFirebaseTokenView.as_view()),
]
