from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from drf_yasg.generators import OpenAPISchemaGenerator
from collections import OrderedDict
from rest_framework import permissions


class CustomSchemaGenerator(OpenAPISchemaGenerator):
    def get_paths_object(self, paths):
        ordered_paths = OrderedDict()
        custom_order = [
            '/send_otp_sms/',
            '/verify_otp/',
            '/send_otp_email/',
            '/verify_otp_email/',
            '/is_user_exist/',
            '/manage_user_account/',
            '/upload_user_photo/',
            '/update_firebase_token/',
            '/update_user_name/',
            '/update_user_gender/',
            '/update_user_email/',
            '/manage_user_address_detail/',
            '/manage_user_withdraw_request/',
            '/manage_user_bank_account/',
            '/get_user_all_transaction_history/',
            '/get_user_overall_transaction_summary/',
        ]

        for path in custom_order:
            if path in paths:
                ordered_paths[path] = paths[path]

            # Add any paths that weren't explicitly ordered
        for path, path_item in paths.items():
            if path not in ordered_paths:
                ordered_paths[path] = path_item

        return super().get_paths_object(ordered_paths)


schema_view_common = get_schema_view(
    openapi.Info(
        title="Common App API",
        default_version='v1',
        description="API documentation for Common App",
        terms_of_service="https://www.example.com/policies/terms/",
        contact=openapi.Contact(email="contact@example.com"),
        license=openapi.License(name="BSD License"),
    ),
    public=True,
    permission_classes=[permissions.AllowAny],
    generator_class=CustomSchemaGenerator
)

