"""Run locally only, with GitHub CLI logged into your own account."""
import base64
import subprocess
import msal
from cryptography.fernet import Fernet
from direct_hotmail import CLIENT, ACCOUNT, SCOPES

REPO = 'russellyeo-oss/energy-alert-assistant'

def main():
    subprocess.run(['gh','auth','status'], check=True)
    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(CLIENT, authority='https://login.microsoftonline.com/consumers', token_cache=cache)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if 'user_code' not in flow:
        raise RuntimeError('Could not start Microsoft device sign-in')
    print('Sign in with your personal Hotmail account only.')
    print(flow['message'])
    result = app.acquire_token_by_device_flow(flow)
    accounts = app.get_accounts()
    if not result.get('access_token') or len(accounts) != 1 or accounts[0].get('username','').casefold() != ACCOUNT:
        raise RuntimeError('Authorisation failed or incorrect account; no secrets changed')
    for name, value in [('ENERGY_HOTMAIL_CACHE_KEY', Fernet.generate_key()),
                        ('ENERGY_HOTMAIL_TOKEN_B64', base64.b64encode(cache.serialize().encode()))]:
        subprocess.run(['gh','secret','set',name,'--repo',REPO],input=value,check=True)
    print('Hotmail secrets installed. Run the read-only probe before switching the rule.')

if __name__ == '__main__':
    main()
