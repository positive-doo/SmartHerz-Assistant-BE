import json
import os
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import main


def event_row(event_id=10, **extra):
    return {
        'id': event_id,
        'title': {'rendered': 'Događaj &amp; kultura'},
        'content': {'rendered': '<p>Opis događaja.</p>'},
        'link': f'https://example.test/events/{event_id}',
        'mec_start_date': '2026-11-01',
        'mec_end_date': '2026-11-02',
        **extra,
    }


class EventsSourceTests(unittest.TestCase):
    def test_event_municipalities_resolve_taxonomy_ids_without_inventing_details(self):
        rows = [event_row(mec_category=[8, 6, 8, 80]), event_row(11)]
        categories = [
            {'id': 6, 'name': 'Trebinje'},
            {'id': 8, 'name': 'Nevesinje'},
            {'id': 80, 'name': 'Nevesinje'},
        ]
        with patch('main._fetch_integration_wordpress', side_effect=[rows, categories]) as fetch:
            events = main._fetch_integration_events()
        self.assertEqual(events[0].municipality, 'Nevesinje, Trebinje')
        self.assertEqual(events[0].name, 'Događaj & kultura')
        self.assertEqual(events[0].start_date, date(2026, 11, 1))
        self.assertEqual(events[0].end_date, date(2026, 11, 2))
        missing = events[1].model_dump(mode='json', exclude_none=True)
        self.assertNotIn('municipality', missing)
        self.assertTrue(set(missing).isdisjoint({
            'latitude', 'longitude', 'address', 'location', 'organizer',
            'start_time', 'end_time',
        }))
        event_request = fetch.call_args_list[0].args
        category_request = fetch.call_args_list[1].args
        self.assertIn('mec_category', event_request[1].split(','))
        self.assertEqual(category_request[0], 'https://smart-herz.modoos.rs/wp-json/wp/v2/mec_category')

    def test_unknown_municipality_id_does_not_silently_export_wrong_data(self):
        with patch('main._fetch_integration_wordpress', side_effect=[
            [event_row(mec_category=[999])], [{'id': 6, 'name': 'Trebinje'}],
        ]):
            with self.assertRaises(ValueError):
                main._fetch_integration_events()

    def test_destination_updated_at_uses_gmt_value_and_leaves_missing_fields_out(self):
        rows = [{
            'id': page_id,
            'title': {'rendered': f'Page {page_id}'},
            'slug': f'page-{page_id}',
            'link': f'https://example.test/page/{page_id}',
            'modified': '2026-10-21T14:30:00',
            'modified_gmt': '2026-10-21T12:30:00',
        } for page_id in main._INTEGRATION_DESTINATION_PAGE_IDS + main._INTEGRATION_ROUTE_PAGE_IDS]
        rows[1].pop('modified_gmt')
        with patch('main._fetch_integration_wordpress', return_value=rows) as fetch:
            destinations, routes = main._fetch_integration_pages()
        self.assertEqual(len(destinations), 8)
        self.assertEqual(len(routes), 5)
        self.assertEqual(destinations[0].updated_at, datetime(2026, 10, 21, 12, 30, tzinfo=timezone.utc))
        self.assertIsNone(destinations[1].updated_at)
        self.assertNotIn('updated_at', destinations[1].model_dump(exclude_none=True))
        self.assertIn('modified_gmt', fetch.call_args.args[1].split(','))

    def test_bot_receives_json_dates_and_today_with_the_same_event_fields(self):
        event = main.IntegrationEvent(
            id='event_10', name='Događaj', municipality='Trebinje',
            start_date=date(2026, 11, 1), end_date=date(2026, 11, 2),
        )
        before = datetime.now(timezone.utc).date()
        with patch('main._fetch_integration_events', return_value=[event]) as fetch:
            result = main.get_tourism_events(include_past=True)
        after = datetime.now(timezone.utc).date()
        self.assertIn(date.fromisoformat(result['as_of_date']), [before, after])
        self.assertEqual(result['events'], [event.model_dump(mode='json', exclude_none=True)])
        self.assertEqual(json.loads(json.dumps(result))['events'][0]['start_date'], '2026-11-01')
        fetch.assert_called_once_with()

    def test_current_feed_excludes_past_and_undated_events_but_keeps_ongoing_and_future(self):
        events = [
            main.IntegrationEvent(id='past', name='Past', start_date=date(2026, 10, 1), end_date=date(2026, 10, 20)),
            main.IntegrationEvent(id='ongoing', name='Ongoing', start_date=date(2026, 10, 20), end_date=date(2026, 10, 21)),
            main.IntegrationEvent(id='today', name='Today', start_date=date(2026, 10, 21)),
            main.IntegrationEvent(id='future', name='Future', start_date=date(2026, 11, 1)),
            main.IntegrationEvent(id='undated', name='Undated'),
        ]
        with patch('main.datetime', wraps=datetime) as clock, \
             patch('main._fetch_integration_events', return_value=events):
            clock.now.return_value = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
            current = main.get_tourism_events()
            historical = main.get_tourism_events(include_past=True)
        self.assertEqual(current['as_of_date'], '2026-10-21')
        self.assertEqual([event['id'] for event in current['events']], ['ongoing', 'today', 'future'])
        self.assertEqual([event['id'] for event in historical['events']], [event.id for event in events])

    def test_empty_event_feed_is_distinct_from_a_failed_source_and_never_reuses_data(self):
        with patch('main._fetch_integration_events', side_effect=[[], URLError('offline')]) as fetch:
            empty = main.get_tourism_events()
            failed = main.get_tourism_events()
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(empty['events'], [])
        self.assertNotIn('error', empty)
        self.assertTrue(failed['error'])
        self.assertEqual(failed['events'], [])

    def test_json_and_stream_use_the_event_tool_and_sanitize_its_output(self):
        source = {'as_of_date': '2026-10-21', 'events': [
            {'id': 'event_10', 'name': 'Događaj', 'municipality': 'Trebinje',
             'url': 'https://example.test/removed-link'},
        ]}
        sanitized = {'as_of_date': '2026-10-21', 'events': [
            {'id': 'event_10', 'name': 'Događaj', 'municipality': 'Trebinje'},
        ]}
        query = 'Koji su događaji u Trebinju u novembru?'
        for streaming, include_past in [(False, True), (True, False)]:
            with self.subTest(streaming=streaming, include_past=include_past):
                client = MagicMock()
                tool_call = SimpleNamespace(type='function_call', name='get_tourism_events',
                                            call_id='live-events', arguments=json.dumps({'include_past': include_past}))
                first = SimpleNamespace(output=[tool_call], output_text='')
                final = SimpleNamespace(output_text='Događaj u Trebinju.')
                client.responses.create.side_effect = [first, final]
                stream = client.responses.stream.return_value.__enter__.return_value
                stream.__iter__.return_value = iter([
                    SimpleNamespace(type='response.output_text.delta', delta=final.output_text),
                ])
                stream.get_final_response.return_value = final
                with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}), \
                     patch('main.OpenAI', return_value=client), \
                     patch('main.get_assistant_instructions', return_value='Instructions'), \
                     patch('main.get_tourism_events', return_value=source) as fetch, \
                     patch('main.sanitize_tool_result', return_value=sanitized) as sanitize:
                    answer = ''.join(main.answer_query_stream(query)) if streaming else main.answer_query(query)
                self.assertEqual(answer, final.output_text)
                fetch.assert_called_once_with(include_past=include_past)
                sanitize.assert_called_once_with(source)
                first_args = client.responses.create.call_args_list[0].kwargs
                self.assertIn(main.EVENTS_TOOL, first_args['tools'])
                final_args = client.responses.stream.call_args.kwargs if streaming else client.responses.create.call_args_list[1].kwargs
                output = final_args['input'][-1]
                self.assertEqual(output['type'], 'function_call_output')
                self.assertEqual(output['call_id'], 'live-events')
                self.assertEqual(json.loads(output['output']), sanitized)
                self.assertEqual(final_args['tool_choice'], 'none')

    def test_historical_year_overrides_wrong_flag_in_json_and_stream(self):
        event = main.IntegrationEvent(
            id='event_2816', name='Dučićeve večeri poezije', municipality='Trebinje',
            start_date=date(2025, 11, 7), end_date=date(2025, 11, 9),
        )
        cases = [
            ('Koji je termin Dučićevih večeri u Trebinju za novembar 2025?', True, []),
            ('Prikaži događaje u 2027. godini.', False, []),
            ('Koji su predstojeći događaji?', False, [
                {'role': 'user', 'content': 'Ranije sam pitao za novembar 2025.'},
            ]),
        ]
        for streaming in (False, True):
            for query, expected_past, history in cases:
                with self.subTest(streaming=streaming, query=query):
                    client = MagicMock()
                    client.responses.create.side_effect = [
                        SimpleNamespace(output=[SimpleNamespace(
                            type='function_call', name='get_tourism_events',
                            call_id='events', arguments='{"include_past": false}',
                        )], output_text=''),
                        SimpleNamespace(output_text='Proveren termin.'),
                    ]
                    stream = client.responses.stream.return_value.__enter__.return_value
                    stream.__iter__.return_value = iter([
                        SimpleNamespace(type='response.output_text.delta', delta='Proveren termin.'),
                    ])
                    stream.get_final_response.return_value = SimpleNamespace(output_text='Proveren termin.')
                    with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}), \
                         patch('main.datetime', wraps=datetime) as clock, \
                         patch('main.OpenAI', return_value=client), \
                         patch('main.get_assistant_instructions', return_value='Instructions'), \
                         patch('main._fetch_integration_events', return_value=[event]), \
                         patch('main.sanitize_tool_result', side_effect=lambda value: value):
                        clock.now.return_value = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
                        if streaming:
                            list(main.answer_query_stream(query, history=history))
                            final_args = client.responses.stream.call_args.kwargs
                        else:
                            main.answer_query(query, history=history)
                            final_args = client.responses.create.call_args_list[1].kwargs
                    source = json.loads(final_args['input'][-1]['output'])
                    self.assertEqual(bool(source['events']), expected_past)
                    if expected_past:
                        self.assertEqual(source['events'][0]['start_date'], '2025-11-07')
                        self.assertEqual(source['events'][0]['end_date'], '2025-11-09')
                        self.assertEqual(source['events'][0]['municipality'], 'Trebinje')
                    self.assertIn('Current UTC date: 2026-10-21.',
                                  client.responses.create.call_args_list[0].kwargs['instructions'])


if __name__ == '__main__':
    unittest.main()
