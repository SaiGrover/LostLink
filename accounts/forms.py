from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

User = get_user_model()

# A small warm palette students are assigned an avatar colour from.
AVATAR_COLORS = ['#E8623C', '#12A594', '#7C5CFF', '#E0398B', '#F2A63B', '#2FA36B']


class RegisterForm(UserCreationForm):
    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60, required=False)
    email = forms.EmailField()
    enrolment_no = forms.CharField(max_length=30, required=False,
                                   label='Enrolment number')

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'username', 'email',
                  'enrolment_no', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'field-input')
        placeholders = {
            'first_name': 'First name',
            'last_name': 'Last name',
            'username': 'e.g. saanvi_g',
            'email': 'you@college.edu',
            'enrolment_no': 'e.g. 22CS10001',
            'password1': 'Create a strong password',
            'password2': 'Confirm your password',
        }
        for name, ph in placeholders.items():
            self.fields[name].widget.attrs['placeholder'] = ph
        # The custom UI renders its own password-requirements checklist.
        self.fields['password1'].help_text = ''

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        # New self-registrations are always Students; staff roles are granted
        # by an administrator (RBAC, synopsis Section 8).
        user.role = User.Role.STUDENT
        idx = (len(self.cleaned_data.get('username', '')) or 0) % len(AVATAR_COLORS)
        user.avatar_color = AVATAR_COLORS[idx]
        if commit:
            user.save()
        return user


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'field-input')
        self.fields['username'].widget.attrs['placeholder'] = 'Username'
        self.fields['password'].widget.attrs['placeholder'] = 'Password'


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'enrolment_no', 'phone')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'field-input')

    def clean_email(self):
        # Same rule as registration: one account per email, compared without case.
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if email and User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Another account already uses this email.')
        return email
