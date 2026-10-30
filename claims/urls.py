from django.urls import path

from . import views

app_name = 'claims'

urlpatterns = [
    path('<int:pk>/return/', views.confirm_return, name='return'),
    path('<int:pk>/dispute/', views.dispute_claim, name='dispute'),
    path('start/<int:item_id>/', views.start_claim, name='start'),
    path('mine/', views.my_claims, name='mine'),
    path('<int:pk>/', views.claim_detail, name='detail'),
    path('<int:pk>/question/', views.add_question, name='add_question'),
    path('<int:pk>/answer/<int:qid>/', views.answer_question, name='answer'),
    path('<int:pk>/decide/<str:decision>/', views.decide_claim, name='decide'),
]
