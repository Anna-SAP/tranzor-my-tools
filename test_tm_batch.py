import unittest
import tm_panel as tm

class BatchSearchTests(unittest.TestCase):
    def test_six_keys_markdown_crlf_blank_and_duplicate(self):
        keys = ['EXTENSION_NUMBER_ALREADY_EXISTS_FOR_CHECK', 'EMPTY_TEXT', 'INVALID_TEXT',
                'INVALID_DESTINATION', 'INVALID_EXT_TYPE', 'MAXIMUM_PROMPT_TEXT_LENGTH_REACHED']
        raw = '\r\n\r\n'.join(k.replace('_', r'\_') for k in keys) + '\nEMPTY_TEXT\n'
        calls = []
        def search(**params):
            calls.append((params['opus_id'], params.get('target_language'), params['match_mode']))
            return {'entries': [], 'total': 0}
        view = tm.search_tm_batch(tm.QueryIntent(query=raw, target_language='en-GB, zh-CN;en-GB'), search_fn=search)
        self.assertEqual(len(calls), 12)
        self.assertEqual({c[0] for c in calls}, set(keys))
        self.assertTrue(all(c[2] == 'fuzzy' for c in calls))
        self.assertEqual(len(view['query_results']), 12)
        self.assertEqual(view['status']['ice'], 'no_probe')
        self.assertEqual(view['kpis']['ice_store_misses'], 0)

    def test_failure_preserves_other_keys_and_overlap_dedupes(self):
        def search(**params):
            if params['opus_id'] == 'BROKEN':
                raise RuntimeError('upstream unavailable')
            return {'entries': [{'translation_id': '1', 'opus_id': 'EMPTY_TEXT',
                                  'source_text': 'Original', 'target_language': 'en-GB',
                                  'translated_text': 'Live record'}], 'total': 1}
        view = tm.search_tm_batch(tm.QueryIntent(query='EMPTY\nEMPTY_TEXT\nBROKEN'), search_fn=search)
        self.assertEqual(view['kpis']['record_rows'], 1)
        self.assertEqual(view['status']['records'], 'partial')
        self.assertIn('BROKEN [all] / records', view['errors'])
        self.assertEqual(view['query_results'][2]['status']['records'], 'error')

    def test_limits_fail_before_network(self):
        def unexpected(**params):
            self.fail('network must not run')
        view = tm.search_tm_batch(tm.QueryIntent(query='\n'.join(str(i) for i in range(101))), search_fn=unexpected)
        self.assertEqual(view['error'], 'batch_limit')

if __name__ == '__main__':
    unittest.main()
