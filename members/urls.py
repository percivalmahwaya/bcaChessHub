from django.urls import path
from . import views
from . import lichess_views

urlpatterns = [
    path('', views.rankings, name='rankings'),
    path('signup/', views.signup, name='signup'),
    path('manage/', views.manage_members, name='manage_members'),
    path('admin/stats/', views.admin_stats, name='admin_stats'),
    path('profile/edit/', views.edit_profile, name='edit_profile'),
    path('profile/password/', views.change_password, name='change_password'),
    path('2fa/setup/', views.setup_2fa, name='setup_2fa'),
    path('2fa/disable/', views.disable_2fa, name='disable_2fa'),
    # Lichess. These sit above the <int:pk> catch-all deliberately: a path
    # segment that is not a number would never reach it, but keeping the
    # ordering obvious is cheaper than rediscovering why one day.
    path('lichess/start/', lichess_views.lichess_start, name='lichess_start'),
    path('lichess/callback/', lichess_views.lichess_callback, name='lichess_callback'),
    path('lichess/welcome/', lichess_views.lichess_finish_signup, name='lichess_finish_signup'),
    path('lichess/refresh/', lichess_views.lichess_refresh, name='lichess_refresh'),
    path('lichess/unlink/', lichess_views.lichess_unlink, name='lichess_unlink'),

    path('<int:pk>/', views.player_profile, name='player_profile'),
]
