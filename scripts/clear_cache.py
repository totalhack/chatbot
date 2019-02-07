from chatbot import app
from chatbot.configs import load_bot_configs
from chatbot.core import get_nlu_cache, get_convo_cache, DiskCache

load_bot_configs(app.config, load_tests=True)

nlu_cache = get_nlu_cache(app.config)
if isinstance(nlu_cache, DiskCache):
    print('Clearing NLU DiskCache')
    nlu_cache.clear()

convo_cache = get_convo_cache(app.config)
if isinstance(convo_cache, DiskCache):
    print('Clearing Convo DiskCache')
    convo_cache.clear()
