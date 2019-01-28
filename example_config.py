'''
NOTE: Set CHATBOT_CONFIG env var to the path to your config file
'''

SECRET_KEY = 'mysupersecretkey'
DEBUG = True
PROFILE = False

### Database connection info
SQLALCHEMY_DATABASE_URI = '<my database uri>'
SQLALCHEMY_TRACK_MODIFICATIONS = False

TEST_BASE_URL = 'http://127.0.0.1:9000'

### LUIS account info
NLU_CACHE = True
NLU_CONFIG = {
    'luis_base_url': 'https://westus.api.cognitive.microsoft.com',
    'luis_app_id': '<my luis app id>'
    'luis_app_version': '<my luis app version>',
    'luis_subkey': '<my luis subkey>'
}

### Directory where bot config files live
BOT_CONFIG_DIRECTORY = '/etc/config/chatbot/'
