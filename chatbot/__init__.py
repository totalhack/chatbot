"""Init flask app"""
from flask import Flask
app = Flask(__name__, static_url_path='/static')
app.config.from_envvar('CHATBOT_CONFIG')
