from cachetools import LRUCache, TTLCache
from collections import OrderedDict
import copy

from diskcache import Cache
import requests
import usaddress

from chatbot.model import *
from chatbot.utils import *

CONVO_CACHE = None
NLU_CACHE = None

DEFAULT_CONVO_CACHE_SIZE = 1000
DEFAULT_CONVO_CACHE_TTL = 3600*48
DEFAULT_NLU_CACHE_SIZE = 1000
DEFAULT_NLU_DISK_CACHE_TTL = 3600*24

class CommonIntents(object):
    Cancel = 'Cancel'
    ConfirmYes = 'ConfirmYes'
    ConfirmNo = 'ConfirmNo'
    Help = 'Help'
    NoIntent = 'None' # TODO: for NLUs to convert intent names to canon values
    Repeat = 'Repeat'
    Greeting = 'Greeting'
    Why = 'Why'

class ResponseTypes(object):
    Active = 'Active'
    Deferred = 'Deferred'
    Resumed = 'Resumed'

class Actions(object):
    CancelIntent = 'CancelIntent'
    EndConversation = 'EndConversation'
    NoAction = 'NoAction'
    Repeat = 'Repeat'
    RepeatSlot = 'RepeatSlot'
    ReplaceSlot = 'ReplaceSlot'

class VariableActions(object):
    Trigger = 'Trigger'
    ConfirmSwitchIntent = 'ConfirmSwitchIntent'
    RemoveIntent = 'RemoveIntent'
    RepeatSlotAndRemoveIntent = 'RepeatSlotAndRemoveIntent'

DEFAULT_FOLLOW_UP_ACTIONS = {
    CommonIntents.ConfirmYes: Actions.NoAction,
    CommonIntents.ConfirmNo: Actions.RepeatSlot,
}

def assert_valid_intent_name(metadata, intent_name):
    assert intent_name in metadata['INTENT_METADATA'], 'Invalid intent name: %s' % intent_name

def is_common_intent(val):
    types = get_class_vars(CommonIntents)
    if val in types:
        return True
    return False

def get_entity_handler(name):
    return import_object(name)

def get_nlu(name):
    return import_object(name)

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
                        entity_actions=message_dict.get('entity_actions', None),
                        help=message_dict.get('help', None),
                        why=message_dict.get('why', None))
    elif message_type == 'slot':
        return Slot(message_dict['name'],
                    message_dict['prompts'],
                    entity_handler_name=message_dict.get('entity_handler_name', None),
                    follow_up=message_dict.get('follow_up', None),
                    help=message_dict.get('help', None),
                    why=message_dict.get('why', None))
    else:
        assert False, 'Invalid message type: %s' % message_type

class Question(Message):
    repr_attrs = ['name']

    def __init__(self, name, prompts, intent_actions=None, entity_actions=None, help=None, why=None):
        super(Question, self).__init__(name, prompts)
        self.intent_actions = intent_actions
        self.help = help
        self.why = why
        if intent_actions:
            assert isinstance(intent_actions, dict), 'Invalid type for intent_actions, must be dict: %s' % type(intent_actions)
        self.entity_actions = entity_actions
        if entity_actions:
            assert isinstance(entity_actions, dict), 'Invalid type for entity_actions, must be dict: %s' % type(entity_actions)

    def get_intent_actions(self):
        return self.intent_actions

    def get_entity_actions(self):
        return self.entity_actions

    def get_help(self):
        if not self.help:
            return None
        help = random.choice(self.help)
        return help

    def get_why(self):
        if not self.why:
            return None
        why = random.choice(self.why)
        return why

class Slot(Question):
    repr_attrs = ['name', 'value']

    @classmethod
    def from_dict(cls, metadata, slot_name, slot_info):
        assert isinstance(slot_info, dict), 'Invalid type for slot_info, must be dict: %s' % type(slot_info)
        prompts = slot_info['prompts']

        follow_up_info = slot_info.get('follow_up', {})
        follow_up = FollowUp.from_dict(slot_name, follow_up_info)

        entity_handler_name = slot_info.get('entity_handler', None)
        if (not entity_handler_name) and slot_name in metadata['ENTITY_HANDLERS']:
            entity_handler_name = metadata['ENTITY_HANDLERS'][slot_name]

        autofill = slot_info.get('autofill', None)
        help = slot_info.get('help', None)
        why = slot_info.get('why', None)

        return cls(slot_name, prompts, entity_handler_name=entity_handler_name, follow_up=follow_up, autofill=autofill, help=help, why=why)

    def __init__(self, name, prompts, entity_handler_name=None, follow_up=None, autofill=None, help=None, why=None):
        # TODO: should slot be filled by an action? Allow overriding intent/entity actions for slots?
        entity_actions = {name: Actions.NoAction}
        super(Slot, self).__init__(name, prompts, entity_actions=entity_actions, help=help, why=why)
        if entity_handler_name:
            assert type(entity_handler_name) in (str, unicode), 'Invalid entity handler: %s' % entity_handler_name
        self.entity_handler_name = entity_handler_name
        self.follow_up = follow_up
        if follow_up:
            assert type(follow_up) == FollowUp
        self.autofill = autofill
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
                        copy.deepcopy(self.prompts),
                        entity_handler_name=self.entity_handler_name,
                        follow_up=copy.deepcopy(self.follow_up),
                        autofill=self.autofill,
                        help=self.help,
                        why=self.why)
        if isinstance(self.value, Entity):
            new_slot.value = self.value.copy()
        else:
            new_slot.value = copy.deepcopy(self.value)
        return new_slot

class FollowUp(Question):
    repr_attrs = ['name']

    @classmethod
    def from_dict(cls, slot_name, follow_up_info):
        assert isinstance(follow_up_info, dict), 'Invalid type for follow up info, must be dict: %s' % type(follow_up_info)
        follow_up = None
        if follow_up_info:
            follow_up_name = '%s_follow_up' % slot_name
            # If they provide the slot answer, process it and continue
            entity_actions = {slot_name: Actions.ReplaceSlot}
            follow_up = cls(follow_up_name, follow_up_info['prompts'],
                            intent_actions=follow_up_info.get('intent_actions', DEFAULT_FOLLOW_UP_ACTIONS),
                            entity_actions=entity_actions,
                            help=follow_up_info.get('help', None),
                            why=follow_up_info.get('why', None))
        return follow_up

class Intent(PrintMixin, JSONMixin):
    repr_attrs = ['name', 'score']

    def __init__(self, metadata, name, score, responses=None, slots=None, repeatable=False, preemptive=False, fulfillment=None, is_answer=False, is_greeting=False, help=None, why=None):
        self.name = name
        self.score = score
        self.repeatable = repeatable
        self.preemptive = preemptive
        self.fulfillment = fulfillment
        self.fulfillment_data = None
        self.help = help
        self.why = why
        self.is_answer = is_answer
        self.is_greeting = is_greeting
        self.is_common_intent = is_common_intent(name)
        if not self.is_common_intent:
            assert not self.preemptive, 'Preemptive bot intents are not currently supported'
        self.responses = {}
        if responses:
            for response_type, response_texts in responses.items():
                assert response_type in (ResponseTypes.Active, ResponseTypes.Resumed, ResponseTypes.Deferred), 'Invalid response type: %s' % response_type
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
                    context=convo.context,
                    slot_data=slot_value_data)

    def fulfill(self, convo, tx, slot_data):
        # Set the fulfillment data on this even if there is no fulfillment URL
        # to call, so clients can still have a clean way of getting all slot
        # data for a particular intent.
        fulfillment_data = self.get_fulfillment_data(convo, tx, slot_data)
        self.fulfillment_data = fulfillment_data

        if not self.fulfillment:
            dbg('Nothing to fulfill for intent %s' % self.name)
            return

        dbg('Handling fulfillment for intent %s: %s' % (self.name, self.fulfillment))
        url = self.fulfillment['url']
        headers = {'Content-type': 'application/json'}
        status_code = None

        try:
            resp = requests.post(url, json=fulfillment_data)
            status_code = resp.status_code
            content = resp.content
            resp.raise_for_status()
            return FulfillmentResponse('%s_fulfillment' % self.name, **resp.json())
        except Exception, e:
            content = str(e)
            raise
        finally:
            ff = Fulfillments(conversation_id=convo.id,
                              url=url,
                              status_code=status_code,
                              response=content,
                              data=json.dumps(fulfillment_data))
            db.session.merge(ff)
            db.session.commit()

    def get_help(self):
        if not self.help:
            return None
        help = random.choice(self.help)
        return help

    def get_why(self):
        if not self.why:
            return None
        why = random.choice(self.why)
        return why

class Entity(PrintMixin, JSONMixin):
    repr_attrs = ['name', 'type', 'value', 'score']

    def __init__(self, name, type, start_index=None, end_index=None, score=None, value=None, from_context=False):
        self.name = name
        self.type = type
        self.slot_name = type
        self.start_index = start_index
        self.end_index = end_index
        self.score = score
        self.value = value
        self.from_context = from_context

    def copy(self):
        new_entity = Entity(self.name,
                            self.type,
                            start_index=self.start_index,
                            end_index=self.end_index,
                            score=self.score,
                            value=copy.deepcopy(self.value),
                            from_context=self.from_context)
        return new_entity

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
        address_entities = []
        address_parts = []
        address_part_map = {
            'AddressNumber': 'street_number',
            'StreetName': 'street_name',
            'StreetNamePostType': 'street_type',
            'OccupancyIdentifier': 'unit_number',
            'PlaceName': 'city',
            'StateName': 'state',
        }

        street_address_parts = []
        for label, value in address_dict.items():
            if label in ['Recipient', 'NotAddress']:
                continue
            if label in ['AddressNumber', 'StreetName', 'StreetNamePostType']:
                street_address_parts.append(value)

            address_parts.append(value)
            if label in address_part_map:
                address_part_name = address_part_map[label]
                entity = Entity(name=address_part_name,
                                type=address_part_name,
                                start_index=None,
                                end_index=None,
                                score=None,
                                value=value)
                address_entities.append(entity)

        address_value = ' '.join(address_parts)
        address_entity = Entity(name='address',
                                type='address',
                                start_index=None,
                                end_index=None,
                                score=None,
                                value=address_value)
        entities.insert(0, address_entity)

        street_address_value = ' '.join(street_address_parts)
        street_address_entity = Entity(name='street_address',
                                       type='street_address',
                                       start_index=None,
                                       end_index=None,
                                       score=None,
                                       value=street_address_value)
        entities.insert(1, street_address_entity)

        for entity in address_entities:
            entities.insert(1, entity)

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
            elif isinstance(message, dict):
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

    def __init__(self, query, intents, entities=None):
        self.query = query
        assert intents
        self.intents = sorted(intents, key=lambda x: x.score, reverse=True)
        self.top_intent = self.intents[0]
        self.entities = entities or []

    def filter_intents(self, score):
        return [x for x in self.intents if ((x.score is None or x.score > score) and x.name != 'None')]

    def filter_entities(self, score):
        return [x for x in self.entities if (x.score is None or x.score > score)]

    def add_entities_from_context(self, context):
        for k,v in context.items():
            self.entities.append(Entity(name=k, type=k, value=v, from_context=True))

    def get_valid(self, intent_threshold=0, entity_threshold=0):
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
    def __init__(self, metadata, intent_name):
        intents = [get_triggered_intent(metadata, intent_name)]
        super(TriggeredIntentResponse, self).__init__(None, intents)

#---- Cache Stuff

class DiskCache(object):
    def __init__(self, cache_dir, ttl=None):
        self.ttl = ttl
        self.cache = Cache(cache_dir, eviction_policy='least-recently-used')

    def __getitem__(self, key):
        return self.cache[key]

    def __setitem__(self, key, value):
        return self.cache.set(key, value, expire=self.ttl)

    def get(self, key, default=None):
        return self.cache.get(key, default=default)

    def set(self, key, value):
        return self.cache.set(key, value, expire=self.ttl)

    def clear(self):
        self.cache.clear()

def get_nlu_cache(app_config):
    # TODO: in production, replace with something multi-process friendly
    global NLU_CACHE
    if NLU_CACHE is not None:
        return NLU_CACHE

    if not app_config.get('NLU_CACHE', False):
        return None

    if app_config['DEBUG']:
        dbg('Initializing DiskCache for NLU', app_config=app_config)
        cache_dir = app_config.get('NLU_DISK_CACHE_DIR', '/tmp')
        ttl = app_config.get('NLU_DISK_CACHE_TTL', DEFAULT_NLU_DISK_CACHE_TTL)
        NLU_CACHE = DiskCache(cache_dir, ttl=ttl)
        return NLU_CACHE

    dbg('Initializing LRUCache for NLU', app_config=app_config)
    nlu_size = app_config.get('NLU_CACHE_SIZE', DEFAULT_NLU_CACHE_SIZE)
    NLU_CACHE = LRUCache(nlu_size)
    return NLU_CACHE

def get_convo_cache(app_config):
    # TODO: in production, replace with something multi-process friendly
    global CONVO_CACHE
    if CONVO_CACHE is not None:
        return CONVO_CACHE
    dbg('Initializing TTLCache for conversations', app_config=app_config)
    cache_size = app_config.get('CONVO_CACHE_SIZE', DEFAULT_CONVO_CACHE_SIZE)
    cache_ttl = app_config.get('CONVO_CACHE_TTL', DEFAULT_CONVO_CACHE_TTL)
    CONVO_CACHE = TTLCache(cache_size, cache_ttl)
    return CONVO_CACHE

def setup_caching(app_config):
    get_nlu_cache(app_config)
    get_convo_cache(app_config)
