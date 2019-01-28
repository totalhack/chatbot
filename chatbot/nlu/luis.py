import requests

from azure.cognitiveservices.language.luis.authoring import LUISAuthoringClient
from azure.cognitiveservices.language.luis.authoring.models.application_publish_object import ApplicationPublishObject
from azure.cognitiveservices.language.luis.authoring.models.example_label_object import ExampleLabelObject
from azure.cognitiveservices.language.luis.runtime import LUISRuntimeClient
from msrest.authentication import CognitiveServicesCredentials
from flask import current_app

from chatbot.core import *
from chatbot.utils import *

PAGE_SIZE = 250
RAW_INTENT_LIMIT = 10

MODEL_ENDPOINT_TEMPLATE = '%(base_url)s/luis/webapi/v2.0/apps/%(app_id)s/versions/%(app_version)s/models/%(model_id)s/%(endpoint)s'

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

    luis_result = client.prediction.resolve(config['luis_app_id'], query, verbose=verbose, staging=staging, timezone_offset='-300')
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

    TRAINED_STATUS = set(['UpToDate', 'Success'])

    def __init__(self, config):
        super(LUISNLU, self).__init__(config)
        self.base_url = config['luis_base_url']
        self.subkey = config['luis_subkey']
        self.app_id = config['luis_app_id']
        self.app_version = config['luis_app_version']
        self.runtime_client = LUISRuntimeClient(self.base_url, CognitiveServicesCredentials(self.subkey))
        self.authoring_client = LUISAuthoringClient(self.base_url, CognitiveServicesCredentials(self.subkey))

    def paged_request(self, endpoint, *args, **kwargs):
        app_version = kwargs.get('app_version', self.app_version)
        if 'app_version' in kwargs: del kwargs['app_version']
        return paged_call(endpoint, 'take', 'skip', PAGE_SIZE, self.app_id, app_version, *args, **kwargs)

    def raw_api_request(self, template, **kwargs):
        kwargs['base_url'] = kwargs.get('base_url', self.base_url)
        kwargs['app_id'] = kwargs.get('app_id', self.app_id)
        kwargs['app_version'] = kwargs.get('app_version', self.app_version)
        url = template % kwargs
        headers = {'Ocp-Apim-Subscription-Key': self.subkey}
        return paged_get(url, 'take', 'skip', PAGE_SIZE, params=kwargs.get('params', {}), headers=headers)

    def raw_model_endpoint_request(self, model_id, endpoint, app_version=None):
        app_version = app_version or self.app_version
        results = self.raw_api_request(MODEL_ENDPOINT_TEMPLATE, model_id=model_id, endpoint=endpoint, app_version=app_version)
        return results

    def get_raw_prediction(self, query, staging=True):
        verbose = True # Needed to get multiple intents returned
        response = luis_predict(self.runtime_client, query, config=self.config, staging=staging, verbose=verbose)
        return response

    def get_intent_results_from_raw_response(self, raw):
        intents = []
        for intent in raw['intents'][:RAW_INTENT_LIMIT]:
            intents.append(IntentResult(intent['intent'], intent['score']))
        return intents

    def get_entity_results_from_raw_response(self, raw):
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

    @classmethod
    def to_application(cls, result):
        return Application(result.id, result.name, result.active_version,
                           description=result.description,
                           created_at=parse_date(result.created_date_time),
                           production_endpoint=result.endpoints['PRODUCTION']['endpointUrl'],
                           staging_endpoint=result.endpoints['STAGING']['endpointUrl'])

    def get_application(self):
        result = self.authoring_client.apps.get(self.app_id)
        return LUISNLU.to_application(result)

    def get_applications(self):
        # TODO: paging support required?
        results = self.authoring_client.apps.list()
        return [LUISNLU.to_application(result) for result in results]

    @classmethod
    def to_application_version(cls, result):
        return ApplicationVersion(result.version, created_at=result.created_date_time, updated_at=result.last_modified_date_time)

    def get_application_versions(self):
        results = self.authoring_client.versions.list(self.app_id)
        return [LUISNLU.to_application_version(result) for result in results]

    def clone_version(self, old_version, new_version):
        dbg('Cloning version %s to %s' % (old_version, new_version))
        version = self.authoring_client.versions.clone(self.app_id, old_version, version=new_version)
        return version

    def clone_current_version(self, new_version):
        version = self.clone_version(self.app_version, new_version)
        return version

    @classmethod
    def to_application_training_status(cls, results):
        status = ApplicationTrainingStatus.TRAINED
        trained_count = 0
        for result in results:
            if result.details.status in cls.TRAINED_STATUS:
                trained_count += 1
                continue
            status = ApplicationTrainingStatus.IN_PROGRESS
        return ApplicationTrainingStatus(status, len(results), trained_count)

    def get_application_training_status(self, app_version=None):
        app_version = app_version or self.app_version
        results = self.authoring_client.train.get_status(self.app_id, app_version)
        return LUISNLU.to_application_training_status(results)

    @classmethod
    def to_application_training_result(cls, result):
        status = ApplicationTrainingResult.IN_PROGRESS
        if result.status in cls.TRAINED_STATUS or result.status == ApplicationTrainingResult.TRAINED:
            status = ApplicationTrainingResult.TRAINED
        return ApplicationTrainingResult(status)

    def train(self, async=False, app_version=None):
        app_version = app_version or self.app_version
        dbg('Training app, async:%s' % async)
        result = self.authoring_client.train.train_version(self.app_id, app_version)
        if async:
            return LUISNLU.to_application_training_result(result)
        result = poll_call(self.get_application_training_status, 'status', 'Trained', 1, 100, app_version=app_version)
        return LUISNLU.to_application_training_result(result)

    @classmethod
    def to_application_publish_result(cls, result):
        env = ApplicationPublishResult.PRODUCTION
        if result.is_staging:
            env = ApplicationPublishResult.STAGING
        return ApplicationPublishResult(result.version_id, env,
                                        region=result.region,
                                        published_at=parse_date(result.published_date_time),
                                        endpoint=result.endpoint_url)

    def publish(self, is_staging=True, region='westus', app_version=None):
        app_version = app_version or self.app_version
        dbg('Publishing app version %s, is_staging:%s region:%s' % (app_version, is_staging, region))
        publish_obj = ApplicationPublishObject(version_id=app_version, is_staging=is_staging, region=region)
        result = self.authoring_client.apps.publish(self.app_id, publish_obj)
        return LUISNLU.to_application_publish_result(result)

    @classmethod
    def to_entity(cls, result):
        # TODO: untested
        return Entity(result.name, result.type, api_id=result.id)

    def get_entity(self, id, app_version=None):
        app_version = app_version or self.app_version
        result = self.authoring_client.model.get_entity(self.app_id, app_version, id)
        return LUISNLU.to_entity(result)

    def get_entities(self, app_version=None):
        app_version = app_version or self.app_version
        results = self.paged_request(self.authoring_client.model.list_entities, app_version=app_version)
        return [LUISNLU.to_entity(result) for result in results]

    @classmethod
    def to_intent(cls, result):
        return Intent(result.name, api_id=result.id)

    def get_intent(self, id, app_version=None):
        app_version = app_version or self.app_version
        result = self.authoring_client.model.get_intent(self.app_id, app_version, id)
        return LUISNLU.to_intent(result)

    def get_intents(self, app_version=None):
        app_version = app_version or self.app_version
        results = self.paged_request(self.authoring_client.model.list_intents, app_version=app_version)
        return [LUISNLU.to_intent(result) for result in results]

    def add_intent(self, name, app_version=None):
        app_version = app_version or self.app_version
        dbg('Adding intent %s to LUIS app %s/%s' % (name, self.app_id, app_version))
        # This just returns the intent ID as a string currently.
        intent_id = self.authoring_client.model.add_intent(self.app_id, app_version, name)
        return Intent(name, api_id=intent_id)

    def get_models(self, app_version=None):
        app_version = app_version or self.app_version
        results = self.authoring_client.model.list_models(self.app_id, app_version)
        return [result.as_dict() for result in results]

    @classmethod
    def to_utterance(cls, result):
        if isinstance(result, dict):
            return Utterance(result['text'], intent_name=result['intentLabel'], intent_api_id=result['intentId'], api_id=result['id'])
        else:
            return Utterance(result.text, intent_name=result.intentLabel, intent_api_id=result.intentId, api_id=result.id)

    def get_utterances(self, intent, app_version=None):
        app_version = app_version or self.app_version
        # It seems the python SDK is out of sync with the API, as this currently
        # gets a Resource Not Found error.
        # results = self.authoring_client.model.examples_method(self.app_id, self.app_version, intent['id'])
        results = self.raw_model_endpoint_request(intent['api_id'], 'reviewLabels', app_version=app_version)
        return [LUISNLU.to_utterance(x) for x in results]

    def add_utterance(self, intent, utterance, app_version=None):
        app_version = app_version or self.app_version
        dbg('Adding utterance "%s" to intent %s' % (utterance, intent['name']))
        example = ExampleLabelObject(text=utterance, intent_name=intent['name'])
        result = self.authoring_client.examples.add(self.app_id, app_version, example)
        return LUISNLU.to_utterance(result)
