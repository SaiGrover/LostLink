from django.urls import path

from . import views

app_name = 'items'

urlpatterns = [
    path('', views.home, name='home'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('report/', views.report_item, name='report'),
    path('search/', views.search, name='search'),
    path('search/api/', views.search_api, name='search_api'),
    path('lost/', views.browse, {'report_type': 'lost'}, name='lost'),
    path('found/', views.browse, {'report_type': 'found'}, name='found'),
    path('mine/', views.my_items, name='mine'),
    path('item/<int:pk>/', views.item_detail, name='detail'),
    path('item/<int:pk>/edit/', views.edit_item, name='edit'),
    path('item/<int:pk>/withdraw/', views.withdraw_item, name='withdraw'),
    path('item/<int:pk>/flag/', views.flag_item, name='flag'),
]
