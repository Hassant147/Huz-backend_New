class LegacyAuthDeprecationHeaderMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        legacy_source = getattr(request, "_legacy_token_used", "")
        if legacy_source:
            response["X-Auth-Deprecated"] = legacy_source

        return response
