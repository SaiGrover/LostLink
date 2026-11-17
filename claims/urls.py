from django.urls import path

from . import views

app_name = 'claims'

urlpatterns = [
    path('start/<int:item_id>/', views.start_claim, name='start'),
    path('mine/', views.my_claims, name='mine'),
    path('<int:pk>/', views.claim_detail, name='detail'),
    path('<int:pk>/question/', views.add_question, name='add_question'),
    path('<int:pk>/answer/<int:qid>/', views.answer_question, name='answer'),
    path('<int:pk>/decide/<str:decision>/', views.decide_claim, name='decide'),
    path('<int:pk>/withdraw/', views.withdraw_claim, name='withdraw'),
    path('<int:pk>/reconsider/', views.appeal_claim, name='appeal'),
    path('<int:pk>/evidence/', views.add_evidence, name='add_evidence'),
    path('<int:pk>/evidence/<int:eid>/', views.evidence_file, name='evidence_file'),
    path('<int:pk>/dispute/', views.dispute_claim, name='dispute'),
    path('<int:pk>/dispute/resolve/', views.resolve_dispute, name='resolve_dispute'),
    path('<int:pk>/handover/code/', views.issue_code, name='issue_code'),
    path('<int:pk>/handover/verify/', views.verify_code, name='verify_code'),
    # Documented moderator exception (the normal path is the owner's code).
    path('<int:pk>/return/', views.confirm_return, name='return'),
]
