import unittest
from unittest.mock import MagicMock, patch
import direct_hotmail as d

class QueueDeliveryTests(unittest.TestCase):
    def message(self):
        return {'subject':'Energy Alert Assistant – 1 relevant link(s)',
                'body':{'contentType':'HTML','content':'<a href="https://example.com">News</a>'},
                'toRecipients':[{'emailAddress':{'address':d.DESTINATION}}]}

    @patch.dict('os.environ', {'ENERGY_DELIVERY_MODE':'queue'})
    def test_queue_html(self):
        session = MagicMock()
        with patch.object(d,'folder',return_value='queue/id') as folder, patch.object(d,'request') as request:
            request.return_value.status_code = 201
            d.submit_digest(session,self.message())
            folder.assert_called_once_with(session,'ENB Outbound Queue')
            request.assert_called_once_with(session,'POST','/me/mailFolders/queue%2Fid/messages',json=self.message())

    @patch.dict('os.environ', {'ENERGY_DELIVERY_MODE':'queue'})
    def test_queue_failure_no_fallback(self):
        with patch.object(d,'folder',return_value='queue'), patch.object(d,'request') as request:
            request.return_value.status_code = 202
            with self.assertRaises(RuntimeError):
                d.submit_digest(MagicMock(),self.message())
            self.assertEqual(request.call_count,1)

    @patch.dict('os.environ', {'ENERGY_DELIVERY_MODE':'queue'})
    def test_missing_folder_no_send(self):
        with patch.object(d,'folder',side_effect=RuntimeError('missing')), patch.object(d,'request') as request:
            with self.assertRaises(RuntimeError):
                d.submit_digest(MagicMock(),self.message())
            request.assert_not_called()

    @patch.dict('os.environ', {'ENERGY_DELIVERY_MODE':'queue'})
    def test_recipient_allowlist(self):
        message = self.message()
        message['toRecipients'][0]['emailAddress']['address'] = 'other@example.com'
        with patch.object(d,'request') as request:
            with self.assertRaises(RuntimeError):
                d.submit_digest(MagicMock(),message)
            request.assert_not_called()
