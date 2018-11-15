import json
from pprint import pprint
import random
import requests
import unittest

from chatbot import app
TESTS = app.config.get('TESTS', {})
TEST_BASE_URL = app.config.get('TEST_BASE_URL', 'http://127.0.0.1:9000')

def make_request(input_data, convo_id=None):
    data = {'debug': 1}
    if convo_id: data['conversation_id'] = convo_id
    data['input'] = json.dumps(input_data)
    resp = requests.post(TEST_BASE_URL + '/chat', data=data)
    resp.raise_for_status()
    if 'Something went wrong' in resp.content:
        print resp.json()['error']
        raise Exception('Something went wrong')
    return resp

# https://stackoverflow.com/questions/32899/how-do-you-generate-dynamic-parametrized-unit-tests-in-python
class TestChatBotMeta(type):
    def __new__(mcs, name, bases, dict):
        def gen_test(convo):
            def test(self):
                self.converse(convo)
            return test

        for tname, convo in TESTS.items():
            test_name = "test%s" % tname
            dict[test_name] = gen_test(convo)

        return type.__new__(mcs, name, bases, dict)

class TestChatBot(unittest.TestCase):
    __metaclass__ = TestChatBotMeta

    def setUp(self):
        self.convo_id = None

    def tearDown(self):
        self.convo_id = None

    def converse(self, convo):
        print '---- Convo ID: %s' % self.convo_id
        for i, message_tuple in enumerate(convo):
            expected_intent = None
            expected_message_name = None
            if len(message_tuple) == 1:
                input_data = message_tuple[0]
            elif len(message_tuple) == 2:
                input_data, expected_intent = message_tuple
            elif len(message_tuple) == 3:
                input_data, expected_intent, expected_message_name = message_tuple
            else:
                assert False, 'Invalid message tuple: %s' % message_tuple

            print '\n---- USER: %s' % input_data
            resp = make_request(input_data, convo_id=self.convo_id)
            data = resp.json()
            assert data['status'] == 'success', 'Error: %s' % data
            print 'BOT:', data['response']

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
    suite = unittest.TestLoader().loadTestsFromTestCase(TestChatBot)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
