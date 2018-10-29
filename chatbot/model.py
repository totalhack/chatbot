from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.declarative import declared_attr 

from chatbot import app

db = SQLAlchemy(app)

class SaveMixin(object):
    save_attrs = []

    def get_save_data(self):
        return {key:getattr(self, key) for key in self.save_attrs}

class TimestampMixin(object):
    # https://stackoverflow.com/questions/3923910/sqlalchemy-move-mixin-columns-to-end
    @declared_attr
    def created_at(cls):
        return db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @declared_attr
    def updated_at(cls):
        return db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

class Conversations(TimestampMixin, db.Model):
    # TODO: this should be in the context of a particular source/partner
    id = db.Column(db.String(50), primary_key=True, autoincrement=False)
    data = db.Column(db.Text, nullable=False)

class Transactions(TimestampMixin, db.Model):
    id = db.Column(db.String(50), primary_key=True, autoincrement=False)
    conversation_id = db.Column(db.String(50), db.ForeignKey('conversations.id'), nullable=False)
    conversation = db.relationship('Conversations', backref=db.backref('transactions', lazy=True))
    data = db.Column(db.Text, nullable=False)

if __name__ == '__main__':
    print 'Creating database...'
    db.create_all()