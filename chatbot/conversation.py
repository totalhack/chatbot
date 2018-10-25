from collections import OrderedDict
import requests
import uuid

import usaddress

from chatbot import app
from chatbot.utils import *

INTENT_FILTER_THRESHOLD = 0.50
ENTITY_FILTER_THRESHOLD = 0.50
MAX_SLOT_ATTEMPTS = 2

class CommonIntents(object):
    REPEAT = 'Repeat'
    CONFIRM_YES = 'ConfirmYes'
    CONFIRM_NO = 'ConfirmNo'
    HELP = 'Help'
    NONE = 'None'
    WELCOME = 'Welcome'

class ResponseType(object):
    ACTIVE = 'active'
    RESUMED = 'resumed'
    DEFERRED = 'deferred'

COMMON_MESSAGES = {
    'fallback': [
        "Sorry, I didn't get that",
    ],

    'help': [
        "Stil need to add a help message",
    ],

    'intents_complete': [
        "Is there anything else I can help you with today?",
    ],

    'intent_aborted': [
        "I'm sorry, I'm unable to help you at this time",
    ],

    'goodbye': [
        "Thanks. Have a nice day!"
    ]
}

INTENT_METADATA = {
    CommonIntents.WELCOME: {
        'responses': {
            ResponseType.ACTIVE: [
                'Hi, how are you?',
            ],
        },
        'is_greeting': True,
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
}

APP_INTENT_METADATA = app.config.get('APP_INTENT_METADATA', {})
INTENT_METADATA.update(APP_INTENT_METADATA)

def luis(query, staging=True, verbose=True):
    params = {
        'subscription-key': app.config['LUIS_SUBKEY'],
        'staging': 'true' if staging else 'false',
        'verbose': 'true' if verbose else 'false',
        'timezoneOffset': '-300',
        'q': query,
    }
    resp = requests.get(app.config['LUIS_URL'], params=params)
    resp.raise_for_status()
    return resp.json()

class Action(object):
    NONE = 'none'
    END_CONVERSATION = 'end_conversation'

class Slot(PrintMixin, JSONMixin):
    repr_attrs = ['name']

    def __init__(self, name, prompts, entity_handler_name=None):
        self.name = name
        self.prompts = prompts
        if entity_handler_name:
            assert type(entity_handler_name) in (str, unicode) and entity_handler_name in globals(), 'Invalid entity handler: %s' % entity_handler_name
        self.entity_handler_name = entity_handler_name
        self.value = None

    def get_prompt(self):
        prompt = random.choice(self.prompts)
        return prompt

    def copy(self):
        new_slot = Slot(self.name, self.prompts, self.entity_handler_name)
        new_slot.value = self.value
        return new_slot

class SlotGroup(OrderedDictPlus, JSONMixin):
    def get_next_slot(self):
        return self.values()[0]

    def get_next_prompt(self):
        return self.get_next_slot().get_prompt()

class Intent(PrintMixin, JSONMixin):
    repr_attrs = ['name', 'score']

    def __init__(self, name, score, responses=None, slots=None, repeatable=False, preemptive=False, is_answer=False, is_greeting=False):
        self.name =name
        self.score = score
        self.repeatable = repeatable
        self.preemptive = preemptive
        self.is_answer = is_answer
        self.is_greeting = is_greeting
        self.responses = {}
        if responses:
            for response_type, response_texts in responses.items():
                assert response_type in (ResponseType.ACTIVE, ResponseType.RESUMED, ResponseType.DEFERRED), 'Invalid response type: %s' % response_type
                assert type(response_texts) in (tuple, list)
                self.responses[response_type] = response_texts

        self.slots = SlotGroup()
        if slots:
            for slot_tuple in slots.items():
                slot_name, slot_prompts, entity_handler_name = (None, None, None)
                if len(slot_tuple) == 2:
                    slot_name, slot_prompts = slot_tuple
                else:
                    slot_name, slot_prompts, entity_handler_name = slot_tuple
                assert type(slot_prompts) in (tuple, list)

                if (not entity_handler_name) and slot_name in SLOT_ENTITY_HANDLERS:
                    entity_handler_name = SLOT_ENTITY_HANDLERS[slot_name]
                self.slots[slot_name] = Slot(slot_name, slot_prompts, entity_handler_name=entity_handler_name)

    def get_remaining_intent_slots(self):
        return SlotGroup([(k, v) for k,v in self.slots.items() if v.value is None])

    def get_completed_intent_slots(self):
        return SlotGroup([(k, v) for k,v in self.slots.items() if v.value is not None])

class Entity(PrintMixin, JSONMixin):
    repr_attrs = ['name', 'type']

    def __init__(self, name, type, start_index, end_index, score=None, values=None):
        self.name = name
        self.type = type
        self.slot_name = type
        self.start_index = start_index
        self.end_index = end_index
        self.score = score
        self.values = values

class EntityHandler(JSONMixin):
    def process(self, query, nlu_entities, translations={}):
        entities = []
        for entity in nlu_entities:
            entities.append(Entity(name=entity['entity'],
                                   type=translations.get(entity['type'], entity['type']),
                                   start_index=entity['startIndex'],
                                   end_index=entity['endIndex'],
                                   score=entity.get('score', None),
                                   values=entity.get('resolution', None)))
        return entities

class AddressEntityHandler(EntityHandler):
    def process(self, query, nlu_entities, translations={}):
        entities = super(AddressEntityHandler, self).process(query, nlu_entities, translations=translations)
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
                                values=address_value)

        entities.append(address_entity)
        return entities

SLOT_ENTITY_HANDLERS = {
    'address': 'AddressEntityHandler',
}

class IntentResponse(PrintMixin, JSONMixin):
    repr_attrs = ['query', 'intents', 'entities']

    def __init__(self, query, intents, entities=[]):
        self.query = query
        assert intents
        self.intents = sorted(intents, key=lambda x: x.score, reverse=True)
        self.top_intent = self.intents[0]
        self.entities = entities or []

    def filter_intents(self, score):
        return [x for x in self.intents if x.score > score]

    def filter_entities(self, score):
        return [x for x in self.entities if (x.score is None or x.score > score)]

    def get_valid(self, intent_threshold=INTENT_FILTER_THRESHOLD, entity_threshold=ENTITY_FILTER_THRESHOLD):
        valid_intents = self.filter_intents(intent_threshold)
        valid_entities = self.filter_entities(entity_threshold)
        return valid_intents, valid_entities

class TriggeredIntentResponse(IntentResponse):
    def __init__(self, intent_name):
        assert intent_name in INTENT_METADATA, 'Invalid intent name: %s' % intent_name
        meta = INTENT_METADATA[intent_name]
        score = 1
        intents = [Intent(intent_name, score, **meta)]
        super(TriggeredIntentResponse, self).__init__(None, intents, entities=None)

class LUISResponse(IntentResponse):
    ENTITY_TRANSLATIONS = {
        'geographyV2': 'address',
        'builtin.personName': 'fullname',
        'builtin.email': 'email',
        'builtin.phonenumber': 'phonenumber'
    }

    def __init__(self, luis_json, last_tx=None):
        intents = []
        for intent in luis_json['intents']:
            name = intent['intent']
            meta = INTENT_METADATA.get(name, {})
            intents.append(Intent(intent['intent'], intent['score'], **meta))

        pprint(luis_json['entities'])

        entity_handler_name = 'EntityHandler'
        if last_tx and last_tx.slot_prompted:
            slot = last_tx.slot_prompted
            entity_handler_name = slot.entity_handler_name or entity_handler_name
        entity_handler = globals()[entity_handler_name]

        entities = entity_handler().process(luis_json['query'], luis_json['entities'], self.ENTITY_TRANSLATIONS)
        super(LUISResponse, self).__init__(luis_json['query'], intents, entities=entities)

class Input(PrintMixin, JSONMixin):
    repr_attrs = ['type', 'value']
    types = [
        'text',
        'intent'
    ]

    def __init__(self, input):
        self.raw_input = input
        self.type = None
        self.value = None

        if type(input) in (str, unicode):
            self.type = 'text'
            self.value = input
        elif type(input) == dict:
            self.type = input['type']
            self.value = input['value']
        else:
            assert False, 'Invalid input: %s' % input

        assert self.type in self.types, 'Invalid input type: %s' % self.type

class Transaction(JSONMixin):
    def __init__(self):
        self.id = str(uuid.uuid4())
        self.input = None
        self.intent_response = None
        self.response_messages = OrderedDict()
        self.response_message_text = None
        self.slots_filled = SlotGroup()
        self.slot_prompted = None
        self.active_intent = None
        self.new_intents = []
        self.aborted_intents = []
        self.completed_intent = None
        self.expected_entities = None
        self.expected_intents = None
        self.expected_text = None

    def add_filled_slot(self, intent, entity):
        self.slots_filled[intent.name] = entity

    def add_new_intent(self, intent):
        self.new_intents.append(intent)

    def prepend_new_intent(self, intent):
        self.new_intents.insert(0, intent)

    def abort_intent(self, intent):
        self.aborted_intents.append(intent)

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

    def format_response_message(self):
        response_message = ' '.join(self.response_messages.values())
        self.response_message_text = response_message
        return response_message

    def copy_data_from_transaction(self, other_tx):
        self.response_messages = other_tx.response_messages.copy()
        self.response_message_text = other_tx.response_message_text
        self.slot_prompted = None
        if other_tx.slot_prompted:
            self.slot_prompted = other_tx.slot_prompted.copy()

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
                    dbg('Expected entity %s found in asnwer' % entity, color='blue')
                    return True, action

        if self.expected_text:
            assert False, 'Not supported yet'

        dbg('Transaction went unanswered', color='blue')
        return False, None

class Conversation(JSONMixin):
    def __init__(self, conversation_id, nlu='luis'):
        self.id = conversation_id
        self.nlu = 'luis'
        self.transactions = OrderedDictPlus()
        self.intents = OrderedDictPlus()
        self.active_intents = OrderedDictPlus()
        self.completed_intents = OrderedDictPlus()
        self.active_intent = None
        self.slot_attempts = OrderedDictPlus()

    def understand(self, tx, input):
        last_tx = self.get_last_transaction()

        if input.type == 'intent':
            intent_response = TriggeredIntentResponse(input.value)
        elif input.type == 'text':
            if self.nlu == 'luis':
                intent_response = LUISResponse(luis(input.value), last_tx)
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

    def get_intent_slots(self, intent):
        return self.intents[intent.name].slots

    def add_slot_attempt(self, intent, slot):
        if intent.name not in self.slot_attempts:
            self.slot_attempts[intent.name] = {}
        if slot.name not in self.slot_attempts[intent.name]:
            self.slot_attempts[intent.name][slot.name] = 0
        attempts = self.slot_attempts[intent.name][slot.name] + 1
        if attempts > MAX_SLOT_ATTEMPTS:
            return False
        self.slot_attempts[intent.name][slot.name] = attempts
        return True

    def clear_slot_attempts(self, intent):
        self.slot_attempts[intent.name] = 0

    def abort_intent(self, tx, intent):
        tx.abort_intent(intent)
        tx.add_response_message('intent_aborted', random.choice(COMMON_MESSAGES['intent_aborted']))
        self.clear_slot_attempts(intent)
        self.remove_active_intent(intent)

    # TODO: manage data structure instead of rebuilding?
    def get_filled_slots(self):
        filled_slots = {}
        for intent_name, intent in self.intents.items():
            for slot_name, slot in intent.slots.items():
                if slot.value is not None:
                    filled_slots.setdefaut(slot_name, SlotGroup())[intent_name] = slot.value
        return filled_slots

    def get_filled_slots_by_name(self, slot_name):
        filled_slots = self.get_filled_slots()
        return filled_slots.get(slot_name, SlotGroup())

    def fill_intent_slot(self, tx, intent, entity):
        dbg('Filling slot %s for intent %s' % (entity.slot_name, intent.name), color='magenta')
        slot = self.intents[intent.name].slots[entity.slot_name]
        slot.value = entity
        tx.add_filled_slot(intent, entity)

    def fill_intent_slots_with_entities(self, tx, intent, entities):
        remaining_slots = intent.get_remaining_intent_slots()
        if remaining_slots:
            for entity in entities:
                if entity.slot_name in remaining_slots:
                    self.fill_intent_slot(tx, intent, entity)
                    del remaining_slots[entity.slot_name]
        return remaining_slots

    def fill_intent_slots_with_filled_slots(self, tx, intent):
        remaining_slots = intent.get_remaining_intent_slots()
        if remaining_slots:
            for slot_name, slot in remaining_slots.items():
                filled_slots = self.get_filled_slots_by_name(slot_name)
                if not any(filled_slots.values()):
                    # This slot has not values, so we cant fill anything. Move on.
                    continue

                # This slot has already been filled. Reuse its value.
                # TODO: ask confirmation about this being correct? Dont just take
                # first value?
                self.fill_intent_slot(tx, intent, filled_slots.values()[0])

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
            assert intent.name not in self.intents
        self.intents[intent.name] = intent
        self.active_intents[intent.name] = intent

    def prepend_active_intent(self, intent):
        if not intent.repeatable:
            assert intent.name not in self.intents
        self.intents[intent.name] = intent
        self.active_intents.prepend(intent.name, intent)

    def remove_active_intent(self, intent):
        if intent.name in self.active_intents:
            del self.active_intents[intent.name]
        if self.active_intent and self.active_intent.name == intent.name:
            self.active_intent = None

    def add_completed_intent(self, tx, intent):
        dbg('Intent %s completed' % intent.name, color='white')
        self.completed_intents[intent.name] = intent
        self.remove_active_intent(intent)
        tx.completed_intent = intent

    def active_intent_completed(self, tx):
        self.add_completed_intent(tx, self.active_intent)
        self.active_intent = None

    def remove_completed_intent(self, intent):
        assert False, 'Probably shouldnt allow this'
        if intent.name in self.completed_intents:
            del self.completed_intents[intent.name]

    def add_intent_message(self, tx, intent, response_type=ResponseType.ACTIVE):
        response = random.choice(intent.responses.get(response_type, ['']))
        if not response:
            warn('No response for intent %s' % intent)

        slot_prompt = ''
        if response_type in [ResponseType.ACTIVE, ResponseType.RESUMED] and self.get_intent_slots(intent):
            remaining_slots = self.fill_intent_slots_with_filled_slots(tx, intent)
            if not remaining_slots:
                return # The intent was satisfied by existing data
            # There are slots to prompt for this intent still
            slot = remaining_slots.get_next_slot()
            if not self.add_slot_attempt(self.active_intent, slot):
                # We've already asked this question the max number of times
                self.abort_intent(tx, intent)
                return
            slot_prompt = slot.get_prompt()
            tx.slot_prompted = slot

        if response: tx.add_response_message('%s:%s' % (intent.name, response_type), response)
        if slot_prompt: tx.add_response_message('%s:%s' % (intent.name, slot.name), slot_prompt)

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

                if intent.name == CommonIntents.REPEAT:
                    dbg('Repeat Intent', color='white')
                    if last_tx:
                        tx.copy_data_from_transaction(last_tx)
                    else:
                        tx.add_response_message('fallback', random.choice(COMMON_MESSAGES['fallback']))
                    return

                if intent.name == CommonIntents.HELP:
                    dbg('Help Intent', color='white')
                    tx.add_response_message('help', random.choice(COMMON_MESSAGES['help']))
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
                if action == Action.NONE:
                    pass # Just continue on?
                elif action == Action.END_CONVERSATION:
                    tx.add_response_message('goodbye', random.choice(COMMON_MESSAGE['goodbye']))
                    return
            else:
                dbg('Last TX not answered', color='white')
                # TODO: add "i didnt get that" or similar to response
                tx.copy_data_from_transaction(last_tx)
                return

        # All oneopff and preemptive intents should have been handled before this
        for intent in self.active_intents.values():
            if intent.is_answer:
                dbg('Removing is_answer intents from active list: %s' % intent.name, color='white')
                self.remove_active_intent(intent)

        # Handle ongoing intent
        if self.active_intent:
            tx.active_intent = self.active_intent
            dbg('Active intent %s' % self.active_intent.name, color='white')
            remaining_slots = self.fill_intent_slots_with_entities(tx, self.active_intent, valid_entities)
            if remaining_slots:
                slot = remaining_slots.get_next_slot()
                if not self.add_slot_attempt(self.active_intent, slot):
                    # We have already asked the max number of times
                    self.abort_intent(tx, intent)
                    return
                slot_prompt = slot.get_prompt()
                tx.slot_prompted = slot
                tx.add_response_message('%s:%s' % (self.active_intent.name, slot.name), slot_prompt)
                return
            else:
                self.active_intent_completed(tx)

        # We've handlded any one-off or active intents, move on to other intents
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

            dbg('Handling %s intent: %s' % (response_type, intent), color='white')
            self.add_intent_message(tx, intent, response_type=response_type)

        if not tx.response_messages:
            if tx.completed_intent:
                expected_intents = {CommonIntents.CONFIRM_YES: Action.NONE,
                                    CommonIntents.CONFIRM_NO: Action.END_CONVERSATION}
                for intent in APP_INTENT_METADATA.keys():
                    expected_intents[intent] = Action.NONE
                tx.add_response_message('intents_complete',
                                        random.choice(COMMON_MESSAGES['intents_complete']),
                                        expected_intents=expected_intents)
            else:
                tx.add_response_message('fallback', random.choice(COMMON_MESSAGES['fallback']))

    def process_intent_response(self, tx, intent_response):
        valid_intents, valid_entities = intent_response.get_valid()
        if not valid_intents:
            warn('no valid intents found')
        self.create_response_message(tx, valid_intents, valid_entities)

    def create_transaction(self):
        tx = Transaction()
        self.transactions[tx.id] = tx
        return tx

    def reply(self, tx, input):
        input = Input(input)
        tx.input = input
        intent_response = self.understand(tx, input)
        self.process_intent_response(tx, intent_response)
        response_message = tx.format_response_message()
        return response_message
