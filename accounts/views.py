import qrcode
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.shortcuts import redirect, render
from django.utils.http import urlencode
from qrcode.image.svg import SvgPathImage

from attachments.utils import is_safe_return_to

from .forms import LoginForm, RegisterForm
from .models import ApiToken

User = get_user_model()

NEW_APP_TOKEN_SESSION_KEY = "new_api_token"


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


@login_required
def app_view(request):
    """Mobilappen: a one-time QR code that logs the iPhone app in, and the list of the
    user's app logins (accounts.ApiToken) with revocation. The clear-text token lives in
    the session only until the page that shows the QR code has rendered."""
    if request.method == "POST":
        revoke_id = request.POST.get("revoke", "")
        if revoke_id.isdigit():
            deleted, _ = request.user.api_tokens.filter(pk=int(revoke_id)).delete()
            if deleted:
                messages.success(request, "Appinloggningen har återkallats.")
        else:
            request.session[NEW_APP_TOKEN_SESSION_KEY] = ApiToken.issue(request.user, name="QR-kod")
        return redirect("accounts:app")

    server_url = request.build_absolute_uri("/")
    login_link = qr_svg = None
    raw_token = request.session.pop(NEW_APP_TOKEN_SESSION_KEY, None)
    if raw_token:
        login_link = f"saldovibe://login?{urlencode({'server': server_url, 'token': raw_token})}"
        qr_svg = qrcode.make(login_link, image_factory=SvgPathImage, box_size=10, border=1).to_string(
            encoding="unicode"
        )
    return render(
        request,
        "accounts/app.html",
        {
            "server_url": server_url,
            "login_link": login_link,
            "qr_svg": qr_svg,
            "qr_lifetime_minutes": int(ApiToken.UNUSED_LIFETIME.total_seconds() // 60),
            "tokens": request.user.api_tokens.all(),
        },
    )


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
