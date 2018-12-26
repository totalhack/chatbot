import json
from pprint import pprint
import random
import requests
import sys
import unittest

from chatbot import app
from chatbot.metadata import *

load_bot_metadata(app.config, load_tests=True)
assert BOT_METADATA

TEST_BASE_URL = app.config.get('TEST_BASE_URL', 'http://127.0.0.1:9000')

def make_request(bot, input_data, convo_id=None, intent_metadata=None):
    data = {'debug': 1,
            'bot': bot,
            'input': json.dumps(input_data)}
    if convo_id: data['conversation_id'] = convo_id
    if intent_metadata:
        data['metadata'] = json.dumps({'INTENT_METADATA': intent_metadata})
    resp = requests.post(TEST_BASE_URL + '/chat', data=data)
    resp.raise_for_status()
    if 'Something went wrong' in resp.content:
        print resp.json()['error']
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

        for bot, bot_metadata in BOT_METADATA.items():
            for tname, convo in bot_metadata.get('TESTS', {}).items():
                test_name = "test%s%s" % (clean_name(bot).title(), clean_name(tname))
                test_dict[test_name] = gen_test(bot, convo)

        return type.__new__(mcs, name, bases, test_dict)

class TestChatBot(unittest.TestCase):
    __metaclass__ = TestChatBotMeta

    def setUp(self):
        self.convo_id = None

    def tearDown(self):
        self.convo_id = None

    def converse(self, bot, convo):
        print '---- Bot: %s Convo ID: %s' % (bot, self.convo_id)
        for i, message_tuple in enumerate(convo):
            expected_intent = None
            expected_message_name = None
            intent_metadata = {}
            if len(message_tuple) == 1:
                input_data = message_tuple[0]
            elif len(message_tuple) == 2:
                input_data, expected_intent = message_tuple
            elif len(message_tuple) == 3:
                input_data, expected_intent, expected_message_name = message_tuple
            elif len(message_tuple) == 4:
                input_data, expected_intent, expected_message_name, intent_metadata = message_tuple
            else:
                assert False, 'Invalid message tuple: %s' % message_tuple

            print 'USER: %s' % input_data
            resp = make_request(bot, input_data, convo_id=self.convo_id, intent_metadata=intent_metadata)
            data = resp.json()
            assert data['status'] == 'success', 'Error: %s' % data
            print '\nBOT:', data['response']

            if not self.convo_id:
                self.convo_id = data['conversation_id']
            else:
                assert self.convo_id == data['conversation_id'], 'Conversation ID mismatch'

            if expected_intent:
                top_intent = data['transaction']['intent_response']['top_intent']['name']
                self.assertEqual(top_intent, expected_intent)
            if expected_message_name:
                message_name = data['transaction']['response_messages'].keys()[0]
                self.assertEqual(message_name, expected_message_name)

            if data['completed_intent']:
                print 'Completed intent %s' % data['completed_intent']['name']
                print 'Fulfillment data: %s' % data['fulfillment_data'].get('slot_data', None)

            if data['completed_conversation']:
                print 'Completed conversation'

if __name__ == '__main__':
    if len(sys.argv) > 1:
        suite = unittest.TestSuite()
        for testname in sys.argv[1:]:
            suite.addTest(TestChatBot(testname))
    else:
        suite = unittest.TestLoader().loadTestsFromTestCase(TestChatBot)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
