import json
from pprint import pprint

from test import make_request

convo_id = None

while True:
    text = raw_input('USER: ')
    resp = make_request(text, convo_id=convo_id)
    data = resp.json()
    print 'BOT:', data['response']
    if not convo_id:
        convo_id = data['conversation_id']
    else:
        assert convo_id == data['conversation_id'], 'Conversation ID mismatch'
