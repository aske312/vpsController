import unittest
from unittest.mock import patch

from tests.api.support import api
import assistant_commands
import assistant_control
import connection_apps


class ConnectionAppsTests(unittest.TestCase):
    def test_windows_apps_include_our_client_and_platform_selection_is_bounded(self):
        windows = connection_apps.guide('windows')
        self.assertIn('Nodera', windows)
        self.assertIn('https://github.com/aske312/NoderaClient', windows)
        self.assertNotIn('NoderaClient', connection_apps.guide('ios'))
        with self.assertRaises(Exception): connection_apps.guide('../../private')

    def test_chat_and_bot_share_the_apps_command_without_calling_a_model(self):
        with patch.object(assistant_control,'read',return_value={}), patch.object(assistant_commands.roles,'require') as require:
            text=assistant_commands.execute(api,'panel','/apps windows')
        self.assertIn('Nodera',text)
        require.assert_called_once_with({},'panel','subscription_read')

    def test_bot_apps_route_accepts_subscription_users_without_admin_access(self):
        with patch.object(assistant_commands.roles, 'require') as require, \
             patch.object(assistant_control, 'require_bot_actor') as admin, \
             patch.object(assistant_control, 'read', return_value={}), \
             patch.object(assistant_commands, 'execute', return_value='apps'):
            result = api.nodera_command(assistant_commands.Command(command='/apps windows'), {}, '11')
            self.assertIn('Windows', result['text'])
            self.assertTrue(any('Nodera' in button['text'] for row in result['buttons'] for button in row))
        require.assert_called_once_with({}, '11', 'subscription_read')
        admin.assert_not_called()
