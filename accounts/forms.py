# accounts/forms.py
from django.contrib.auth.forms import UserCreationForm
 
 
class SignUpForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        fields = UserCreationForm.Meta.fields + ("email",)
