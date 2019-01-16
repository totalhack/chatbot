import requests

from azure.cognitiveservices.language.luis.authoring import LUISAuthoringClient
from azure.cognitiveservices.language.luis.runtime import LUISRuntimeClient
from msrest.authentication import CognitiveServicesCredentials
from flask import current_app

from chatbot.core import *
from chatbot.nlu.core import *
from chatbot.utils import *

PAGE_SIZE = 250

def luis_predict(client, query, config=None, staging=True, verbose=True):
    if not config:
        config = current_app.config

    key = (query, staging, verbose)
    nlu_cache = get_nlu_cache(config)
    if nlu_cache is not None:
        nlu_result = nlu_cache.get(key, None)
        if nlu_result:
            dbg('Using cached NLU result for key %s' % str(key))
            return nlu_result

    luis_result = client.prediction.resolve(config['LUIS_APP_ID'], query, verbose=verbose, staging=staging, timezone_offset='-300')
    result = luis_result.as_dict()

    if nlu_cache is not None:
        nlu_cache[key] = result
    return result

class LUISNLU(NLU):
    ENTITY_TRANSLATIONS = {
        'geographyV2': 'address',
        'builtin.personName': 'fullname',
        'builtin.email': 'email',
        'builtin.phonenumber': 'phonenumber'
    }

    def __init__(self, config):
        super(LUISNLU, self).__init__(config)
        self.runtime_client = LUISRuntimeClient(config['LUIS_BASE_URL'], CognitiveServicesCredentials(config['LUIS_SUBKEY']))
        self.authoring_client = LUISAuthoringClient(config['LUIS_BASE_URL'], CognitiveServicesCredentials(config['LUIS_SUBKEY']))

    def paged_request(self, endpoint, *args, **kwargs):
        return paged_call(endpoint, 'take', 'skip', PAGE_SIZE, self.config['LUIS_APP_ID'], self.config['LUIS_APP_VERSION'], *args, **kwargs)

    def get_raw_response(self, query, staging=True):
        verbose = True # Needed to get multiple intents returned
        response = luis_predict(self.runtime_client, query, config=self.config, staging=staging, verbose=verbose)
        return response

    def get_intents_from_raw_response(self, metadata, raw):
        intents = []
        for intent in raw['intents']:
            name = intent['intent']
            meta = metadata['INTENT_METADATA'].get(name, {})
            intents.append(Intent(metadata, intent['intent'], intent['score'], **meta))
        return intents

    def get_entities_from_raw_response(self, metadata, raw):
        entities = []
        for entity in raw['entities']:
            entity['type'] = self.ENTITY_TRANSLATIONS.get(entity['type'], entity['type'])
            if 'resolution' in entity.keys():
                resolution = entity['resolution']
                if 'values' in resolution:
                    entity['value'] = entity['resolution'].get('values', [])
                else:
                    entity['value'] = entity['resolution'].get('value', None)
            else:
                entity['value'] = entity['entity']

            if entity['type'] == 'fullname':
                if '@' in entity['value']:
                    warn('Skipping fullname entity with @ symbol: %s' % entity)
                    continue
            entities.append(entity)
        return entities

    def get_application(self, app_id):
        result = self.authoring_client.apps.get(app_id)
        return result.as_dict()

    def get_applications(self):
        # TODO: paging support required?
        results = self.authoring_client.apps.list()
        return [result.as_dict() for result in results]

    def get_application_versions(self, app_id):
        results = self.authoring_client.versions.list(app_id)
        return [result.as_dict() for result in results]

    def get_entity(self, id):
        result = self.authoring_client.model.get_entity(self.config['LUIS_APP_ID'], self.config['LUIS_APP_VERSION'], id)
        return result.as_dict()

    def get_entities(self):
        results = self.paged_request(self.authoring_client.model.list_entities)
        return [result.as_dict() for result in results]

    def get_intent(self, id):
        result = self.authoring_client.model.get_intent(self.config['LUIS_APP_ID'], self.config['LUIS_APP_VERSION'], id)
        return result.as_dict()

    def get_intents(self):
        results = self.paged_request(self.authoring_client.model.list_intents)
        return [result.as_dict() for result in results]

if __name__ == '__main__':
    from chatbot import app
    nlu = LUISNLU(app.config['NLU_CONFIG'])
    pprint(nlu.get_intents())
    pprint(nlu.get_entities())
    pprint(nlu.get_applications())
    pprint(nlu.get_application_versions(app.config['NLU_CONFIG']['LUIS_APP_ID']))
