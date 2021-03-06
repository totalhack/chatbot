"""Config checking and generation"""
from collections import OrderedDict
import copy
import glob
import os
from urllib.parse import urlparse

from marshmallow import Schema, fields, ValidationError

from chatbot.core import (Actions,
                          ResponseTypes,
                          Intent,
                          InteractionMap)
from toolbox import (dbg,
                     error,
                     json,
                     st,
                     initializer,
                     dictmerge,
                     get_class_vars,
                     JSONMixin,
                     MappingMixin)

BOT_CONFIGS = {}
COMMON_INTENT_CONFIGS = {}
COMMON_INTENT_FILE = os.path.join(os.path.dirname(__file__), 'nlu/common_intents.json')
SMALLTALK_INTENT_FILE = os.path.join(os.path.dirname(__file__), 'nlu/smalltalk_intents.json')
COMMON_INTERACTIONS = {}
COMMON_INTERACTIONS_FILE = os.path.join(os.path.dirname(__file__), 'nlu/common_interactions.json')

DEFAULT_NLU_CLASS = 'chatbot.nlu.luis.LUISNLU'
DEFAULT_NEW_INTENT_LIMIT = 2 # TODO: not all NLUs will support returning multiple intents
DEFAULT_INTENT_FILTER_THRESHOLD = 0.50
DEFAULT_ENTITY_FILTER_THRESHOLD = 0.50
DEFAULT_MAX_QUESTION_ATTEMPTS = 2
DEFAULT_MAX_CONSECUTIVE_INTERACTION_ATTEMPTS = 2
DEFAULT_MAX_CONSECUTIVE_REPEAT_ATTEMPTS = 2

COMMON_ENTITY_HANDLERS = {
    'address': 'AddressEntityHandler',
    'street_address': 'AddressEntityHandler',
}

def is_main_config_file(filename):
    if filename == os.environ['CHATBOT_CONFIG']:
        return True
    return False

def parse_schema_file(filename, schema):
    """Parse a marshmallow schema file"""
    f = open(filename)
    raw = f.read()
    f.close()
    try:
        # This does the schema check, but has a bug in object_pairs_hook so order is not preserved
        result = schema.loads(raw)
        result = json.loads(raw, object_pairs_hook=OrderedDict)
    except ValidationError as e:
        error('Schema Validation Error')
        print(json.dumps(str(e), indent=2))
        raise
    return result

def get_all_bot_configs():
    return BOT_CONFIGS

def get_bot_config(bot):
    return BOT_CONFIGS[bot]

def check_bot_intent_configs(bot_intent_configs):
    """Additional checks to enforce supported behavior"""
    for intent_name, intent_config in bot_intent_configs.items():
        assert 'is_preemptive' not in intent_config,\
            'Preemptive bot intents are not currently supported: %s' % intent_name

def convert_to_intent_objects(intent_configs, entity_handlers):
    """Convert intent configs dicts to Intent objects"""
    for name, intent_config in intent_configs.items():
        kwargs = copy.deepcopy(intent_config)
        kwargs['entity_handlers'] = kwargs.get('entity_handlers', entity_handlers)
        if 'name' in kwargs:
            del kwargs['name']
        intent = Intent(name, **kwargs)
        intent_configs[name] = intent

def clear_utterances(intent_configs):
    """Remove utterance section from intent configs"""
    for intent_config in intent_configs.values():
        if 'utterances' in intent_config:
            del intent_config['utterances']

def update_intents(intent_configs, update_dict):
    """Perform a dict update on each intent config"""
    for intent_config in intent_configs.values():
        intent_config.update(copy.deepcopy(update_dict))

class BotConfig(JSONMixin, MappingMixin):
    """Holds all configuration information for a bot"""
    @initializer
    def __init__(self, name, intent_configs, entity_handlers, common_interactions, nlu_class, nlu_config,
                 intent_filter_threshold=DEFAULT_INTENT_FILTER_THRESHOLD,
                 entity_filter_threshold=DEFAULT_ENTITY_FILTER_THRESHOLD,
                 new_intent_limit=DEFAULT_NEW_INTENT_LIMIT,
                 max_question_attempts=DEFAULT_MAX_QUESTION_ATTEMPTS,
                 max_consecutive_interaction_attempts=DEFAULT_MAX_CONSECUTIVE_INTERACTION_ATTEMPTS,
                 max_consecutive_repeat_attempts=DEFAULT_MAX_CONSECUTIVE_REPEAT_ATTEMPTS,
                 smalltalk=False, tests=None):
        pass

    def merge_dict(self, bot_config_dict):
        """Merge this config with another dict-based config"""
        result = dictmerge(copy.deepcopy(self), bot_config_dict, overwrite=True)
        # The above is sufficient if the new dict only has updates to existing objects
        # or updates to built-in types. If it has a new value for a complex object
        # that isn't present in the current BotConfig, it would just show up as a dict
        # in the result. As a workaround, since this is currently really only used in
        # testing, ensure intent objects are properly formed by forcing to json
        # and then rebuilding each intent_config object.
        for intent_name, intent_config in result.intent_configs.items():
            result.intent_configs[intent_name] = intent_config.to_dict()
        convert_to_intent_objects(result.intent_configs, result.entity_handlers)
        return result

class BotConfigLoader(JSONMixin):
    """Helpers for loading bot configs"""
    def __init__(self, app_config):
        self.app_config = app_config
        self.configs = {}

    def load_bot_configs(self, load_tests=False, load_utterances=False):
        """Load bot configs based on app config settings"""
        load_common_intent_configs()
        load_common_interactions()

        if self.app_config.get('BOT_CONFIG_DIRECTORY', None):
            self.load_bot_configs_from_directory(load_tests=load_tests, load_utterances=load_utterances)
        else:
            assert False, 'Must specify BOT_CONFIG_DIRECTORY in main config'

        if self.app_config['DEBUG'] and self.app_config.get('test_base_url', None):
            print('Overriding fulfillment base URLs with TEST_BASE_URL: %s' % self.app_config['TEST_BASE_URL'])
            for bot_config in self.configs.values():
                for intent in bot_config.intent_configs.values():
                    if intent.fulfillment and intent.fulfillment.get('url', None):
                        parsed = urlparse(intent.fulfillment['url'])
                        url = self.app_config['TEST_BASE_URL'] + parsed.path
                        if parsed.query:
                            url = url + '?' + parsed.query
                        intent.fulfillment['url'] = url

    def load_bot_configs_from_directory(self, load_tests=False, load_utterances=False):
        """Load bot configs from app config directory"""
        directory = self.app_config['BOT_CONFIG_DIRECTORY'].rstrip('/')
        files = glob.glob("%s/*.json" % directory)
        bot_file_schema = BotConfigFileSchema()
        intent_file_schema = IntentConfigFileSchema()
        smalltalk_intent_configs = None

        count = 0
        for filename in files:
            bot_config = parse_schema_file(filename, bot_file_schema)
            bot_name = os.path.basename(filename).split('.json')[0]

            entity_handlers = copy.deepcopy(COMMON_ENTITY_HANDLERS)
            bot_entity_handlers = bot_config.get('entity_handlers', {})
            entity_handlers.update(bot_entity_handlers)

            common_interactions = copy.deepcopy(COMMON_INTERACTIONS)
            bot_common_interactions = bot_config.get('common_interactions', {})
            common_interactions.update(bot_common_interactions)
            common_interactions = InteractionMap(common_interactions)

            intent_configs = copy.deepcopy(COMMON_INTENT_CONFIGS)
            bot_intent_configs = bot_config.get('intent_configs', {})
            check_bot_intent_configs(bot_intent_configs)
            intent_configs.update(bot_intent_configs)

            # We always add the smalltalk configs whether it is enabled or not so the bot
            # can recognize which intents are smalltalk even if it doesnt support them
            if not smalltalk_intent_configs:
                result = parse_schema_file(SMALLTALK_INTENT_FILE, intent_file_schema)
                smalltalk_intent_configs = result['intent_configs']
                update = {'is_smalltalk': True, 'is_repeatable': True, 'is_preemptive': True}
                update_intents(smalltalk_intent_configs, update)
            intent_configs.update(smalltalk_intent_configs)

            if not load_utterances:
                clear_utterances(intent_configs)

            convert_to_intent_objects(intent_configs, entity_handlers)

            self.configs[bot_name] = BotConfig(
                bot_name,
                intent_configs,
                entity_handlers,
                common_interactions,
                bot_config.get('nlu_class', self.app_config.get('NLU_CLASS', DEFAULT_NLU_CLASS)),
                bot_config.get('nlu_config', self.app_config['NLU_CONFIG']),
                new_intent_limit=bot_config.get('new_intent_limit',
                                                self.app_config.get('NEW_INTENT_LIMIT', DEFAULT_NEW_INTENT_LIMIT)),
                intent_filter_threshold=bot_config.get('intent_filter_threshold', DEFAULT_INTENT_FILTER_THRESHOLD),
                entity_filter_threshold=bot_config.get('entity_filter_threshold', DEFAULT_ENTITY_FILTER_THRESHOLD),
                max_question_attempts=bot_config.get('max_question_attempts', DEFAULT_MAX_QUESTION_ATTEMPTS),
                max_consecutive_interaction_attempts=bot_config.get('max_consecutive_interaction_attempts',
                                                                    DEFAULT_MAX_CONSECUTIVE_INTERACTION_ATTEMPTS),
                max_consecutive_repeat_attempts=bot_config.get('max_consecutive_repeat_attempts',
                                                               DEFAULT_MAX_CONSECUTIVE_REPEAT_ATTEMPTS),
                smalltalk=bot_config.get('smalltalk', False),
                tests=bot_config.get('tests', {}) if load_tests else {}
            )

            count += 1

        dbg('Loaded %d bot configs' % count)

def load_common_intent_configs():
    """Load common intent configs from JSON file"""
    schema = IntentConfigFileSchema()
    result = parse_schema_file(COMMON_INTENT_FILE, schema)
    COMMON_INTENT_CONFIGS.update(result['intent_configs'])

def load_common_interactions():
    """Load common interaction configs from JSON file"""
    schema = InteractionFileSchema()
    result = parse_schema_file(COMMON_INTERACTIONS_FILE, schema)
    COMMON_INTERACTIONS.update(result['interactions'])

def load_bot_configs(app_config, load_tests=False):
    """Helper for loading bot configs and updating global reference"""
    if not BOT_CONFIGS:
        loader = BotConfigLoader(app_config)
        loader.load_bot_configs(load_tests=load_tests)
        BOT_CONFIGS.update(loader.configs)

#-------- Schema Validation

def is_int_greater_or_equal_to_one(val):
    if not isinstance(val, int):
        raise ValidationError('Must be an integer >= 1: %s' % val)
    if val >= 1:
        return True
    raise ValidationError('Must be an integer >= 1: %s' % val)

def is_zero_to_one(val):
    if val is None:
        raise ValidationError('Must be a number between 0 and 1: %s' % val)
    val = float(val)
    if 0 <= val <= 1:
        return True
    raise ValidationError('Must be a number between 0 and 1: %s' % val)

def is_valid_response_type(val):
    types = get_class_vars(ResponseTypes)
    if val in types:
        return True
    raise ValidationError('Invalid response type: %s' % val)

def is_valid_response(val):
    if isinstance(val, list):
        if not all([isinstance(x, str) for x in val]):
            raise ValidationError('Invalid Responses format: %s' % val)
    elif isinstance(val, dict):
        schema = ResponsesSchema()
        schema.load(val)
    else:
        raise ValidationError('Invalid Responses format: %s' % val)

def is_valid_action(val):
    actions = get_class_vars(Actions)
    if isinstance(val, dict):
        schema = ActionSchema()
        schema.load(val)
        if val['name'] in actions:
            return True
    else:
        if val in actions:
            return True
    raise ValidationError('Invalid action: %s' % val)

def is_valid_message(val):
    if isinstance(val, str):
        return

    if isinstance(val, dict):
        message_type = (val.get('type', None) or '').lower()
        if not message_type:
            raise ValidationError('Message does not specify type: %s' % val)
        if message_type == 'text':
            schema = TextMessageSchema()
        elif message_type == 'button':
            schema = ButtonMessageSchema()
        else:
            raise ValidationError('Invalid Message type: %s' % message_type)
        schema.load(val)
    else:
        raise ValidationError('Invalid Message format: %s' % val)

def is_valid_interaction(val):
    if isinstance(val, list):
        if not all([isinstance(x, str) for x in val]):
            raise ValidationError('Invalid Interaction format: %s' % val)
    elif isinstance(val, dict):
        schema = InteractionSchema()
        schema.load(val)
    else:
        raise ValidationError('Invalid Interaction format: %s' % val)

class BaseSchema(Schema):
    class Meta:
        # Use the json module as imported from utils
        json_module = json

class ActionSchema(BaseSchema):
    name = fields.Str(required=True)
    params = fields.Dict(keys=fields.Str(), values=fields.Field())

class ActionField(fields.Field):
    def _validate(self, value):
        is_valid_action(value)
        super(ActionField, self)._validate(value)

class TextMessageSchema(BaseSchema):
    type = fields.Str(required=True)
    text = fields.Str(required=True)

class ButtonMessageSchema(BaseSchema):
    type = fields.Str(required=True)
    label = fields.Str(required=True)

class MessageField(fields.Field):
    def _validate(self, value):
        is_valid_message(value)
        super(MessageField, self)._validate(value)

class InteractionSchema(BaseSchema):
    messages = fields.List(MessageField())
    help = fields.List(MessageField())
    why = fields.List(MessageField())
    entity_actions = fields.Dict(keys=fields.Str(), values=ActionField())
    # TODO: validate it is a valid intent
    intent_actions = fields.Dict(keys=fields.Str(), values=ActionField())
    action = ActionField()

class InteractionField(fields.Field):
    def _validate(self, value):
        is_valid_interaction(value)
        super(InteractionField, self)._validate(value)

class ResponsesSchema(BaseSchema):
    Active = fields.List(MessageField())
    Deferred = fields.List(MessageField())
    Resumed = fields.List(MessageField())

class ResponsesField(fields.Field):
    def _validate(self, value):
        is_valid_response(value)
        super(ResponsesField, self)._validate(value)

class SlotFollowUpSchema(BaseSchema):
    messages = fields.List(MessageField(), required=True)
    help = fields.List(MessageField())
    why = fields.List(MessageField())
    entity_actions = fields.Dict(keys=fields.Str(), values=ActionField())
    # TODO: validate it is a valid intent
    intent_actions = fields.Dict(keys=fields.Str(), values=ActionField())
    action = ActionField()

class IntentSlotSchema(BaseSchema):
    messages = fields.List(MessageField())
    help = fields.List(MessageField())
    why = fields.List(MessageField())
    follow_up = fields.Nested(SlotFollowUpSchema)
    entity_handler = fields.Str()
    autofill = fields.Boolean()

class IntentFulfillmentSchema(BaseSchema):
    url = fields.Url(required=True)

class IntentConfigSchema(BaseSchema):
    responses = ResponsesField()
    utterances = fields.List(fields.Str())
    slots = fields.Dict(keys=fields.Str(), values=fields.Nested(IntentSlotSchema))
    fulfillment = fields.Nested(IntentFulfillmentSchema)
    help = fields.List(MessageField())
    why = fields.List(MessageField())
    is_repeatable = fields.Boolean()
    is_preemptive = fields.Boolean()
    is_answer = fields.Boolean()
    is_greeting = fields.Boolean()
    is_smalltalk = fields.Boolean()

class BotConfigFileSchema(BaseSchema):
    intent_filter_threshold = fields.Float(validate=is_zero_to_one)
    entity_filter_threshold = fields.Float(validate=is_zero_to_one)
    new_intent_limit = fields.Integer(validate=is_int_greater_or_equal_to_one)
    max_question_attempts = fields.Integer()
    max_consecutive_interaction_attempts = fields.Integer()
    max_consecutive_repeat_attempts = fields.Integer()
    smalltalk = fields.Boolean()
    nlu_class = fields.Str()
    nlu_config = fields.Dict(keys=fields.Str(), values=fields.Str())
    common_interactions = fields.Dict(keys=fields.Str(), values=InteractionField(), required=True)
    entity_handlers = fields.Dict(keys=fields.Str(), values=fields.Str())
    intent_configs = fields.Dict(keys=fields.Str(), values=fields.Nested(IntentConfigSchema), required=True)
    # TODO: validate the list items
    tests = fields.Dict(keys=fields.Str(), values=fields.List(fields.List(fields.Field(allow_none=True))))

class IntentConfigFileSchema(BaseSchema):
    intent_configs = fields.Dict(keys=fields.Str(), values=fields.Nested(IntentConfigSchema), required=True)

class InteractionFileSchema(BaseSchema):
    interactions = fields.Dict(keys=fields.Str(), values=InteractionField, required=True)
