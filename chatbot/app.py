from cachetools import TTLCache
from collections import OrderedDict
import json

from flask import render_template, request, Response
import requests
import traceback

from chatbot import app
from chatbot.utils import *
from chatbot.conversation import *

# TODO: replace with an external cache
CACHE_SIZE = 100
CACHE_TTL = 3600*48
CONVO_CACHE = TTLCache(CACHE_SIZE, CACHE_TTL)

@app.route('/')
def home():
    return render_template('home.html', conversation_id=request.values.get('id', None))

@app.route('/chat', methods=['POST'])
def chat():
    debug = request.values.get('debug', None)
    try:
        input = json.loads(request.values['input'])
        conversation_id = request.values['conversation_id']
        dbg('Conversation ID: %s' % conversation_id, color='green')
        dbg('Input: %s' % input, color='green')

        convo = CONVO_CACHE.get(conversation_id, None)
        if not convo:
            dbg('Creating new conversation', color='green')
            convo = Conversation(conversation_id)

        tx = convo.create_transaction()

        reply = convo.reply(tx, input)
        dbg('Replying: %s' % reply, color='green')
        
        CONVO_CACHE[conversation_id] = convo
        response = {'status': 'success', 'response': reply}
        if debug:
            response['tx'] = tx
        return jsonr(response)

    except Exception, e:
        print traceback.format_exc()
        error(str(e))
        response = {'status': 'success', 'response': 'Something went wrong.'}
        if debug: response['error'] = traceback.format_exc()
        return jsonr(response)

if __name__ == "__main__":
    app.run(port=9000)
    
