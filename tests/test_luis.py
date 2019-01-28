from pprint import pprint
import sys
import unittest

from chatbot import app
from chatbot.core import *
from chatbot.metadata import *
from chatbot.nlu.luis import *
from test_utils import *

load_bot_configs(app.config)

class TestLUIS(unittest.TestCase):
    def setUp(self):
        self.nlu = LUISNLU(app.config['NLU_CONFIG'])

    def tearDown(self):
        pass

    def testGetApplications(self):
        results = self.nlu.get_applications()
        [self.assertIsInstance(x, Application) for x in results]

    def testGetApplicationVersions(self):
        results = self.nlu.get_application_versions()
        [self.assertIsInstance(x, ApplicationVersion) for x in results]

    def testGetApplicationTrainingStatus(self):
        result = self.nlu.get_application_training_status()
        self.assertIsInstance(result, ApplicationTrainingStatus)

    def testTrainApplication(self):
        result = self.nlu.train()
        self.assertIsInstance(result, ApplicationTrainingResult)

    def testPublishApplication(self):
        result = self.nlu.publish()
        self.assertIsInstance(result, ApplicationPublishResult)

    def testGetEntities(self):
        results = self.nlu.get_entities()
        [self.assertIsInstance(x, Entity) for x in results]

    def testGetIntents(self):
        results = self.nlu.get_intents()
        [self.assertIsInstance(x, Intent) for x in results]

    def testGetUtterances(self):
        intents = self.nlu.get_intents()
        for intent in intents[:1]:
            results = self.nlu.get_utterances(intent)
            [self.assertIsInstance(x, Utterance) for x in results]

@climax.command(parents=[testcli])
@climax.argument('testnames', type=str, nargs='*', help='Names of tests to run')
def main(testnames, debug):
    run_tests(TestLUIS, testnames, debug)

if __name__ == '__main__':
    main()
