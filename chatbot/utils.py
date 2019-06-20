from flask import Response, current_app

from toolbox import dbg as _dbg, json

# Monkey patch dbg() to add the current_app logic
def dbg(msg, label='parent', config=None, **kwargs):
    if config and not config.get('DEBUG', False):
        return
    elif current_app and not current_app.config['DEBUG']:
        return
    _dbg(msg, label=label, config=config, **kwargs)

def jsonr(obj):
    return Response(json.dumps(obj), mimetype="application/json")
