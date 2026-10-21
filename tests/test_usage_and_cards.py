import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import main
from chat_recommendations import csv_recommendations


CATALOGUE = {
    'places': [
        {'id':'poi_280','name':'Planina Crvanj','municipality':'Nevesinje, Kalinovik'},
        {'id':'poi_281','name':'Planina Zelengora','municipality':'Gacko, Kalinovik'},
        {'id':'poi_3','name':'Platani','municipality':'Trebinje'},
    ],
    'routes': [{'id':'route_1968','name':'Između planina i neba'}],
}


class UsageAndCardsTests(unittest.TestCase):
    def setUp(self):
        main._sessions.clear()
        self.client = TestClient(main.app)

    def test_cards_are_selected_from_current_source_without_invented_fields(self):
        cards = csv_recommendations('Planina Crvanj pripada ruti Izmedju planina i neba.', CATALOGUE)
        self.assertEqual([c['id'] for c in cards], ['poi_280','route_1968'])
        self.assertEqual(cards[0]['municipality'], 'Nevesinje, Kalinovik')
        self.assertFalse(any('url' in c or 'image_url' in c or 'description' in c for c in cards))
        self.assertEqual(csv_recommendations('Nepostojeći novi POI', CATALOGUE), [])
        self.assertEqual(csv_recommendations('Platanima', CATALOGUE), [])
        self.assertIsNone(csv_recommendations('Planina Crvanj', {}))
        self.assertEqual(csv_recommendations('Planina Crvanj', {'error':'offline'}), [])

    def test_cards_recognize_cyrillic_names_and_do_not_duplicate_mentions(self):
        cards = csv_recommendations('Планина Црвањ, Између планина и неба. Планина Црвањ.', CATALOGUE)
        self.assertEqual([c['id'] for c in cards], ['poi_280','route_1968'])

    def test_json_and_stream_return_cards_and_keep_one_session_per_cookie(self):
        sessions = []
        def answer(query, history=None, filters=None, catalogue_out=None):
            catalogue_out.update(CATALOGUE)
            return 'Planina Crvanj — Između planina i neba.'
        def stream(*args, **kwargs):
            yield answer(*args, **kwargs)
        with patch('main._record_session_activity', side_effect=lambda s:sessions.append(s.sql_thread_id)), \
             patch('main.answer_query', side_effect=answer), patch('main.answer_query_stream', side_effect=stream):
            first = self.client.post('/api/chat', json={'query':'Crvanj'})
            second = self.client.post('/api/chat/stream', json={'query':'Koja ruta?'})
        self.assertEqual(first.status_code, 200)
        chunks = [json.loads(line[6:]) for line in second.text.splitlines() if line.startswith('data: ')]
        self.assertTrue(chunks[-1]['done'])
        self.assertEqual(first.json()['recommendations'], chunks[-1]['recommendations'])
        self.assertEqual([c['id'] for c in chunks[-1]['recommendations']], ['poi_280','route_1968'])
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0], sessions[1])
        other = main.ChatSession()
        self.assertNotEqual(other.sql_thread_id, sessions[0])

    def test_catalogue_and_cards_are_not_reused_by_another_request(self):
        def answer(query, history=None, filters=None, catalogue_out=None):
            if query == 'First':
                catalogue_out.update(CATALOGUE)
            return 'Planina Crvanj'
        with patch('main._record_session_activity'), patch('main.answer_query', side_effect=answer):
            first = self.client.post('/api/chat', json={'query':'First'})
            second = self.client.post('/api/chat', json={'query':'Second'})
        self.assertTrue(first.json()['recommendations'])
        self.assertNotIn('recommendations', second.json())

    def test_failed_stream_does_not_record_a_session_or_send_cards(self):
        def stream(*args, **kwargs):
            kwargs['catalogue_out'].update(CATALOGUE)
            yield 'Planina Crvanj'
            raise RuntimeError('simulated failure')
        with patch('main._record_session_activity') as activity, patch('main.answer_query_stream', side_effect=stream):
            response = self.client.post('/api/chat/stream', json={'query':'Crvanj'})
        activity.assert_not_called()
        final = json.loads(response.text.strip().split('\n\n')[-1][6:])
        self.assertIn('error', final)
        self.assertNotIn('recommendations', final)

    def test_usage_writes_only_session_metadata_and_commits(self):
        db = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = db
        session = main.ChatSession()
        with patch('main._sql_connection', return_value=context):
            main._record_session_activity(session)
        db.commit.assert_called_once()
        sql, *values = db.cursor.return_value.execute.call_args.args
        self.assertIn('UPDLOCK, HOLDLOCK', sql)
        self.assertIn('last_message_at', sql)
        self.assertNotIn('closed_at', sql)
        self.assertNotIn('title', sql)
        self.assertEqual(values, [session.sql_thread_id,66,session.sql_thread_id,66])

    def test_usage_failure_keeps_the_successful_chat_answer(self):
        with patch('main._sql_connection', side_effect=RuntimeError('offline')), \
             patch('main.answer_query', return_value='Uspešan odgovor'):
            response = self.client.post('/api/chat', json={'query':'Zdravo'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['response'], 'Uspešan odgovor')


if __name__ == '__main__':
    unittest.main()
