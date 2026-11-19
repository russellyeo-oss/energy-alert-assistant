import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import delivery_recovery as d

ID = '<original@example.com>'
RAW = b'From: postmaster@outlook.com\r\nContent-Type: text/plain\r\n\r\n550 5.7.501 Service unavailable.\r\nMessage-ID: <original@example.com>\r\n'

def notice():
    sender = {'emailAddress': {'address':'postmaster@outlook.com'}}
    return {'id':'ndr','from':sender,'sender':sender,
            'internetMessageId':'<ndr@example.com>',
            'receivedDateTime':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),
            'body':{'contentType':'text','content':d.DESTINATION+'\n550 5.7.501 Spam abuse detected\nMessage-ID:\n '+ID},
            'categories':[]}

def original(subject='Oz Check — Nothing of note found'):
    return {'id':'sent','subject':subject,'internetMessageId':ID,
            'toRecipients':[{'emailAddress':{'address':d.DESTINATION}}],
            'body':{'contentType':'Text','content':'Original report'},'categories':[]}

class RecoveryTests(unittest.TestCase):
    def test_matching_microsoft_ndr(self):
        self.assertTrue(d.trusted_sender(notice()))
        self.assertEqual(d.failure_ids(notice(),RAW),[ID])
        n=notice()
        n['sender']={'emailAddress':{'address':'postmaster@evil.example'}}
        self.assertFalse(d.trusted_sender(n))
    def test_unrelated_error_or_recipient_ignored(self):
        n=notice()
        n['body']['content']=n['body']['content'].replace(d.DESTINATION,'other@example.com')
        self.assertEqual(d.failure_ids(n,RAW),[])
        n=notice()
        n['body']['content']=n['body']['content'].replace('5.7.501','5.1.1')
        self.assertEqual(d.failure_ids(n, RAW.replace(b'5.7.501',b'5.1.1')),[])
    def test_original_body_and_claim_before_retry(self):
        m=original()
        with patch.object(d,'request') as req:
            req.return_value.status_code=202
            self.assertEqual(d.retry_report(None,m),'submitted')
            self.assertEqual(req.call_args_list[0].args[1],'PATCH')
            payload=req.call_args_list[1].kwargs['json']
            self.assertEqual(payload['message']['body'],m['body'])
            self.assertTrue(payload['message']['subject'].endswith('[retry 1/2]'))
            self.assertEqual(d.retry_report(None,m),'already-claimed')
            self.assertEqual(req.call_count,3)
    def test_second_retry_and_limit(self):
        with patch.object(d,'request') as req:
            req.return_value.status_code=202
            self.assertEqual(d.retry_report(None,original('Energy Alert Assistant – 3 relevant link(s) [retry 1/2]')),'submitted')
            self.assertTrue(req.call_args_list[1].kwargs['json']['message']['subject'].endswith('[retry 2/2]'))
            req.reset_mock()
            self.assertEqual(d.retry_report(None,original('Oz Check — Australian energy findings [retry 2/2]')),'limit-reached')
            self.assertEqual(req.call_count,1)
    def test_delay_and_duplicate_notice(self):
        n=notice()
        recent=notice()
        recent['receivedDateTime']=datetime.now(timezone.utc).isoformat()
        m=original()
        with patch.object(d,'pages',return_value=iter([recent,n,n])), patch.object(d,'sent_report',return_value=m), patch.object(d,'request') as req:
            req.return_value.content=RAW
            req.return_value.status_code=202
            d.run(None)
            self.assertEqual(sum(c.args[1]=='POST' for c in req.call_args_list),1)
    def test_wrong_original_recipient_or_nonreport(self):
        m=original('Unrelated email')
        with patch.object(d,'pages',return_value=iter([m])):
            self.assertIsNone(d.sent_report(None,ID))
        m=original()
        m['toRecipients']=[]
        with patch.object(d,'pages',return_value=iter([m])):
            self.assertIsNone(d.sent_report(None,ID))
    def test_uncertain_submission_is_not_retried(self):
        m=original()
        with patch.object(d,'request') as req:
            req.return_value.status_code=500
            with self.assertRaises(RuntimeError):
                d.retry_report(None,m)
            self.assertIn(d.REVIEW,m['categories'])
            self.assertEqual(d.retry_report(None,m),'already-claimed')

if __name__=='__main__':
    unittest.main()
