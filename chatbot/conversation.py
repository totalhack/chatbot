from cachetools import LRUCache
from collections import OrderedDict
import copy
from importlib import import_module
import requests
import sys
import uuid

from flask import current_app
import usaddress

from chatbot.model import *
from chatbot.utils import *

# TODO: replace with an external cache
CACHE_SIZE = 1000
NLU_CACHE = LRUCache(CACHE_SIZE)
warn('Replace NLU cache for production!')

INTENT_FILTER_THRESHOLD = 0.50
ENTITY_FILTER_THRESHOLD = 0.50
MAX_QUESTION_ATTEMPTS = 2

class CommonIntents(object):
    CANCEL = 'Cancel'
    CONFIRM_YES = 'ConfirmYes'
    CONFIRM_NO = 'ConfirmNo'
    HELP = 'Help'
    NONE = 'None'
    REPEAT = 'Repeat'
    WELCOME = 'Welcome'

class ResponseType(object):
    ACTIVE = 'active'
    DEFERRED = 'deferred'
    RESUMED = 'resumed'

class Actions(object):
    CANCEL_INTENT = 'cancel_intent'
    END_CONVERSATION = 'end_conversation'
    NONE = 'none'
    REPEAT = 'repeat'
    REPEAT_SLOT = 'repeat_slot'
    REPLACE_SLOT = 'replace_slot'

COMMON_MESSAGES = {
    'fallback': [
        "Sorry, I didn't get that",
    ],

    'help': [
        "Stil need to add a help message",
    ],

    'intents_complete': {
        'prompts': [
            "Is there anything else I can help you with today?",
        ],
        'intent_actions': {CommonIntents.CONFIRM_YES: Actions.NONE,
                           CommonIntents.CONFIRM_NO: Actions.END_CONVERSATION}
    },

    'intent_aborted': {
        'prompts': ["I'm sorry, I'm unable to help you at this time"],
        'action': Actions.END_CONVERSATION,
    },

    'intent_canceled': {
        'prompts': [
            "Are you sure you want to cancel the current intent?",
        ],
        'intent_actions': {CommonIntents.CONFIRM_YES: Actions.CANCEL_INTENT,
                           CommonIntents.CONFIRM_NO: Actions.NONE}
    },

    'goodbye': [
        "Thanks. Have a nice day!"
    ]
}

INTENT_METADATA = {
    CommonIntents.CANCEL: {
        'repeatable': True,
        'preemptive': True,
        'is_answer': False
    },

    CommonIntents.CONFIRM_NO: {
        'repeatable': True,
        'preemptive': True,
        'is_answer': True
    },

    CommonIntents.CONFIRM_YES: {
        'repeatable': True,
        'preemptive': True,
        'is_answer': True
    },

    CommonIntents.HELP: {
        'repeatable': True,
        'preemptive': True,
        'is_answer': False
    },

    CommonIntents.REPEAT: {
        'repeatable': True,
        'preemptive': True,
        'is_answer': False
    },

    CommonIntents.WELCOME: {
        'responses': {
            ResponseType.ACTIVE: [
                'Hi, how are you?',
            ],
        },
        'is_greeting': True,
    },
}

ENTITY_HANDLERS = {
    'address': 'AddressEntityHandler',
}

DEFAULT_FOLLOW_UP_ACTIONS = {
    CommonIntents.CONFIRM_YES: Actions.NONE,
    CommonIntents.CONFIRM_NO: Actions.REPEAT_SLOT,
}

APP_INTENT_METADATA = None
APP_ENTITY_HANDLERS = None
APP_COMMON_MESSAGES = None

def set_app_data_from_config(config):
    global APP_INTENT_METADATA
    APP_INTENT_METADATA = config.get('APP_INTENT_METADATA', {})
    INTENT_METADATA.update(APP_INTENT_METADATA)

    global APP_ENTITY_HANDLERS
    APP_ENTITY_HANDLERS = config.get('APP_ENTITY_HANDLERS', {})
    ENTITY_HANDLERS.update(APP_ENTITY_HANDLERS)

    global APP_COMMON_MESSAGES
    APP_COMMON_MESSAGES = config.get('APP_COMMON_MESSAGES', {})
    COMMON_MESSAGES.update(APP_COMMON_MESSAGES)

def set_intent_metdata(metadata):
    global INTENT_METADATA
    INTENT_METADATA = metadata

def update_intent_metdata(updates):
    global INTENT_METADATA
    dictmerge(INTENT_METADATA, updates, overwrite=True)

def get_default_metadata():
    return dict(INTENT_METADATA=INTENT_METADATA,
                ENTITY_HANDLERS=ENTITY_HANDLERS,
                COMMON_MESSAGES=COMMON_MESSAGES)

def assert_valid_intent_name(metadata, intent_name):
    assert intent_name in metadata['INTENT_METADATA'], 'Invalid intent name: %s' % intent_name

def get_entity_handler(name):
    if '.' not in name:
        module_name = __name__ # current module name
        object_name = name
    else:
        module_name = '.'.join(name.split('.')[:-1])
        object_name = name.split('.')[-1]
    return getattr(import_module(module_name), object_name)

def luis(query, staging=True, verbose=True):
    key = (query, staging, verbose)
    nlu_result = NLU_CACHE.get(key, None)
    if nlu_result:
        dbg('Using cached NLU result for key %s' % str(key))
        return nlu_result

    params = {
        'subscription-key': current_app.config['LUIS_SUBKEY'],
        'staging': 'true' if staging else 'false',
        'verbose': 'true' if verbose else 'false',
        'timezoneOffset': '-300',
        'q': query,
    }
    resp = requests.get(current_app.config['LUIS_URL'], params=params)
    resp.raise_for_status()
    result = resp.json()
    NLU_CACHE[key] = result
    return result

class Message(PrintMixin, JSONMixin):
    repr_attrs = ['name']

    def __init__(self, name, prompts):
        self.name = name
        self.prompts = prompts

    def get_prompt(self):
        prompt = random.choice(self.prompts)
        return prompt

class MessageGroup(OrderedDictPlus, JSONMixin):
    def get_next(self):
        return self.values()[0]

    def get_next_prompt(self):
        return self.get_next().get_prompt()

def message_from_dict(message_dict):
    message_type = message_dict['type'].lower()
    if message_type == 'message':
        return Message(message_dict['name'], message_dict['prompts'])
    elif message_type == 'question':
        return Question(message_dict['name'],
                        message_dict['prompts'],
                        intent_actions=message_dict.get('intent_actions', None),
                        entity_actions=message_dict.get('entity_actions', None))
    elif message_type == 'slot':
        return Slot(message_dict['name'],
                    message_dict['prompts'],
                    entity_handler_name=message_dict.get('entity_handler_name', None),
                    follow_up=message_dict.get('follow_up', None))
    else:
        assert False, 'Invalid message type: %s' % message_type

class Question(Message):
    repr_attrs = ['name']

    def __init__(self, name, prompts, intent_actions=None, entity_actions=None):
        super(Question, self).__init__(name, prompts)
        self.intent_actions = intent_actions
        if intent_actions:
            assert type(intent_actions) == dict, 'Invalid type for intent_actions, must be dict: %s' % type(intent_actions)
        self.entity_actions = entity_actions
        if entity_actions:
            assert type(entity_actions) == dict, 'Invalid type for entity_actions, must be dict: %s' % type(entity_actions)

    def get_intent_actions(self):
        return self.intent_actions

    def get_entity_actions(self):
        return self.entity_actions

class Slot(Question):
    repr_attrs = ['name', 'value']

    @classmethod
    def from_dict(cls, metadata, slot_name, slot_info):
        assert type(slot_info) == dict, 'Invalid type for slot_info, must be dict: %s' % type(slot_info)
        prompts = slot_info['prompts']

        follow_up_info = slot_info.get('follow_up', {})
        follow_up = FollowUp.from_dict(slot_name, follow_up_info)

        entity_handler_name = slot_info.get('entity_handler', None)
        if (not entity_handler_name) and slot_name in metadata['ENTITY_HANDLERS']:
            entity_handler_name = metadata['ENTITY_HANDLERS'][slot_name]
        return cls(slot_name, prompts, entity_handler_name=entity_handler_name, follow_up=follow_up)

    def __init__(self, name, prompts, entity_handler_name=None, follow_up=None):
        super(Slot, self).__init__(name, prompts)
        if entity_handler_name:
            assert type(entity_handler_name) in (str, unicode), 'Invalid entity handler: %s' % entity_handler_name
        self.entity_handler_name = entity_handler_name
        self.follow_up = follow_up
        if follow_up:
            assert type(follow_up) == FollowUp
        self.value = None

    def get_follow_up_prompt(self):
        if not self.follow_up:
            return None
        return self.follow_up.get_prompt()

    def get_follow_up_intent_actions(self):
        if not self.follow_up:
            return None
        return self.follow_up.get_intent_actions()

    def copy(self):
        new_slot = Slot(self.name,
                        self.prompts,
                        entity_handler_name=self.entity_handler_name,
                        follow_up=self.follow_up)
        new_slot.value = self.value
        return new_slot

class FollowUp(Question):
    repr_attrs = ['name']

    @classmethod
    def from_dict(cls, slot_name, follow_up_info):
        assert type(follow_up_info) == dict, 'Invalid type for follow up info, must be dict: %s' % type(follow_up_info)
        follow_up = None
        if follow_up_info:
            follow_up_name = '%s_follow_up' % slot_name
            # If they provide the slot answer, process it and continue
            # TODO: This may be overly simplistic. What if the same slot entity
            # is mentioned but doesnt need replacing? This may hijack the processing of
            # that message.
            entity_actions = {slot_name: Actions.REPLACE_SLOT}
            follow_up = cls(follow_up_name, follow_up_info['prompts'],
                            intent_actions=follow_up_info.get('intent_actions', DEFAULT_FOLLOW_UP_ACTIONS),
                            entity_actions=entity_actions)
        return follow_up

class Intent(PrintMixin, JSONMixin):
    repr_attrs = ['name', 'score']

    def __init__(self, metadata, name, score, responses=None, slots=None, repeatable=False, preemptive=False, fulfillment=None, is_answer=False, is_greeting=False):
        self.name =name
        self.score = score
        self.repeatable = repeatable
        self.preemptive = preemptive
        self.fulfillment = fulfillment
        self.fulfillment_data = None
        self.is_answer = is_answer
        self.is_greeting = is_greeting
        self.responses = {}
        if responses:
            for response_type, response_texts in responses.items():
                assert response_type in (ResponseType.ACTIVE, ResponseType.RESUMED, ResponseType.DEFERRED), 'Invalid response type: %s' % response_type
                assert type(response_texts) in (tuple, list)
                self.responses[response_type] = response_texts

        self.slots = MessageGroup()
        if slots:
            for slot_name, slot_info in slots.items():
                self.slots[slot_name] = Slot.from_dict(metadata, slot_name, slot_info)

    def get_remaining_intent_slots(self):
        return MessageGroup([(k, v) for k,v in self.slots.items() if v.value is None])

    def get_completed_intent_slots(self):
        return MessageGroup([(k, v) for k,v in self.slots.items() if v.value is not None])

    def get_fulfillment_data(self, convo, tx, slot_data):
        slot_value_data = {k:slot_data[k].value.value for k in self.slots}
        return dict(conversation_id=convo.id,
                    transaction_id=tx.id,
                    intent_name=self.name,
                    slot_data=slot_value_data)

    def fulfill(self, convo, tx, slot_data):
        # Set the fulfillment data on this even if there is no fulfillment URL
        # to call, so clients can still have a clean way of getting all slot
        # data for a particular intent.
        fulfillment_data = self.get_fulfillment_data(convo, tx, slot_data)
        self.fulfillment_data = fulfillment_data

        if not self.fulfillment:
            dbg('Nothing to fulfill for intent %s' % self.name, color='white')
            return

        dbg('Handling fulfillment for intent %s: %s' % (self.name, self.fulfillment), color='white')
        url = self.fulfillment['url']
        headers = {'Content-type': 'application/json'}

        try:
            resp = requests.post(url, json=fulfillment_data)
            resp.raise_for_status() # TODO: how should a failure here be handled on the front end?
            return FulfillmentResponse('%s_fulfillment' % self.name, **resp.json())
        finally:
            ff = Fulfillments(conversation_id=convo.id,
                              url=url,
                              status_code=resp.status_code,
                              response=resp.content,
                              data=json.dumps(fulfillment_data))
            db.session.merge(ff)
            db.session.commit()

class Entity(PrintMixin, JSONMixin):
    repr_attrs = ['name', 'type', 'value', 'score']

    def __init__(self, name, type, start_index=None, end_index=None, score=None, value=None):
        self.name = name
        self.type = type
        self.slot_name = type
        self.start_index = start_index
        self.end_index = end_index
        self.score = score
        self.value = value

class EntityHandler(JSONMixin):
    def process(self, query, nlu_entities):
        entities = []
        for entity in nlu_entities:
            entities.append(Entity(name=entity['entity'],
                                   type=entity['type'],
                                   start_index=entity.get('startIndex', None),
                                   end_index=entity.get('endIndex', None),
                                   score=entity.get('score', None),
                                   value=entity.get('value', None)))
        return entities

class QueryEntityHandler(EntityHandler):
    '''Just echoes the query back as an entity'''
    def process(self, query, nlu_entities):
        entities = super(QueryEntityHandler, self).process(query, nlu_entities)
        query_entity = Entity(name='query',
                              type='query',
                              start_index=None,
                              end_index=None,
                              score=None,
                              value=query)
        entities.insert(0, query_entity)
        return entities

class AddressEntityHandler(EntityHandler):
    def process(self, query, nlu_entities):
        entities = super(AddressEntityHandler, self).process(query, nlu_entities)
        address = usaddress.parse(query)

        if not address:
            return entities

        address_dict = OrderedDict([(v,k) for k,v in address])

        address_parts = []
        for label in usaddress.LABELS:
            if label in ['Recipient', 'NotAddress']:
                continue
            part = address_dict.get(label, None)
            if not part:
                continue
            address_parts.append(part)

        address_value = ' '.join(address_parts)
        address_entity = Entity(name='address',
                                type='address',
                                start_index=None,
                                end_index=None,
                                score=None,
                                value=address_value)

        entities.insert(0, address_entity)
        return entities

class FulfillmentResponse(PrintMixin, JSONMixin):
    repr_attrs = ['status', 'response']

    def __init__(self, name, status=None, status_reason=None, message=None, action=None):
        self.name = name
        assert status and type(status) in (str, unicode), 'Invalid status: %s' % status
        self.status = status
        self.status_reason = status_reason
        self.action = action
        self.raw_message = message
        self.message = message
        if message:
            if type(message) in (str, unicode):
                self.message = Message(name, [message])
            elif type(message) == dict:
                message['name'] = message.get('name', name)
                self.message = message_from_dict(message)
            else:
                assert False, 'Invalid message: %s' % message

    def success(self):
        if self.status.lower() == 'success':
            return True
        return False

class IntentResponse(PrintMixin, JSONMixin):
    repr_attrs = ['query', 'intents', 'entities']

    def __init__(self, query, intents, entities=[]):
        self.query = query
        assert intents
        self.intents = sorted(intents, key=lambda x: x.score, reverse=True)
        self.top_intent = self.intents[0]
        self.entities = entities or []

    def filter_intents(self, score):
        return [x for x in self.intents if ((x.score is None or x.score > score) and x.name != 'None')]

    def filter_entities(self, score):
        return [x for x in self.entities if (x.score is None or x.score > score)]

    def get_valid(self, intent_threshold=INTENT_FILTER_THRESHOLD, entity_threshold=ENTITY_FILTER_THRESHOLD):
        valid_intents = self.filter_intents(intent_threshold)
        valid_entities = self.filter_entities(entity_threshold)
        return valid_intents, valid_entities

def get_triggered_intent(metadata, intent_name):
    assert_valid_intent_name(metadata, intent_name)
    meta = metadata['INTENT_METADATA'][intent_name]
    score = 1
    intent = Intent(metadata, intent_name, score, **meta)
    return intent

class TriggeredIntentResponse(IntentResponse):
    def __init__(self, metadata, intent_name, context=None):
        intents = [get_triggered_intent(metadata, intent_name)]

        entities = []
        if context:
            assert type(context) == dict, 'Invalid context: %s' % context
            for k,v in context.items():
                entities.append(Entity(name=k, type=k, value=v))

        super(TriggeredIntentResponse, self).__init__(None, intents, entities=entities)

class LUISResponse(IntentResponse):
    ENTITY_TRANSLATIONS = {
        'geographyV2': 'address',
        'builtin.personName': 'fullname',
        'builtin.email': 'email',
        'builtin.phonenumber': 'phonenumber'
    }

    def __init__(self, metadata, luis_json, last_tx=None):
        intents = []
        for intent in luis_json['intents']:
            name = intent['intent']
            meta = metadata['INTENT_METADATA'].get(name, {})
            intents.append(Intent(metadata, intent['intent'], intent['score'], **meta))

        entities = []
        for entity in luis_json['entities']:
            entity['type'] = self.ENTITY_TRANSLATIONS.get(entity['type'], entity['type'])
            if 'resolution' in entity.keys():
                resolution = entity['resolution']
                if 'values' in resolution:
                    entity['value'] = entity['resolution'].get('values', [])
                else:
                    entity['value'] = entity['resolution'].get('value', None)
            else:
                entity['value'] = entity['entity']

            # TODO: find a better home for special cases of filtering raw entities
            if entity['type'] == 'fullname':
                if '@' in entity['value']:
                    warn('Skipping fullname entity with @ symbol: %s' % entity)
                    continue
            entities.append(entity)

        entity_handler_name = 'EntityHandler'
        if last_tx and last_tx.question and getattr(last_tx.question, 'entity_handler_name', None):
            entity_handler_name = last_tx.question.entity_handler_name or entity_handler_name
        entity_handler = get_entity_handler(entity_handler_name)

        entities = entity_handler().process(luis_json['query'], entities)
        super(LUISResponse, self).__init__(luis_json['query'], intents, entities=entities)

class Input(PrintMixin, JSONMixin):
    repr_attrs = ['type', 'value']
    types = [
        'action',
        'intent',
        'text',
    ]

    def __init__(self, input):
        self.raw_input = input
        self.type = None
        self.value = None
        self.context = None

        if type(input) in (str, unicode):
            self.type = 'text'
            self.value = input
        elif type(input) == dict:
            self.type = input['type']
            self.value = input['value']
            self.context = input.get('context', {})
        else:
            assert False, 'Invalid input: %s' % input

        assert self.type in self.types, 'Invalid input type: %s' % self.type

class Transaction(JSONMixin, SaveMixin):
    save_attrs = ['id',
                  'conversation_id',
                  'input',
                  'response_message_text',
                  'slots_filled',
                  'question',
                  'active_intent',
                  'new_intents',
                  'aborted_intents',
                  'canceled_intents',
                  'completed_intent',
                  'expected_entities',
                  'expected_intents',
                  'expected_text']

    dont_copy_attrs = [
        'id',
        'conversation_id',
        'input',
        'intent_response',
        'new_intents',
    ]

    def __init__(self, conversation_id):
        self.conversation_id = conversation_id
        self.id = str(uuid.uuid4())
        self.input = None
        self.intent_response = None
        self.response_messages = OrderedDict()
        self.response_message_text = None
        self.slots_filled = MessageGroup()
        self.question = None
        self.active_intent = None
        self.new_intents = []
        self.aborted_intents = []
        self.canceled_intents = []
        self.completed_intent = None
        self.expected_entities = None
        self.expected_intents = None
        self.expected_text = None
        self.is_repeat = False
        self.repeat_reason = None

    def save(self):
        tx = Transactions(id=self.id, conversation_id=self.conversation_id, data=json.dumps(self.get_save_data()))
        db.session.merge(tx)
        db.session.commit()

    def add_filled_slot(self, intent, entity):
        self.slots_filled[intent.name] = entity

    def add_new_intent(self, intent):
        self.new_intents.append(intent)

    def prepend_new_intent(self, intent):
        self.new_intents.insert(0, intent)

    def abort_intent(self, intent):
        dbg('Aborting intent: %s' % intent.name, color='magenta')
        self.aborted_intents.append(intent)

    def cancel_intent(self, intent):
        dbg('Canceling intent: %s' % intent.name, color='magenta')
        self.canceled_intents.append(intent)

    def add_response_message(self, message_name, message, expected_entities=None, expected_intents=None, expected_text=None):
        self.response_messages[message_name] = message
        dbg('Adding response message: %s: %s' % (message_name, message), color='blue')
        if expected_entities or expected_intents or expected_text:
            assert not self.requires_answer(), 'A transaction can only require a single answer'

        if expected_entities:
            assert type(expected_entities) == dict
            self.expected_entities = expected_entities
        if expected_intents:
            assert type(expected_intents) == dict
            self.expected_intents = expected_intents
        if expected_text:
            assert type(expected_text) == dict
            self.expected_text = expected_text

    # This method and friends probably need to be refactored
    def add_response_message_object(self, msg):
        self.add_response_message(msg.name, msg.get_prompt(),
                                  expected_entities=getattr(msg, 'entity_actions', None),
                                  expected_intents=getattr(msg, 'intent_actions', None))

    def format_response_message(self, context={}):
        response_message = ' '.join([x for x in self.response_messages.values() if x])
        if '{' in response_message or '}' in response_message:
            assert ('{' in response_message) and ('}' in response_message), 'Invalid message template, open or close braces missing: "%s"' % response_message
            # TODO: we shouldnt allow prompts that require context values that
            # are not filled if there are other options
            try:
                response_message = response_message.format(**context)
            except KeyError, e:
                raise KeyError('Invalid message template, could not find "%s" in context' % str(e))
            self.response_message_text = response_message
        return response_message

    def copy_data_from_transaction(self, other_tx):
        for k,v in vars(other_tx).items():
            if k in self.dont_copy_attrs:
                continue
            if isinstance(v, dict):
                v = v.copy()
            setattr(self, k, v)

    def requires_answer(self):
        if self.expected_entities or self.expected_intents or self.expected_text:
            return True
        return False

    def is_answered(self, entities, intents, input):
        if not self.requires_answer():
            return True, None

        if self.expected_entities:
            for entity, action in self.expected_entities.items():
                if entity in [x.slot_name for x in entities]:
                    dbg('Expected entity %s found in answer' % entity, color='blue')
                    return True, action

        if self.expected_intents:
            for intent, action in self.expected_intents.items():
                if intent in [x.name for x in intents]:
                    dbg('Expected intent %s found in answer' % intent, color='blue')
                    return True, action

        if self.expected_text:
            assert False, 'Not supported yet'

        dbg('Transaction went unanswered', color='blue')
        return False, None

class Conversation(JSONMixin, SaveMixin):
    save_attrs = ['id',
                  'nlu',
                  'intents',
                  'active_intents',
                  'completed_intents',
                  'active_intent',
                  'question_attempts']

    def __init__(self, metadata=None, nlu='luis'):
        self.id = str(uuid.uuid4())
        self.metadata = get_default_metadata()
        if metadata:
            dbg('Updating conversation metadata: %s' % metadata, color='magenta')
            self.metadata = dictmerge(copy.deepcopy(self.metadata), metadata, overwrite=True)
        self.nlu = nlu
        self.transactions = OrderedDictPlus()
        # TODO: this intent dict will also contain values as slots become
        # filled, but similar intent objects get passed around/used that dont
        # have those values. This is a bit confusing and should be cleaned up.
        self.intents = OrderedDictPlus()
        self.active_intents = OrderedDictPlus()
        self.completed_intents = OrderedDictPlus()
        self.active_intent = None
        self.question_attempts = OrderedDictPlus()
        self.completed = False

    def save(self):
        convo = Conversations(id=self.id, data=json.dumps(self.get_save_data()))
        db.session.merge(convo)
        db.session.commit()

    def do_action(self, tx, action, valid_entities=None, valid_intents=None, skip_common_messages=False):
        dbg('Do action %s' % action, color='magenta')

        if action == Actions.NONE:
            pass

        elif action == Actions.CANCEL_INTENT:
            self.cancel_intent(tx)

        elif action == Actions.END_CONVERSATION:
            self.completed = True
            if not skip_common_messages:
                self.add_common_response_message(tx, self.metadata, 'goodbye')

        elif action == Actions.REPLACE_SLOT:
            last_tx = self.get_last_transaction()
            slots_filled = last_tx.slots_filled
            assert slots_filled, 'Trying to replace slot but no slot filled on previous transaction'
            filled_slot_names = [x.slot_name for x in slots_filled.values()]

            for entity in valid_entities:
                if entity.slot_name in filled_slot_names:
                    filled_slot = self.fill_intent_slot(tx, self.active_intent, entity)
                    if filled_slot.follow_up:
                        fu = filled_slot.follow_up
                        dbg('Adding follow-up %s during REPLACE_SLOT' % fu.name, color='magenta')
                        question, prompt = self.get_question_and_prompt(tx, fu)
                        if not question:
                            # Can happen if we've exhausted the question/slot and the intent is aborted
                            # TODO: check specifically for the intent being aborted?
                            return
                        tx.add_response_message('%s:%s' % (self.active_intent.name, question.name), prompt,
                                                expected_entities=question.entity_actions,
                                                expected_intents=question.intent_actions)

        elif action == Actions.REPEAT_SLOT:
            last_tx = self.get_last_transaction()
            slots_filled = last_tx.slots_filled
            assert slots_filled, 'Trying to repeat slot but no slot filled on previous transaction'
            for slot_name, slot in slots_filled.items():
                self.clear_filled_slot(self.active_intent, slot)

        elif action.startswith('Trigger'):
            intent_name = ''.join(action.split('Trigger')[1:])
            intent = get_triggered_intent(self.metadata, intent_name)
            self.prepend_active_intent(intent)

        else:
            assert False, 'Unrecognized action: %s' % action

    def understand(self, tx, input):
        last_tx = self.get_last_transaction()

        if input.type == 'intent':
            intent_response = TriggeredIntentResponse(self.metadata, input.value, input.context)
        elif input.type == 'text':
            if self.nlu == 'luis':
                intent_response = LUISResponse(self.metadata, luis(input.value), last_tx)
            else:
                assert False, 'nlu not supported: %s' % self.nlu
        else:
            assert False, 'Invalid input: %s' % input

        dbg(vars(intent_response), color='blue')
        tx.intent_response = intent_response
        return intent_response

    def get_last_transaction(self):
        # Assumes current transaction already added
        txs = self.transactions.values()
        if len(txs) < 2:
            return None
        return txs[-2]

    def transaction_repeatable(self, last_tx):
        if not last_tx.question:
            return True
        question_attempts = self.get_question_attempts(self.active_intent, last_tx.question)
        if question_attempts < MAX_QUESTION_ATTEMPTS:
            return True
        return False

    def repeat_transaction(self, tx, last_tx, reason=None):
        if last_tx.question:
            assert self.add_question_attempt(self.active_intent, last_tx.question), 'Unable to repeat transaction. Question exhausted: %s' % last_tx.question
        tx.copy_data_from_transaction(last_tx)
        tx.repeat = True
        tx.repeat_reason = reason

    def get_intent_slots(self, intent):
        return self.intents[intent.name].slots

    def get_question_attempts(self, intent, question):
        attempts = self.question_attempts.get(intent.name, {}).get(question.name, 0)
        return attempts

    def add_question_attempt(self, intent, question):
        '''Tracks count of attempts for each question'''
        if intent.name not in self.question_attempts:
            self.question_attempts[intent.name] = {}
        if question.name not in self.question_attempts[intent.name]:
            self.question_attempts[intent.name][question.name] = 0
        attempts = self.question_attempts[intent.name][question.name] + 1
        if attempts > MAX_QUESTION_ATTEMPTS:
            return False
        self.question_attempts[intent.name][question.name] = attempts
        return True

    def clear_question_attempts(self, intent):
        self.question_attempts[intent.name] = 0

    def clear_filled_slot(self, intent, slot):
        current_value = self.intents[intent.name].slots[slot.slot_name].value
        assert current_value, 'Slot %s on intent %s is not filled' % (slot, intent)
        self.intents[intent.name].slots[slot.slot_name].value = None
        dbg('Clearing filled slot %s for intent %s' % (slot.slot_name, intent.name), color='magenta')

    def get_next_question_and_prompt(self, tx, remaining_questions):
        question = remaining_questions.get_next()

        if not self.add_question_attempt(self.active_intent, question):
            # We've asked this question the max number of times already
            self.abort_intent(tx)
            return None, None

        tx.question = question
        prompt = question.get_prompt()
        return question, prompt

    def get_question_and_prompt(self, tx, question):
        return self.get_next_question_and_prompt(tx, MessageGroup([(question.name, question)]))

    def abort_intent(self, tx):
        if not self.active_intent:
            error('Trying to abort intent but no intent is active on conversation')
            return
        tx.abort_intent(self.active_intent)
        self.add_common_response_message(tx, self.metadata, 'intent_aborted')
        self.clear_question_attempts(self.active_intent)
        self.remove_active_intent(self.active_intent)

    def cancel_intent(self, tx):
        if not self.active_intent:
            error('Trying to cancel intent but no intent is active on conversation')
            return
        tx.cancel_intent(self.active_intent)
        self.clear_question_attempts(self.active_intent)
        self.remove_active_intent(self.active_intent)

    # TODO: manage data structure instead of rebuilding?
    def get_filled_slots(self):
        filled_slots = {}
        for intent_name, intent in self.intents.items():
            for slot_name, slot in intent.slots.items():
                if slot.value is not None:
                    filled_slots.setdefault(slot_name, MessageGroup())[intent_name] = slot.value
        return filled_slots

    def get_filled_slots_by_intent(self, intent):
        '''returns a simple map of slot names to values for this intent'''
        filled_slots = {}
        for slot_name, slot in intent.slots.items():
            if slot.value is not None:
                filled_slots[slot_name] = slot.value.value
        return filled_slots

    def get_filled_slots_by_name(self, slot_name):
        filled_slots = self.get_filled_slots()
        return filled_slots.get(slot_name, MessageGroup())

    def fill_intent_slot(self, tx, intent, entity):
        dbg('Filling slot %s for intent %s' % (entity.slot_name, intent.name), color='magenta')
        slot = self.intents[intent.name].slots[entity.slot_name]
        slot.value = entity
        tx.add_filled_slot(intent, entity)
        return slot

    def fill_intent_slots_with_entities(self, tx, intent, entities):
        '''This can return slots and/or follow ups for filled slots'''
        remaining_questions = intent.get_remaining_intent_slots()
        if not entities:
            return remaining_questions

        follow_up_added = False
        if remaining_questions:
            for entity in entities:
                if entity.slot_name in remaining_questions:
                    slot = self.intents[intent.name].slots[entity.slot_name]
                    if slot.follow_up and follow_up_added:
                        warn('Not filling slot %s with additional follow-up %s' % (entity.slot_name, slot.follow_up.name))
                        continue

                    filled_slot = self.fill_intent_slot(tx, intent, entity)
                    del remaining_questions[entity.slot_name]
                    if filled_slot.follow_up:
                        fu = filled_slot.follow_up
                        dbg('Adding follow-up %s' % fu.name, color='cyan')
                        remaining_questions.prepend(fu.name, fu)
                        follow_up_added = True

            if not remaining_questions:
                dbg('All slots filled by existing slot data', color='cyan')
                if intent == self.active_intent:
                    self.active_intent_completed(tx)
                else:
                    self.add_completed_intent(tx, intent)

        return remaining_questions

    def fill_intent_slots_with_filled_slots(self, tx, intent):
        '''Only returns remaining slots, no follow-ups. TODO: consider changing that.'''
        remaining_slots = intent.get_remaining_intent_slots()
        if remaining_slots:
            for slot_name, slot in remaining_slots.items():
                filled_slots = self.get_filled_slots_by_name(slot_name)
                if not any(filled_slots.values()):
                    # This slot has no values, so we cant fill anything. Move on.
                    continue

                # This slot has already been filled. Reuse its value.
                filled_slot = self.fill_intent_slot(tx, intent, filled_slots.values()[0])

            remaining_slots = intent.get_remaining_intent_slots()
            if not remaining_slots:
                dbg('All slots filled by existing slot data', color='cyan')
                if intent == self.active_intent:
                    self.active_intent_completed(tx)
                else:
                    self.add_completed_intent(tx, intent)

        return remaining_slots

    def add_active_intent(self, intent):
        if not intent.repeatable:
            assert intent.name not in self.intents, 'Intent is not repeatable: %s' % intent.name
        dbg('Adding active intent %s' % intent.name, color='cyan')
        self.intents[intent.name] = intent
        self.active_intents[intent.name] = intent

    def prepend_active_intent(self, intent):
        if not intent.repeatable:
            assert intent.name not in self.intents, 'Intent is not repeatable: %s' % intent.name
        dbg('Prepending active intent %s' % intent.name, color='cyan')
        self.intents[intent.name] = intent
        self.active_intents.prepend(intent.name, intent)

    def remove_active_intent(self, intent):
        if intent.name in self.active_intents:
            del self.active_intents[intent.name]
        if self.active_intent and self.active_intent.name == intent.name:
            self.active_intent = None

    def add_completed_intent(self, tx, intent):
        dbg('Intent %s completed' % intent.name, color='white')
        try:
            response = intent.fulfill(self, tx, self.get_intent_slots(intent))
            if response:
                if not response.success():
                    warn('Fulfillment did not succeed! Reason: %s' % response.status_reason)
                if response.message:
                    dbg('Adding fulfillment response %s' % response, color='white')
                    tx.add_response_message_object(response.message)
                if response.action:
                    self.do_action(tx, response.action, skip_common_messages=True if response.message else False)
        finally:
            self.completed_intents[intent.name] = intent
            self.remove_active_intent(intent)
            tx.completed_intent = intent

    def active_intent_completed(self, tx):
        if self.active_intent:
            self.add_completed_intent(tx, self.active_intent)
            self.active_intent = None

    def remove_completed_intent(self, intent):
        assert False, 'Probably shouldnt allow this'
        if intent.name in self.completed_intents:
            del self.completed_intents[intent.name]

    def add_new_intent_message(self, tx, intent, response_type=ResponseType.ACTIVE, entities=None):
        '''Gets the message(s) at the start of a new intent'''
        response = random.choice(intent.responses.get(response_type, ['']))
        if not response:
            warn('No response for intent %s' % intent)

        prompt = ''
        if response_type in [ResponseType.ACTIVE, ResponseType.RESUMED] and self.get_intent_slots(intent):
            remaining_questions = self.fill_intent_slots_with_entities(tx, intent, entities)
            if not remaining_questions:
                return # The intent was satisfied by data in collected entities
            assert not any([type(x) == FollowUp for x in remaining_questions]), 'FollowUp found while adding message for new intent: %s' % remaining_questions

            # TODO: add this back in but require asking confirmation
            #remaining_questions = self.fill_intent_slots_with_filled_slots(tx, intent)
            #if not remaining_questions:
            #    return # The intent was satisfied by existing slot data
            #assert not any([type(x) == FollowUp for x in remaining_questions]), 'FollowUp found while adding message for new intent: %s' % remaining_questions

            # There are slots to prompt for this intent still
            question, prompt = self.get_next_question_and_prompt(tx, remaining_questions)
            if not question:
                return

        if response: tx.add_response_message('%s:%s' % (intent.name, response_type), response)
        if prompt: tx.add_response_message('%s:%s' % (intent.name, question.name), prompt)

    def add_common_response_message(self, tx, metadata, message_name):
        message_info = metadata['COMMON_MESSAGES'][message_name]
        message = None
        expected_entities = None
        expected_intents = None

        if not message_info:
            pass
        elif type(message_info) == str:
            message = message_info
        elif type(message_info) in (list, tuple):
            message = random.choice(message_info)
        else:
            message = random.choice(message_info['prompts'])
            expected_entities = message_info.get('entity_actions', None)
            expected_intents = message_info.get('intent_actions', None)
            action = message_info.get('action', None)

            if expected_intents:
                # XXX: Can this be made unnecessary when processing expected_intents?
                for intent in metadata['INTENT_METADATA'].keys():
                    if intent not in expected_intents:
                        expected_intents[intent] = Actions.NONE

            if action:
                self.do_action(tx, action, skip_common_messages=True if message else False)

        tx.add_response_message(message_name, message, expected_entities=expected_entities, expected_intents=expected_intents)

    def create_response_message(self, tx, valid_intents, valid_entities):
        last_tx = self.get_last_transaction()

        # Analyze new intents
        for i, intent in enumerate(valid_intents):
            if intent.is_greeting:
                if i > 0:
                    warn('greetings only allowed as top intent, skipping %s' % intent.name)
                    continue
                if last_tx:
                    warn('greetings only allowed on first transaction, skipping %s' % intent.name)
                    continue
            if intent.name in self.active_intents and not intent.repeatable:
                warn('intent %s already active' % intent.name)
                continue
            if intent.name in self.completed_intents and not intent.repeatable:
                warn('intent %s already completed' % intent.name)

            if intent.preemptive:
                self.prepend_active_intent(intent)
                tx.prepend_new_intent(intent)

                if intent.name == CommonIntents.CANCEL:
                    dbg('Cancel Intent', color='white')
                    self.add_common_response_message(tx, self.metadata, 'intent_canceled')
                    return

                if intent.name == CommonIntents.REPEAT:
                    dbg('Repeat Intent', color='white')
                    if last_tx:
                        self.repeat_transaction(tx, last_tx, reason='user request')
                    else:
                        self.add_common_response_message(tx, self.metadata, 'fallback')
                    return

                if intent.name == CommonIntents.HELP:
                    dbg('Help Intent', color='white')
                    self.add_common_response_message(tx, self.metadata, 'help')
                    return
            else:
                self.add_active_intent(intent)
                tx.add_new_intent(intent)

        # Handle transactional questions that require answers
        # TODO: combine with slot logic?
        if last_tx and last_tx.requires_answer():
            is_answered, action = last_tx.is_answered(valid_entities, valid_intents, tx.input)
            if is_answered:
                dbg('Last TX answered', color='white')
                self.do_action(tx, action, valid_entities=valid_entities, valid_intents=valid_intents)
                if self.completed or tx.response_messages:
                    return
            else:
                dbg('Last TX not answered', color='white')
                # TODO: add "i didnt get that" or similar to response
                if self.transaction_repeatable(last_tx):
                    self.repeat_transaction(tx, last_tx, reason='last transaction not answered')
                else:
                    self.abort_intent(tx)
                return

        # All one-off and preemptive intents should have been handled before this
        for intent in self.active_intents.values():
            if intent.is_answer:
                dbg('Removing is_answer intent from active list: %s' % intent.name, color='white')
                self.remove_active_intent(intent)
            elif intent.preemptive and not intent.slots:
                # We assume these already displayed any relevant message
                dbg('Removing preemptive intent with no slots from active list: %s' % intent.name, color='white')
                self.remove_active_intent(intent)

        # Handle ongoing intent
        if self.active_intent:
            tx.active_intent = self.active_intent
            dbg('Active intent %s' % self.active_intent.name, color='white')
            remaining_questions = self.fill_intent_slots_with_entities(tx, self.active_intent, valid_entities)
            if remaining_questions:
                question, prompt = self.get_next_question_and_prompt(tx, remaining_questions)
                if not question:
                    # Can happen if we've exhausted the question/slot and the intent is aborted
                    # TODO: check specifically for the intent being aborted?
                    return

                tx.add_response_message('%s:%s' % (self.active_intent.name, question.name), prompt,
                                        expected_entities=question.entity_actions,
                                        expected_intents=question.intent_actions)
                return
            else:
                self.active_intent_completed(tx)

        # We've handled any one-off or active intents, move on to other intents
        # that were already registered
        for i, intent in enumerate(self.active_intents.values()):
            message = None
            if i == 0:
                self.active_intent = intent
                tx.active_intent = intent
                response_type = ResponseType.ACTIVE
                if tx.completed_intent and (intent not in tx.new_intents):
                    # The user completed an intent with their most recent response
                    # but they have already queued up other intents from previous transactions
                    response_type = ResponseType.RESUMED
            else:
                response_type = Response.DEFERRED

            dbg('Handling %s intent: %s' % (response_type, intent.name), color='white')
            self.add_new_intent_message(tx, intent, response_type=response_type, entities=valid_entities)

        if not tx.response_messages:
            if tx.completed_intent or (self.completed_intents and not self.active_intents):
                self.add_common_response_message(tx, self.metadata, 'intents_complete')
            else:
                self.add_common_response_message(tx, self.metadata, 'fallback')

    def process_intent_response(self, tx, intent_response):
        valid_intents, valid_entities = intent_response.get_valid()
        if not valid_intents:
            warn('no valid intents found')
        self.create_response_message(tx, valid_intents, valid_entities)

    def create_transaction(self):
        tx = Transaction(self.id)
        self.transactions[tx.id] = tx
        return tx

    def reply(self, tx, input):
        input = Input(input)
        tx.input = input

        if input.type == 'action':
            self.do_action(tx, input.value)
        else:
            intent_response = self.understand(tx, input)
            self.process_intent_response(tx, intent_response)

        response_message = None
        if tx.response_messages:
            context = {}
            if self.active_intent:
                context = self.get_filled_slots_by_intent(self.active_intent)
            response_message = tx.format_response_message(context=context)

        return response_message
