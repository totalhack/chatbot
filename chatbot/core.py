from cachetools import LRUCache, TTLCache
from diskcache import Cache

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
