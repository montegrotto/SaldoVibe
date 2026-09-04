from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm

User = get_user_model()


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="E-postadress",
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "din@email.se", "autofocus": True}),
    )
    password = forms.CharField(
        label="Lösenord",
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Lösenord"}),
    )


class RegisterForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Lösenord",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    password2 = forms.CharField(
        label="Bekräfta lösenord",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name")
        widgets = {
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
        }
        labels = {
            "email": "E-postadress",
            "first_name": "Förnamn",
            "last_name": "Efternamn",
        }

    def clean_password2(self):
        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Lösenorden matchar inte.")
        if p2:
            password_validation.validate_password(p2, self.instance)
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class StyledPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        label="E-postadress",
        max_length=254,
        widget=forms.EmailInput(attrs={"class": "form-control", "autocomplete": "email", "autofocus": True}),
    )


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].label = "Nytt lösenord"
        self.fields["new_password2"].label = "Bekräfta nytt lösenord"
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
