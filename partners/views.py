from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from drf_yasg.generators import OpenAPISchemaGenerator
from collections import OrderedDict
from rest_framework import permissions


class CustomSchemaGenerator(OpenAPISchemaGenerator):
    def get_paths_object(self, paths):
        ordered_paths = OrderedDict()
        custom_order = [
            '/partner_login/',
            '/is_user_exist/',
            '/create_partner_profile/',
            '/get_partner_profile/',
            '/resend_otp/',
            '/verify_otp/',
            '/partner_service/',
            '/register_as_individual/',
            '/update_individual_partner_profile/',
            '/register_as_company/',
            '/update_partner_company_profile/',
            '/check_username_exist/',
            '/get_partner_address_detail/',
            '/update_partner_address_detail/',
            '/update_company_logo/',
            '/change_partner_password/',
        ]

        for path in custom_order:
            if path in paths:
                ordered_paths[path] = paths[path]

        for path, path_item in paths.items():
            if path not in ordered_paths:
                ordered_paths[path] = path_item

        return super().get_paths_object(ordered_paths)


schema_view_partner = get_schema_view(
    openapi.Info(
        title="Partner App API",
        default_version='v1',
        description="API documentation for Partner App",
        terms_of_service="https://www.example.com/policies/terms/",
        contact=openapi.Contact(email="contact@example.com"),
        license=openapi.License(name="BSD License"),
    ),
    public=True,
    permission_classes=[permissions.AllowAny],
    generator_class=CustomSchemaGenerator
)
