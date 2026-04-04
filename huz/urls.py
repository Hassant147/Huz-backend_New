from django.conf import settings
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve


urlpatterns = [
    path('api/v1/', include('booking.api_urls')),
    path('api/v1/', include('common.api_urls')),
    path('api/v1/', include('partners.api_urls')),
    path('api/v1/', include('management.api_urls')),
    # path('huz_team/', include('team.urls')),
    path('management/', include('management.urls')),
    path('admin/', admin.site.urls),
]

if settings.ENABLE_API_DOCS:
    from drf_yasg import openapi
    from drf_yasg.views import get_schema_view
    from rest_framework import permissions

    schema_view = get_schema_view(
        openapi.Info(
            title="Your API",
            default_version='v1',
            description="API documentation",
            terms_of_service="https://www.google.com/policies/terms/",
            contact=openapi.Contact(email="contact@yourapi.local"),
            license=openapi.License(name="BSD License"),
        ),
        public=True,
        permission_classes=(permissions.AllowAny,),
    )

    urlpatterns += [
        path('huz_swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
        path('huz_redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    ]

if settings.SERVE_MEDIA_AND_STATIC_FROM_DJANGO:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
        re_path(r'^static/(?P<path>.*)$', serve, {'document_root': settings.STATIC_ROOT}),
    ]
