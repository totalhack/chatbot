"""App server with chat() endpoint"""
import traceback

from cachetools import TTLCache
from flask import request, Response, current_app
from slackclient import SlackClient
from slackeventsapi import SlackEventAdapter

from chatbot import app
from chatbot.configs import load_bot_configs
from chatbot.conversation import Conversation, Channel, Input, ErrorOutput, SuccessOutput
from chatbot.core import Actions, CommonIntents, get_convo_cache, setup_caching
from chatbot.model import db
from chatbot.utils import dbg, jsonr
from toolbox import warn, error, json, st

db.init_app(app)
load_bot_configs(app.config)
setup_caching(app.config)

slack_events_adapter = SlackEventAdapter(app.config['SLACK_SIGNING_SECRET'], endpoint="/slack/event", server=app)
slack_client = SlackClient(app.config['SLACK_BOT_TOKEN'])

# TODO: better home and configurable for production
SLACK_CONVO_CACHE_SIZE = 1000
SLACK_CONVO_CACHE_TTL = 60*60
slack_convo_cache = TTLCache(SLACK_CONVO_CACHE_SIZE, SLACK_CONVO_CACHE_TTL)

@slack_events_adapter.on("app_mention")
def slack_app_mention(event_data):
    dbg('App mention!')
    dbg(event_data)

@slack_events_adapter.on("message")
def slack_message(event_data):
    dbg('Message!')
    dbg(event_data)

    event = event_data["event"]
    slack_app_id = event_data["api_app_id"]
    slack_channel = event["channel"]
    slack_channel_type = event["channel_type"]
    slack_user = event.get("user", event.get('username', None))
    # message = "Hello <@%s>! :tada:" % message["user"]

    bot = app.config['SLACK_APP_BOT_MAP'].get(slack_app_id, None)
    assert bot, 'Could not find bot mapped for slack app: %s' % slack_app_id

    slack_convo_id = '%s:%s' % (slack_channel, slack_user)
    convo_id = slack_convo_cache.get(slack_convo_id, None)
    # TODO: ability to pass more channel-specific info to store
    channel = Channel.create('slack')

    if event.get("subtype") is None:
        input = channel.format_input(event['text'], context=event)
        output = converse(channel, bot, convo_id, input)
        if not convo_id:
            slack_convo_cache[slack_convo_id] = output['conversation_id']

        dbg(output)
        if output['status'] == 'error':
            msg = 'An error occurred. Please try again later.'
            slack_client.api_call("chat.postMessage", channel=slack_channel, text=msg)
        else:
            for msg in output['value']:
                # TODO: change API call based on message type
                slack_client.api_call("chat.postMessage", channel=slack_channel, text=msg['text'])

@slack_events_adapter.on("error")
def slack_error(err):
    error(str(err))

@app.route('/slack/message_action', methods=['POST'])
def slack_message_action():
    dbg('Message Action!')
    data = request.get_json()
    dbg(data)

@app.route('/slack/message_options', methods=['POST'])
def slack_message_options():
    dbg('Message Options!')
    data = request.get_json()
    dbg(data)

def converse(channel, bot, convo_id, input, bot_config=None):
    dbg('Conversation ID: %s / Bot: %s' % (convo_id, bot))
    dbg('Input: %s' % input)

    convo_cache = get_convo_cache(app.config)

    if convo_id:
        convo = convo_cache.get(convo_id, None)
        if not convo:
            return ErrorOutput('No conversation found for ID %s' % convo_id)
        if convo.completed:
            return ErrorOutput('Conversation %s is already completed' % convo_id)
    else:
        dbg('Creating new conversation')
        convo = Conversation(bot, bot_config=bot_config)
        convo_id = convo.id
        convo.save()

    tx = convo.create_transaction(Channel.create(channel), input=Input(input))

    reply = convo.reply(tx)
    dbg('Replying: %s' % reply)

    convo.save()
    tx.save()

    fulfillment_data = None
    if tx.completed_intent_name:
        fulfillment_data = convo.get_fulfillment_data(tx, tx.completed_intent_name)

    convo_cache[convo_id] = convo

    output = SuccessOutput(reply,
                           conversation_id=convo_id,
                           transaction_id=tx.id,
                           completed_intent_name=tx.completed_intent_name,
                           completed_conversation=convo.completed,
                           fulfillment_data=fulfillment_data)
    if app.config['DEBUG']:
        output['transaction'] = tx
    return output

@app.route('/chat', methods=['POST'])
def chat():
    """Converse with a bot"""
    try:
        input = json.loads(request.values['input'])
        bot = request.values['bot']
        channel = request.values['channel']
        bot_config = None
        if app.config['DEBUG']:
            bot_config = json.loads(request.values.get('bot_config', '{}'))
        convo_id = request.values.get('conversation_id', None)

        response = converse(channel, bot, convo_id, input, bot_config=bot_config)
        return jsonr(response)

    except Exception as e:
        # TODO: classify and return error types/codes
        # TODO: log errors, store partially completed convo/tx objects with status?
        dbg(traceback.format_exc())
        error(str(e))
        response = {'status': 'error', 'response': 'Something went wrong.'}
        if app.config['DEBUG']:
            response['error'] = traceback.format_exc()
        return jsonr(response)

@app.route('/fulfillment', methods=['POST'])
def fulfillment():
    """Example fulfillment"""
    data = request.json
    dbg('called')
    dbg(data)
    response = {'status': 'success', 'interaction': None}
    return jsonr(response)

@app.route('/fulfillment_with_interaction', methods=['POST'])
def fulfillment_with_interaction():
    """Example fulfillment with a interaction response"""
    data = request.json
    dbg('called')
    dbg(data)
    response = {'status': 'success', 'interaction': 'Great job, you finished this!'}
    return jsonr(response)

@app.route('/fulfillment_with_question', methods=['POST'])
def fulfillment_with_question():
    """Example fulfillment with a question response"""
    data = request.json
    dbg('called')
    dbg(data)
    response = {'status': 'success',
                'interaction': {'type': 'question',
                                'messages': ['I couldnt find anyone to help. Would you like to try MyIntent instead?'],
                                'intent_actions': {CommonIntents.Yes: {'name': 'TriggerIntent',
                                                                       'params': {'intent_name': 'MyIntent'}},
                                                   CommonIntents.No: Actions.EndConversation}}}
    return jsonr(response)

@app.route('/fulfillment_with_action', methods=['POST'])
def fulfillment_with_action():
    """Example fulfillment with an action response"""
    data = request.json
    dbg('called')
    dbg(data)
    response = {'status': 'success', 'interaction': 'Great job, you are done.', 'action': Actions.EndConversation}
    return jsonr(response)

@app.route('/fulfillment_with_error_status', methods=['POST'])
def fulfillment_with_error_status():
    """Example fulfillment with an error response"""
    data = request.json
    dbg('called')
    dbg(data)
    response = {'status': 'error', 'interaction': None, 'status_reason': 'Fulfillment failed'}
    return jsonr(response)

if __name__ == "__main__":
    if app.config.get('DEBUG', False) and app.config.get('PROFILE', False):
        from werkzeug.contrib.profiler import ProfilerMiddleware
        warn('Using Profiler')
        f = open('/tmp/chatbot_profiler.log', 'w')
        app.wsgi_app = ProfilerMiddleware(app.wsgi_app, stream=f, restrictions=[20])
    app.run(port=9000)
