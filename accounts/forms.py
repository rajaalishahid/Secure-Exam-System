from django import forms
from django.contrib.auth.validators import UnicodeUsernameValidator
from .models import User
from classes.models import ClassRoom


class LoginForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)


class CreateTeacherForm(forms.Form):
    username = forms.CharField(max_length=150, validators=[UnicodeUsernameValidator()])
    email = forms.EmailField(label='Email address (for reference, no email will be sent)')
    full_name = forms.CharField(max_length=150)
    identity_verified = forms.BooleanField(required=True, label='I verified this person’s identity in person')

    def clean_username(self):
        value = self.cleaned_data['username']
        if User.objects.filter(username=value).exists():
            raise forms.ValidationError('Username is already in use.')
        return value


class CreateStudentForm(forms.Form):
    full_name = forms.CharField(max_length=150, label='Full Name')
    institution_id = forms.CharField(
        max_length=50,
        label='Student ID',
        validators=[UnicodeUsernameValidator()],
    )
    class_id = forms.ModelChoiceField(queryset=ClassRoom.objects.none(), label='Class')

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        classes = ClassRoom.objects.filter(teacher=user).order_by('name')
        self.fields['class_id'].queryset = classes
        if not self.is_bound and classes.count() == 1:
            self.fields['class_id'].initial = classes.first()

    def clean_institution_id(self):
        value = self.cleaned_data['institution_id']
        if User.objects.filter(institution_id=value).exists() or User.objects.filter(username=value).exists():
            raise forms.ValidationError('Student ID is already in use.')
        return value
