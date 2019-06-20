"""Core classes and functions"""
from collections import OrderedDict
import copy
import datetime
import random

from cachetools import LRUCache, TTLCache
from diskcache import Cache
import requests
import usaddress

from chatbot.model import db, Fulfillments
from toolbox import (dbg,
                     st,
                     json,
                     get_class_vars,
                     string_has_format_args,
                     get_string_format_args,
                     import_object,
                     OrderedDictPlus,
                     PrintMixin,
                     JSONMixin,
                     MappingMixin,
                     initializer)

CONVO_CACHE = None
NLU_CACHE = None

DEFAULT_CONVO_CACHE_SIZE = 1000
DEFAULT_CONVO_CACHE_TTL = 3600*48
DEFAULT_NLU_CACHE_SIZE = 1000
DEFAULT_NLU_DISK_CACHE_TTL = 3600*24

class CommonIntents():
    Cancel = 'Cancel'
    Yes = 'Yes'
    No = 'No'
    Help = 'Help'
    NoIntent = 'None' # TODO: This is specific to LUIS
    Repeat = 'Repeat'
    Greeting = 'Greeting'
    Unsure = 'Unsure'
    Why = 'Why'

class ResponseTypes():
    Active = 'Active'
    Deferred = 'Deferred'
    Resumed = 'Resumed'

class Actions():
    CancelIntent = 'CancelIntent'
    ConfirmCancelIntent = 'ConfirmCancelIntent'
    ConfirmSwitchIntent = 'ConfirmSwitchIntent'
    EndConversation = 'EndConversation'
    Help = 'Help'
    NoAction = 'NoAction'
    RemoveIntent = 'RemoveIntent'
    Repeat = 'Repeat'
    RepeatSlot = 'RepeatSlot'
    RepeatSlotAndRemoveIntent = 'RepeatSlotAndRemoveIntent'
    ReplaceSlot = 'ReplaceSlot'
    TriggerIntent = 'TriggerIntent'
    Why = 'Why'

DEFAULT_FOLLOW_UP_ACTIONS = {
    CommonIntents.Yes: Actions.NoAction,
    CommonIntents.No: Actions.RepeatSlot,
}

def assert_valid_intent_name(bot_config, intent_name):
    assert intent_name in bot_config.intent_configs, 'Invalid intent name: %s' % intent_name

def is_common_intent(val):
    types = get_class_vars(CommonIntents)
    if val in types:
        return True
    return False

def get_entity_handler(name):
    return import_object(name)

def get_nlu(bot_config):
    nlu_class = import_object(bot_config.nlu_class)
    return nlu_class(bot_config.nlu_config)

class Action(PrintMixin, JSONMixin, MappingMixin):
    repr_attrs = ['name', 'params']

    def __init__(self, definition):
        if isinstance(definition, dict):
            self.name = definition['name']
            self.params = definition.get('params', {})
        elif isinstance(definition, str):
            self.name = definition
            self.params = {}
        elif definition is None:
            self.name = None
            self.params = None
        else:
            assert False, 'Invalid type for Action: %s' % definition

    def __nonzero__(self):
        if self.name:
            return True
        return False

class ActionMap(JSONMixin, MappingMixin):
    def __init__(self, *args, **kwargs):
        self.update(dict(*args, **kwargs))

    def __setitem__(self, key, value):
        if not isinstance(value, Action):
            value = Action(value)
        self.__dict__[key] = value

class Message(PrintMixin, JSONMixin, MappingMixin):
    repr_attrs = ['value']

    @initializer
    def __init__(self, value=None):
        self.validate(value)
        self.value = self.clean(value)

    def get_message(self):
        return self

    def requires_context(self):
        raise NotImplementedError

    def satisfied_by_context(self, context):
        raise NotImplementedError

    def validate(self, value):
        raise NotImplementedError

    def clean(self, value):
        return value

    @classmethod
    def create(cls, message):
        if isinstance(message, Message):
            return message
        if isinstance(message, str):
            return TextMessage(message)
        if isinstance(message, dict):
            message_type = message['type'].lower()
            if message_type == 'text':
                return TextMessage(message['text'])
            if message_type == 'button':
                return ButtonMessage(message)
            assert False, 'Invalid message_type: %s' % message_type
        assert False, 'Invalid message type: %s' % message

class TextMessage(Message):
    def requires_context(self):
        if string_has_format_args(self.value):
            return True
        return False

    def satisfied_by_context(self, context):
        format_args = get_string_format_args(self.value)
        for format_arg in format_args:
            if format_arg not in context:
                return False
        return True

    def validate(self, value):
        assert isinstance(value, str), 'TextMessage value must be str type'

    def clean(self, value):
        return value.strip()

class ButtonMessage(Message):
    def requires_context(self):
        format_args = get_string_format_args(self.value['label'])
        if format_args:
            return True
        return False

    def satisfied_by_context(self, context):
        format_args = get_string_format_args(self.value['label'])
        for format_arg in format_args:
            if format_arg not in context:
                return False
        return True

    def validate(self, value):
        assert isinstance(value, dict), 'ButtonMessage value must be dict type'
        assert 'label' in value, 'ButtonMessage must have a label'

    def clean(self, value):
        value['label'] = value['label'].strip()
        return value

class MessageOptions(PrintMixin, JSONMixin, MappingMixin):
    repr_attrs = ['messages']

    @initializer
    def __init__(self, messages=None):
        if messages:
            assert all([isinstance(x, Message) for x in messages]), 'Must be message objects: %s' % messages

    def get_message(self):
        if not self.messages:
            return None
        message = random.choice(self.messages)
        return message

    @classmethod
    def create(cls, messages):
        messages = [Message.create(message) for message in messages] if messages else None
        return cls(messages)

class MessageGroup(OrderedDictPlus, JSONMixin):
    """An ordered map of Messages or MessageOptions"""
    def get_next(self):
        return list(self.values())[0]

    def get_next_message(self):
        return self.get_next().get_message()

class MessageMap(JSONMixin, MappingMixin):
    def __init__(self, *args, **kwargs):
        self.update(dict(*args, **kwargs))

    def __setitem__(self, key, value):
        if not isinstance(value, Message):
            value = Message.create(value)
        self.__dict__[key] = value

class MessageOptionsMap(JSONMixin, MappingMixin):
    def __init__(self, *args, **kwargs):
        self.update(dict(*args, **kwargs))

    def __setitem__(self, key, value):
        if not isinstance(value, MessageOptions):
            value = MessageOptions.create(value)
        self.__dict__[key] = value

class Interaction(PrintMixin, JSONMixin, MappingMixin):
    @initializer
    def __init__(self, name, messages=None, action=None):
        if not isinstance(messages, MessageOptions):
            self.messages = MessageOptions(messages)
        if action and not isinstance(action, Action):
            self.action = Action(action)

    def get_message(self):
        return self.messages.get_message()

    @classmethod
    def infer_type_from_dict(cls, interaction):
        assert isinstance(interaction, dict), 'Invalid interaction type: %s' % interaction
        # TODO: is there a better way to do this? We can tell its a question because
        # its expecting an answer, but we can't tell if its a Slot or Question
        if 'intent_actions' in interaction or 'entity_actions' in interaction:
            return 'question'
        return 'interaction'

    @classmethod
    def from_dict(cls, interaction_dict):
        interaction_type = interaction_dict['type'].lower()
        if interaction_type == 'interaction':
            return Interaction(interaction_dict['name'],
                               messages=MessageOptions.create(interaction_dict['messages']),
                               action=interaction_dict.get('action', None))
        if interaction_type == 'question':
            return Question(interaction_dict['name'],
                            MessageOptions.create(interaction_dict['messages']),
                            intent_actions=ActionMap(interaction_dict.get('intent_actions', {})),
                            entity_actions=ActionMap(interaction_dict.get('entity_actions', {})),
                            help=interaction_dict.get('help', None),
                            why=interaction_dict.get('why', None))
        if interaction_type == 'slot':
            return Slot(interaction_dict['name'],
                        MessageOptions.create(interaction_dict['messages']),
                        entity_handler_name=interaction_dict.get('entity_handler_name', None),
                        follow_up=interaction_dict.get('follow_up', None),
                        help=interaction_dict.get('help', None),
                        why=interaction_dict.get('why', None))
        assert False, 'Invalid interaction type: %s' % interaction_type

    @classmethod
    def create(cls, name, interaction):
        if isinstance(interaction, Interaction):
            return interaction
        if isinstance(interaction, str):
            return Interaction(name, messages=MessageOptions.create([interaction]))
        if isinstance(interaction, list):
            return Interaction(name, messages=MessageOptions.create(interaction))
        if isinstance(interaction, dict):
            interaction['type'] = interaction.get('type', cls.infer_type_from_dict(interaction))
            interaction['name'] = interaction.get('name', name)
            interaction['messages'] = interaction.get('messages', None)
            return cls.from_dict(interaction)
        assert False, 'Invalid interaction type: %s' % interaction

class InteractionMap(JSONMixin, MappingMixin):
    def __init__(self, *args, **kwargs):
        self.update(dict(*args, **kwargs))

    def __setitem__(self, key, value):
        if not isinstance(value, Interaction):
            value = Interaction.create(key, value)
        self.__dict__[key] = value

def is_valid_response_type(val):
    types = get_class_vars(ResponseTypes)
    if val in types:
        return True
    return False

class ResponseMap(MessageOptionsMap):
    def __setitem__(self, key, value):
        assert is_valid_response_type(key), 'Invalid response type: %s' % key
        super(ResponseMap, self).__setitem__(key, value)

    def get_response(self, rtype):
        if rtype not in self:
            return ''
        return self[rtype].get_message()

class Question(Interaction):
    repr_attrs = ['name']

    @initializer
    def __init__(self, name, messages, action=None, intent_actions=None, entity_actions=None, help=None, why=None):
        if not isinstance(messages, MessageOptions):
            self.messages = MessageOptions.create(messages)
        if action and not isinstance(action, Action):
            self.action = Action(action)
        if intent_actions:
            assert isinstance(intent_actions, ActionMap),\
                'Invalid type for intent_actions, must be dict: %s' % type(intent_actions)
        if entity_actions:
            assert isinstance(entity_actions, ActionMap),\
                'Invalid type for entity_actions, must be dict: %s' % type(entity_actions)
        if not isinstance(help, MessageOptions):
            self.help = MessageOptions.create(help)
        if not isinstance(why, MessageOptions):
            self.why = MessageOptions.create(why)

    def get_help(self):
        self.help.get_message()

    def get_why(self):
        self.why.get_message()

    def get_intent_actions(self):
        return self.intent_actions

    def get_entity_actions(self):
        return self.entity_actions

class Slot(Question):
    repr_attrs = ['name']

    @classmethod
    def from_dict(cls, slot_name, slot_info, entity_handlers=None):
        assert isinstance(slot_info, dict), 'Invalid type for slot_info, must be dict: %s' % type(slot_info)
        messages = slot_info['messages']

        follow_up_info = slot_info.get('follow_up', {})
        if not follow_up_info:
            follow_up_info = {}
        follow_up = FollowUp.from_dict(slot_name, follow_up_info)

        entity_handler_name = slot_info.get('entity_handler', None)
        if (not entity_handler_name) and slot_name in entity_handlers:
            entity_handler_name = entity_handlers[slot_name]

        autofill = slot_info.get('autofill', None)
        help = slot_info.get('help', None)
        why = slot_info.get('why', None)

        return cls(slot_name, messages, entity_handler_name=entity_handler_name, follow_up=follow_up,
                   autofill=autofill, help=help, why=why)

    def __init__(self, name, messages, entity_handler_name=None, follow_up=None, autofill=None, help=None, why=None):
        # TODO: should slot be filled by an action? Allow overriding intent/entity actions for slots?
        entity_actions = ActionMap({name: Actions.NoAction})
        super(Slot, self).__init__(name, messages, entity_actions=entity_actions, help=help, why=why)
        if entity_handler_name:
            assert isinstance(entity_handler_name, str), 'Invalid entity handler: %s' % entity_handler_name
        self.entity_handler_name = entity_handler_name
        self.follow_up = follow_up
        if follow_up:
            assert isinstance(follow_up, FollowUp)
        self.autofill = autofill

    def get_follow_up_messages(self):
        if not self.follow_up:
            return None
        return self.follow_up.get_message()

    def get_follow_up_intent_actions(self):
        if not self.follow_up:
            return None
        return self.follow_up.get_intent_actions()

    def copy(self):
        new_slot = Slot(self.name,
                        copy.deepcopy(self.messages),
                        entity_handler_name=self.entity_handler_name,
                        follow_up=copy.deepcopy(self.follow_up),
                        autofill=self.autofill,
                        help=self.help,
                        why=self.why)
        return new_slot

class SlotResults(OrderedDictPlus, JSONMixin):
    pass

class SlotResult(JSONMixin, MappingMixin):
    @initializer
    def __init__(self, name, value):
        pass

    def copy(self):
        new = SlotResult(self.name, None)
        if isinstance(self.value, EntityResult):
            new.value = self.value.copy()
        else:
            new.value = copy.deepcopy(self.value)
        return new

class FollowUp(Question):
    repr_attrs = ['name']

    @classmethod
    def from_dict(cls, slot_name, follow_up_info):
        assert isinstance(follow_up_info, dict),\
            'Invalid type for follow up info, must be dict: %s' % type(follow_up_info)
        follow_up = None
        if follow_up_info:
            follow_up_name = '%s_follow_up' % slot_name
            # If they provide the slot answer, process it and continue
            entity_actions = {slot_name: Actions.ReplaceSlot}
            follow_up = cls(follow_up_name, follow_up_info['messages'],
                            intent_actions=ActionMap(follow_up_info.get('intent_actions', DEFAULT_FOLLOW_UP_ACTIONS)),
                            entity_actions=ActionMap(entity_actions),
                            help=follow_up_info.get('help', None),
                            why=follow_up_info.get('why', None))
        return follow_up

class Intent(PrintMixin, JSONMixin, MappingMixin):
    repr_attrs = ['name']

    @initializer
    def __init__(self, name, responses=None, slots=None, entity_handlers=None, fulfillment=None,
                 is_repeatable=False, is_preemptive=False, is_answer=False, is_greeting=False, is_smalltalk=None,
                 help=None, why=None, api_id=None):
        if self.is_app_intent:
            assert not self.is_preemptive, 'Preemptive bot intents are not currently supported'

        if responses:
            if not isinstance(responses, dict):
                responses = {ResponseTypes.Active:responses}
            self.responses = ResponseMap()
            self.responses.update(responses)

        if help:
            self.help = MessageOptions.create(help)

        if why:
            self.why = MessageOptions.create(why)

        self.slots = MessageGroup()
        if slots:
            for slot_name, slot_info in slots.items():
                self.slots[slot_name] = Slot.from_dict(slot_name, slot_info, entity_handlers=entity_handlers)

    @property
    def is_app_intent(self):
        return not (self.is_common_intent or self.is_smalltalk)

    @property
    def is_common_intent(self):
        return is_common_intent(self.name)

    def get_slot_results_container(self):
        '''Creates a container to store slot results'''
        results = SlotResults()
        for slot_name in self.slots.keys():
            results[slot_name] = SlotResult(slot_name, None)
        return results

    def get_fulfillment_data(self, convo, tx, slot_data):
        slot_value_data = {k:slot_data[k].value for k in self.slots}
        return dict(conversation_id=convo.id,
                    transaction_id=tx.id,
                    intent_name=self.name,
                    context=convo.context,
                    slot_data=slot_value_data)

    def fulfill(self, convo, tx, slot_data):
        if not self.fulfillment:
            dbg('Nothing to fulfill for intent %s' % self.name)
            return

        dbg('Handling fulfillment for intent %s: %s' % (self.name, self.fulfillment))
        url = self.fulfillment['url']
        status_code = None

        fulfillment_data = self.get_fulfillment_data(convo, tx, slot_data)

        try:
            resp = requests.post(url, json=fulfillment_data)
            status_code = resp.status_code
            content = resp.content
            resp.raise_for_status()
            return FulfillmentResponse('%s_fulfillment' % self.name, **resp.json())
        except Exception as e:
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
        return self.help.get_message()

    def get_why(self):
        if not self.why:
            return None
        return self.why.get_message()

class IntentResult(PrintMixin, JSONMixin, MappingMixin):
    repr_attrs = ['name', 'score']

    @initializer
    def __init__(self, name, score):
        pass

class Entity(PrintMixin, MappingMixin):
    repr_attrs = ['name', 'type']

    @initializer
    def __init__(self, name, type, api_id=None):
        pass

class EntityResult(PrintMixin, JSONMixin, MappingMixin):
    repr_attrs = ['name', 'slot_name', 'type', 'value', 'score']

    @initializer
    def __init__(self, name, type, start_index=None, end_index=None, score=None, value=None, from_context=False):
        self.slot_name = type

    def copy(self):
        new_entity = EntityResult(self.name,
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
            entities.append(EntityResult(name=entity['entity'],
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
        query_entity = EntityResult(name='query',
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
                entity = EntityResult(name=address_part_name,
                                      type=address_part_name,
                                      start_index=None,
                                      end_index=None,
                                      score=None,
                                      value=value)
                address_entities.append(entity)

        address_value = ' '.join(address_parts)
        address_entity = EntityResult(name='address',
                                      type='address',
                                      start_index=None,
                                      end_index=None,
                                      score=None,
                                      value=address_value)
        entities.insert(0, address_entity)

        street_address_value = ' '.join(street_address_parts)
        street_address_entity = EntityResult(name='street_address',
                                             type='street_address',
                                             start_index=None,
                                             end_index=None,
                                             score=None,
                                             value=street_address_value)
        entities.insert(1, street_address_entity)

        for entity in address_entities:
            entities.insert(1, entity)

        return entities

class FulfillmentResponse(PrintMixin, JSONMixin, MappingMixin):
    repr_attrs = ['status', 'response']

    @initializer
    def __init__(self, name, status=None, status_reason=None, interaction=None, action=None):
        assert status and isinstance(status, str), 'Invalid status: %s' % status

        if action and not isinstance(action, Action):
            self.action = Action(action)

        if interaction:
            if isinstance(interaction, str):
                self.interaction = Interaction(name, [interaction])
            elif isinstance(interaction, dict):
                interaction['name'] = interaction.get('name', name)
                self.interaction = Interaction.from_dict(interaction)
            else:
                assert False, 'Invalid interaction: %s' % interaction

    def success(self):
        if self.status.lower() == 'success':
            return True
        return False

class IntentPrediction(PrintMixin, JSONMixin, MappingMixin):
    repr_attrs = ['query', 'intent_results', 'entity_results']

    def __init__(self, query, intent_results, entity_results=None):
        self.query = query
        assert intent_results
        self.intent_results = sorted(intent_results, key=lambda x: x.score, reverse=True)
        self.top_intent_result = self.intent_results[0]
        self.entity_results = entity_results or []

    def filter_intent_results(self, score):
        return [x for x in self.intent_results if ((x.score is None or x.score > score) and x.name != 'None')]

    def filter_entity_results(self, score):
        return [x for x in self.entity_results if (x.score is None or x.score > score)]

    def add_entity_results_from_context(self, context):
        for k,v in context.items():
            self.entity_results.append(EntityResult(name=k, type=k, value=v, from_context=True))

    def get_valid(self, intent_threshold=0, entity_threshold=0):
        valid_intent_results = self.filter_intent_results(intent_threshold)
        valid_entity_results = self.filter_entity_results(entity_threshold)
        return valid_intent_results, valid_entity_results

def get_triggered_intent_result(bot_config, intent_name):
    assert_valid_intent_name(bot_config, intent_name)
    score = 1
    intent_result = IntentResult(intent_name, score)
    return intent_result

class TriggeredIntentPrediction(IntentPrediction):
    def __init__(self, bot_config, intent_name):
        intents = [get_triggered_intent_result(bot_config, intent_name)]
        super(TriggeredIntentPrediction, self).__init__(None, intents)

class Application(PrintMixin, MappingMixin):
    repr_attrs = ['id', 'name', 'version']

    @initializer
    def __init__(self, id, name, version, description=None, created_at=None,
                 production_endpoint=None, staging_endpoint=None):
        if created_at:
            assert isinstance(created_at, datetime.date), 'Invalid created_at date object: %s' % created_at

class ApplicationVersion(PrintMixin, MappingMixin):
    repr_attrs = ['version', 'created_at', 'updated_at']

    @initializer
    def __init__(self, version, created_at=None, updated_at=None):
        if created_at:
            assert isinstance(created_at, datetime.date), 'Invalid created_at date object: %s' % created_at
        if updated_at:
            assert isinstance(updated_at, datetime.date), 'Invalid updated_at date object: %s' % updated_at

class ApplicationTrainingStatus(PrintMixin, MappingMixin):
    repr_attrs = ['status', 'model_count', 'models_trained']

    TRAINED = 'Trained'
    IN_PROGRESS = 'In Progress'

    @initializer
    def __init__(self, status, model_count, models_trained):
        pass

class ApplicationTrainingResult(ApplicationTrainingStatus):
    repr_attrs = ['status']

    @initializer
    def __init__(self, status):
        pass

class ApplicationPublishResult(PrintMixin, MappingMixin):
    repr_attrs = ['version', 'environment', 'published_at']

    STAGING = 'Staging'
    PRODUCTION = 'Production'

    @initializer
    def __init__(self, version, environment, region=None, published_at=None, endpoint=None):
        pass

class Utterance(PrintMixin, MappingMixin):
    repr_attrs = ['name', 'intent_name']

    @initializer
    def __init__(self, name, intent_name=None, intent_api_id=None, api_id=None):
        pass

class NLU():
    @initializer
    def __init__(self, config):
        pass

    def get_raw_prediction(self, query, staging=True):
        raise NotImplementedError

    def get_intent_results_from_raw_response(self, raw):
        raise NotImplementedError

    def get_entity_results_from_raw_response(self, raw):
        raise NotImplementedError

    def process_query(self, query, last_tx=None):
        raw = self.get_raw_prediction(query)
        intent_results = self.get_intent_results_from_raw_response(raw)
        entity_results = self.get_entity_results_from_raw_response(raw)

        entity_handler_name = 'EntityHandler'
        if last_tx and last_tx.question and getattr(last_tx.question, 'entity_handler_name', None):
            entity_handler_name = last_tx.question.entity_handler_name or entity_handler_name
        entity_handler = get_entity_handler(entity_handler_name)

        entity_results = entity_handler().process(query, entity_results)
        return IntentPrediction(query, intent_results, entity_results=entity_results)

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

    def get_application_training_status(self, app_version=None):
        raise NotImplementedError

    def train(self, asynchronous=True, app_version=None):
        raise NotImplementedError

    def publish(self, is_staging=True, region=None, app_version=None):
        raise NotImplementedError

    def get_entity(self, id, app_version=None):
        raise NotImplementedError

    def get_entities(self, app_version=None):
        raise NotImplementedError

    def get_intent(self, id, app_version=None):
        raise NotImplementedError

    def get_intents(self, app_version=None):
        raise NotImplementedError

    def add_intent(self, name, app_version=None):
        raise NotImplementedError

    def get_utterances(self, intent, app_version=None):
        raise NotImplementedError

    def add_utterance(self, intent, utterance, app_version=None):
        raise NotImplementedError

#---- Cache Stuff

class DiskCache():
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
        dbg('Initializing DiskCache for NLU', config=app_config)
        cache_dir = app_config.get('NLU_DISK_CACHE_DIR', '/tmp')
        ttl = app_config.get('NLU_DISK_CACHE_TTL', DEFAULT_NLU_DISK_CACHE_TTL)
        NLU_CACHE = DiskCache(cache_dir, ttl=ttl)
        return NLU_CACHE

    dbg('Initializing LRUCache for NLU', config=app_config)
    nlu_size = app_config.get('NLU_CACHE_SIZE', DEFAULT_NLU_CACHE_SIZE)
    NLU_CACHE = LRUCache(nlu_size)
    return NLU_CACHE

def get_convo_cache(app_config):
    # TODO: in production, replace with something multi-process friendly
    global CONVO_CACHE
    if CONVO_CACHE is not None:
        return CONVO_CACHE
    dbg('Initializing TTLCache for conversations', config=app_config)
    cache_size = app_config.get('CONVO_CACHE_SIZE', DEFAULT_CONVO_CACHE_SIZE)
    cache_ttl = app_config.get('CONVO_CACHE_TTL', DEFAULT_CONVO_CACHE_TTL)
    CONVO_CACHE = TTLCache(cache_size, cache_ttl)
    return CONVO_CACHE

def setup_caching(app_config):
    get_nlu_cache(app_config)
    get_convo_cache(app_config)
