from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.declarative import declared_attr

db = SQLAlchemy()

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

class Fulfillments(TimestampMixin, db.Model):
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    conversation_id = db.Column(db.String(50), db.ForeignKey('conversations.id'), nullable=False)
    conversation = db.relationship('Conversations', backref=db.backref('fulfillments', lazy=True))
    url = db.Column(db.String(400), nullable=False)
    status_code = db.Column(db.Integer, nullable=True)
    response = db.Column(db.Text, nullable=False)
    data = db.Column(db.Text, nullable=False)

if __name__ == '__main__':
    from chatbot import app
    db.init_app(app)
    with app.app_context():
        print('Creating database...')
        db.create_all()
