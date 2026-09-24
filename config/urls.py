from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from core.views import home, security, site_search
from members.views import dashboard, verify_2fa

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home, name='home'),
    path('security/', security, name='security'),
    path('search/', site_search, name='search'),
    path('tournaments/', include('tournaments.urls')),
    path('news/', include('news.urls')),
    path('clubs/', include('associations.urls')),
    # The old path, live and linked from a deployed navigation bar since
    # June. A permanent redirect rather than a 404, because somebody's
    # bookmark is not a good reason to lose them.
    path('associations/', RedirectView.as_view(url='/clubs/', permanent=True)),
    path('associations/<int:pk>/',
         RedirectView.as_view(pattern_name='club_detail', permanent=True)),
    path('rankings/', include('members.urls')),
    path('dashboard/', dashboard, name='dashboard'),
    path('matches/', include('matches.urls')),
    path('payments/', include('payments.urls')),
    path('notifications/', include('notifications.urls')),
    path('2fa/verify/', verify_2fa, name='verify_2fa'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='/'), name='logout'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
