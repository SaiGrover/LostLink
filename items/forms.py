import re
from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from . import media
from .models import Category, Item, Location, ReportFlag


_NUMBER_RUN = re.compile(r'\d[\d -]{5,}\d')
_DATE_LIKE = re.compile(r'^\d{1,4}[-/ ]\d{1,2}[-/ ]\d{1,4}$')
_PRIVATE_PATTERNS = [
    (re.compile(r'(?i)\b(?:s/?n|serial(?:\s*no\.?)?|imei|roll\s*no\.?|enrol+ment\s*no\.?)\b\s*[:#-]?\s*\w+'),
     'a serial or ID number'),
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+'), 'an email address'),
]


def _reject_private_identifiers(value, field_label):
    """Public text must not reveal what ownership verification relies on.

    Flags long digit runs (ID, serial, phone numbers; dates are allowed),
    labelled serial/IMEI/roll numbers and email addresses.
    """
    found = None
    for run in _NUMBER_RUN.findall(value):
        run = run.strip()
        if len(re.sub(r'\D', '', run)) >= 7 and not _DATE_LIKE.match(run):
            found = 'an ID, serial or phone number'
            break
    if found is None:
        found = next((what for pattern, what in _PRIVATE_PATTERNS if pattern.search(value)), None)
    if found:
        raise forms.ValidationError(
            f'The {field_label} seems to contain {found}. Public reports are visible to everyone, '
            'so put identifiers like this in "Private verification details" instead.')


class ItemForm(forms.ModelForm):
    declare_sensitive = forms.BooleanField(
        required=False, label='This photo shows personal details',
        help_text='For example an ID or bank card, a name, number, phone number or QR code. '
                  'Others then see only a blurred preview; you, moderators and the verified '
                  'owner see the full photo.')

    class Meta:
        model = Item
        fields = ('report_type', 'title', 'category', 'location', 'event_date', 'event_time',
                  'description', 'brand', 'color', 'image', 'private_details')
        widgets = {
            'report_type': forms.RadioSelect(),
            'event_date': forms.DateInput(attrs={'type': 'date'}),
            'event_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}),
            'description': forms.Textarea(attrs={'rows': 4}),
            'private_details': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['report_type'].choices = Item.Type.choices
        self.fields['category'].queryset = Category.objects.all()
        self.fields['location'].queryset = Location.objects.all()
        self.fields['category'].empty_label = 'Choose a category'
        self.fields['location'].empty_label = 'Choose a campus zone'
        for name, field in self.fields.items():
            if name in ('report_type', 'declare_sensitive'):
                continue
            css = 'field-input'
            if name == 'image':
                css = 'field-file'
                field.widget.attrs['accept'] = 'image/jpeg,image/png,image/webp,image/gif'
            field.widget.attrs.setdefault('class', css)
        self.fields['brand'].widget.attrs['placeholder'] = 'e.g. Apple, Nike (optional)'
        self.fields['color'].widget.attrs['placeholder'] = 'e.g. Black (optional)'
        self.fields['title'].widget.attrs['placeholder'] = 'e.g. Black leather wallet'
        self.fields['description'].widget.attrs['placeholder'] = (
            'Colour, material, brand, general condition. Keep serial numbers, names, ID numbers '
            'and hidden marks for the private box below.')
        self.fields['private_details'].label = 'Private verification details (optional)'
        self.fields['private_details'].required = False
        self.fields['private_details'].help_text = (
            'Only you and moderators see this. Note something the real owner would know but '
            'the public report does not show, e.g. the last digits of a serial number, a hidden '
            'scratch or what is inside. It is used to check claims, never shown publicly or matched.')
        self.fields['private_details'].widget.attrs['placeholder'] = (
            'e.g. Sticker inside the lid; serial ends in 4F2; library card inside')
        self.fields['event_time'].label = 'Approximate time'
        self.fields['event_date'].widget.attrs['max'] = timezone.localdate().isoformat()
        if self.instance.pk and self.instance.photo_sensitive:
            # Reporters can add protection; only a moderator can remove it.
            self.fields['declare_sensitive'].initial = True
            self.fields['declare_sensitive'].disabled = True
            self.fields['declare_sensitive'].help_text = (
                'This photo is shown to others as a blurred preview. Only a moderator can change that.')

    # --- upload hardening (see items/media.py) ----------------------------
    MAX_IMAGE_MB = media.DEFAULT_MAX_BYTES // (1024 * 1024)
    ALLOWED_FORMATS = media.ALLOWED_FORMATS

    def clean_title(self):
        value = ' '.join(self.cleaned_data['title'].split())
        if len(value) < 3:
            raise forms.ValidationError('Give the item a short, descriptive title.')
        _reject_private_identifiers(value, 'title')
        return value

    def clean_description(self):
        value = self.cleaned_data['description'].strip()
        if len(value) < 10:
            raise forms.ValidationError(
                'Describe the item in at least 10 characters so it can be matched.')
        _reject_private_identifiers(value, 'description')
        return value

    def clean_private_details(self):
        return (self.cleaned_data.get('private_details') or '').strip()[:1000]

    def clean_event_date(self):
        value = self.cleaned_data['event_date']
        if value > timezone.localdate():
            raise forms.ValidationError('The date cannot be in the future.')
        return value

    def clean_image(self):
        """Only a newly uploaded file is processed: it is validated, stripped of
        metadata and re-encoded, so the original bytes are never stored. An
        existing photo kept on edit is left untouched (it may even be missing
        from storage, which must not break editing the text)."""
        image = self.cleaned_data.get('image')
        if not isinstance(image, UploadedFile):
            return image
        declared = (getattr(image, 'content_type', '') or '').lower()
        if declared == 'image/svg+xml' or declared.startswith(('text/', 'application/xhtml')):
            raise forms.ValidationError('Please upload a JPG, PNG, WEBP or GIF image.')
        try:
            return media.sanitize_upload(image)
        except media.ImageRejected as exc:
            raise forms.ValidationError(str(exc))

    def save(self, commit=True):
        item = super().save(commit=False)
        image = self.cleaned_data.get('image')
        declared = self.cleaned_data.get('declare_sensitive')
        if isinstance(image, UploadedFile):
            # A new photo starts a fresh privacy review.
            item.photo_reviewed_by = None
            item.photo_reviewed_at = None
            if declared:
                item.photo_sensitive = True
                item.photo_sensitivity_source = Item.PhotoSensitivitySource.REPORTER
            elif media.suggests_sensitive(item.title, item.description, item.category):
                item.photo_sensitive = True
                item.photo_sensitivity_source = Item.PhotoSensitivitySource.AUTO
            else:
                item.photo_sensitive = False
                item.photo_sensitivity_source = ''
        elif declared and not item.photo_sensitive:
            item.photo_sensitive = True
            item.photo_sensitivity_source = Item.PhotoSensitivitySource.REPORTER
        if commit:
            item.save()
            self.save_m2m()
        return item


class FlagForm(forms.ModelForm):
    class Meta:
        model = ReportFlag
        fields = ('reason', 'note')
        widgets = {
            'reason': forms.Select(attrs={'class': 'field-input'}),
            'note': forms.TextInput(attrs={'class': 'field-input',
                                           'placeholder': 'Optional detail for the moderator'}),
        }


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ('name', 'icon', 'description', 'related')
        widgets = {
            'name': forms.TextInput(attrs={'class': 'field-input', 'placeholder': 'Category name'}),
            'icon': forms.TextInput(attrs={'class': 'field-input', 'maxlength': 4, 'placeholder': '📦'}),
            'description': forms.TextInput(attrs={'class': 'field-input', 'placeholder': 'Optional description'}),
            'related': forms.SelectMultiple(attrs={'class': 'field-input', 'size': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['related'].required = False
        self.fields['related'].label = 'Compatible categories (partial credit)'
        if self.instance.pk:
            self.fields['related'].queryset = Category.objects.exclude(pk=self.instance.pk)


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ('name', 'adjacent')
        widgets = {
            'name': forms.TextInput(attrs={'class': 'field-input', 'placeholder': 'Zone name'}),
            'adjacent': forms.SelectMultiple(attrs={'class': 'field-input', 'size': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['adjacent'].required = False
        self.fields['adjacent'].label = 'Adjacent zones (partial credit)'
        if self.instance.pk:
            self.fields['adjacent'].queryset = Location.objects.exclude(pk=self.instance.pk)
