'''
NOTE: Set CHATBOT_CONFIG env var to the path to your config file
'''
from collections import OrderedDict

from chatbot.conversation import CommonIntents, Actions

SECRET_KEY = 'mysupersecretkey'
DEBUG = True

TEST_BASE_URL = 'http://127.0.0.1:9000'

### Database connection info
SQLALCHEMY_DATABASE_URI = '<my database uri>'
SQLALCHEMY_TRACK_MODIFICATIONS = False

### LUIS account info
LUIS_URL = '<my luis url>'
LUIS_SUBKEY = '<my luis subkey>'

### App-specific overrides for messages (optional)
APP_COMMON_MESSAGES = {
    'intents_complete': {
        'prompts': [
            'We are all set. Need anything else?'
        ],
        'intent_actions': {CommonIntents.CONFIRM_YES: Actions.NONE,
                           CommonIntents.CONFIRM_NO: Actions.END_CONVERSATION}
    }
}

### App-specific overrides for entity handling (optional)
APP_ENTITY_HANDLERS = {
    'query': 'QueryEntityHandler',
}

### App-specific intents (required)
APP_INTENT_METADATA = {
    'IntentXYZ': {
        # Responses are how the bot will respond when the intent is first handled, deferred, or resumed.
        # The bot will randomly pick from the list of possible responses if you include multiple strings.
        'responses': {
            'active': [
                'Lets do XYZ now.',
                'I will start with XYZ now.',
            ],
            'deferred': [
                'We will get back to your XYZ later.',
            ],
            'resumed': [
                'Lets get back to your XYZ now.'
            ]
        },

        # Slots are entities that must be filled/extracted from the user.
        'slots': OrderedDict([
            ('zipcode', {'prompts': ['Can I have your zipcode?'],
                         # Optional follow up for confirmation
                         'follow_up': {'prompts': ['I heard {zipcode}. Is that correct?'],
                                       'intent_actions': {CommonIntents.CONFIRM_YES: Actions.NONE,
                                                          CommonIntents.CONFIRM_NO: Actions.REPEAT_SLOT}},
                         # Optionally specify custom entity handler for this
                         # particular slot on this intent. It can be a default
                         # class name or import string such as mypackage.module.MyEntityHandler.
                         'entity_handler': 'EntityHandler'}),

            ('email', {'prompts': ['Can I have your email?']}),
        ]),

        'fulfillment': {
            # Will be called after the intent has completed
            'url': TEST_BASE_URL + '/fulfillment',
        }
    },
}

### App-specific tests picked up by test.py
# Keys of the TESTS dict are the test names, values are a list of tuples that define
# what the user will say and what is expected back from the bot.
#
# Tuples have 1-4 arguments:
# (input, expected intent in reply, expected message name in reply, intent metadata override dict)
# The input can be a string (i.e. user utterance) or a dictionary that specifies
# a type of input such as a specific triggered intent.
TESTS = {
    'IntentXYZ': [
       ('Hi', 'Welcome'),
       ('I would like XYZ', 'IntentXYZ'),
       ('My zip is 12345',),
       ('Yes',), # Answer to the follow-up
       ('My Email is test@test.com', None, 'intents_complete'),
       ('No I am all set',  None, 'goodbye')
    ],

    # Trigger a specific intent and pass context to start filling slots (optional)
    'TriggerIntent': [
       ({'type': 'intent', 'value': 'IntentXYZ', 'context': {'zipcode': '02140'}}, 'IntentXYZ'),
    ],

    'Repeat': [
        ('Hi', 'Welcome'),
        ('Can you repeat that?', 'Repeat'),
    ],

}
