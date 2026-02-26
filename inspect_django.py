import os
import sys
import django
import json

base_dir = "/Users/macbook/Desktop/Huz/Huz-Backend"
sys.path.append(base_dir)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'huz.settings')
try:
    django.setup()
    from django.urls import get_resolver, URLPattern, URLResolver
    
    def get_urls(resolver=None, prefix=''):
        urls = []
        if resolver is None:
            resolver = get_resolver()
        for pattern in resolver.url_patterns:
            if isinstance(pattern, URLPattern):
                url = prefix + str(pattern.pattern)
                view_name = pattern.callback.__module__ + '.' + pattern.callback.__name__ if hasattr(pattern.callback, '__name__') else str(pattern.callback)
                urls.append({
                    "path": url,
                    "view": view_name,
                    "name": pattern.name
                })
            elif isinstance(pattern, URLResolver):
                url = prefix + str(pattern.pattern)
                urls.extend(get_urls(pattern, url))
        return urls
        
        with open('/Users/macbook/Desktop/Huz/api_inventory_raw.json', 'w') as f:
            json.dump(get_urls(), f, indent=2)
except Exception as e:
    print(f"Error: {e}")
