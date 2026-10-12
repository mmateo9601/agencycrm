from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.views.decorators.http import require_safe
from django.views.generic import TemplateView

from apps.accounts.selectors import workspaces_for_user
from common.health import database_available


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/home.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            workspaces=Paginator(workspaces_for_user(user=self.request.user), 25).get_page(
                self.request.GET.get("page")
            ),
            **kwargs,
        )


@require_safe
def health(request):
    available = database_available()
    return JsonResponse(
        {"app": "ok", "database": "ok" if available else "unavailable"},
        status=200 if available else 503,
    )
