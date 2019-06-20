import unittest

from chatbot import app
from chatbot.core import (Application,
                          ApplicationVersion,
                          ApplicationTrainingStatus,
                          ApplicationTrainingResult,
                          ApplicationPublishResult,
                          Entity,
                          Intent,
                          Utterance)
from chatbot.configs import load_bot_configs
from chatbot.nlu.luis import LUISNLU
from test_utils import run_tests
from toolbox import testcli, climax

load_bot_configs(app.config)

class TestLUIS(unittest.TestCase):
    def setUp(self):
        self.nlu = LUISNLU(app.config['NLU_CONFIG'])

    def tearDown(self):
        pass

    def testGetApplications(self):
        results = self.nlu.get_applications()
        for x in results:
            self.assertIsInstance(x, Application)

    def testGetApplicationVersions(self):
        results = self.nlu.get_application_versions()
        for x in results:
            self.assertIsInstance(x, ApplicationVersion)

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
        for x in results:
            self.assertIsInstance(x, Entity)

    def testGetIntents(self):
        results = self.nlu.get_intents()
        for x in results:
            self.assertIsInstance(x, Intent)

    def testGetUtterances(self):
        intents = self.nlu.get_intents()
        for intent in intents[:1]:
            results = self.nlu.get_utterances(intent)
            for x in results:
                self.assertIsInstance(x, Utterance)

@climax.command(parents=[testcli])
@climax.argument('testnames', type=str, nargs='*', help='Names of tests to run')
def main(testnames, debug):
    run_tests(TestLUIS, testnames, debug)

if __name__ == '__main__':
    main()
