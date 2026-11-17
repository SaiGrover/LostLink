"""Authorization boundaries owned by the accounts area: role escalation via
the people-and-roles screen, the profile form and the Django admin; CSRF-safe
sign-out."""
from django.contrib.admin.sites import site
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from accounts.models import User
from config.testing import make_user, make_users


class RoleEscalationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = make_users()

    def change_role(self, actor, target, role):
        self.client.force_login(actor)
        return self.client.post(reverse('dashboard:users'), {'user_id': target.pk, 'role': role})

    def test_moderator_cannot_promote_self_or_others(self):
        mod = self.u['mod']
        self.assertEqual(self.change_role(mod, mod, 'admin').status_code, 403)
        self.assertEqual(self.change_role(mod, self.u['owner'], 'moderator').status_code, 403)
        mod.refresh_from_db()
        self.assertEqual(mod.role, User.Role.MODERATOR)
        self.assertEqual(User.objects.get(pk=self.u['owner'].pk).role, User.Role.STUDENT)

    def test_student_cannot_promote_self(self):
        owner = self.u['owner']
        self.assertEqual(self.change_role(owner, owner, 'admin').status_code, 403)
        owner.refresh_from_db()
        self.assertEqual(owner.role, User.Role.STUDENT)

    def test_admin_cannot_change_own_role_or_a_superuser(self):
        admin = self.u['admin']
        root = User.objects.create_superuser(username='root', password='pass-word-123', email='r@x.edu')
        self.change_role(admin, admin, 'student')
        self.change_role(admin, root, 'student')
        admin.refresh_from_db()
        root.refresh_from_db()
        self.assertEqual(admin.role, User.Role.ADMIN)
        self.assertTrue(root.is_superuser)

    def test_unknown_role_value_is_ignored(self):
        self.change_role(self.u['admin'], self.u['owner'], 'superuser')
        self.assertEqual(User.objects.get(pk=self.u['owner'].pk).role, User.Role.STUDENT)

    def test_role_change_needs_post(self):
        self.client.force_login(self.u['admin'])
        self.client.get(reverse('dashboard:users'), {'user_id': self.u['owner'].pk, 'role': 'admin'})
        self.assertEqual(User.objects.get(pk=self.u['owner'].pk).role, User.Role.STUDENT)

    def test_role_change_needs_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.u['admin'])
        response = client.post(reverse('dashboard:users'), {'user_id': self.u['owner'].pk, 'role': 'admin'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(User.objects.get(pk=self.u['owner'].pk).role, User.Role.STUDENT)

    def test_profile_form_ignores_privilege_fields(self):
        owner = self.u['owner']
        self.client.force_login(owner)
        self.client.post(reverse('accounts:profile'), {
            'first_name': 'Olive', 'last_name': '', 'email': 'owner@x.edu', 'enrolment_no': '',
            'phone': '', 'role': 'admin', 'is_staff': 'on', 'is_superuser': 'on'})
        owner.refresh_from_db()
        self.assertEqual(owner.role, User.Role.STUDENT)
        self.assertFalse(owner.is_staff or owner.is_superuser)


class DjangoAdminPrivilegeTests(TestCase):
    def test_staff_with_change_user_permission_cannot_edit_privilege_fields(self):
        staff = make_user('helper', is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='change_user'))
        request = RequestFactory().get('/')
        request.user = User.objects.get(pk=staff.pk)
        model_admin = site._registry[User]
        readonly = model_admin.get_readonly_fields(request, staff)
        for field in ('role', 'is_staff', 'is_superuser', 'groups', 'user_permissions',
                      'email_verified_at'):
            self.assertIn(field, readonly)

    def test_superuser_can_edit_privilege_fields(self):
        root = User.objects.create_superuser(username='root', password='pass-word-123', email='r@x.edu')
        request = RequestFactory().get('/')
        request.user = root
        self.assertNotIn('role', site._registry[User].get_readonly_fields(request, root))


class SignOutTests(TestCase):
    def test_get_does_not_sign_out(self):
        user = make_user('ana')
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:logout'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sign out?')
        self.assertEqual(self.client.session.get('_auth_user_id'), str(user.pk))

    def test_post_signs_out_and_needs_csrf(self):
        user = make_user('ana')
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        self.assertEqual(client.post(reverse('accounts:logout')).status_code, 403)
        self.assertEqual(client.session.get('_auth_user_id'), str(user.pk))
        self.client.force_login(user)
        self.assertRedirects(self.client.post(reverse('accounts:logout')), reverse('items:home'))
        self.assertNotIn('_auth_user_id', self.client.session)
