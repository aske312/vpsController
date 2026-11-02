"""Normalize profile names before admission can launch a mutation worker."""
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.api.support import manager


class ProfileNameTests(unittest.TestCase):
    def test_whitespace_only_names_are_rejected_on_create_and_update(self):
        for model in (manager.ProfileCreate, manager.ProfileUpdate):
            for name in ('', ' ', '\t\r\n', '\u00a0\u2003'):
                with self.subTest(model=model.__name__, name=repr(name)), self.assertRaises(ValidationError):
                    model(name=name)

    def test_name_is_trimmed_and_length_is_checked_after_normalization(self):
        for model in (manager.ProfileCreate, manager.ProfileUpdate):
            for name in ('Профиль Windows', 'Я' * 80):
                with self.subTest(model=model.__name__, length=len(name)):
                    self.assertEqual(model(name=' \t' + name + '\n ').name, name)
            with self.assertRaises(ValidationError):
                model(name='Я' * 81)

    def test_omitted_or_null_update_name_does_not_become_an_empty_rename(self):
        self.assertNotIn('name', manager.ProfileUpdate().model_dump(exclude_unset=True))
        self.assertIsNone(manager.ProfileUpdate(name=None).name)

    def test_http_validation_rejects_blank_names_without_starting_a_worker(self):
        with patch.dict(manager.app.dependency_overrides, {manager.require_mihomo_admission: lambda: None}, clear=True), \
                patch.object(manager, 'start_profile_operation') as start:
            client = TestClient(manager.app)
            for method, path in (('POST', '/api/mihomo/profiles'), ('PATCH', '/api/mihomo/profiles/fixture')):
                for name in ('   ', '\t\n', '\u00a0'):
                    with self.subTest(method=method, name=repr(name)):
                        response = client.request(method, path, json={'name': name})
                        self.assertEqual(response.status_code, 422)
                        self.assertEqual(response.json()['detail'][0]['loc'], ['body', 'name'])
            start.assert_not_called()
