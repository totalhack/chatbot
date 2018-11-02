from cachetools import TTLCache
from collections import OrderedDict
import json
from json import JSONEncoder
from pprint import pprint, pformat
import random
import sys

from flask import Response, current_app

# https://stackoverflow.com/questions/16664874/how-can-i-add-an-element-at-the-top-of-an-ordereddict-in-python
class OrderedDictPlus(OrderedDict):
    def prepend(self, key, value, dict_setitem=dict.__setitem__):
        root = self._OrderedDict__root
        first = root[1]

        if key in self:
            link = self._OrderedDict__map[key]
            link_prev, link_next, _ = link
            link_prev[1] = link_next
            link_next[0] = link_prev
            link[0] = root
            link[1] = first
            root[1] = first[0] = link
        else:
            root[1] = first[0] = self._OrderedDict__map[key] = [root, first, key]
            dict_setitem(self, key, value)

def _default(self, obj):
    return getattr(obj.__class__, 'to_json', _default.default)(obj)

_default.default = JSONEncoder().default
JSONEncoder.default = _default

def jsonr(obj):
    return Response(json.dumps(obj), mimetype="application/json")

class FontColors:
    ENDC = '\033[0m'
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    INVERTED = '\033[7m'

def log(msg, label='parent', indent=0, color=None, format_func=pformat):
    if type(msg) not in (str, unicode):
        msg = pformat(msg)
    if indent is not None and int(indent): sg = msg + (' ' * int(indent))
    if label:
        if label == 'parent':
            label = sys._getframe().f_back.f_code.co_name
        msg = label.strip() + ':' + msg
    if color: msg = getattr(FontColors, color.upper()) + msg + FontColors.ENDC
    print msg

def dbg(msg, label='parent', **kwargs):
    if not current_app.config['DEBUG']:
        return
    if label == 'parent':
        label = sys._getframe().f_back.f_code.co_name
    log(msg, label=label, **kwargs)

def warn(msg, label='WARNING'):
    log(msg, label=label, color='yellow')

def error(msg, label='ERROR'):
    log(msg, label=label, color='red')

class JSONMixin(object):
    def to_json(self):
        return self.__dict__

    def to_jsons(self):
        return json.dumps(self.__dict__)

class PrintMixin(object):
    repr_attrs = []

    def __repr__(self):
        if self.repr_attrs:
            return "<%s %s>" % (type(self).__name__, ' '.join(['%s=%s' % (field, getattr(self, field)) for field in self.repr_attrs]))
        else:
            return "<%s %s>" % (type(self).__name__, id(self))

    def __str__(self):
        return str(vars(self))
