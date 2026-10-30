from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('moderation/', views.moderation, name='moderation'),
    path('moderation/report/<int:pk>/<str:decision>/', views.review_report,
         name='review_report'),
    path('analytics/', views.analytics, name='analytics'),
    path('users/', views.manage_users, name='users'),
    path('taxonomy/', views.taxonomy, name='taxonomy'),
    path('export/csv/', views.export_csv, name='export_csv'),
    path('export/pdf/', views.export_pdf, name='export_pdf'),
]
