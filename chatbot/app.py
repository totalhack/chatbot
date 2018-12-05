from cachetools import TTLCache
from collections import OrderedDict
from pprint import pprint
import json

from flask import render_template, request, Response
import requests
import traceback

from chatbot import app
from chatbot.conversation import *
from chatbot.utils import *

db.init_app(app)
set_app_data_from_config(app.config)

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
        convo_id = request.values.get('conversation_id', None)
        dbg('Conversation ID: %s' % convo_id, color='green')
        dbg('Input: %s' % input, color='green')

        if convo_id:
            convo = CONVO_CACHE.get(convo_id, None)
            if not convo:
                response = {'status': 'error', 'response': 'No conversation found for ID %s' % convo_id}
                return jsonr(response)
        else:
            dbg('Creating new conversation', color='green')
            convo = Conversation()
            convo_id = convo.id
            convo.save()

        tx = convo.create_transaction()

        reply = convo.reply(tx, input)
        dbg('Replying: %s' % reply, color='green')

        convo.save()
        tx.save()

        CONVO_CACHE[convo_id] = convo
        response = {'status': 'success',
                    'response': reply,
                    'conversation_id': convo_id,
                    'transaction_id': tx.id,
                    'completed_intent': tx.completed_intent,
                    'completed_conversation': convo.completed,
                    'fulfillment_data': tx.completed_intent.fulfillment_data if tx.completed_intent else None}
        if debug:
            response['transaction'] = tx
        return jsonr(response)

    except Exception, e:
        print traceback.format_exc()
        error(str(e))
        response = {'status': 'error', 'response': 'Something went wrong.'}
        if debug: response['error'] = traceback.format_exc()
        return jsonr(response)

@app.route('/fulfillment', methods=['POST'])
def fulfillment():
    data = request.json
    dbg('fulfillment called', color='magenta')
    pprint(data)
    response = {'status': 'success', 'message': None}
    #response = {'status': 'success', 'message': 'Great job, you finished this!'}
    #response = {'status': 'success',
    #            'message': {'type': 'question',
    #                        'prompts': ['I couldnt find anyone to help. Would you like to try MyIntent instead?'],
    #                        'intent_actions': {CommonIntents.CONFIRM_YES: 'TriggerMyIntent',
    #                                           CommonIntents.CONFIRM_NO: Actions.END_CONVERSATION}}}
    return jsonr(response)

if __name__ == "__main__":
    app.run(port=9000)

