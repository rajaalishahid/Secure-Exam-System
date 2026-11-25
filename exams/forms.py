from django import forms
from django.core.exceptions import ValidationError
from classes.models import ClassRoom


class CreateQuizForm(forms.Form):
    title = forms.CharField(max_length=200)
    description = forms.CharField(required=False, widget=forms.Textarea)
    class_id = forms.ModelChoiceField(queryset=ClassRoom.objects.none(), label='Existing class', required=False)
    new_class_name = forms.CharField(max_length=200, required=False, label='New class name',
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Computer Science - Section B'}))
    question_count = forms.IntegerField(min_value=1, max_value=100, initial=5,
        label='Number of questions')

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['class_id'].queryset = ClassRoom.objects.filter(teacher=user)
        try:
            self.question_count = int(self.data.get('question_count', 5)) if self.is_bound else self.initial.get('question_count',5)
        except (TypeError, ValueError):
            self.question_count = 5
        self.question_count = max(1, min(100, self.question_count))
        for index in range(1, self.question_count + 1):
            prefix = f'question_{index}_'
            self.fields[prefix+'prompt'] = forms.CharField(widget=forms.Textarea)
            for option in 'abcd':
                self.fields[prefix+option] = forms.CharField(max_length=400)
            self.fields[prefix+'correct'] = forms.ChoiceField(choices=[(v, f'Option {v}') for v in 'ABCD'])
            self.fields[prefix+'marks'] = forms.IntegerField(min_value=1, max_value=1000, initial=1)

    def question_rows(self):
        return [{'prompt': self[f'question_{i}_prompt'],
            'options': [(letter.upper(), self[f'question_{i}_{letter}']) for letter in 'abcd'],
            'correct': self[f'question_{i}_correct'], 'marks': self[f'question_{i}_marks']}
            for i in range(1, self.question_count + 1)]

    def clean(self):
        cleaned=super().clean()
        if not cleaned.get('class_id'): raise ValidationError('Select an admin-assigned class.')
        return cleaned
