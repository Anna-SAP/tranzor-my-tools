import unittest
from unittest.mock import patch, Mock
import tm_panel as tm

KEYS = ['EXTENSION_NUMBER_ALREADY_EXISTS_FOR_CHECK','EMPTY_TEXT','INVALID_TEXT',
        'INVALID_DESTINATION','INVALID_EXT_TYPE','MAXIMUM_PROMPT_TEXT_LENGTH_REACHED']

class LiveStoreTests(unittest.TestCase):
    def pair(self, key, ident=1):
        return {'pair_id': ident, 'pair_type': 'tranzor',
                'source_id': f"RingCentral.webModule.{'a'*32}.app.visualIVR.{key}",
                'source': '<span data-ext="{extension}">{menuName}, Ext.{extension}</span>: original source',
                'target': '<span data-ext="{extension}">{menuName}, Ext.{extension}</span>: corrected target',
                'target_language': 'en-GB', 'project': 'web/web', 'updated_date': '2026-09-10T11:00:40'}

    def test_six_keys_without_any_history_use_live_pairs_and_preserve_ice_difference(self):
        seen = []
        def store(**kw):
            seen.append(kw['key'])
            return {'store':'shared_tm', 'entries':[self.pair(kw['key'], KEYS.index(kw['key'])+1)], 'total':1}
        def ice(items, langs):
            self.assertEqual(langs, ['en-GB'])
            return [{'opusID': i['opusID'], 'stringValue': i['stringValue'],
                     'translationMemoryMatchingResult': {'type':'ICE', 'translations':{'en-GB':'old ext.'}}} for i in items]
        view = tm.search_tm_batch(tm.QueryIntent(query='\n'.join(KEYS), target_language='en-GB'),
            search_fn=lambda **kw: {'entries':[], 'total':0}, store_fn=store, ice_fn=ice)
        self.assertEqual(seen,KEYS)
        self.assertEqual(view['kpis']['shared_hits'],6)
        self.assertEqual(view['kpis']['record_rows'],0)
        self.assertEqual(view['kpis']['ice_store_hits'],6)
        rows=[r for g in view['lineages'] for b in g['hashes'] for r in b['records']]
        self.assertTrue(all('Ext.' in r['translated_text'] for r in rows))
        self.assertTrue(all(h['translations']['en-GB']=='old ext.' for h in view['ice']))

    def test_ice_only_still_resolves_source_from_live_store(self):
        probes=[]
        def ice(items, langs):
            probes.extend(items)
            return []
        view=tm.search_tm_batch(tm.QueryIntent(query='EMPTY_TEXT', layers=frozenset({'ice'})),
            store_fn=lambda **kw: {'store':'shared_tm','entries':[self.pair('EMPTY_TEXT')],'total':1}, ice_fn=ice)
        self.assertEqual(len(probes),1)
        self.assertIn('Ext.', probes[0]['stringValue'])
        self.assertEqual(view['status']['shared'],'ok')

    def test_store_failure_is_not_empty_or_history_provenance(self):
        def unavailable(**kw): raise RuntimeError('API is not deployed')
        view=tm.search_tm_batch(tm.QueryIntent(query='EMPTY_TEXT'),store_fn=unavailable)
        self.assertEqual(view['status']['shared'],'error')
        self.assertTrue(view['errors'])
        self.assertEqual(view['status']['ice'],'no_probe')

    def test_404_and_malformed_payload(self):
        with patch('requests.get', return_value=Mock(status_code=404)):
            with self.assertRaisesRegex(RuntimeError,'not deployed'):
                tm.default_search_store(key='EMPTY_TEXT')
        with self.assertRaises(ValueError):
            tm.search_live_store(tm.QueryIntent(query='x'),lambda **kw: {'entries': [], 'total':0})

    def test_new_request_observes_changed_store_text(self):
        pair=self.pair('EMPTY_TEXT')
        def store(**kw): return {'store':'shared_tm','entries':[dict(pair)],'total':1}
        first=tm.search_live_store(tm.QueryIntent(query='EMPTY_TEXT'),store)[0][0]
        pair['target']='new server value'
        second=tm.search_live_store(tm.QueryIntent(query='EMPTY_TEXT'),store)[0][0]
        self.assertNotEqual(first['translated_text'],second['translated_text'])

class LivePaginationTests(unittest.TestCase):
    def test_all_pages_and_report_limit(self):
        offsets=[]
        pair=LiveStoreTests().pair('EMPTY_TEXT')
        def store(**kw):
            offsets.append(kw['offset'])
            return {'store':'shared_tm','total':2001,
                    'entries':[{**pair,'pair_id':kw['offset']+i} for i in range(200)]}
        rows,truncated=tm.search_live_store(tm.QueryIntent(query='EMPTY'),store)
        self.assertEqual(len(rows),2000)
        self.assertEqual(offsets,list(range(0,2000,200)))
        self.assertTrue(truncated)


if __name__=='__main__': unittest.main()
