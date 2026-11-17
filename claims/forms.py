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
        fields = ('text', 'expected_answer')
        widgets = {
            'text': forms.TextInput(attrs={
                'class': 'field-input',
                'placeholder': 'e.g. What was inside the wallet?'}),
            'expected_answer': forms.TextInput(attrs={
                'class': 'field-input',
                'placeholder': 'What you expect (private — never shown to the claimant)'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['expected_answer'].required = False
        self.fields['expected_answer'].label = 'Expected answer (private, optional)'


class AnswerForm(forms.Form):
    answer = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'field-input',
                                     'placeholder': 'Your answer…'}))


CHECK_WIDGET = forms.Select(attrs={'class': 'field-input'})


class DecisionForm(forms.Form):
    """Moderator decision with a structured evidence checklist.

    Free-text answers are never treated as automatically correct: the
    reviewer records what each independent check showed, and approval is
    refused when a check contradicts the claim or none supports it.
    """
    note = forms.CharField(max_length=280, widget=forms.TextInput(
        attrs={'class': 'field-input', 'placeholder': 'Reason, shared with the claimant'}))
    check_statement = forms.ChoiceField(label='Statement vs. the report', widget=CHECK_WIDGET, choices=[
        ('consistent', 'Consistent with the report'), ('partial', 'Partly consistent'),
        ('inconsistent', 'Contradicts the report')])
    check_private = forms.ChoiceField(label='Private verification details', widget=CHECK_WIDGET, choices=[
        ('matches', 'Claim matches the private details'), ('partial', 'Partly matches'),
        ('mismatch', 'Contradicts the private details'), ('not_available', 'No private details on file')])
    check_answers = forms.ChoiceField(label='Answers to private questions', widget=CHECK_WIDGET, choices=[
        ('correct', 'Answers are correct'), ('partial', 'Partly correct'),
        ('incorrect', 'Answers are wrong'), ('not_applicable', 'Not applicable')])
    check_evidence = forms.ChoiceField(label='Supporting evidence', widget=CHECK_WIDGET, choices=[
        ('supports', 'Evidence supports ownership'), ('not_conclusive', 'Not conclusive'),
        ('contradicts', 'Evidence contradicts the claim'), ('none_provided', 'None provided')])

    def checklist(self):
        return {k: self.cleaned_data[k] for k in
                ('check_statement', 'check_private', 'check_answers', 'check_evidence')}


class RejectForm(forms.Form):
    note = forms.CharField(max_length=280)


class AppealForm(forms.Form):
    statement = forms.CharField(
        label='Updated statement', min_length=20, max_length=4000,
        widget=forms.Textarea(attrs={'rows': 4, 'class': 'field-input',
                                     'placeholder': 'Correct or expand your explanation…'}))
    additional_details = forms.CharField(
        label='What is new?', required=False, max_length=2000,
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'field-input',
                                     'placeholder': 'e.g. I found the purchase receipt'}))


class EvidenceForm(forms.Form):
    note = forms.CharField(required=False, max_length=280, widget=forms.TextInput(
        attrs={'class': 'field-input', 'placeholder': 'What does this show? (optional)'}))


class DisputeResolutionForm(forms.Form):
    outcome = forms.ChoiceField(choices=[
        ('dismissed', 'Dismiss — the claim continues'),
        ('revoked', 'Uphold — revoke the approval'),
        ('rejected', 'Uphold — reject the claim')], widget=CHECK_WIDGET)
    note = forms.CharField(min_length=10, max_length=500, widget=forms.Textarea(
        attrs={'rows': 2, 'class': 'field-input', 'placeholder': 'Reason and evidence considered'}))


class HandoverCodeForm(forms.Form):
    code = forms.RegexField(regex=r'^\s*\d{3}\s?\d{3}\s*$', max_length=8, widget=forms.TextInput(
        attrs={'class': 'field-input otp-input', 'inputmode': 'numeric', 'autocomplete': 'one-time-code',
               'placeholder': '6-digit code', 'pattern': '[0-9 ]*'}),
        error_messages={'invalid': 'Enter the 6-digit code.'})


class ExceptionHandoverForm(forms.Form):
    reason = forms.ChoiceField(choices=[], widget=CHECK_WIDGET)
    note = forms.CharField(min_length=20, max_length=500, widget=forms.Textarea(
        attrs={'rows': 3, 'class': 'field-input',
               'placeholder': 'Who was present, where, and how ownership and receipt were confirmed'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .services import EXCEPTION_REASONS
        self.fields['reason'].choices = list(EXCEPTION_REASONS.items())
