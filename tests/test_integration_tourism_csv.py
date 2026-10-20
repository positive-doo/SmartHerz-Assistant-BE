import csv
import io
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs,urlsplit

import integration_tourism as tourism


def response(rows):
    text = io.StringIO()
    csv.writer(text).writerows(rows)
    return io.BytesIO(('\ufeff' + text.getvalue()).encode('utf-8'))


class CsvTests(unittest.TestCase):
    master = [["ID", " Naziv ", "Opstina"], ["1", "POI, sa zarezom", "Trebinje"], ["2", "Planina", "Gacko, Kalinovik"]]
    route = [["ID", " Naziv ", "Opstina", "Opis", "Lat"], ["1.0", "POI, sa zarezom", "Trebinje", "unused", "bad coordinate"],
             ["2", "Planina", "Gacko"], ["2", "Planina", "Kalinovik"], ["", "", "", "formula tail"]]

    def test_downloads_all_tabs_and_uses_only_basic_fields(self):
        with patch.object(tourism, 'urlopen', side_effect=[response(self.master)] + [response(self.route) for _ in range(5)]) as fetch:
            result = tourism.fetch_tourism_catalogue()
        self.assertEqual(result['places'], [
            {'id':'poi_1','name':'POI, sa zarezom','category':'poi','municipality':'Trebinje'},
            {'id':'poi_2','name':'Planina','category':'poi','municipality':'Gacko, Kalinovik'}])
        self.assertTrue(all(ids == ['poi_1','poi_2'] for ids in result['route_poi_ids'].values()))
        self.assertEqual([parse_qs(urlsplit(c.args[0].full_url).query)['gid'][0] for c in fetch.call_args_list],
                         [tourism.POI_GID,*tourism.ROUTE_GIDS.values()])
        self.assertTrue(all(c.kwargs['timeout'] == 20 for c in fetch.call_args_list))

    def test_each_export_downloads_fresh_data(self):
        updated = [*self.master, ['3', 'Novi POI', 'Trebinje']]
        replies = [response(self.master)] + [response(self.route) for _ in range(5)]
        replies += [response(updated)] + [response(self.route) for _ in range(5)]
        with patch.object(tourism, 'urlopen', side_effect=replies) as fetch:
            first, second = tourism.fetch_tourism_catalogue(), tourism.fetch_tourism_catalogue()
        self.assertEqual(fetch.call_count, 12)
        self.assertEqual(len(first['places']), 2)
        self.assertEqual(len(second['places']), 3)
        self.assertEqual(second['places'][-1]['id'], 'poi_3')

    def test_missing_empty_or_duplicate_master_ids_fail(self):
        cases = [[self.master[0]], [*self.master, self.master[1]], [self.master[0], ['', 'No ID','Trebinje']],
                 [self.master[0], ['1','', 'Trebinje']], [self.master[0], ['1','POI','']]]
        for rows in cases:
            with self.subTest(rows=rows), patch.object(tourism,'urlopen',return_value=response(rows)), self.assertRaises(ValueError):
                tourism.fetch_tourism_catalogue()

    def test_invalid_ids_are_not_renumbered_or_guessed(self):
        for value in ['', '0', '-1', '1.5', 'NaN', 'Infinity', '=A2+1', 'text']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                tourism._poi_id(value)

    def test_unknown_route_id_wrong_name_and_municipality_fail(self):
        for row in [['99','Unknown','Trebinje'], ['1','Wrong POI','Trebinje'], ['1','POI, sa zarezom','Gacko']]:
            with self.subTest(row=row), patch.object(tourism,'urlopen',side_effect=[response(self.master),response([self.route[0],row])]), self.assertRaises(ValueError):
                tourism.fetch_tourism_catalogue()

    def test_html_wrong_columns_and_partial_rows_are_not_empty_success(self):
        for content in [b'<html>Please sign in</html>', b'Name,ID,Opstina\nPOI,1,Trebinje', b'ID,Naziv,Opstina\n1,POI']:
            with self.subTest(content=content), patch.object(tourism,'urlopen',return_value=io.BytesIO(content)), self.assertRaises(ValueError):
                tourism.fetch_tourism_catalogue()

    def test_failure_of_a_later_route_propagates_instead_of_partial_catalogue(self):
        with patch.object(tourism,'urlopen',side_effect=[response(self.master),response(self.route),URLError('later source down')]), self.assertRaises(URLError):
            tourism.fetch_tourism_catalogue()


if __name__ == '__main__':
    unittest.main()
