from django import forms

from .models import Claim, VerificationQuestion


class ClaimForm(forms.ModelForm):
    class Meta:
        model = Claim
        fields = ('statement', 'contact_phone', 'contact_email')
        widgets = {
            'statement': forms.Textarea(attrs={
                'rows': 4, 'class': 'field-input',
                'placeholder': 'Details only the owner would know…'}),
            'contact_phone': forms.TextInput(attrs={
                'class': 'field-input', 'placeholder': 'e.g. +91 98xxxxxxx'}),
            'contact_email': forms.EmailInput(attrs={
                'class': 'field-input', 'placeholder': 'you@college.edu'}),
        }

    def __init__(self, *args, found=True, **kwargs):
        super().__init__(*args, **kwargs)
        if found:
            self.fields['statement'].label = 'Proof of ownership'
            self.fields['statement'].help_text = (
                'Explain why this item is yours — details only the owner would know.')
        else:
            self.fields['statement'].label = 'Where / how you found it'
            self.fields['statement'].widget.attrs['placeholder'] = (
                'e.g. Found it near the library entrance on Tuesday…')
            self.fields['statement'].help_text = (
                'Tell the owner where you found it and how they can identify it with you.')
        self.fields['contact_phone'].label = 'Contact phone'
        self.fields['contact_email'].label = 'Contact email'
        self.fields['contact_email'].required = False
        self.fields['contact_phone'].required = False


class QuestionForm(forms.ModelForm):
    class Meta:
        model = VerificationQuestion
        fields = ('text',)
        widgets = {
            'text': forms.TextInput(attrs={
                'class': 'field-input',
                'placeholder': 'e.g. What was inside the wallet?'}),
        }


class AnswerForm(forms.Form):
    answer = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'field-input',
                                     'placeholder': 'Your answer…'}))


class DecisionForm(forms.Form):
    note = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'field-input',
                                      'placeholder': 'Optional note to the claimant'}))
