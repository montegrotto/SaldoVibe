from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.db import transaction as db_transaction
from django.shortcuts import redirect, render

from attachments.utils import is_safe_return_to

from .forms import LoginForm, RegisterForm

User = get_user_model()


def login_view(request):
    if request.user.is_authenticated:
        return redirect("bookkeeping:dashboard")
    if request.method == "POST":
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            next_url = request.GET.get("next", "")
            return redirect(next_url if is_safe_return_to(next_url) else "bookkeeping:dashboard")
    else:
        form = LoginForm(request)
    return render(request, "accounts/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("accounts:login")


def register_view(request):
    if request.user.is_authenticated:
        return redirect("bookkeeping:dashboard")
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            with db_transaction.atomic():
                user = form.save(commit=False)
                if not User.objects.exists():
                    # The first registered user becomes system administrator
                    # with access to the admin interface.
                    user.is_staff = True
                    user.is_superuser = True
                user.save()
            login(request, user)
            if user.is_superuser:
                messages.success(
                    request,
                    "Du är den första användaren och har fått administratörsbehörighet.",
                )
            return redirect("bookkeeping:dashboard")
    else:
        form = RegisterForm()
    return render(request, "accounts/register.html", {"form": form})
