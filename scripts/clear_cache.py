from chatbot import app
from chatbot.core import *
from chatbot.metadata import *

load_bot_metadata(app.config, load_tests=True)
assert BOT_METADATA

nlu_cache = get_nlu_cache(app.config)
if isinstance(nlu_cache, DiskCache):
    print 'Clearing NLU DiskCache'
    nlu_cache.clear()

convo_cache = get_convo_cache(app.config)
if isinstance(convo_cache, DiskCache):
    print 'Clearing Convo DiskCache'
    convo_cache.clear()
