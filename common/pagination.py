from django.conf import settings
from rest_framework.pagination import PageNumberPagination


class CustomPagination(PageNumberPagination):
    page_size = getattr(settings, "DEFAULT_API_PAGE_SIZE", 10)
    page_size_query_param = "page_size"
    max_page_size = 100
