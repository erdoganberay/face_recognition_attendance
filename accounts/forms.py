# accounts/forms.py
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

FC = {'class': 'form-control'}


class SignUpForm(UserCreationForm):
    # Extra User fields
    first_name = forms.CharField(max_length=100, required=True, widget=forms.TextInput(attrs=FC))
    last_name = forms.CharField(max_length=100, required=True, widget=forms.TextInput(attrs=FC))
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs=FC))

    # Student fields
    student_id = forms.CharField(max_length=20, required=True, widget=forms.TextInput(attrs=FC))
    date_of_birth = forms.DateField(
        required=True,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ('username', 'password1', 'password2'):
            self.fields[field_name].widget.attrs.update(FC)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            'first_name',
            'last_name',
            'username',
            'email',
            'password1',
            'password2',
        )
