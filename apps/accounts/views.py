from django.contrib.auth.views import LoginView

from .services import record_login


class ApplicationLoginView(LoginView):
    def form_valid(self, form):
        response = super().form_valid(form)
        record_login(user=form.get_user())
        return response
