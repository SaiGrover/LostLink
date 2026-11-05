from django import forms
from django.utils import timezone

from .models import Category, Item, Location, ReportFlag


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ('report_type', 'title', 'category', 'location', 'event_date', 'event_time',
                  'description', 'brand', 'color', 'image')
        widgets = {
            'report_type': forms.RadioSelect(),
            'event_date': forms.DateInput(attrs={'type': 'date'}),
            'event_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}),
            'description': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['report_type'].choices = Item.Type.choices
        self.fields['category'].queryset = Category.objects.all()
        self.fields['location'].queryset = Location.objects.all()
        self.fields['category'].empty_label = 'Choose a category'
        self.fields['location'].empty_label = 'Choose a campus zone'
        for name, field in self.fields.items():
            if name == 'report_type':
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
            'Colour, material, brand, distinctive marks — anything that identifies it.')
        self.fields['event_time'].label = 'Approximate time'
        self.fields['event_date'].widget.attrs['max'] = timezone.localdate().isoformat()

    # --- upload hardening ------------------------------------------------
    MAX_IMAGE_MB = 5
    ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
    ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF'}
    MAX_PIXELS = 40_000_000  # guards against decompression bombs

    def clean_title(self):
        value = ' '.join(self.cleaned_data['title'].split())
        if len(value) < 3:
            raise forms.ValidationError('Give the item a short, descriptive title.')
        return value

    def clean_description(self):
        value = self.cleaned_data['description'].strip()
        if len(value) < 10:
            raise forms.ValidationError(
                'Describe the item in at least 10 characters so it can be matched.')
        return value

    def clean_event_date(self):
        value = self.cleaned_data['event_date']
        if value > timezone.localdate():
            raise forms.ValidationError('The date cannot be in the future.')
        return value

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if not image or not hasattr(image, 'size'):
            return image
        if image.size > self.MAX_IMAGE_MB * 1024 * 1024:
            raise forms.ValidationError(
                f'Image must be under {self.MAX_IMAGE_MB} MB.')
        content_type = getattr(image, 'content_type', None)
        if content_type and content_type not in self.ALLOWED_IMAGE_TYPES:
            raise forms.ValidationError('Please upload a JPG, PNG, WEBP or GIF image.')
        # Verify the actual bytes, not just the declared type.
        try:
            from PIL import Image as PILImage
            image.seek(0)
            with PILImage.open(image) as probe:
                fmt = probe.format
                width, height = probe.size
            image.seek(0)
        except Exception:
            raise forms.ValidationError('That file is not a readable image.')
        if fmt not in self.ALLOWED_FORMATS:
            raise forms.ValidationError('Please upload a JPG, PNG, WEBP or GIF image.')
        if width * height > self.MAX_PIXELS:
            raise forms.ValidationError('That image is too large in pixel dimensions.')
        return image


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
