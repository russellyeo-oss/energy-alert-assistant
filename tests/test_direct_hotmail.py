import unittest
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import direct_hotmail as d

def message(sender='googlealerts@google.com'):
    return {'id':'id1','from':{'emailAddress':{'address':sender}},
            'sender':{'emailAddress':{'address':sender}},'categories':[]}

class DirectTests(unittest.TestCase):
    def test_only_google_sender_allowed(self):
        self.assertTrue(d.eligible(message()))
        self.assertFalse(d.eligible(message('other@example.com')))
        m = message()
        m['sender']['emailAddress']['address'] = 'other@example.com'
        self.assertFalse(d.eligible(m))

    def test_probe_only_reads(self):
        with patch.object(d,'folder',side_effect=['source','destination']), patch.object(d,'pages',return_value=[message()]), patch.object(d,'mark') as mark, patch.object(d,'move') as move, patch.object(d,'request') as req:
            d.run(MagicMock(),probe=True)
            mark.assert_not_called()
            move.assert_not_called()
            req.assert_not_called()

    def test_category_keeps_unrelated_labels(self):
        m = message()
        m['categories'] = ['Personal',d.CLAIM]
        with patch.object(d,'request') as req:
            d.mark(MagicMock(),m,d.COMPLETE)
            self.assertEqual(req.call_args.kwargs['json'],{'categories':['Personal',d.COMPLETE]})

    def test_digest_accepted_before_move(self):
        m = message()
        calls = []
        article = {'headline':'Gas news','url':'https://example.com/news'}
        editorial = SimpleNamespace(extract_article_links=lambda _: [article],
                                   filter_relevant_articles=lambda articles: articles)
        with patch.dict(sys.modules,{'energy_alert_cloud':editorial}), patch.object(d,'folder',side_effect=['source','destination']), patch.object(d,'pages',return_value=[m]), patch.object(d,'mark',side_effect=lambda s,m,c: calls.append(c)), patch.object(d,'move',side_effect=lambda *a: calls.append('move')), patch.object(d,'request',side_effect=lambda *a,**k: (calls.append('send') or SimpleNamespace(status_code=202))):
            d.run(MagicMock())
        self.assertEqual(calls,[d.CLAIM,'send',d.COMPLETE,'move'])

    def test_failed_send_retains_message(self):
        article = {'headline':'Gas news','url':'https://example.com/news'}
        editorial = SimpleNamespace(extract_article_links=lambda _: [article],
                                   filter_relevant_articles=lambda articles: articles)
        with patch.dict(sys.modules,{'energy_alert_cloud':editorial}), patch.object(d,'folder',side_effect=['source','destination']), patch.object(d,'pages',return_value=[message()]), patch.object(d,'mark') as mark, patch.object(d,'move') as move, patch.object(d,'request',side_effect=RuntimeError('delivery error')):
            with self.assertRaises(RuntimeError):
                d.run(MagicMock())
            self.assertEqual(mark.call_count,1)
            self.assertEqual(mark.call_args.args[-1],d.CLAIM)
            move.assert_not_called()

if __name__ == '__main__':
    unittest.main()
