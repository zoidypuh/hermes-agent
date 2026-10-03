"""Real manager/provider/API-boundary replay; backend ranking is a labeled stub.

This does NOT measure semantic selection, live Honcho, freshness, conflicts or
card-05 caps. Expected labels define the synthetic backend response, not an
implemented selector. We test query propagation, isolation and fail-closed
behavior through production classes. No API/backend/model calls.
"""
import json
import os
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from agent.turn_context import compose_user_api_content
from plugins.memory.honcho.evidence import HonchoEvidenceContext
from tests.plugins import test_honcho_current_query as helpers

FIXTURES = Path(__file__).parent / 'fixtures' / 'honcho-query-v0.2.json'

class QueryFixtureReplay(unittest.TestCase):
    def test_all_fixture_transport_contracts(self):
        doc = json.loads(FIXTURES.read_text())
        rows = []
        for case in doc['cases']:
            with self.subTest(case=case['case_id']):
                # Labeled oracle at external I/O boundary; NOT a ranking algorithm.
                expected = {s['id'] for s in case['expected_facts']}
                selected = [s for s in case['sources'] if s['id'] in expected]
                empty = case['empty_injection_expected'] or bool(case['failure_injection'])
                calls = []
                def lookup(query, **kwargs):
                    calls.append((query, kwargs))
                    if case['failure_injection'] == 'backend_error':
                        raise RuntimeError('synthetic outage')
                    return [] if empty else [SimpleNamespace(id=s['id'], content=s['fact'],
                        observed_id='human', observer_id='agent', session_id='test', created_at=s['as_of']) for s in selected]
                peer = SimpleNamespace(conclusions_of=lambda target: SimpleNamespace(query=lookup))
                helper = helpers.CurrentQueryTests()
                manager = helper.manager(peer)
                manager.dialectic_query = lambda *a, **kw: ''
                manager.pop_auth_notice = lambda: ''
                provider = helper.provider(manager)
                provider._config.timeout = 2.0
                provider._base_context_cache = 'OLD_OFF_TOPIC'
                provider._prefetch_result = 'OLD_OFF_TOPIC'
                history = case['session']['history']
                for n, msg in enumerate(history, 1):
                    provider.on_turn_start(n, msg['content'])
                query = case['session']['query']
                provider.on_turn_start(len(history)+1, query)
                started = time.monotonic()
                with patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')):
                    text = provider.prefetch(query)
                elapsed_ms = (time.monotonic()-started)*1000
                wire = compose_user_api_content(query, text, '') or query
                self.assertEqual(len(calls), 1)
                request = calls[0][0]
                self.assertIn(query, request)
                self.assertEqual(calls[0][1]['top_k'], 12)
                if history and query.startswith(('Same ', 'And ', 'Which of ')):
                    self.assertIn(history[-1]['content'], request)
                elif history:
                    self.assertNotIn(history[-1]['content'], request)
                self.assertNotIn('OLD_OFF_TOPIC', wire)
                self.assertNotIn('GENERIC_CARD', wire)
                for forbidden in case['forbidden_facts']:
                    self.assertNotIn(forbidden['id'], wire)
                if empty:
                    self.assertEqual(text, '')
                    self.assertEqual(wire, query)
                else:
                    # Transport oracle is deliberately not a semantic-quality claim.
                    # The stricter final boundary can omit weak/oversized candidates.
                    if text:
                        self.assertIsInstance(text, HonchoEvidenceContext)
                        self.assertLessEqual(wire.count('\"source_id\":'), 3)
                self.assertLess(elapsed_ms, case['metrics']['latency']['budget_ms'])
                rows.append({'case':case['case_id'], 'passed':True,
                             'latency_ms':round(elapsed_ms,3), 'context_chars':len(text),
                             'backend': 'labeled synthetic oracle',
                             'semantic_quality_measured':False})
        self.assertEqual(len(rows), len(doc['cases']))
        output = os.environ.get('HONCHO_CARD04_EVIDENCE')
        if output:
            Path(output).write_text(json.dumps({'cases':rows, 'count':len(rows),
                'scope':'real provider transport/API-boundary contract, not semantic selection or final count/size caps'}, indent=2)+'\n')

if __name__ == '__main__':
    unittest.main()
