from django import forms
from .models import ClassRoom


class CreateClassForm(forms.ModelForm):
    class Meta:
        model = ClassRoom
        fields = ['name']
        labels = {'name': 'Class name'}
        widgets = {'name': forms.TextInput(attrs={'placeholder': 'e.g. CS-101 · Introduction to Computing'})}
