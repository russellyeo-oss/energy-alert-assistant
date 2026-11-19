"""Direct, opt-in Google Alerts intake. No original email is forwarded or logged."""
import argparse
import base64
import html
import os
from pathlib import Path
from urllib.parse import quote, urlparse

import msal
import requests
from cryptography.fernet import Fernet

API = 'https://graph.microsoft.com/v1.0'
CLIENT = '94ff3ea8-9a85-4eb3-a3f6-015eb054e7ca'
ACCOUNT = 'russellyeo@hotmail.co.uk'
DESTINATION = 'russell.yeo@aspermont.com'
INBOX = 'Energy Alerts To Process'
DONE = 'Energy Alerts Processed'
CLAIM = 'EnergyAlertAssistant delivery pending'
COMPLETE = 'EnergyAlertAssistant complete'
SCOPES = ['https://graph.microsoft.com/Mail.ReadWrite', 'https://graph.microsoft.com/Mail.Send']
CACHE = Path('.hotmail-cache/token.enc')


def authenticate():
    key = Fernet(os.environ['ENERGY_HOTMAIL_CACHE_KEY'].encode())
    raw = (key.decrypt(CACHE.read_bytes()).decode() if CACHE.exists()
           else base64.b64decode(os.environ['ENERGY_HOTMAIL_TOKEN_B64']).decode())
    cache = msal.SerializableTokenCache()
    cache.deserialize(raw)
    app = msal.PublicClientApplication(CLIENT, authority='https://login.microsoftonline.com/consumers', token_cache=cache)
    accounts = app.get_accounts()
    if len(accounts) != 1 or accounts[0].get('username', '').casefold() != ACCOUNT:
        raise RuntimeError('Wrong personal account; no processing performed')
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result or not result.get('access_token'):
        raise RuntimeError('Hotmail authorisation renewal required')
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(key.encrypt(cache.serialize().encode()))
    session = requests.Session()
    session.headers.update({'Authorization': 'Bearer ' + result['access_token'],
                            'Prefer': 'IdType="ImmutableId"'})
    return session


def request(session, method, path, **kwargs):
    url = path if path.startswith('https://') else API + path
    if urlparse(url).netloc != 'graph.microsoft.com':
        raise RuntimeError('Unexpected Graph pagination destination')
    response = session.request(method, url, timeout=40, **kwargs)
    response.raise_for_status()
    return response


def pages(session, path, params=None):
    while path:
        data = request(session, 'GET', path, params=params).json()
        yield from data.get('value', [])
        path, params = data.get('@odata.nextLink'), None


def folder(session, name):
    matches = [x['id'] for x in pages(session, '/me/mailFolders/inbox/childFolders', {'$top':100})
               if x.get('displayName','').casefold() == name.casefold()]
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one personal Inbox subfolder: ' + name)
    return matches[0]


def eligible(message):
    def address(field):
        return message.get(field, {}).get('emailAddress', {}).get('address', '').casefold()
    return address('from') == 'googlealerts@google.com' and address('sender') == 'googlealerts@google.com'


def message_path(message):
    return '/me/messages/' + quote(message['id'], safe='')


def mark(session, message, category):
    categories = [x for x in message.get('categories', []) if x not in (CLAIM, COMPLETE)] + [category]
    request(session, 'PATCH', message_path(message), json={'categories':categories})
    message['categories'] = categories


def move(session, message, destination):
    request(session, 'POST', message_path(message) + '/move', json={'destinationId':destination})


def run(session, probe=False):
    source, destination = folder(session, INBOX), folder(session, DONE)
    messages = list(pages(session, '/me/mailFolders/' + quote(source,safe='') + '/messages',
                          {'$top':50, '$select':'id,subject,from,sender,body,categories'}))
    google = [m for m in messages if eligible(m)]
    print(f'Folder visible: {len(messages)} messages; {len(google)} verified Google Alert senders')
    if probe:
        return
    from energy_alert_cloud import extract_article_links, filter_relevant_articles
    pending = []
    candidates = {}
    for m in google:
        if COMPLETE in m.get('categories', []):
            move(session, m, destination)
            continue
        if CLAIM in m.get('categories', []):
            print('ATTENTION: claimed message retained for Sent Items reconciliation')
            continue
        links = extract_article_links(m.get('body', {}).get('content', ''))
        if not links:
            print('Unparsed or all-excluded alert retained for review')
            continue
        pending.append(m)
        for a in links:
            if urlparse(a['url']).scheme in ('http','https'):
                candidates.setdefault(a['url'], a)
    if not pending:
        return
    relevant = filter_relevant_articles(list(candidates.values()))
    if relevant:
        lines = ['<p><b>Relevant energy links</b></p>']
        for a in relevant:
            lines.append('<p><a href="' + html.escape(a['url'], quote=True) + '">' +
                         html.escape(a['headline'] or a['url']) + '</a></p>')
        lines.append(f'<hr><p>Processed {len(pending)} Google Alert emails.</p>')
        for m in pending:
            mark(session, m, CLAIM)
        result = request(session, 'POST','/me/sendMail', json={
            'message':{'subject':f'Energy Alert Assistant – {len(relevant)} relevant link(s)',
                       'body':{'contentType':'HTML','content':'\n'.join(lines)},
                       'toRecipients':[{'emailAddress':{'address':DESTINATION}}]},
            'saveToSentItems':True})
        if result.status_code != 202:
            raise RuntimeError('Digest acceptance not confirmed; claimed messages retained')
    for m in pending:
        mark(session, m, COMPLETE)
    for m in pending:
        move(session, m, destination)
    print(f'Processed {len(pending)} alerts; {len(relevant)} relevant links')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', action='store_true')
    run(authenticate(), parser.parse_args().probe)
