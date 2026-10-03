"""Offline real-provider regression tests. Network boundaries are synthetic."""
import unittest
from types import SimpleNamespace
from plugins.memory.honcho import HonchoMemoryProvider
from plugins.memory.honcho.client import HonchoClientConfig
from plugins.memory.honcho.session import HonchoSession, HonchoSessionManager

def grounded(query):
    return dict(schema="honcho-evidence-v1", workspace="ws", peer="human", query=query,
                sources=[dict(id="query-source", workspace_id="ws", peer_id="human", content=query, metadata={})])

class Peer:
    def __init__(self, result=None, error=None):
        self.result = result or SimpleNamespace(representation='', peer_card=[])
        self.error = error
        self.calls = []
    def context(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result
    def representation(self, **kwargs):
        raise AssertionError('Generic representation fallback forbidden')
    def get_card(self, **kwargs):
        raise AssertionError('Generic card fallback forbidden')

class CurrentQueryTests(unittest.TestCase):
    def manager(self, peer):
        cfg = HonchoClientConfig(api_key='synthetic', workspace_id='synthetic-workspace', peer_name='human', ai_peer='agent')
        manager = HonchoSessionManager(honcho=SimpleNamespace(), config=cfg)
        session = HonchoSession(key='test', user_peer_id='human', assistant_peer_id='agent', honcho_session_id='test')
        manager._cache['test'] = session
        manager._get_or_create_peer = lambda peer_id: peer
        return manager

    def test_empty_current_lookup_does_not_fetch_summary_or_ai(self):
        peer = Peer()
        manager = self.manager(peer)
        manager._sessions_cache['test'] = SimpleNamespace(context=lambda **kw: SimpleNamespace(summary=SimpleNamespace(content='UNRELATED_SUMMARY')))
        result = manager.get_prefetch_context('test', 'garage door sensor', current_query_only=True)
        self.assertFalse(any(result.values()), result)
        self.assertEqual(len(peer.calls), 1)
        self.assertEqual(peer.calls[0]['search_query'], 'garage door sensor')
        self.assertEqual(peer.calls[0]['target'], 'human')

    def provider(self, manager):
        p = HonchoMemoryProvider()
        p._config = HonchoClientConfig(api_key='synthetic', timeout=0.2, context_tokens=1000)
        p._manager, p._session_key = manager, 'test'
        p._session_initialized = True
        p._recall_mode = 'hybrid'
        p._turn_count = 2
        return p

    def test_normal_prefetch_uses_current_query_not_cached_previous_topic(self):
        calls = []
        manager = SimpleNamespace(get_grounded_context=lambda session, query, **kw: calls.append((session, query, kw)) or {},
                                  dialectic_query=lambda *a, **kw: 'GENERIC_DIALECTIC',
                                  pop_context_result=lambda session: {}, pop_auth_notice=lambda: '')
        p = self.provider(manager)
        p._base_context_cache = 'OLD_COMFYUI_CONTEXT'
        p._prefetch_result = 'OLD_RELAY_DIALECTIC'
        p._prefetch_result_fired_at = 1
        self.assertEqual(p.prefetch('How do I descale my espresso machine?'), '')
        self.assertEqual(calls[0][1], 'How do I descale my espresso machine?')

    def test_followup_uses_only_bounded_previous_dialogue(self):
        calls = []
        manager = SimpleNamespace(get_grounded_context=lambda s, q, **kw: calls.append(q) or {}, pop_auth_notice=lambda: '')
        p = self.provider(manager)
        p.on_turn_start(1, 'What is the default model on the GPU box?')
        p.on_turn_start(2, 'Same question for the Mac.')
        p.prefetch('Same question for the Mac.')
        self.assertIn('What is the default model on the GPU box?', calls[0])
        self.assertIn('Same question for the Mac.', calls[0])
        calls.clear()
        p.on_turn_start(3, 'Now: what is the recall mode?')
        p.prefetch('Now: what is the recall mode?')
        self.assertNotIn('GPU', calls[0])
        self.assertNotIn('Mac', calls[0])

    def test_failed_lookup_is_empty_without_generic_dialectic(self):
        def fail(*a, **kw):
            raise RuntimeError('synthetic outage')
        p = self.provider(SimpleNamespace(get_grounded_context=fail, dialectic_query=lambda *a, **kw: 'UNRELATED', pop_auth_notice=lambda: ''))
        self.assertEqual(p.prefetch('Which model is the default for the intern profile?'), '')

    def test_context_cadence_cannot_drop_a_new_topic(self):
        calls = []
        p = self.provider(SimpleNamespace(get_grounded_context=lambda s, q, **kw: calls.append(q) or grounded(q),
                                         dialectic_query=lambda *a, **kw: '', pop_auth_notice=lambda: ''))
        p._context_cadence = 10
        p._last_context_turn = 1
        self.assertIn('recall mode', p.prefetch('What is the recall mode?'))
        self.assertEqual(calls, ['What is the recall mode?'])

    def test_queue_does_not_start_previous_query_workers(self):
        queued = []
        manager = SimpleNamespace(prefetch_context=lambda *a: queued.append(a), dialectic_query=lambda *a, **kw: queued.append(a))
        p = self.provider(manager)
        p.queue_prefetch('Debug the voice relay')
        self.assertEqual(queued, [])
        self.assertIsNone(p._prefetch_thread)

    def test_session_switch_clears_followup_context(self):
        p = self.provider(SimpleNamespace())
        p.on_turn_start(1, 'Which GPU model is configured?')
        p.on_session_switch('another')
        p.on_turn_start(2, 'And the other one?')
        self.assertEqual(p._current_recall_query('And the other one?'), 'And the other one?')

    def test_initialize_does_not_start_generic_prewarm(self):
        from unittest.mock import patch
        p = HonchoMemoryProvider()
        cfg = HonchoClientConfig(api_key='synthetic', recall_mode='hybrid', session_strategy='per-session')
        manager = SimpleNamespace(get_or_create=lambda s: SimpleNamespace(messages=[]))
        with patch('plugins.memory.honcho.client.get_honcho_client', return_value=SimpleNamespace()), \
             patch('plugins.memory.honcho.session.HonchoSessionManager', return_value=manager), \
             patch.object(p, '_spawn_dialectic') as spawn:
            p._do_session_init(cfg, 'test')
            spawn.assert_not_called()

    def test_automatic_context_ignores_generic_peer_card(self):
        peer = Peer(SimpleNamespace(representation='QUERY_EVIDENCE', peer_card=['UNRELATED_GENERIC_CARD']))
        result = self.manager(peer).get_prefetch_context('test', 'recall mode', current_query_only=True)
        self.assertNotIn('UNRELATED_GENERIC_CARD', str(result))
        self.assertIn('QUERY_EVIDENCE', str(result))

    def test_current_lookup_excludes_most_frequent_generic_conclusions(self):
        peer = Peer()
        self.manager(peer).get_prefetch_context('test', 'relay port', current_query_only=True)
        self.assertIs(peer.calls[0].get('include_most_frequent'), False)

    def test_non_text_dialectic_is_rejected_not_injected(self):
        p = self.provider(SimpleNamespace(get_grounded_context=lambda *a, **kw: {'representation': 'evidence'},
                                         dialectic_query=lambda *a, **kw: SimpleNamespace(), pop_auth_notice=lambda: ''))
        self.assertEqual(p.prefetch('What is the recall mode?'), '')

    def test_blank_current_query_never_fetches_generic_context(self):
        peer = Peer(SimpleNamespace(representation='GENERIC', peer_card=[]))
        self.assertEqual(self.manager(peer).get_prefetch_context('test', '', current_query_only=True), {})
        self.assertEqual(peer.calls, [])

    def test_followup_does_not_inherit_another_authors_question(self):
        p = self.provider(SimpleNamespace())
        p.on_turn_start(1, 'Private topic from another participant', author_id='person-a')
        p.on_turn_start(2, 'And the other one?', author_id='person-b')
        self.assertEqual(p._current_recall_query('And the other one?'), 'And the other one?')

    def test_session_switch_invalidates_same_turn_request_receipt(self):
        calls = []
        p = self.provider(SimpleNamespace(get_grounded_context=lambda s, q, **kw: calls.append(q) or grounded(q),
                                         dialectic_query=lambda *a, **kw: '', pop_auth_notice=lambda: ''))
        self.assertIn('relay', p.prefetch('What relay is configured?'))
        p.on_session_switch('new-conversation')
        self.assertIn('relay', p.prefetch('What relay is configured?'))
        self.assertEqual(len(calls), 2)

if __name__ == '__main__':
    unittest.main()
