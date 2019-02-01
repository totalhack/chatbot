from collections import OrderedDict
from pprint import pprint
import random
import requests
import sys
import unittest

from chatbot import app
from chatbot.configs import *
from chatbot.utils import *
from test_utils import *

load_bot_configs(app.config, load_tests=True)

TEST_BASE_URL = app.config.get('TEST_BASE_URL', 'http://127.0.0.1:9000')

def make_request(bot, input_data, convo_id=None, intent_configs=None):
    data = {'debug': 1,
            'bot': bot,
            'input': json.dumps(input_data)}
    if convo_id: data['conversation_id'] = convo_id
    if intent_configs:
        data['bot_config'] = json.dumps({'intent_configs': intent_configs})
    resp = requests.post(TEST_BASE_URL + '/chat', data=data)
    resp.raise_for_status()
    if 'Something went wrong' in resp.text:
        print(resp.json()['error'])
        raise Exception('Something went wrong')
    return resp

def clean_name(name):
    return name.replace(' ', '_').replace('-', '_')

# https://stackoverflow.com/questions/32899/how-do-you-generate-dynamic-parametrized-unit-tests-in-python
class TestChatBotMeta(type):
    def __new__(mcs, name, bases, test_dict):
        def gen_test(bot, convo):
            def test(self):
                self.converse(bot, convo)
            return test

        for bot, bot_config in list(get_all_bot_configs().items()):
            for tname, convo in list(bot_config.tests.items()):
                test_name = "test%s%s" % (clean_name(bot).title(), clean_name(tname))
                test_dict[test_name] = gen_test(bot, convo)

        return type.__new__(mcs, name, bases, test_dict)

class TestChatBot(TestBase, metaclass=TestChatBotMeta):
    def setUp(self):
        self.convo_id = None

    def tearDown(self):
        self.convo_id = None

    def converse(self, bot, convo):
        print('---- Bot: %s Convo ID: %s' % (bot, self.convo_id))
        for i, message_tuple in enumerate(convo):
            expected_intent = None
            expected_message_name = None
            intent_configs = {}
            if len(message_tuple) == 1:
                input_data = message_tuple[0]
            elif len(message_tuple) == 2:
                input_data, expected_intent = message_tuple
            elif len(message_tuple) == 3:
                input_data, expected_intent, expected_message_name = message_tuple
            elif len(message_tuple) == 4:
                input_data, expected_intent, expected_message_name, intent_configs = message_tuple
            else:
                assert False, 'Invalid message tuple: %s' % message_tuple

            print('USER: %s' % input_data)
            resp = make_request(bot, input_data, convo_id=self.convo_id, intent_configs=intent_configs)
            data = resp.json(object_pairs_hook=OrderedDict)
            assert data['status'] == 'success', 'Error: %s' % data
            print('\nBOT:', data['response'])

            if not self.convo_id:
                self.convo_id = data['conversation_id']
            else:
                assert self.convo_id == data['conversation_id'], 'Conversation ID mismatch'

            if expected_intent:
                top_intent = data['transaction']['intent_prediction']['top_intent_result']['name']
                self.assertEqual(top_intent, expected_intent)
            if expected_message_name:
                message_names = list(data['transaction']['response_messages'].keys())
                self.assertIn(expected_message_name, message_names)

            if data['completed_intent_name']:
                print('Completed intent %s' % data['completed_intent_name'])
                print('Fulfillment slot data: %s' % data['fulfillment_data'].get('slot_data', None))

            if data['completed_conversation']:
                print('Completed conversation')

@climax.command(parents=[testcli])
@climax.argument('testnames', type=str, nargs='*', help='Names of tests to run')
def main(testnames, debug):
    run_tests(TestChatBot, testnames, debug)

if __name__ == '__main__':
    main()
