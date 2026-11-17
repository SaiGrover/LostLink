"""Review follow-ups: campus-domain production check, trusted-proxy client
addresses and shared-cache warnings."""
from django.test import RequestFactory, SimpleTestCase, override_settings

from accounts.checks import campus_domain_configured, shared_cache_for_rate_limits
from accounts.ratelimit import client_ip


def ids():
    return {m.id for check in (campus_domain_configured, shared_cache_for_rate_limits) for m in check(None)}


class CampusDomainCheckTests(SimpleTestCase):
    @override_settings(LOSTLINK_PRODUCTION=True, LOSTLINK_ALLOWED_EMAIL_DOMAINS=[],
                       LOSTLINK_REQUIRE_CAMPUS_DOMAIN=True)
    def test_production_without_campus_domain_is_an_error(self):
        self.assertIn('accounts.E001', ids())

    @override_settings(LOSTLINK_PRODUCTION=True, LOSTLINK_ALLOWED_EMAIL_DOMAINS=['college.edu'],
                       LOSTLINK_REQUIRE_CAMPUS_DOMAIN=True)
    def test_configured_domain_passes(self):
        self.assertNotIn('accounts.E001', ids())

    @override_settings(LOSTLINK_PRODUCTION=True, LOSTLINK_ALLOWED_EMAIL_DOMAINS=[],
                       LOSTLINK_REQUIRE_CAMPUS_DOMAIN=False)
    def test_open_deployment_must_opt_out_explicitly(self):
        self.assertNotIn('accounts.E001', ids())

    @override_settings(LOSTLINK_PRODUCTION=False, LOSTLINK_ALLOWED_EMAIL_DOMAINS=[])
    def test_development_is_not_blocked(self):
        self.assertNotIn('accounts.E001', ids())


class SharedCacheCheckTests(SimpleTestCase):
    @override_settings(LOSTLINK_PRODUCTION=True,
                       CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
    def test_local_memory_cache_in_production_warns(self):
        self.assertIn('accounts.W003', ids())

    @override_settings(LOSTLINK_PRODUCTION=True, LOSTLINK_TRUSTED_PROXY_COUNT=1,
                       CACHES={'default': {'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
                                           'LOCATION': 'lostlink_cache'}})
    def test_shared_cache_and_trusted_proxy_pass(self):
        self.assertFalse({'accounts.W003', 'accounts.W004'} & ids())


class ClientIpTests(SimpleTestCase):
    def request(self, xff=None, remote='10.0.0.2'):
        extra = {'REMOTE_ADDR': remote}
        if xff is not None:
            extra['HTTP_X_FORWARDED_FOR'] = xff
        return RequestFactory().get('/', **extra)

    @override_settings(LOSTLINK_TRUSTED_PROXY_COUNT=0)
    def test_forwarded_header_ignored_without_trusted_proxies(self):
        self.assertEqual(client_ip(self.request('198.51.100.9, 10.0.0.2')), '10.0.0.2')

    @override_settings(LOSTLINK_TRUSTED_PROXY_COUNT=1)
    def test_spoofed_first_value_is_not_used_behind_one_proxy(self):
        # The client sent "198.51.100.9"; the proxy appended the real 203.0.113.7.
        self.assertEqual(client_ip(self.request('198.51.100.9, 203.0.113.7')), '203.0.113.7')
        # Varying the spoofed value cannot create fresh buckets.
        self.assertEqual(client_ip(self.request('1.2.3.4, 203.0.113.7')), '203.0.113.7')

    @override_settings(LOSTLINK_TRUSTED_PROXY_COUNT=2)
    def test_two_proxies_use_the_second_value_from_the_right(self):
        self.assertEqual(client_ip(self.request('6.6.6.6, 203.0.113.7, 10.1.1.1')), '203.0.113.7')

    @override_settings(LOSTLINK_TRUSTED_PROXY_COUNT=2)
    def test_short_header_falls_back_to_connection_address(self):
        self.assertEqual(client_ip(self.request('203.0.113.7')), '10.0.0.2')
