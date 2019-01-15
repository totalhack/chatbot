from flask import current_app

from chatbot.core import *
from chatbot.utils import *

class NLU(object):
    def __init__(self, config):
        self.config = config

    def get_raw_response(self, metadata, query):
        raise NotImplementedError

    def get_intents_from_raw_response(self, metadata, raw):
        raise NotImplementedError

    def get_entities_from_raw_response(self, metadata, raw):
        raise NotImplementedError

    def process_query(self, metadata, query, last_tx=None):
        raw = self.get_raw_response(metadata, query)
        intents = self.get_intents_from_raw_response(metadata, raw)
        entities = self.get_entities_from_raw_response(metadata, raw)

        entity_handler_name = 'EntityHandler'
        if last_tx and last_tx.question and getattr(last_tx.question, 'entity_handler_name', None):
            entity_handler_name = last_tx.question.entity_handler_name or entity_handler_name
        entity_handler = get_entity_handler(entity_handler_name)

        entities = entity_handler().process(query, entities)
        return IntentResponse(query, intents, entities=entities)

    def get_intents(self, config):
        raise NotImplementedError
