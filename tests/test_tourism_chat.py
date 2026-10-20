import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from urllib.error import URLError

import main


class TourismChatTests(unittest.TestCase):
    def setUp(self):
        self.catalogue = json.loads((main.BASE_DIR / 'tests/fixtures/tourism_20261020.json').read_text(encoding='utf-8'))

    def test_chat_receives_complete_basic_catalogue_and_named_route_links(self):
        with patch('main.fetch_tourism_catalogue', return_value=self.catalogue):
            result = main.get_tourism_catalogue()
        self.assertEqual(result['places'], self.catalogue['places'])
        self.assertEqual(len(result['places']), 281)
        self.assertEqual({r['id']: r['poi_ids'] for r in result['routes']}, self.catalogue['route_poi_ids'])
        self.assertEqual(sum(len(r['poi_ids']) for r in result['routes']), 481)
        crvanj = next(p for p in result['places'] if p['id'] == 'poi_280')
        self.assertEqual(crvanj['municipality'], 'Nevesinje, Kalinovik')
        self.assertEqual([r['name'] for r in result['routes'] if crvanj['id'] in r['poi_ids']], ['Između planina i neba'])

    def test_tool_reads_again_and_does_not_cache_old_pois(self):
        updated = json.loads(json.dumps(self.catalogue))
        updated['places'][0]['name'] = 'Updated POI'
        with patch('main.fetch_tourism_catalogue', side_effect=[self.catalogue, updated]) as source:
            first, second = main.get_tourism_catalogue(), main.get_tourism_catalogue()
        self.assertEqual(source.call_count, 2)
        self.assertNotEqual(first['places'][0]['name'], second['places'][0]['name'])

    def test_source_failure_is_reported_without_partial_or_snapshot_data(self):
        for error in [URLError('offline'), ValueError('inconsistent route')]:
            with self.subTest(error=error), patch('main.fetch_tourism_catalogue', side_effect=error):
                result = main.get_tourism_catalogue()
            self.assertTrue(result['error'])
            self.assertEqual(result['places'], [])
            self.assertEqual(result['routes'], [])

    def test_model_can_use_csv_with_other_tools_in_the_same_answer(self):
        calls = [SimpleNamespace(type='function_call', name=name, call_id=name, arguments='{}')
                 for name in ['get_tourism_catalogue', 'search_knowledge']]
        client = Mock()
        client.responses.create.side_effect = [SimpleNamespace(output=calls, output_text=''),
                                               SimpleNamespace(output_text='Verified answer')]
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}), \
             patch('main.OpenAI', return_value=client), \
             patch('main.get_assistant_instructions', return_value=main.TOURISM_INSTRUCTIONS), \
             patch('main.fetch_tourism_catalogue', return_value=self.catalogue), \
             patch('main.search_knowledge', return_value={'results': []}) as knowledge:
            answer = main.answer_query('U kojoj ruti je Planina Crvanj?')
        self.assertEqual(answer, 'Verified answer')
        knowledge.assert_called_once_with('U kojoj ruti je Planina Crvanj?')
        first_args = client.responses.create.call_args_list[0].kwargs
        self.assertIn(main.TOURISM_TOOL, first_args['tools'])
        outputs = [item for item in client.responses.create.call_args_list[1].kwargs['input'] if isinstance(item, dict) and item.get('type') == 'function_call_output']
        self.assertEqual(len(outputs), 2)
        self.assertEqual(json.loads(outputs[0]['output'])['places'], self.catalogue['places'])
        self.assertEqual(client.responses.create.call_args_list[1].kwargs['tool_choice'], 'none')

    def test_streaming_answer_uses_the_same_csv_tool(self):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(output=[SimpleNamespace(
            type='function_call', name='get_tourism_catalogue', call_id='csv', arguments='{}')], output_text='')
        stream = client.responses.stream.return_value.__enter__.return_value
        stream.__iter__.return_value = iter([SimpleNamespace(type='response.output_text.delta', delta='Između planina i neba')])
        stream.get_final_response.return_value = SimpleNamespace(output_text='Između planina i neba')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}), \
             patch('main.OpenAI', return_value=client), \
             patch('main.get_assistant_instructions', return_value=main.TOURISM_INSTRUCTIONS), \
             patch('main.fetch_tourism_catalogue', return_value=self.catalogue) as source:
            result = list(main.answer_query_stream('A kojoj ruti pripada?', history=[{'role': 'user', 'content': 'Planina Crvanj'}]))
        self.assertEqual(result, ['Između planina i neba'])
        source.assert_called_once()
        sent = client.responses.stream.call_args.kwargs['input']
        self.assertEqual(sent[0]['content'], 'Planina Crvanj')
        self.assertEqual(len(json.loads(sent[-1]['output'])['places']), 281)


if __name__ == '__main__':
    unittest.main()
