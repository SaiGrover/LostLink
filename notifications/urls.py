from django.urls import path

from . import views

app_name = 'notifications'

urlpatterns = [
    path('', views.inbox, name='inbox'),
    path('open/<int:pk>/', views.open_note, name='open'),
    path('read/<int:pk>/', views.mark_read, name='read'),
    path('read-all/', views.mark_all_read, name='read_all'),
    path('unread-count/', views.unread_count, name='unread_count'),
]
