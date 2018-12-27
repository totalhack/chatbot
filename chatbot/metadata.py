from collections import OrderedDict
import copy
import glob
import json
import os

from marshmallow import Schema, fields, ValidationError

from chatbot.core import *
from chatbot.utils import *

BOT_METADATA = {}

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
        'intent_actions': {CommonIntents.ConfirmYes: Actions.NoAction,
                           CommonIntents.ConfirmNo: Actions.EndConversation}
    },

    'intent_aborted': {
        'prompts': ["I'm sorry, I'm unable to help you at this time"],
        'action': Actions.EndConversation,
    },

    'intent_canceled': {
        'prompts': [
            "Are you sure you want to cancel the current intent?",
        ],
        'intent_actions': {CommonIntents.ConfirmYes: Actions.CancelIntent,
                           CommonIntents.ConfirmNo: Actions.NoAction}
    },

    'goodbye': [
        "Thanks. Have a nice day!"
    ]
}

INTENT_METADATA = {
    CommonIntents.Cancel: {
        'repeatable': True,
        'preemptive': True,
        'is_answer': False
    },

    CommonIntents.ConfirmNo: {
        'repeatable': True,
        'preemptive': True,
        'is_answer': True
    },

    CommonIntents.ConfirmYes: {
        'repeatable': True,
        'preemptive': True,
        'is_answer': True
    },

    CommonIntents.Help: {
        'repeatable': True,
        'preemptive': True,
        'is_answer': False
    },

    CommonIntents.Repeat: {
        'repeatable': True,
        'preemptive': True,
        'is_answer': False
    },

    CommonIntents.Welcome: {
        'responses': {
            ResponseTypes.Active: [
                'Hi, how are you?',
            ],
        },
        'is_greeting': True,
    },
}

ENTITY_HANDLERS = {
    'address': 'AddressEntityHandler',
    'street_address': 'AddressEntityHandler',
}

def is_main_config_file(filename):
    if filename == os.environ['CHATBOT_CONFIG']:
        return True
    return False

def get_all_bot_metadata():
    return BOT_METADATA

def get_bot_metadata(bot):
    return BOT_METADATA[bot]

def load_bot_metadata(app_config, load_tests=False):
    if app_config.get('BOT_METADATA_DIRECTORY', None):
        load_bot_metadata_from_directory(app_config, load_tests=load_tests)
    else:
        assert False, 'Must specify BOT_METADATA_DIRECTORY in main config'

def load_bot_metadata_from_directory(app_config, load_tests=False):
    directory = app_config['BOT_METADATA_DIRECTORY'].rstrip('/')
    files = glob.glob("%s/*.json" % directory)
    schema = BotMetadataSchema()

    count = 0
    for filename in files:
        f = open(filename)
        raw = f.read()
        f.close()

        try:
            bot_metadata = schema.loads(raw, object_pairs_hook=OrderedDict)
        except ValidationError, e:
            error('Metadata Validation Error')
            print json.dumps(e.message, indent=2)
            raise

        bot_name = os.path.basename(filename).split('.json')[0]

        bot_intent_metadata = copy.deepcopy(INTENT_METADATA)
        bot_intent_metadata.update(bot_metadata.get('INTENT_METADATA', {}))
        bot_entity_handlers = copy.deepcopy(ENTITY_HANDLERS)
        bot_entity_handlers.update(bot_metadata.get('ENTITY_HANDLERS', {}))
        bot_common_messages = copy.deepcopy(COMMON_MESSAGES)
        bot_common_messages.update(bot_metadata.get('COMMON_MESSAGES', {}))

        BOT_METADATA[bot_name] = dict(
            INTENT_METADATA=bot_intent_metadata,
            ENTITY_HANDLERS=bot_entity_handlers,
            COMMON_MESSAGES=bot_common_messages,
        )
        if load_tests:
            BOT_METADATA[bot_name]['TESTS'] = bot_metadata.get('TESTS', {})
        count += 1

    print 'Loaded %d bot configs' % count

#-------- Schema Validation

def is_valid_response_type(val):
    types = get_class_vars(ResponseTypes)
    if val in types:
        return True
    raise ValidationError('Invalid response type: %s' % val)

def is_valid_action(val):
    actions = get_class_vars(Actions)
    if val in actions:
        return True
    raise ValidationError('Invalid action: %s' % val)

class MessageSchema(Schema):
    prompts = fields.List(fields.Str())
    entity_actions = fields.Dict(keys=fields.Str(), values=fields.Str(validate=is_valid_action))
    # TODO: validate it is a valid intent
    intent_actions = fields.Dict(keys=fields.Str(), values=fields.Str(validate=is_valid_action))
    action = fields.Str(validate=is_valid_action)

class MessageField(fields.Field):
    def _validate(self, value):
        if type(value) == list:
            if not all([type(x) in (str, unicode) for x in value]):
                raise ValidationError('Invalid Message format: %s' % value)
        elif isinstance(value, dict):
            schema = MessageSchema()
            result = schema.load(value)
        else:
            raise ValidationError('Invalid Message format: %s' % value)
        super(MessageField, self)._validate(value)

class SlotFollowUpSchema(Schema):
    prompts = fields.List(fields.Str(), required=True)
    entity_actions = fields.Dict(keys=fields.Str(), values=fields.Str(validate=is_valid_action))
    # TODO: validate it is a valid intent
    intent_actions = fields.Dict(keys=fields.Str(), values=fields.Str(validate=is_valid_action))
    action = fields.Str(validate=is_valid_action)

class IntentSlotSchema(Schema):
    prompts = fields.List(fields.Str())
    follow_up = fields.Nested(SlotFollowUpSchema)
    entity_handler = fields.Str()

class IntentFulfillmentSchema(Schema):
    url = fields.Url(required=True)

class IntentMetadataSchema(Schema):
    responses = fields.Dict(keys=fields.Str(validate=is_valid_response_type), values=fields.List(fields.Str()))
    # XXX This needs to preserve order!
    slots = fields.Dict(keys=fields.Str(), values=fields.Nested(IntentSlotSchema))
    fulfillment = fields.Nested(IntentFulfillmentSchema)
    repeatable = fields.Boolean()
    preemptive = fields.Boolean()
    is_answer = fields.Boolean()
    is_greeting = fields.Boolean()

class BotMetadataSchema(Schema):
    COMMON_MESSAGES =  fields.Dict(keys=fields.Str(), values=MessageField(), required=True)
    ENTITY_HANDLERS = fields.Dict(keys=fields.Str(), values=fields.Str())
    INTENT_METADATA = fields.Dict(keys=fields.Str(), values=fields.Nested(IntentMetadataSchema), required=True)
    # TODO: validate the list items
    TESTS = fields.Dict(keys=fields.Str(), values=fields.List(fields.List(fields.Field(allow_none=True))))