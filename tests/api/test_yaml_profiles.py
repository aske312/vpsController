"""Profiles have one YAML contract; retired switches never restore old features."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from tests.api.support import manager


class YamlProfileTests(unittest.TestCase):
    def test_retired_settings_are_removed_without_changing_connections(self):
        legacy = {'id':'fixture','routing':{'block_ads':True,'block_ads_rules':'@default','client_config_format':'xray','tunnel_fragment':True,'route_dns':True},
                  'devices':[{'id':'common','routing':{'client_config_format':'singbox','block_ads':True}}],
                  'common_device_id':'common','connections':[{'id':'channel','device_id':'common','component':'transport-reality','credential':{'uuid':'same-secret'}}]}
        normalized=manager.normalize_profile(deepcopy(legacy))
        self.assertEqual(normalized['connections'][0]['credential']['uuid'],'same-secret')
        self.assertEqual(normalized['devices'][0]['id'],'common')
        for routing in (normalized['routing'],normalized['devices'][0]['routing'],manager.device_routing(legacy,'common')):
            self.assertFalse(any(key=='client_config_format' or key.startswith(('block_ads','tunnel_fragment')) for key in routing))
        self.assertTrue(normalized['routing']['route_dns'])
        self.assertTrue(legacy['routing']['block_ads'])

    def test_advertising_preset_and_provider_are_absent_even_with_legacy_flag(self):
        with patch.object(manager, 'SUBMODULE_ROOT', Path(__file__).resolve().parents[2] / 'protocol-images/mihomo/modules'):
            self.assertFalse(any(row['key'].startswith('block_ads') for row in manager.routing_schema()))
        self.assertNotIn('block_ads',manager.DIRECT_RULE_PRESETS)
        self.assertNotIn('ads-extended',manager.SECURITY_PROVIDERS)
        self.assertEqual(manager.profile_rules({'block_ads':True,'block_ads_rules':'@default'}),[])

    def test_all_saved_formats_and_client_names_receive_yaml(self):
        for saved in ('mihomo','singbox','xray','uri'):
            item={'id':'fixture','devices':[{'id':'common','routing':{'client_config_format':saved}}], 'common_device_id':'common','connections':[]}
            with self.subTest(saved=saved), patch.object(manager,'render_profile',return_value='proxies: []\n') as render, patch.object(manager,'validate_rendered_profile') as validate:
                text,extension=manager.render_client_profile(item,'common',client_name='Happ')
                self.assertEqual((text,extension),('proxies: []\n','yaml'))
                self.assertNotIn('client_config_format',render.call_args.args[0]['devices'][0]['routing'])
                validate.assert_called_once_with(text)

    def test_explicit_non_yaml_formats_fail_closed_before_rendering(self):
        with patch.object(manager,'render_profile') as render:
            for format in ('xray','singbox','uri','invalid'):
                with self.subTest(format=format), self.assertRaises(manager.HTTPException) as rejected:
                    manager.render_client_profile({},'common',format)
                self.assertEqual(rejected.exception.status_code,422)
            render.assert_not_called()
