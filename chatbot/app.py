from collections import OrderedDict
from pprint import pprint

from flask import render_template, request, Response
import requests
import traceback

from chatbot import app
from chatbot.configs import *
from chatbot.conversation import *
from chatbot.core import *
from chatbot.utils import *

db.init_app(app)
load_bot_configs(app.config)
setup_caching(app.config)

@app.route('/chat', methods=['POST'])
def chat():
    debug = request.values.get('debug', None)
    try:
        input = json.loads(request.values['input'])
        bot = request.values['bot']
        bot_config = None
        if app.config['DEBUG']:
            bot_config = json.loads(request.values.get('bot_config', '{}'))
        convo_id = request.values.get('conversation_id', None)
        dbg('Conversation ID: %s / Bot: %s' % (convo_id, bot))
        dbg('Input: %s' % input)

        convo_cache = get_convo_cache(app.config)

        if convo_id:
            convo = convo_cache.get(convo_id, None)
            if not convo:
                response = {'status': 'error', 'response': 'No conversation found for ID %s' % convo_id}
                return jsonr(response)
            if convo.completed:
                response = {'status': 'error', 'response': 'Conversation %s is already completed' % convo_id}
                return jsonr(response)
        else:
            dbg('Creating new conversation')
            convo = Conversation(bot, bot_config=bot_config)
            convo_id = convo.id
            convo.save()

        tx = convo.create_transaction()

        reply = convo.reply(tx, input)
        dbg('Replying: %s' % reply)

        convo.save()
        tx.save()

        convo_cache[convo_id] = convo
        response = {'status': 'success',
                    'response': reply,
                    'conversation_id': convo_id,
                    'transaction_id': tx.id,
                    'completed_intent_name': tx.completed_intent_name,
                    'completed_conversation': convo.completed,
                    'fulfillment_data': convo.get_fulfillment_data(tx, tx.completed_intent_name) if tx.completed_intent_name else None}
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
    dbg('fulfillment called')
    dbg(data)
    response = {'status': 'success', 'message': None}
    return jsonr(response)

@app.route('/fulfillment_with_message', methods=['POST'])
def fulfillment_with_message():
    data = request.json
    dbg('fulfillment_with_message called')
    dbg(data)
    response = {'status': 'success', 'message': 'Great job, you finished this!'}
    return jsonr(response)

@app.route('/fulfillment_with_question', methods=['POST'])
def fulfillment_with_question():
    data = request.json
    dbg('fulfillment_with_question called')
    dbg(data)
    response = {'status': 'success',
                'message': {'type': 'question',
                            'prompts': ['I couldnt find anyone to help. Would you like to try MyIntent instead?'],
                            'intent_actions': {CommonIntents.Yes: 'TriggerMyIntent',
                                               CommonIntents.No: Actions.EndConversation}}}
    return jsonr(response)

@app.route('/fulfillment_with_action', methods=['POST'])
def fulfillment_with_action():
    data = request.json
    dbg('fulfillment_with_action called')
    dbg(data)
    response = {'status': 'success', 'message': 'Great job, you are done.', 'action': Actions.EndConversation}
    return jsonr(response)

@app.route('/fulfillment_with_error_status', methods=['POST'])
def fulfillment_with_error_status():
    data = request.json
    dbg('fulfillment_with_error_status called')
    dbg(data)
    response = {'status': 'error', 'message': None, 'status_reason': 'Fulfillment failed'}
    return jsonr(response)

if __name__ == "__main__":
    if app.config.get('DEBUG', False) and app.config.get('PROFILE', False):
        from werkzeug.contrib.profiler import ProfilerMiddleware
        warn('Using Profiler')
        f = open('/tmp/chatbot_profiler.log', 'w')
        app.wsgi_app = ProfilerMiddleware(app.wsgi_app, stream=f, restrictions=[20])
    app.run(port=9000)
