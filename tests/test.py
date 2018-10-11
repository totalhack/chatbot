from pprint import pprint
import random
import requests
import unittest

from chatbot import app
TESTS = app.config.get('TESTS', {})

def make_request(text, convo_id=None):
    params = {'debug': 1}
    if convo_id: params['conversation_id'] = convo_id
    resp = requests.post('http://127.0.0.1:9000/chat', params=params, data={'text': text})
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
        self.convo_id = random.randint(0, 1E7)

    def tearDown(self):
        self.convo_id = None

    def converse(self, convo):
        print '---- Convo ID: %s' % self.convo_id
        for i, message_tuple in enumerate(convo):
            expected_intent = None
            expected_message_name = None
            if len(message_tuple) == 1:
                line = message_tuple
            if len(message_tuple) == 2:
                line, expected_intent = message_tuple
            if len(message_tuple) == 3:
                line, expected_intent, expected_message_name = message_tuple

            print '\n---- USER: %s' % line
            resp = make_request(line, convo_id=self.convo_id)
            data = resp.json()
            print 'BOT:', data['response']
            if expected_intent:
                top_intent = data['tx']['intent_response']['top_intent']['name']
                self.assertEqual(top_intent, expected_intent)
            if expected_message_name:
                message_name = data['tx']['response_messages'].keys()[0]
                self.assertEqual(message_name, expected_message_name)

if __name__ == '__main__':
    suite = unittest.TestLoader().loadTestsFromTestCase(TestChatBot)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
