'''
NOTE: Set CHATBOT_CONFIG env var to the path to your config file
'''

SECRET_KEY = 'mysupersecretkey'
DEBUG = True

TEST_BASE_URL = 'http://127.0.0.1:9000'

### Database connection info
SQLALCHEMY_DATABASE_URI = '<my database uri>'
SQLALCHEMY_TRACK_MODIFICATIONS = False

### LUIS account info
NLU_CACHE = True
NLU_CONFIG = {
    'LUIS_BASE_URL': 'https://westus.api.cognitive.microsoft.com',
    'LUIS_APP_ID': '<my luis app id>'
    'LUIS_APP_VERSION': '<my luis app version>',
    'LUIS_SUBKEY': '<my luis subkey>'
}

### Directory where bot metadata files live
BOT_METADATA_DIRECTORY = '/etc/config/chatbot/'
