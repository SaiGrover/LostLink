from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('moderation/', views.moderation, name='moderation'),
    path('moderation/report/<int:pk>/<str:decision>/', views.review_report,
         name='review_report'),
    path('moderation/flag/<int:pk>/<str:decision>/', views.resolve_flag, name='resolve_flag'),
    path('moderation/photo/<int:pk>/<str:decision>/', views.photo_privacy, name='photo_privacy'),
    path('moderation/history/', views.decision_history, name='decisions'),
    path('analytics/', views.analytics, name='analytics'),
    path('users/', views.manage_users, name='users'),
    path('users/<int:pk>/status/', views.user_status, name='user_status'),
    path('taxonomy/', views.taxonomy, name='taxonomy'),
    path('taxonomy/<str:kind>/<int:pk>/edit/', views.taxonomy_edit, name='taxonomy_edit'),
    path('taxonomy/<str:kind>/<int:pk>/delete/', views.taxonomy_delete, name='taxonomy_delete'),
    path('export/csv/', views.export_csv, name='export_csv'),
    path('export/pdf/', views.export_pdf, name='export_pdf'),
]
