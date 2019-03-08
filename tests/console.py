import json
from pprint import pprint

from test import make_request

convo_id = None
bot = input('BOT NAME: ')

while True:
    text = input('USER: ')
    resp = make_request(bot, text, convo_id=convo_id)
    data = resp.json()
    print('BOT:', data['response'])
    if not convo_id:
        convo_id = data['conversation_id']
    else:
        assert convo_id == data['conversation_id'], 'Conversation ID mismatch'
