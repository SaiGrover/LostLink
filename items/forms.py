from django import forms

from .models import Category, Item, Location


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
        for name, field in self.fields.items():
            if name == 'report_type':
                continue
            css = 'field-input'
            if name == 'image':
                css = 'field-file'
            field.widget.attrs.setdefault('class', css)
        self.fields['brand'].widget.attrs['placeholder'] = 'e.g. Apple, Nike (optional)'
        self.fields['color'].widget.attrs['placeholder'] = 'e.g. Black (optional)'
        self.fields['title'].widget.attrs['placeholder'] = 'e.g. Black leather wallet'
        self.fields['event_time'].label = 'Approximate time'

    # --- upload hardening ------------------------------------------------
    MAX_IMAGE_MB = 5
    ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}

    def clean_event_date(self):
        from django.utils import timezone
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
        return image


class ClaimStartForm(forms.Form):
    statement = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4, 'class': 'field-input',
                                     'placeholder': 'Describe proof this item is yours…'}),
        label='Proof of ownership')
