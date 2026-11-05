from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from config.testing import make_users


class RegistrationAndLoginTests(TestCase):
    def test_register_creates_student_and_signs_in(self):
        response = self.client.post(reverse('accounts:register'), {
            'first_name': 'Riya', 'last_name': 'S', 'username': 'riya_s', 'email': 'Riya@College.edu',
            'enrolment_no': '22CS1', 'password1': 'campus-recovery-9', 'password2': 'campus-recovery-9'})
        self.assertRedirects(response, reverse('items:dashboard'))
        user = User.objects.get(username='riya_s')
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertEqual(user.email, 'riya@college.edu')
        self.assertFalse(user.is_staff_role)

    def test_register_rejects_duplicate_email_and_mismatched_passwords(self):
        User.objects.create_user(username='taken', email='dup@x.edu', password='pass-word-123')
        response = self.client.post(reverse('accounts:register'), {
            'first_name': 'A', 'username': 'new_user', 'email': 'DUP@x.edu',
            'password1': 'campus-recovery-9', 'password2': 'campus-recovery-9'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already exists')
        response = self.client.post(reverse('accounts:register'), {
            'first_name': 'A', 'username': 'new_user2', 'email': 'fresh@x.edu',
            'password1': 'campus-recovery-9', 'password2': 'different'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='new_user2').exists())

    def test_login_valid_and_invalid_credentials(self):
        User.objects.create_user(username='sam', password='pass-word-123')
        response = self.client.post(reverse('accounts:login'), {'username': 'sam', 'password': 'wrong'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please enter a correct username and password')
        response = self.client.post(reverse('accounts:login'), {'username': 'sam', 'password': 'pass-word-123'})
        self.assertRedirects(response, reverse('items:dashboard'))

    def test_anonymous_is_redirected_to_login(self):
        for name in ('items:dashboard', 'items:report', 'items:mine', 'claims:mine',
                     'notifications:inbox', 'dashboard:moderation', 'dashboard:analytics'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn(reverse('accounts:login'), response.url)

    def test_profile_update(self):
        user = User.objects.create_user(username='pat', password='pass-word-123')
        self.client.force_login(user)
        response = self.client.post(reverse('accounts:profile'), {
            'first_name': 'Pat', 'last_name': 'K', 'email': 'pat@x.edu', 'enrolment_no': '1', 'phone': '123'})
        self.assertRedirects(response, reverse('accounts:profile'))
        user.refresh_from_db()
        self.assertEqual(user.phone, '123')


class RoleAuthorizationTests(TestCase):
    """Server-side role checks: changing the URL never bypasses authorization."""

    def setUp(self):
        self.users = make_users()

    def test_student_cannot_reach_staff_areas(self):
        self.client.force_login(self.users['owner'])
        for name in ('dashboard:moderation', 'dashboard:decisions', 'dashboard:analytics',
                     'dashboard:users', 'dashboard:taxonomy', 'dashboard:export_csv', 'dashboard:export_pdf'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.assertEqual(self.client.post(reverse('dashboard:review_report', args=[1, 'approve'])).status_code, 403)
        self.assertEqual(self.client.post(reverse('dashboard:users'), {'user_id': 1, 'role': 'admin'}).status_code, 403)

    def test_moderator_has_review_but_not_admin_powers(self):
        self.client.force_login(self.users['mod'])
        self.assertEqual(self.client.get(reverse('dashboard:moderation')).status_code, 200)
        self.assertEqual(self.client.get(reverse('dashboard:decisions')).status_code, 200)
        for name in ('dashboard:analytics', 'dashboard:users', 'dashboard:taxonomy',
                     'dashboard:export_csv', 'dashboard:export_pdf'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_administrator_reaches_everything(self):
        self.client.force_login(self.users['admin'])
        for name in ('dashboard:moderation', 'dashboard:analytics', 'dashboard:users', 'dashboard:taxonomy'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_superuser_without_role_is_treated_as_administrator(self):
        root = User.objects.create_superuser(username='root', password='pass-word-123', email='r@x.edu')
        self.assertTrue(root.is_administrator)
        self.client.force_login(root)
        self.assertEqual(self.client.get(reverse('dashboard:analytics')).status_code, 200)

    def test_self_registration_cannot_choose_a_staff_role(self):
        self.client.post(reverse('accounts:register'), {
            'first_name': 'Eve', 'username': 'eve', 'email': 'eve@x.edu', 'role': 'admin',
            'password1': 'campus-recovery-9', 'password2': 'campus-recovery-9'})
        self.assertEqual(User.objects.get(username='eve').role, User.Role.STUDENT)
