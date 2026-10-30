from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from items.views import serve_media

urlpatterns = [
    path('media/<path:path>', serve_media, name='protected_media'),
    path('django-admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('claims/', include('claims.urls')),
    path('notifications/', include('notifications.urls')),
    path('manage/', include('dashboard.urls')),
    path('', include('items.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / 'static')

admin.site.site_header = 'LostLink Administration'
admin.site.site_title = 'LostLink'
admin.site.index_title = 'Platform management'
