from collections import defaultdict, OrderedDict
import copy
import uuid

from flask import current_app

from chatbot.core import *
from chatbot.metadata import *
from chatbot.model import *
from chatbot.utils import *

class Input(PrintMixin, JSONMixin):
    repr_attrs = ['type', 'value']
    types = [
        'action',
        'intent',
        'text',
    ]

    def __init__(self, input):
        self.raw_input = input
        self.type = None
        self.value = None
        self.context = None

        if type(input) in (str, unicode):
            self.type = 'text'
            self.value = input
        elif isinstance(input, dict):
            self.type = input['type']
            self.value = input['value']
            self.context = input.get('context', {})
        else:
            assert False, 'Invalid input: %s' % input

        assert self.type in self.types, 'Invalid input type: %s' % self.type

class Transaction(JSONMixin, SaveMixin):
    save_attrs = ['id',
                  'conversation_id',
                  'input',
                  'response_message_text',
                  'slots_filled',
                  'question',
                  'active_intent',
                  'new_intents',
                  'aborted_intents',
                  'canceled_intents',
                  'completed_intent',
                  'expected_entities',
                  'expected_intents',
                  'expected_text',
                  'repeat_id',
                  'repeat_reason']

    dont_copy_attrs = [
        'id',
        'conversation_id',
        'input',
        'intent_response',
        'new_intents',
    ]

    def __init__(self, conversation_id):
        self.conversation_id = conversation_id
        self.id = str(uuid.uuid4())
        self.input = None
        self.intent_response = None
        self.response_messages = OrderedDictPlus()
        self.response_message_text = None
        self.slots_filled = MessageGroup()
        self.question = None
        self.active_intent = None
        self.new_intents = []
        self.aborted_intents = []
        self.canceled_intents = []
        self.completed_intent = None
        self.expected_entities = None
        self.expected_intents = None
        self.expected_text = None
        self.repeat_id = None
        self.repeat_reason = None

    def save(self):
        tx = Transactions(id=self.id, conversation_id=self.conversation_id, data=json.dumps(self.get_save_data()))
        db.session.merge(tx)
        db.session.commit()

    def add_filled_slot(self, intent, entity):
        self.slots_filled[intent.name] = entity

    def add_new_intent(self, intent):
        self.new_intents.append(intent)

    def prepend_new_intent(self, intent):
        self.new_intents.insert(0, intent)

    def abort_intent(self, intent):
        dbg('Aborting intent: %s' % intent.name)
        self.aborted_intents.append(intent)

    def cancel_intent(self, intent):
        dbg('Canceling intent: %s' % intent.name)
        self.canceled_intents.append(intent)

    def add_response_message(self, message_name, message, context=None, expected_entities=None, expected_intents=None, expected_text=None, prepend=False):
        if message:
            message = message.strip()
            if not (message.endswith('.') or message.endswith('?')):
                message = message + '.'

            format_args = get_string_format_args(message)
            if format_args:
                assert context, 'Message has format args but no context: %s' % message
                for format_arg in format_args:
                    assert format_arg in context, 'Message arg "%s" can not be satisifed by context: %s' % (format_arg, context)

        if prepend:
            self.response_messages.prepend(message_name, message)
            dbg('Prepending response message: %s: %s' % (message_name, message))
        else:
            self.response_messages[message_name] = message
            dbg('Adding response message: %s: %s' % (message_name, message))

        if expected_entities or expected_intents or expected_text:
            assert not self.requires_answer(), 'A transaction can only require a single answer'

        if expected_entities:
            assert isinstance(expected_entities, dict)
            self.expected_entities = expected_entities
        if expected_intents:
            assert isinstance(expected_intents, dict)
            self.expected_intents = expected_intents
        if expected_text:
            assert isinstance(expected_text, dict)
            self.expected_text = expected_text

    def add_response_message_object(self, msg, context=None, prepend=False):
        self.add_response_message(msg.name, msg.get_prompt(),
                                  context=context,
                                  prepend=prepend,
                                  expected_entities=getattr(msg, 'entity_actions', None),
                                  expected_intents=getattr(msg, 'intent_actions', None))

    def format_response_message(self, context={}):
        response_message = ' '.join([x for x in self.response_messages.values() if x])
        if '{' in response_message or '}' in response_message:
            assert ('{' in response_message) and ('}' in response_message), 'Invalid message template, open or close braces missing: "%s"' % response_message
            # TODO: we shouldnt allow prompts that require context values that
            # are not filled if there are other options
            try:
                response_message = response_message.format(**context)
            except KeyError, e:
                raise KeyError('Invalid message template, could not find "%s" in context' % str(e))
            self.response_message_text = response_message
        return response_message

    def clear_response_messages(self):
        self.response_messages = OrderedDictPlus()
        self.expected_entities = None
        self.expected_intents = None
        self.expected_text = None

    def copy_data_from_transaction(self, other_tx):
        for k,v in vars(other_tx).items():
            if k in self.dont_copy_attrs:
                continue
            if isinstance(v, dict):
                v = copy.deepcopy(v)
            elif hasattr(v, 'copy'):
                v = v.copy()
            setattr(self, k, v)

    def requires_answer(self):
        if self.expected_entities or self.expected_intents or self.expected_text:
            return True
        return False

    def is_answered(self, entities, intents, input):
        if not self.requires_answer():
            return True, None

        if self.expected_entities:
            for entity, action in self.expected_entities.items():
                if entity in [x.slot_name for x in entities]:
                    dbg('Expected entity %s found in answer' % entity)
                    return True, action

        if self.expected_intents:
            for intent, action in self.expected_intents.items():
                if intent in [x.name for x in intents]:
                    dbg('Expected intent %s found in answer' % intent)
                    return True, action

        for intent in intents:
            if not intent.is_common_intent:
                dbg('App intent %s found in answer' % intent.name)
                if self.active_intent and not (self.active_intent == self.completed_intent):
                    return True, '%s%s' % (VariableActions.ConfirmSwitchIntent, intent.name)
                return True, Actions.NoAction

        if self.expected_text:
            assert False, 'Not supported yet'

        dbg('Transaction went unanswered')
        return False, None

class Conversation(JSONMixin, SaveMixin):
    save_attrs = ['id',
                  'bot',
                  'nlu_class',
                  'context',
                  'intents',
                  'active_intents',
                  'completed_intents',
                  'active_intent',
                  'question_attempts']

    def __init__(self, bot, metadata=None):
        self.id = str(uuid.uuid4())
        self.bot = bot
        self.metadata = get_bot_metadata(bot)
        if metadata:
            dbg('Updating conversation metadata: %s' % metadata)
            self.metadata = dictmerge(copy.deepcopy(self.metadata), metadata, overwrite=True)
        self.nlu_class = self.metadata['NLU_CLASS']
        self.context = {}
        self.transactions = OrderedDictPlus()
        # TODO: this intent dict will also contain values as slots become
        # filled, but similar intent objects get passed around/used that dont
        # have those values. This is a bit confusing and should be cleaned up.
        self.intents = OrderedDictPlus()
        self.active_intents = OrderedDictPlus()
        self.completed_intents = OrderedDictPlus()
        self.active_intent = None
        self.question_attempts = OrderedDictPlus()
        self.completed = False
        self.consecutive_message_count = defaultdict(int)
        self.consecutive_repeat_count = 0

    def save(self):
        convo = Conversations(id=self.id, data=json.dumps(self.get_save_data()))
        db.session.merge(convo)
        db.session.commit()

    def do_action(self, tx, action, valid_entities=None, valid_intents=None, skip_common_messages=False):
        dbg('Do action %s' % action)

        if action == Actions.NoAction:
            pass

        elif action == Actions.CancelIntent:
            self.cancel_intent(tx)

        elif action == Actions.EndConversation:
            self.completed = True
            if not skip_common_messages:
                self.add_common_response_message(tx, self.metadata, 'goodbye')

        elif action == Actions.ReplaceSlot:
            last_tx = self.get_last_transaction()
            slots_filled = last_tx.slots_filled
            assert slots_filled, 'Trying to replace slot but no slot filled on previous transaction'
            filled_slot_names = [x.slot_name for x in slots_filled.values()]

            for entity in valid_entities:
                if entity.slot_name in filled_slot_names:
                    filled_slot = self.fill_intent_slot(tx, self.active_intent, entity)
                    if filled_slot.follow_up:
                        fu = filled_slot.follow_up
                        dbg('Adding follow-up %s during REPLACE_SLOT' % fu.name)
                        question, prompt = self.get_question_and_prompt(tx, fu)
                        if not question:
                            # Can happen if we've exhausted the question/slot and the intent is aborted
                            return
                        self.add_response_message(tx, '%s:%s' % (self.active_intent.name, question.name), prompt,
                                                  expected_entities=question.entity_actions,
                                                  expected_intents=question.intent_actions)

        elif action == Actions.RepeatSlot:
            self.repeat_slot()

        elif action.startswith(VariableActions.RepeatSlotAndRemoveIntent):
            intent_name = ''.join(action.split(VariableActions.RepeatSlotAndRemoveIntent)[1:])
            self.remove_active_intent_by_name(intent_name)
            self.repeat_slot()

        elif action.startswith(VariableActions.RemoveIntent):
            intent_name = ''.join(action.split(VariableActions.RemoveIntent)[1:])
            self.remove_active_intent_by_name(intent_name)

        elif action.startswith(VariableActions.ConfirmSwitchIntent):
            intent_name = ''.join(action.split(VariableActions.ConfirmSwitchIntent)[1:])
            msg_name = 'intent_switch:%s' % intent_name

            # TODO: better message, ability to override format?
            msg = 'Are you sure you want to switch intents?'

            # If Yes, cancel the active intent and move on to the new one
            # If No, remove the suggested intent and continue with any active intents
            last_tx = self.get_last_transaction()
            if last_tx and last_tx.question and isinstance(last_tx.question, FollowUp):
                expected_intents = {CommonIntents.ConfirmYes: Actions.CancelIntent,
                                    CommonIntents.ConfirmNo: '%s%s' % (VariableActions.RepeatSlotAndRemoveIntent, intent_name)}
            else:
                expected_intents = {CommonIntents.ConfirmYes: Actions.CancelIntent,
                                    CommonIntents.ConfirmNo: '%s%s' % (VariableActions.RemoveIntent, intent_name)}
            self.add_response_message(tx, msg_name, msg, expected_intents=expected_intents)

        elif action.startswith(VariableActions.Trigger):
            intent_name = ''.join(action.split(VariableActions.Trigger)[1:])
            intent = get_triggered_intent(self.metadata, intent_name)
            self.prepend_active_intent(intent)

        else:
            assert False, 'Unrecognized action: %s' % action

    def understand(self, tx, input):
        last_tx = self.get_last_transaction()

        if input.type == 'intent':
            intent_response = TriggeredIntentResponse(self.metadata, input.value)
        elif input.type == 'text':
            nlu = get_nlu(self.nlu_class)(self.metadata['NLU_CONFIG'])
            intent_response = nlu.process_query(self.metadata, input.value, last_tx=last_tx)
        else:
            assert False, 'Invalid input: %s' % input

        if input.context:
            dbg('Adding context to intent_response: %s' % input.context)
            intent_response.add_entities_from_context(input.context)
            self.context.update(input.context)

        dbg(vars(intent_response))
        tx.intent_response = intent_response
        return intent_response

    def get_last_transaction(self):
        # Assumes current transaction already added
        txs = self.transactions.values()
        if len(txs) < 2:
            return None
        return txs[-2]

    def get_last_transaction_with_slot(self, intent=None):
        # Assumes current transaction already added
        txs = self.transactions.values()
        if len(txs) < 2:
            return None
        for i, tx in enumerate(reversed(txs)):
            if i == 0:
                continue
            if intent and ((not tx.active_intent) or tx.active_intent.name != intent.name):
                continue
            if tx.slots_filled:
                return tx
        return None

    def transaction_repeatable(self, last_tx):
        repeat_tx = last_tx
        if last_tx.repeat_id:
            repeat_tx = self.transactions[last_tx.repeat_id]
        if not repeat_tx.question:
            return True
        question_attempts = self.get_question_attempts(self.active_intent, repeat_tx.question)
        if question_attempts < self.metadata['MAX_QUESTION_ATTEMPTS']:
            return True
        return False

    def repeat_transaction(self, tx, last_tx, reason=None, question_only=False):
        repeat_tx = last_tx
        if last_tx.repeat_id:
            repeat_tx = self.transactions[last_tx.repeat_id]

        if repeat_tx.question:
            assert self.add_question_attempt(self.active_intent, repeat_tx.question), 'Unable to repeat transaction. Question exhausted: %s' % repeat_tx.question

        tx.copy_data_from_transaction(repeat_tx)
        tx.repeat_id = repeat_tx.id
        tx.repeat_reason = reason
        if question_only:
            assert tx.question, 'Last TX does not have a question to repeat'
            tx.clear_response_messages()
            prompt = tx.question.get_prompt()
            self.add_response_message(tx, '%s:%s' % (self.active_intent.name, tx.question.name), prompt,
                                      expected_entities=tx.question.entity_actions,
                                      expected_intents=tx.question.intent_actions)

    def get_intent_slots(self, intent):
        return self.intents[intent.name].slots

    def get_question_attempts(self, intent, question):
        attempts = self.question_attempts.get(intent.name, {}).get(question.name, 0)
        return attempts

    def add_question_attempt(self, intent, question):
        '''Tracks count of attempts for each question'''
        if intent.name not in self.question_attempts:
            self.question_attempts[intent.name] = {}
        if question.name not in self.question_attempts[intent.name]:
            self.question_attempts[intent.name][question.name] = 0
        attempts = self.question_attempts[intent.name][question.name] + 1
        if attempts > self.metadata['MAX_QUESTION_ATTEMPTS']:
            return False
        self.question_attempts[intent.name][question.name] = attempts
        return True

    def clear_question_attempts(self, intent):
        self.question_attempts[intent.name] = 0

    def clear_filled_slot(self, intent, slot):
        current_value = self.intents[intent.name].slots[slot.slot_name].value
        assert current_value, 'Slot %s on intent %s is not filled' % (slot, intent)
        self.intents[intent.name].slots[slot.slot_name].value = None
        dbg('Clearing filled slot %s for intent %s' % (slot.slot_name, intent.name))

    def repeat_slot(self):
        '''This is meant to repeat the last *filled* slot'''
        last_tx = self.get_last_transaction_with_slot(intent=self.active_intent)
        assert last_tx, 'Trying to repeat slot but there is no last transaction with a slot'
        assert last_tx.slots_filled, 'Trying to repeat slot but no slot filled on previous transaction'
        for slot_name, slot in last_tx.slots_filled.items():
            self.clear_filled_slot(self.active_intent, slot)

    def get_next_question_and_prompt(self, tx, remaining_questions):
        question = remaining_questions.get_next()

        if not self.add_question_attempt(self.active_intent, question):
            # We've asked this question the max number of times already
            self.abort_intent(tx)
            return None, None

        tx.question = question
        prompt = question.get_prompt()
        return question, prompt

    def get_question_and_prompt(self, tx, question):
        return self.get_next_question_and_prompt(tx, MessageGroup([(question.name, question)]))

    def abort_intent(self, tx):
        if not self.active_intent:
            error('Trying to abort intent but no intent is active on conversation')
            return
        tx.abort_intent(self.active_intent)
        self.add_common_response_message(tx, self.metadata, 'intent_aborted')
        self.clear_question_attempts(self.active_intent)
        self.remove_active_intent(self.active_intent)

    def cancel_intent(self, tx):
        if not self.active_intent:
            error('Trying to cancel intent but no intent is active on conversation')
            return
        tx.cancel_intent(self.active_intent)
        self.clear_question_attempts(self.active_intent)
        self.remove_active_intent(self.active_intent)

    def get_filled_slots(self):
        filled_slots = {}
        for intent_name, intent in self.intents.items():
            for slot_name, slot in intent.slots.items():
                if slot.value is not None:
                    filled_slots.setdefault(slot_name, MessageGroup())[intent_name] = slot.value
        return filled_slots

    def get_filled_slots_by_intent(self, intent):
        '''returns a simple map of slot names to values for this intent'''
        filled_slots = {}
        for slot_name, slot in intent.slots.items():
            if slot.value is not None:
                filled_slots[slot_name] = slot.value.value
        return filled_slots

    def get_filled_slots_by_name(self, slot_name):
        filled_slots = self.get_filled_slots()
        return filled_slots.get(slot_name, MessageGroup())

    def fill_intent_slot(self, tx, intent, entity):
        dbg('Filling slot %s for intent %s' % (entity.slot_name, intent.name))
        slot = self.intents[intent.name].slots[entity.slot_name]
        slot.value = entity
        tx.add_filled_slot(intent, entity)
        return slot

    def fill_intent_slots_with_entities(self, tx, intent, entities):
        '''This can return slots and/or follow ups for filled slots'''
        remaining_questions = intent.get_remaining_intent_slots()
        if not entities:
            return remaining_questions

        follow_up_added = False
        if remaining_questions:
            for entity in entities:
                if entity.slot_name in remaining_questions:
                    slot = self.intents[intent.name].slots[entity.slot_name]
                    if slot.follow_up and follow_up_added:
                        warn('Not filling slot %s with additional follow-up %s' % (entity.slot_name, slot.follow_up.name))
                        continue

                    filled_slot = self.fill_intent_slot(tx, intent, entity)
                    del remaining_questions[entity.slot_name]
                    if filled_slot.follow_up and not entity.from_context:
                        fu = filled_slot.follow_up
                        dbg('Adding follow-up %s' % fu.name)
                        remaining_questions.prepend(fu.name, fu)
                        follow_up_added = True

            if not remaining_questions:
                dbg('All slots filled by existing slot data')
                if intent == self.active_intent:
                    self.active_intent_completed(tx)
                else:
                    self.add_completed_intent(tx, intent)

        return remaining_questions

    def fill_intent_slots_with_filled_slots(self, tx, intent):
        '''Only returns remaining slots, no follow-ups, as it's assumed if a slot was
        filled and needed a follow-up that would have already happened.'''
        remaining_slots = intent.get_remaining_intent_slots()
        if remaining_slots:
            for slot_name, slot in remaining_slots.items():
                if not slot.autofill:
                    continue

                filled_slots = self.get_filled_slots_by_name(slot_name)
                if not any(filled_slots.values()):
                    # This slot has no values, so we cant fill anything. Move on.
                    continue

                # This slot has already been filled. Reuse its value.
                filled_slot = self.fill_intent_slot(tx, intent, filled_slots.values()[0].copy())

            remaining_slots = intent.get_remaining_intent_slots()
            if not remaining_slots:
                dbg('All slots filled by existing slot data')
                if intent == self.active_intent:
                    self.active_intent_completed(tx)
                else:
                    self.add_completed_intent(tx, intent)

        return remaining_slots

    def add_active_intent(self, intent):
        if not intent.repeatable:
            assert intent.name not in self.intents, 'Intent is not repeatable: %s' % intent.name
        dbg('Adding active intent %s' % intent.name)
        self.intents[intent.name] = intent
        self.active_intents[intent.name] = intent

    def prepend_active_intent(self, intent):
        if not intent.repeatable:
            assert intent.name not in self.intents, 'Intent is not repeatable: %s' % intent.name
        dbg('Prepending active intent %s' % intent.name)
        self.intents[intent.name] = intent
        self.active_intents.prepend(intent.name, intent)

    def remove_active_intent(self, intent):
        if intent.name in self.active_intents:
            del self.active_intents[intent.name]
        if self.active_intent and self.active_intent.name == intent.name:
            self.active_intent = None

    def remove_active_intent_by_name(self, intent_name):
        intent = self.intents[intent_name]
        self.remove_active_intent(intent)

    def add_completed_intent(self, tx, intent):
        dbg('Intent %s completed' % intent.name)
        try:
            response = intent.fulfill(self, tx, self.get_intent_slots(intent))
            if response:
                if not response.success():
                    warn('Fulfillment did not succeed! Reason: %s' % response.status_reason)
                if response.message:
                    dbg('Adding fulfillment response %s' % response)
                    self.add_response_message_object(tx, response.message)
                if response.action:
                    self.do_action(tx, response.action, skip_common_messages=True if response.message else False)
        finally:
            self.completed_intents[intent.name] = intent
            self.remove_active_intent(intent)
            tx.completed_intent = intent

    def active_intent_completed(self, tx):
        if self.active_intent:
            self.add_completed_intent(tx, self.active_intent)
            self.active_intent = None

    def remove_completed_intent(self, intent):
        assert False, 'Probably shouldnt allow this'
        if intent.name in self.completed_intents:
            del self.completed_intents[intent.name]

    def get_message_context(self):
        context = {}
        if self.active_intent:
            context = self.get_filled_slots_by_intent(self.active_intent)
        return context

    def add_new_intent_message(self, tx, intent, response_type=None, entities=None):
        '''Gets the message(s) at the start of a new intent'''
        if not response_type:
            response_type = ResponseTypes.Active
        response = random.choice(intent.responses.get(response_type, ['']))
        if not response:
            warn('No response for intent %s' % intent)

        prompt = ''
        if response_type in [ResponseTypes.Active, ResponseTypes.Resumed] and self.get_intent_slots(intent):
            remaining_questions = self.fill_intent_slots_with_entities(tx, intent, entities)
            if not remaining_questions:
                return # The intent was satisfied by data in collected entities
            assert not any([type(x) == FollowUp for x in remaining_questions]), 'FollowUp found while adding message for new intent: %s' % remaining_questions

            remaining_questions = self.fill_intent_slots_with_filled_slots(tx, intent)
            if not remaining_questions:
                return # The intent was satisfied by existing slot data
            assert not any([type(x) == FollowUp for x in remaining_questions]), 'FollowUp found while adding message for new intent: %s' % remaining_questions

            # There are slots to prompt for this intent still
            question, prompt = self.get_next_question_and_prompt(tx, remaining_questions)
            if not question:
                return

        if response:
            self.add_response_message(tx, '%s:%s' % (intent.name, response_type), response)
        if prompt:
            self.add_response_message(tx, '%s:%s' % (intent.name, question.name), prompt,
                                      expected_entities=question.entity_actions,
                                      expected_intents=question.intent_actions)

    def add_response_message(self, tx, *args, **kwargs):
        kwargs['context'] = kwargs.get('context', self.get_message_context())
        tx.add_response_message(*args, **kwargs)

    def add_response_message_object(self, tx, msg, **kwargs):
        kwargs['context'] = kwargs.get('context', self.get_message_context())
        tx.add_response_message(msg.name, msg.get_prompt(),
                                expected_entities=getattr(msg, 'entity_actions', None),
                                expected_intents=getattr(msg, 'intent_actions', None),
                                **kwargs)

    def get_common_message(self, metadata, message_name):
        return metadata['COMMON_MESSAGES'][message_name]

    def add_common_response_message(self, tx, metadata, message_name, prepend=False):
        message_info = self.get_common_message(metadata, message_name)
        message = None
        expected_entities = None
        expected_intents = None

        if not message_info:
            pass
        elif type(message_info) == str:
            message = message_info
        elif type(message_info) in (list, tuple):
            message = random.choice(message_info)
        else:
            message = random.choice(message_info.get('prompts', [None]))
            expected_entities = message_info.get('entity_actions', None)
            expected_intents = message_info.get('intent_actions', None)
            action = message_info.get('action', None)

            if action:
                self.do_action(tx, action, skip_common_messages=True if message else False)

        self.add_response_message(tx, message_name, message, expected_entities=expected_entities, expected_intents=expected_intents, prepend=prepend)

    def create_response_message(self, tx, valid_intents, valid_entities):
        last_tx = self.get_last_transaction()
        greeted = False

        # Analyze new intents
        for i, intent in enumerate(valid_intents):
            if intent.is_greeting:
                if i > 0:
                    warn('greetings only allowed as top intent, skipping %s' % intent.name)
                    continue
                if last_tx:
                    warn('greetings only allowed on first transaction, skipping %s' % intent.name)
                    continue

            if intent.name in self.active_intents and not intent.repeatable:
                warn('intent %s already active' % intent.name)
                continue

            if intent.name in self.completed_intents and not intent.repeatable:
                warn('intent %s already completed' % intent.name)

            if intent.preemptive:
                self.prepend_active_intent(intent)
                tx.prepend_new_intent(intent)

                if intent.name == CommonIntents.Cancel:
                    dbg('Cancel Intent')
                    self.add_common_response_message(tx, self.metadata, 'intent_canceled')
                    return

                if intent.name == CommonIntents.Repeat:
                    dbg('Repeat Intent')
                    if last_tx:
                        if self.consecutive_repeat_count >= self.metadata['MAX_CONSECUTIVE_REPEAT_ATTEMPTS']:
                            self.add_common_response_message(tx, self.metadata, 'repeat_exhausted')
                        else:
                            if self.transaction_repeatable(last_tx):
                                self.repeat_transaction(tx, last_tx, reason='user request')
                            else:
                                self.abort_intent(tx)
                    else:
                        if self.consecutive_message_count['fallback'] >= self.metadata['MAX_CONSECUTIVE_MESSAGE_ATTEMPTS']:
                            self.add_common_response_message(tx, self.metadata, 'message_exhausted')
                        else:
                            self.add_common_response_message(tx, self.metadata, 'fallback')
                    return

                # TODO: refactor to share logic

                if intent.name == CommonIntents.Help:
                    dbg('Help Intent')
                    if last_tx and last_tx.question and last_tx.question.help:
                        help_msg = last_tx.question.get_help()
                        msg_name = '%s:help' % last_tx.question.name
                        if self.consecutive_message_count[msg_name] >= self.metadata['MAX_CONSECUTIVE_MESSAGE_ATTEMPTS']:
                            self.add_common_response_message(tx, self.metadata, 'message_exhausted')
                            return
                        self.add_response_message(tx, msg_name, help_msg)
                    elif self.active_intent and self.active_intent.help:
                        help_msg = self.active_intent.get_help()
                        msg_name = '%s:help' % self.active_intent.name
                        if self.consecutive_message_count[msg_name] >= self.metadata['MAX_CONSECUTIVE_MESSAGE_ATTEMPTS']:
                            self.add_common_response_message(tx, self.metadata, 'message_exhausted')
                            return
                        self.add_response_message(tx, msg_name, help_msg)
                    else:
                        if self.consecutive_message_count['help'] >= self.metadata['MAX_CONSECUTIVE_MESSAGE_ATTEMPTS']:
                            self.add_common_response_message(tx, self.metadata, 'message_exhausted')
                            return
                        self.add_common_response_message(tx, self.metadata, 'help')

                if intent.name == CommonIntents.Why:
                    dbg('Why Intent')
                    if last_tx and last_tx.question and last_tx.question.why:
                        why_msg = last_tx.question.get_why()
                        msg_name = '%s:why' % last_tx.question.name
                        if self.consecutive_message_count[msg_name] >= self.metadata['MAX_CONSECUTIVE_MESSAGE_ATTEMPTS']:
                            self.add_common_response_message(tx, self.metadata, 'message_exhausted')
                            return
                        self.add_response_message(tx, msg_name, why_msg)
                    elif self.active_intent and self.active_intent.why:
                        why_msg = self.active_intent.get_why()
                        msg_name = '%s:why' % self.active_intent.name
                        if self.consecutive_message_count[msg_name] >= self.metadata['MAX_CONSECUTIVE_MESSAGE_ATTEMPTS']:
                            self.add_common_response_message(tx, self.metadata, 'message_exhausted')
                            return
                        self.add_response_message(tx, msg_name, why_msg)
                    else:
                        if self.consecutive_message_count['why'] >= self.metadata['MAX_CONSECUTIVE_MESSAGE_ATTEMPTS']:
                            self.add_common_response_message(tx, self.metadata, 'message_exhausted')
                            return
                        self.add_common_response_message(tx, self.metadata, 'why')

            else:
                if intent.is_greeting:
                    greeted = True
                self.add_active_intent(intent)
                tx.add_new_intent(intent)

        if (not greeted) and (not last_tx):
            dbg('Adding greeting on first transaction')
            self.add_common_response_message(tx, self.metadata, 'greeting', prepend=True)
            if not valid_intents:
                # It's the first message and we didn't recognize the intent of the user
                self.add_common_response_message(tx, self.metadata, 'initial_prompt')
                return

        # Handle questions that require answers
        if last_tx and last_tx.requires_answer():
            is_answered, action = last_tx.is_answered(valid_entities, valid_intents, tx.input)
            if is_answered:
                self.do_action(tx, action, valid_entities=valid_entities, valid_intents=valid_intents)
                if self.completed or tx.response_messages:
                    return
            else:
                if self.transaction_repeatable(last_tx):
                    # repeat_transaction will overwrite messages, so copy these first
                    tx_messages = copy.deepcopy(tx.response_messages)
                    tx_requires_answer = tx.requires_answer()
                    self.repeat_transaction(tx, last_tx, reason='last transaction not answered', question_only=True)
                    if tx_messages:
                        # Messages were already added to this tx by some intent handled above
                        assert not tx_requires_answer, 'A question was asked while another unanswered question is in progress'
                        for msg_name, msg in reversed(tx_messages.items()):
                            self.add_response_message(tx, msg_name, msg, prepend=True)
                    else:
                        self.add_common_response_message(tx, self.metadata, 'unanswered', prepend=True)
                else:
                    self.abort_intent(tx)
                return

        # All one-off and preemptive intents should have been handled before this
        for intent in self.active_intents.values():
            if intent.is_answer:
                dbg('Removing is_answer intent from active list: %s' % intent.name)
                self.remove_active_intent(intent)
            elif intent.preemptive and not intent.slots:
                # We assume these already displayed any relevant message
                dbg('Removing preemptive intent with no slots from active list: %s' % intent.name)
                self.remove_active_intent(intent)

        # Handle ongoing intent
        if self.active_intent:
            tx.active_intent = self.active_intent
            dbg('Active intent %s' % self.active_intent.name)
            remaining_questions = self.fill_intent_slots_with_entities(tx, self.active_intent, valid_entities)
            if remaining_questions:
                question, prompt = self.get_next_question_and_prompt(tx, remaining_questions)
                if not question:
                    # Can happen if we've exhausted the question/slot and the intent is aborted
                    return

                self.add_response_message(tx, '%s:%s' % (self.active_intent.name, question.name), prompt,
                                          expected_entities=question.entity_actions,
                                          expected_intents=question.intent_actions)
                return
            else:
                self.active_intent_completed(tx)

        # We've handled any one-off or active intents, move on to other intents
        # that were already registered
        for i, intent in enumerate(self.active_intents.values()):
            message = None
            if i == 0:
                self.active_intent = intent
                tx.active_intent = intent
                response_type = ResponseTypes.Active
                if tx.completed_intent and (intent not in tx.new_intents):
                    # The user completed an intent with their most recent response
                    # but they have already queued up other intents from previous transactions
                    response_type = ResponseTypes.Resumed
            else:
                response_type = ResponseTypes.Deferred

            dbg('Handling %s intent: %s' % (response_type, intent.name))
            self.add_new_intent_message(tx, intent, response_type=response_type, entities=valid_entities)

        if not tx.response_messages:
            if tx.completed_intent or (self.completed_intents and not self.active_intents):
                self.add_common_response_message(tx, self.metadata, 'intents_complete')
            else:
                if self.consecutive_message_count['fallback'] >= self.metadata['MAX_CONSECUTIVE_MESSAGE_ATTEMPTS']:
                    self.add_common_response_message(tx, self.metadata, 'message_exhausted')
                else:
                    self.add_common_response_message(tx, self.metadata, 'fallback')

    def process_intent_response(self, tx, intent_response):
        valid_intents, valid_entities = intent_response.get_valid(intent_threshold=self.metadata['INTENT_FILTER_THRESHOLD'],
                                                                  entity_threshold=self.metadata['ENTITY_FILTER_THRESHOLD'])
        if not valid_intents:
            warn('no valid intents found')
        self.create_response_message(tx, valid_intents, valid_entities)

    def create_transaction(self):
        tx = Transaction(self.id)
        self.transactions[tx.id] = tx
        return tx

    def reply(self, tx, input):
        input = Input(input)
        tx.input = input

        if input.type == 'action':
            self.do_action(tx, input.value)
        else:
            intent_response = self.understand(tx, input)
            self.process_intent_response(tx, intent_response)

        response_message = None
        if tx.response_messages:
            context = self.get_message_context()
            response_message = tx.format_response_message(context=context)

        if tx.repeat_id:
            self.consecutive_repeat_count += 1
            dbg('Consecutive repeat messages: %s' % self.consecutive_repeat_count)
        else:
            self.consecutive_repeat_count = 0

        for key in tx.response_messages:
            self.consecutive_message_count[key] += 1
            dbg('Consecutive %s messages: %s' % (key, self.consecutive_message_count[key]))
        for key in self.consecutive_message_count:
            if key not in tx.response_messages:
                self.consecutive_message_count[key] = 0

        return response_message
