from flask import current_app

from chatbot.core import *
from chatbot.utils import *

class NLU(object):
    def __init__(self, config):
        self.config = config

    def get_raw_response(self, query):
        raise NotImplementedError

    def get_intents_from_raw_response(self, metadata, raw):
        raise NotImplementedError

    def get_entities_from_raw_response(self, metadata, raw):
        raise NotImplementedError

    def process_query(self, metadata, query, last_tx=None):
        raw = self.get_raw_response(query)
        intents = self.get_intents_from_raw_response(metadata, raw)
        entities = self.get_entities_from_raw_response(metadata, raw)

        entity_handler_name = 'EntityHandler'
        if last_tx and last_tx.question and getattr(last_tx.question, 'entity_handler_name', None):
            entity_handler_name = last_tx.question.entity_handler_name or entity_handler_name
        entity_handler = get_entity_handler(entity_handler_name)

        entities = entity_handler().process(query, entities)
        return IntentResponse(query, intents, entities=entities)

    def get_application(self):
        raise NotImplementedError

    def get_applications(self):
        # TODO: config limits context to a specific app ID and version, might need a better home
        # or to be made a class method
        raise NotImplementedError

    def get_application_versions(self):
        raise NotImplementedError

    def clone_version(self, old_version, new_version):
        raise NotImplementedError

    def clone_current_version(self, new_version):
        raise NotImplementedError

    def train(self):
        raise NotImplementedError

    def publish(self):
        raise NotImplementedError

    def get_entity(self, id):
        raise NotImplementedError

    def get_entities(self):
        raise NotImplementedError

    def get_intent(self, id):
        raise NotImplementedError

    def get_intents(self):
        raise NotImplementedError

    def add_intent(self, name, utterances):
        raise NotImplementedError

    def get_utterances(self, intent):
        raise NotImplementedError

    def add_utterance(self, intent, text):
        raise NotImplementedError
