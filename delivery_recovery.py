"""Retry only confirmed Microsoft NDRs for our own standalone reports.

Mailbox Sent Items provides durable report content and claim state. Never
resend raw newsletters, never retry solely because delivery is unconfirmed.
"""
import re
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from urllib.parse import quote

from bs4 import BeautifulSoup
from direct_hotmail import API, DESTINATION, authenticate, pages, request

CLAIM = 'Energy report retry claimed'
SUBMITTED = 'Energy report retry submitted'
EXHAUSTED = 'Energy report retry limit reached'
REVIEW = 'Energy report retry needs review'
PREFIXES = ('Oz Check — ', 'Energy Alert Assistant – ')
RETRY = re.compile(r' \[retry ([12])/2\]$')
RETRYABLE = re.compile(r'\b5\.7\.(?:501|520)\b')
ID = re.compile(r'(?im)^\s*(?:original-)?message-id:\s*(<[^\s<>]+>)')


def address(item):
    return item.get('emailAddress', {}).get('address', '').casefold()


def trusted_sender(message):
    sender = address(message.get('from', {}))
    actual = address(message.get('sender', {}))
    if not sender or sender != actual or '@' not in sender:
        return False
    local, domain = sender.rsplit('@', 1)
    return local in ('postmaster', 'mailer-daemon') and any(
        domain == d or domain.endswith('.' + d)
        for d in ('outlook.com', 'hotmail.com', 'live.com', 'microsoft.com',
                  'office365.com', 'outlook.office365.com'))


def plain(body):
    content = body.get('content', '')
    if body.get('contentType', '').casefold() == 'html':
        return BeautifulSoup(content, 'html.parser').get_text('\n')
    return content


def failure_ids(message, raw):
    """Require a named work recipient, a specific spam code and original ID.

    Microsoft NDRs sometimes render original headers as text rather than a
    standards-compliant delivery-status attachment. Accept either format.
    """
    body = plain(message.get('body', {}))
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    ids = set(ID.findall(body))
    text_parts = [body]
    for part in parsed.walk():
        kind = part.get_content_type()
        if kind == 'message/rfc822':
            for original in part.get_payload() or []:
                if original.get('Message-ID'):
                    ids.add(str(original['Message-ID']).strip())
        elif kind == 'text/rfc822-headers':
            payload = part.get_payload(decode=True) or b''
            ids.update(ID.findall(payload.decode('utf-8', errors='replace')))
        elif kind == 'message/delivery-status':
            for block in part.get_payload() or []:
                text_parts.append(str(block))
                if block.get('Original-Message-ID'):
                    ids.add(str(block['Original-Message-ID']).strip())
        elif kind == 'text/plain':
            try:
                text = part.get_content()
                text_parts.append(text)
                ids.update(ID.findall(text))
            except (ValueError, LookupError, TypeError):
                pass
    text = '\n'.join(text_parts)
    if DESTINATION.casefold() not in text.casefold() or not RETRYABLE.search(text):
        return []
    own_id = message.get('internetMessageId')
    return sorted(i for i in ids if i != own_id and i.startswith('<') and i.endswith('>'))


def report_attempt(subject):
    if not subject.startswith(PREFIXES):
        return None
    match = RETRY.search(subject)
    return int(match.group(1)) if match else 0


def sent_report(session, internet_id):
    if len(internet_id) > 500:
        return None
    literal = internet_id.replace("'", "''")
    candidates = list(pages(session, '/me/mailFolders/sentitems/messages', {
        '$filter': "internetMessageId eq '" + literal + "'",
        '$select': 'id,internetMessageId,subject,body,toRecipients,ccRecipients,bccRecipients,categories,isDraft,hasAttachments',
        '$top': 10}))
    matched = [m for m in candidates if m.get('internetMessageId') == internet_id
               and report_attempt(m.get('subject', '')) is not None
               and [address(x) for x in m.get('toRecipients', [])] == [DESTINATION.casefold()]
               and not m.get('ccRecipients') and not m.get('bccRecipients')
               and not m.get('isDraft') and not m.get('hasAttachments')
               and m.get('body', {}).get('content')]
    return matched[0] if len(matched) == 1 else None


def mark(session, message, category):
    categories = list(dict.fromkeys(message.get('categories', []) + [category]))
    request(session, 'PATCH', '/me/messages/' + quote(message['id'], safe=''),
            json={'categories': categories})
    message['categories'] = categories


def retry_report(session, original):
    attempt = report_attempt(original.get('subject', ''))
    if attempt is None:
        return 'not-report'
    categories = original.get('categories', [])
    if CLAIM in categories:
        return 'already-claimed'
    if attempt >= 2:
        if EXHAUSTED not in categories:
            mark(session, original, EXHAUSTED)
        return 'limit-reached'
    subject = RETRY.sub('', original['subject']) + f' [retry {attempt + 1}/2]'
    body = original['body']
    payload = {'message': {'subject': subject,
                           'body': {'contentType': body['contentType'], 'content': body['content']},
                           'toRecipients': [{'emailAddress': {'address': DESTINATION}}]},
               'saveToSentItems': True}
    # Claim the exact original Sent Item BEFORE submission. Multiple NDRs
    # for one send cannot cause multiple retries. Ambiguous submissions
    # require review rather than a blind resend.
    mark(session, original, CLAIM)
    try:
        response = request(session, 'POST', '/me/sendMail', json=payload)
        if response.status_code != 202:
            raise RuntimeError('Retry acceptance not confirmed')
    except Exception:
        mark(session, original, REVIEW)
        raise
    mark(session, original, SUBMITTED)
    return 'submitted'


def run(session, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=7)).isoformat(timespec='seconds').replace('+00:00', 'Z')
    incoming = list(pages(session, '/me/mailFolders/inbox/messages', {
        '$filter': 'receivedDateTime ge ' + cutoff,
        '$select': 'id,subject,from,sender,internetMessageId,receivedDateTime,body,categories',
        '$top': 100}))
    submitted = reviewed = exhausted = 0
    for notice in incoming:
        if not trusted_sender(notice):
            continue
        if any(c in notice.get('categories', []) for c in (SUBMITTED, EXHAUSTED, REVIEW)):
            continue
        try:
            received = datetime.fromisoformat(notice['receivedDateTime'].replace('Z', '+00:00'))
        except (KeyError, ValueError):
            continue
        if now - received < timedelta(minutes=30):
            continue
        raw = request(session, 'GET', '/me/messages/' + quote(notice['id'], safe='') + '/$value').content
        ids = failure_ids(notice, raw)
        if not ids:
            continue
        originals = [m for i in ids if (m := sent_report(session, i)) is not None]
        if len(originals) != 1:
            mark(session, notice, REVIEW)
            reviewed += 1
            continue
        original = originals[0]
        try:
            outcome = retry_report(session, original)
        except Exception:
            mark(session, notice, REVIEW)
            print('::warning::Automated report retry submission uncertain; retained in Hotmail for review.')
            reviewed += 1
            continue
        if outcome == 'submitted' or (outcome == 'already-claimed' and SUBMITTED in original.get('categories', [])):
            mark(session, notice, SUBMITTED)
            submitted += outcome == 'submitted'
        elif outcome == 'limit-reached':
            mark(session, notice, EXHAUSTED)
            exhausted += 1
        else:
            mark(session, notice, REVIEW)
            reviewed += 1
    print(f'Delivery recovery: retries submitted {submitted}; review required {reviewed}; retry limit reached {exhausted}')
    if reviewed or exhausted:
        print('::warning::Unresolved delivery failures retained in Hotmail Inbox and Sent Items; manual review required.')


if __name__ == '__main__':
    run(authenticate())
