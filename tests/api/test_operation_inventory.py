import json
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from tests.api.support import api
import application_diagnostics as diagnostics
import assistant_control
import operation_outcome


class OperationInventoryTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env=types.SimpleNamespace(DATA_DIR=self.root,observe_service=lambda unit:{'unit_present':True,'active':False,'state':'inactive'})
        self.enterContext(patch.object(assistant_control,'read',return_value={'accounts':[],'providers':[]}))

    def write(self, identity, source='system', **fields):
        directory=self.root / {'system':'operations','mihomo':'mihomo/operations','cdn':'cdn-operations'}[source]
        directory.mkdir(parents=True,exist_ok=True)
        value={'id':identity,'action':'update','state':'succeeded','result':'success','started_at':'2090-01-01T00:00:00Z',**fields}
        (directory/(identity+'.json')).write_text(json.dumps(value),encoding='utf-8')
        return value

    def test_lifecycle_outcomes_do_not_confuse_successful_steps_with_failures_or_completion(self):
        for state,result,expected in [('succeeded','passed','success'),('succeeded','success','success'),('running','passed','active'),('running','failed','active'),('failed','passed','error'),('succeeded','unknown','unknown'),('unknown','timeout','unknown'),('cancelled','signal','cancelled'),('succeeded','new-code','unknown')]:
            with self.subTest(state=state,result=result): self.assertEqual(operation_outcome.outcome({'state':state,'result':result}),expected)

    def test_ID_discovery_hides_background_checks_but_explicit_search_finds_them(self):
        self.write('a'*32,action='ai-catalog',result='passed')
        self.write('b'*32,action='protocol-update:nodera',state='failed',result='failed',error_code='runtime_check_failed')
        result=diagnostics.operation_inventory(self.env,diagnostics.OperationsQuery())
        self.assertEqual([row['operation_id'] for row in result['items']],['b'*32])
        self.assertEqual(result['items'][0]['error_code'],'runtime_check_failed')
        checks=diagnostics.operation_inventory(self.env,diagnostics.OperationsQuery(action='ai-catalog'))
        self.assertEqual(checks['items'][0]['operation_id'],'a'*32)
        self.assertEqual(checks['items'][0]['context']['outcome'],'success')
        self.assertEqual(checks['items'][0]['next_step']['arguments']['operation_id'],'a'*32)

    def test_parent_result_owns_delegation_and_pagination_preserves_identities(self):
        for index in range(4):self.write(f'{index:032x}')
        self.write('0'*32,state='failed',result='failed')
        self.write('0'*32,source='mihomo')
        first=diagnostics.operation_inventory(self.env,diagnostics.OperationsQuery(limit=2))
        second=diagnostics.operation_inventory(self.env,diagnostics.OperationsQuery(limit=2,offset=first['next_offset']))
        all_rows=first['items']+second['items']
        self.assertEqual(len({row['operation_id'] for row in all_rows}),4)
        parent=next(row for row in all_rows if row['operation_id']=='0'*32)
        self.assertEqual(parent['context']['outcome'],'error')
        self.assertEqual(parent['source'],'system')

    def test_timeline_includes_scope_result_current_admission_and_recovery_markers(self):
        value=self.write('a'*32,action='ai-catalog',result='passed',stage='complete',health_check={'state':'passed','scope':'provider_catalog','application_function':'not_checked'})
        (self.root/'application-action.json').write_text(json.dumps(value))
        (self.root/'automation-recovery.json').write_text('{}')
        result=diagnostics.timeline(self.env,diagnostics.TimelineQuery(operation_id='a'*32))
        record=result['authoritative_result']
        self.assertEqual(record['result'],'passed')
        self.assertEqual(record['health_check']['scope'],'provider_catalog')
        self.assertEqual(record['health_check']['application_function'],'not_checked')
        self.assertIn('automation-recovery.json',result['current_admission']['recovery_markers'])
        self.assertEqual(result['current_admission']['current_operation']['operation_id'],'a'*32)

    def test_corrupt_optional_metadata_does_not_hide_an_operation_identity(self):
        self.write('a'*32,configuration_before=None,configuration_after=[],changed_components=None,runtime_changed_components={},affected_components=False,health_check={'checks':None})
        result=diagnostics.operation_inventory(self.env,diagnostics.OperationsQuery())
        self.assertEqual(result['items'][0]['operation_id'],'a'*32)
        self.assertEqual(result['items'][0]['configuration_before'],{})

    def test_unlabelled_known_secrets_are_redacted_from_operation_messages(self):
        self.write('a'*32,message='failure fixture-secret')
        with patch.object(assistant_control,'read',return_value={'accounts':[],'providers':[{'api_key':'fixture-secret'}]}):
            result=diagnostics.operation_inventory(self.env,diagnostics.OperationsQuery())
        self.assertNotIn('fixture-secret',json.dumps(result))
        self.assertEqual(result['items'][0]['operation_id'],'a'*32)

    def test_missing_admission_and_unreadable_parent_keep_conflict_context_without_child_success(self):
        self.write('a'*32,source='mihomo')
        (self.root/'operations').mkdir()
        (self.root/'operations'/('a'*32+'.json')).write_text('corrupt')
        (self.root/'automation-recovery.json').write_text('{}')
        result=diagnostics.timeline(self.env,diagnostics.TimelineQuery(operation_id='a'*32))
        self.assertEqual(result['authoritative_result']['state'],'unknown')
        missing=diagnostics.timeline(self.env,diagnostics.TimelineQuery(operation_id='b'*32))
        self.assertIn('automation-recovery.json',missing['current_admission']['recovery_markers'])

    def test_bounded_scan_reports_incomplete_history_instead_of_claiming_no_operations(self):
        self.write('a'*32)
        with patch.object(diagnostics,'MAX_OPERATION_SCAN_BYTES',1):
            result=diagnostics.operation_inventory(self.env,diagnostics.OperationsQuery())
        self.assertTrue(result['coverage']['sources']['system']['byte_limit_reached'])
        self.assertTrue(result['coverage']['sources']['system']['scan_truncated'])
