from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views.decorators.http import require_safe
from django.views.generic import TemplateView

from common.health import database_available


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/home.html"


@require_safe
def health(request):
    available = database_available()
    return JsonResponse(
        {"app": "ok", "database": "ok" if available else "unavailable"},
        status=200 if available else 503,
    )
