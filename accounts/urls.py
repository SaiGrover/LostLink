from django.urls import path

from . import views

app_name = 'accounts'

urlpatterns = [
    path('register/', views.register, name='register'),
    path('login/', views.LostLinkLoginView.as_view(), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),

    # Email verification
    path('verify/', views.verification_pending, name='verification_pending'),
    path('verify/sent/', views.verification_sent, name='verification_sent'),
    path('verify/resend/', views.resend_verification, name='resend_verification'),
    path('verify/done/', views.verification_complete, name='verification_complete'),
    path('verify/<str:token>/', views.verify_email, name='verify_email'),

    # Password recovery
    path('password/reset/', views.PasswordResetView.as_view(), name='password_reset'),
    path('password/reset/sent/', views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('password/reset/<uidb64>/<token>/', views.PasswordResetConfirmView.as_view(),
         name='password_reset_confirm'),
    path('password/reset/done/', views.PasswordResetCompleteView.as_view(),
         name='password_reset_complete'),
]
