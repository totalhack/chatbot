from collections import defaultdict
import copy
import uuid

from chatbot.configs import get_bot_config
from chatbot.core import (Question,
                          FollowUp,
                          Actions,
                          Action,
                          ActionMap,
                          MessageGroup,
                          CommonIntents,
                          ResponseTypes,
                          TriggeredIntentPrediction,
                          get_nlu)
from chatbot.model import db, Transactions, Conversations, SaveMixin
from chatbot.utils import (PrintMixin,
                           JSONMixin,
                           OrderedDictPlus,
                           get_string_format_args,
                           json,
                           dbg,
                           warn,
                           error,
                           st)

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

        if isinstance(input, str):
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
                  'new_intents',
                  'aborted_intents',
                  'canceled_intents',
                  'active_intent_name',
                  'completed_intent_name',
                  'expected_entities',
                  'expected_intents',
                  'expected_text',
                  'repeat_id',
                  'repeat_reason']

    dont_copy_attrs = [
        'id',
        'conversation_id',
        'input',
        'intent_prediction',
        'new_intents',
    ]

    def __init__(self, conversation_id):
        self.conversation_id = conversation_id
        self.id = str(uuid.uuid4())
        self.input = None
        self.intent_prediction = None
        self.response_messages = OrderedDictPlus()
        self.response_message_text = None
        self.slots_filled = MessageGroup()
        self.question = None
        self.new_intents = []
        self.aborted_intents = []
        self.canceled_intents = []
        self.active_intent_name = None
        self.completed_intent_name = None
        self.expected_entities = None
        self.expected_intents = None
        self.expected_text = None
        self.repeat_id = None
        self.repeat_reason = None

    def save(self):
        tx = Transactions(id=self.id, conversation_id=self.conversation_id, data=json.dumps(self.get_save_data()))
        db.session.merge(tx)
        db.session.commit()

    def add_filled_slot(self, intent, slot_result):
        self.slots_filled[intent.name] = slot_result

    def add_new_intent(self, intent_name):
        self.new_intents.append(intent_name)

    def prepend_new_intent(self, intent_name):
        self.new_intents.insert(0, intent_name)

    def abort_intent(self, intent_name):
        dbg('Aborting intent: %s' % intent_name)
        self.aborted_intents.append(intent_name)

    def cancel_intent(self, intent_name):
        dbg('Canceling intent: %s' % intent_name)
        self.canceled_intents.append(intent_name)

    def add_response_message(self, message_name, message, context=None, expected_entities=None, expected_intents=None,
                             expected_text=None, prepend=False):
        if message:
            message = message.strip()
            if not (message.endswith('.') or message.endswith('?')):
                message = message + '.'

            format_args = get_string_format_args(message)
            if format_args:
                assert context, 'Message has format args but no context: %s' % message
                for format_arg in format_args:
                    assert format_arg in context,\
                        'Message arg "%s" can not be satisifed by context: %s' % (format_arg, context)

        if prepend:
            self.response_messages.prepend(message_name, message)
            dbg('Prepending response message: %s: %s' % (message_name, message))
        else:
            self.response_messages[message_name] = message
            dbg('Adding response message: %s: %s' % (message_name, message))

        if expected_entities or expected_intents or expected_text:
            assert not self.requires_answer(), 'A transaction can only require a single answer'

        if expected_entities:
            assert isinstance(expected_entities, ActionMap)
            self.expected_entities = expected_entities
        if expected_intents:
            assert isinstance(expected_intents, ActionMap)
            self.expected_intents = expected_intents
        if expected_text:
            assert isinstance(expected_text, ActionMap)
            self.expected_text = expected_text

    def add_response_message_object(self, msg, context=None, prepend=False):
        self.add_response_message(msg.name, msg.get_prompt(),
                                  context=context,
                                  prepend=prepend,
                                  expected_entities=getattr(msg, 'entity_actions', None),
                                  expected_intents=getattr(msg, 'intent_actions', None))

    def format_response_message(self, context=None):
        if not context:
            context = {}
        response_message = ' '.join([x for x in self.response_messages.values() if x])
        if '{' in response_message or '}' in response_message:
            assert ('{' in response_message) and ('}' in response_message),\
                'Invalid message template, open or close braces missing: "%s"' % response_message
            # TODO: we shouldnt allow prompts that require context values that
            # are not filled if there are other options
            try:
                response_message = response_message.format(**context)
            except KeyError as e:
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

    def copy_data_for_repeat_transaction(self, other_tx, reason=None):
        self.copy_data_from_transaction(other_tx)
        self.repeat_id = other_tx.id
        self.repeat_reason = reason
        self.completed_intent_name = None

    def requires_answer(self):
        if self.expected_entities or self.expected_intents or self.expected_text:
            return True
        return False

    def is_answered(self, entity_results, intent_results, input):
        if not self.requires_answer():
            return True, None

        if self.expected_entities:
            for entity, action in self.expected_entities.items():
                if entity in [x.slot_name for x in entity_results]:
                    dbg('Expected entity %s found in answer' % entity)
                    return True, action

        if self.expected_intents:
            for intent, action in self.expected_intents.items():
                if intent in [x.name for x in intent_results]:
                    dbg('Expected intent %s found in answer' % intent)
                    return True, action

        if self.expected_text:
            assert False, 'Not supported yet'

        return False, None

class Conversation(JSONMixin, SaveMixin):
    save_attrs = ['id',
                  'bot',
                  'context',
                  'intent_slot_results',
                  'pending_intents',
                  'completed_intents',
                  'active_intent_name',
                  'question_attempts']

    def __init__(self, bot, bot_config=None):
        self.id = str(uuid.uuid4())
        self.bot = bot
        self.bot_config = get_bot_config(bot)
        if bot_config:
            dbg('Updating conversation bot config: %s' % bot_config)
            self.bot_config = self.bot_config.merge_dict(bot_config)
        self.nlu = get_nlu(self.bot_config)
        self.context = {}
        self.transactions = OrderedDictPlus()
        self.intent_slot_results = OrderedDictPlus()
        self.pending_intents = OrderedDictPlus()
        self.completed_intents = OrderedDictPlus()
        self.question_attempts = OrderedDictPlus()
        self.active_intent_name = None
        self.completed = False
        self.consecutive_message_count = defaultdict(int)
        self.consecutive_repeat_count = 0

    def save(self):
        convo = Conversations(id=self.id, data=json.dumps(self.get_save_data()))
        db.session.merge(convo)
        db.session.commit()

    def get_intent(self, name):
        return self.bot_config.intent_configs[name]

    @property
    def active_intent(self):
        return self.bot_config.intent_configs.get(self.active_intent_name, None)

    @active_intent.setter
    def active_intent(self, value):
        if value is None:
            self.active_intent_name = value
            return

        if isinstance(value, str):
            self.active_intent_name = value
            return

        self.active_intent_name = value.name

    def _action_CancelIntent(self, tx, **kwargs):
        self.cancel_intent(tx)

    def _action_ConfirmCancelIntent(self, tx, params=None, **kwargs):
        self.add_common_response_message(tx, self.bot_config, 'cancel_intent?')

    def _action_ConfirmSwitchIntent(self, tx, params=None, **kwargs):
        msg_name = 'intent_switch:%s' % params['intent_name']
        # TODO: better message, ability to override format
        msg = 'Are you sure you want to switch intents?'

        # If Yes, cancel the active intent and move on to the new one
        # If No, remove the suggested intent and continue with any active intents
        last_tx = self.get_last_transaction()
        if last_tx and last_tx.question and isinstance(last_tx.question, FollowUp):
            expected_intents = ActionMap({CommonIntents.Yes: Actions.CancelIntent,
                                          CommonIntents.No: {'name': Actions.RepeatSlotAndRemoveIntent,
                                                             'params': params}})
        else:
            expected_intents = ActionMap({CommonIntents.Yes: Actions.CancelIntent,
                                          CommonIntents.No: {'name': Actions.RemoveIntent,
                                                             'params': params}})
        self.add_response_message(tx, msg_name, msg, expected_intents=expected_intents)

    def _action_EndConversation(self, tx, skip_common_messages=False, **kwargs):
        self.completed = True
        if not skip_common_messages:
            self.add_common_response_message(tx, self.bot_config, 'goodbye')

    def _action_Help(self, tx, **kwargs):
        self.add_help_or_why_message(tx, 'help')

    def _action_NoAction(self, *args, **kwargs):
        pass

    def _action_RemoveIntent(self, tx, params=None, **kwargs):
        self.remove_intent(params['intent_name'])

    def _action_Repeat(self, tx, params=None, **kwargs):
        last_tx = self.get_last_transaction()
        reason = params.get('reason', None) if params else None
        question_only = params.get('question_only', False) if params else False

        if last_tx:
            if self.consecutive_repeat_count >= self.bot_config.max_consecutive_repeat_attempts:
                self.add_common_response_message(tx, self.bot_config, 'repeat_exhausted')
            else:
                if self.transaction_repeatable(last_tx):
                    self.repeat_transaction(tx, last_tx, reason=reason, question_only=question_only)
                else:
                    self.abort_intent(tx)
        else:
            if self.consecutive_message_count['fallback'] >= self.bot_config.max_consecutive_message_attempts:
                self.add_common_response_message(tx, self.bot_config, 'message_exhausted')
            else:
                self.add_common_response_message(tx, self.bot_config, 'fallback')

    def _action_RepeatSlot(self, *args, **kwargs):
        self.repeat_slot()

    def _action_RepeatSlotAndRemoveIntent(self, tx, params=None, **kwargs):
        self.remove_intent(params['intent_name'])
        self.repeat_slot()

    def _action_ReplaceSlot(self, tx, params=None, entity_results=None, **kwargs):
        last_tx = self.get_last_transaction()
        slots_filled = last_tx.slots_filled
        assert slots_filled, 'Trying to replace slot but no slot filled on previous transaction'
        filled_slot_names = [x.name for x in slots_filled.values()]

        for entity in entity_results:
            if entity.slot_name in filled_slot_names:
                filled_slot_result = self.fill_intent_slot_result(tx, self.active_intent,
                                                                  entity.slot_name, entity.value)
                filled_slot = self.get_intent_slot(self.active_intent.name, filled_slot_result.name)
                if filled_slot.follow_up:
                    follow_up = filled_slot.follow_up
                    dbg('Adding follow-up %s during REPLACE_SLOT' % follow_up.name)
                    question, prompt = self.get_question_and_prompt(tx, follow_up)
                    if not question:
                        # Can happen if we've exhausted the question/slot and the intent is aborted
                        return
                    self.add_response_message(tx, '%s:%s' % (self.active_intent.name, question.name), prompt,
                                              expected_entities=question.entity_actions,
                                              expected_intents=question.intent_actions)

    def _action_TriggerIntent(self, tx, params=None, **kwargs):
        intent_name = params['intent_name']
        self.prepend_pending_intent(intent_name)

    def _action_Why(self, tx, **kwargs):
        self.add_help_or_why_message(tx, 'why')

    def do_action(self, tx, action, entity_results=None, intent_results=None, skip_common_messages=False):
        dbg('Do action %s' % action)
        func_name = '_action_' + action.name
        func = getattr(self, func_name, None)
        assert func and callable(func), 'Unsupported action: %s' % action.name
        func(tx, params=action.params,
             entity_results=entity_results,
             intent_results=intent_results,
             skip_common_messages=skip_common_messages)

    def understand(self, tx, input):
        last_tx = self.get_last_transaction()

        if input.type == 'intent':
            intent_prediction = TriggeredIntentPrediction(self.bot_config, input.value)
        elif input.type == 'text':
            intent_prediction = self.nlu.process_query(input.value, last_tx=last_tx)
        else:
            assert False, 'Invalid input: %s' % input

        if input.context:
            dbg('Adding context to intent_prediction: %s' % input.context)
            intent_prediction.add_entity_results_from_context(input.context)
            self.context.update(input.context)

        dbg(vars(intent_prediction))
        tx.intent_prediction = intent_prediction
        return intent_prediction

    def get_last_transaction(self):
        # Assumes current transaction already added
        txs = list(self.transactions.values())
        if len(txs) < 2:
            return None
        return txs[-2]

    def get_last_transaction_with_slot(self, intent=None):
        # Assumes current transaction already added
        txs = list(self.transactions.values())
        if len(txs) < 2:
            return None
        for i, tx in enumerate(reversed(txs)):
            if i == 0:
                continue
            if intent and tx.active_intent_name != intent.name:
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
        if question_attempts < self.bot_config.max_question_attempts:
            return True
        return False

    def repeat_transaction(self, tx, last_tx, reason=None, question_only=False):
        repeat_tx = last_tx
        if last_tx.repeat_id:
            repeat_tx = self.transactions[last_tx.repeat_id]

        if repeat_tx.question:
            assert self.add_question_attempt(self.active_intent, repeat_tx.question),\
                'Unable to repeat transaction. Question exhausted: %s' % repeat_tx.question

        tx.copy_data_for_repeat_transaction(repeat_tx, reason=reason)
        if question_only:
            assert tx.question, 'Last TX does not have a question to repeat'
            tx.clear_response_messages()
            prompt = tx.question.get_prompt()
            self.add_response_message(tx, '%s:%s' % (self.active_intent.name, tx.question.name), prompt,
                                      expected_entities=tx.question.entity_actions,
                                      expected_intents=tx.question.intent_actions)

    def get_intent_slot_results(self, intent_name):
        return self.intent_slot_results[intent_name]

    def get_intent_slot_result(self, intent_name, slot_name):
        return self.intent_slot_results[intent_name][slot_name]

    def get_intent_slot(self, intent_name, slot_name):
        '''Gets the definition from the intent config, not the slot result'''
        return self.get_intent(intent_name).slots[slot_name]

    def get_fulfillment_data(self, tx, intent_name):
        intent = self.get_intent(intent_name)
        slot_data = self.get_intent_slot_results(intent_name)
        return intent.get_fulfillment_data(self, tx, slot_data)

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
        if attempts > self.bot_config.max_question_attempts:
            return False
        self.question_attempts[intent.name][question.name] = attempts
        return True

    def clear_question_attempts(self, intent):
        self.question_attempts[intent.name] = 0

    def clear_filled_slot_result(self, intent, slot_result):
        current_value = self.intent_slot_results[intent.name][slot_result.name].value
        if not current_value:
            warn('Slot %s on intent %s is not filled' % (slot_result, intent))
        self.intent_slot_results[intent.name][slot_result.name].value = None
        dbg('Clearing filled slot %s for intent %s' % (slot_result.name, intent.name))

    def repeat_slot(self):
        '''This is meant to repeat the last *filled* slot'''
        last_tx = self.get_last_transaction_with_slot(intent=self.active_intent)
        assert last_tx, 'Trying to repeat slot but there is no last transaction with a slot'
        assert last_tx.slots_filled, 'Trying to repeat slot but no slot filled on previous transaction'
        for slot_result in last_tx.slots_filled.values():
            self.clear_filled_slot_result(self.active_intent, slot_result)

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
        tx.abort_intent(self.active_intent.name)
        self.add_common_response_message(tx, self.bot_config, 'intent_aborted')
        self.clear_question_attempts(self.active_intent)
        self.remove_active_intent()

    def cancel_intent(self, tx):
        if not self.active_intent:
            error('Trying to cancel intent but no intent is active on conversation')
            return
        tx.cancel_intent(self.active_intent.name)
        self.clear_question_attempts(self.active_intent)
        self.remove_active_intent()

    def get_remaining_intent_slots(self, intent):
        return MessageGroup([(k, self.get_intent_slot(intent.name, k))
                             for k,v in self.get_intent_slot_results(intent.name).items() if v.value is None])

    def get_completed_intent_slots(self, intent):
        return MessageGroup([(k, self.get_intent_slot(intent.name, k))
                             for k,v in self.get_intent_slot_results(intent.name).items() if v.value is not None])

    def get_filled_slot_results(self):
        filled_slots = {}
        for intent_name, slot_results in self.intent_slot_results.items():
            for slot_name, slot_result in slot_results.items():
                if slot_result.value is not None:
                    filled_slots.setdefault(slot_name, MessageGroup())[intent_name] = slot_result
        return filled_slots

    def get_filled_slot_results_by_intent(self, intent):
        '''returns a simple map of slot names to values for this intent'''
        filled_slots = {}
        for slot_name, slot_result in self.get_intent_slot_results(intent.name).items():
            if slot_result.value is not None:
                filled_slots[slot_name] = slot_result
        return filled_slots

    def get_filled_slot_results_by_name(self, slot_name):
        filled_slots = self.get_filled_slot_results()
        return filled_slots.get(slot_name, MessageGroup())

    def fill_intent_slot_result(self, tx, intent, slot_name, value):
        dbg('Filling slot %s for intent %s' % (slot_name, intent.name))
        slot_result = self.get_intent_slot_result(intent.name, slot_name)
        slot_result.value = value
        tx.add_filled_slot(intent, slot_result)
        return slot_result

    def fill_intent_slot_results_with_entity_results(self, tx, intent, entity_results):
        '''This can return slots and/or follow ups for filled slots'''
        remaining_questions = self.get_remaining_intent_slots(intent)
        if not entity_results:
            return remaining_questions

        follow_up_added = False
        if remaining_questions:
            for entity in entity_results:
                if entity.slot_name in remaining_questions:
                    slot = self.get_intent_slot(intent.name, entity.slot_name)
                    if slot.follow_up and follow_up_added:
                        warn('Not filling slot %s with additional follow-up %s' %
                             (entity.slot_name, slot.follow_up.name))
                        continue

                    filled_slot_result = self.fill_intent_slot_result(tx, intent, entity.slot_name, entity.value)
                    filled_slot = self.get_intent_slot(intent.name, filled_slot_result.name)
                    del remaining_questions[entity.slot_name]
                    if filled_slot.follow_up and not entity.from_context:
                        fu = filled_slot.follow_up
                        dbg('Adding follow-up %s' % fu.name)
                        remaining_questions.prepend(fu.name, fu)
                        follow_up_added = True

            if not remaining_questions:
                dbg('All slots filled by existing slot data')
                if intent.name == self.active_intent.name:
                    self.active_intent_completed(tx)
                else:
                    self.add_completed_intent(tx, intent.name)

        return remaining_questions

    def fill_intent_slot_results_with_filled_slots(self, tx, intent):
        '''Only returns remaining slots, no follow-ups, as it's assumed if a slot was
        filled and needed a follow-up that would have already happened.'''
        remaining_slots = self.get_remaining_intent_slots(intent)
        if remaining_slots:
            for slot_name, slot in remaining_slots.items():
                if not slot.autofill:
                    continue

                filled_slots = self.get_filled_slot_results_by_name(slot_name)
                if not any(filled_slots.values()):
                    # This slot has no values, so we cant fill anything. Move on.
                    continue

                # TODO: this currently takes the first slot with that name, regardless of intent
                # This slot has already been filled. Reuse its value.
                slot_result_copy = list(filled_slots.values())[0].copy()
                self.fill_intent_slot_result(tx, intent, slot_result_copy.name, slot_result_copy.value)

            remaining_slots = self.get_remaining_intent_slots(intent)
            if not remaining_slots:
                dbg('All slots filled by existing slot data')
                if intent == self.active_intent:
                    self.active_intent_completed(tx)
                else:
                    self.add_completed_intent(tx, intent.name)

        return remaining_slots

    def add_intent_slots(self, intent):
        if intent.name not in self.intent_slot_results:
            self.intent_slot_results[intent.name] = intent.get_slot_results_container()
        elif intent.slots:
            warn('Slots already present for intent %s' % intent.name)

    def is_answered(self, tx, entity_results, intent_results, input):
        is_answered, action = tx.is_answered(entity_results, intent_results, input)
        if not is_answered:
            for intent_result in intent_results:
                intent = self.get_intent(intent_result.name)
                if intent.is_app_intent:
                    dbg('App intent %s found in answer' % intent.name)
                    if tx.active_intent_name and not tx.active_intent_name == tx.completed_intent_name:
                        return True, Action({'name': Actions.ConfirmSwitchIntent,
                                             'params':{'intent_name': intent.name}})
                    return True, Action(Actions.NoAction)
            dbg('Transaction went unanswered')
        return is_answered, action

    def add_pending_intent(self, intent_name):
        intent = self.get_intent(intent_name)
        dbg('Adding pending intent %s' % intent.name)
        self.add_intent_slots(intent)
        self.pending_intents[intent.name] = True

    def prepend_pending_intent(self, intent_name):
        intent = self.get_intent(intent_name)
        dbg('Prepending pending intent %s' % intent.name)
        self.add_intent_slots(intent)
        self.pending_intents.prepend(intent.name, True)

    def remove_pending_intent(self, intent_name):
        intent = self.get_intent(intent_name)
        if intent.name in self.pending_intents:
            del self.pending_intents[intent.name]

    def remove_active_intent(self):
        if not self.active_intent:
            return
        self.remove_pending_intent(self.active_intent.name)
        self.active_intent = None

    def remove_intent(self, intent_name):
        self.remove_pending_intent(intent_name)
        if self.active_intent and self.active_intent.name == intent_name:
            self.active_intent = None

    def add_completed_intent(self, tx, intent_name):
        intent = self.get_intent(intent_name)
        dbg('Intent %s completed' % intent.name)
        try:
            response = intent.fulfill(self, tx, self.get_intent_slot_results(intent.name))
            if response:
                if not response.success():
                    warn('Fulfillment did not succeed! Reason: %s' % response.status_reason)
                if response.message:
                    dbg('Adding fulfillment response %s' % response)
                    self.add_response_message_object(tx, response.message)
                if response.action:
                    self.do_action(tx, response.action, skip_common_messages=bool(response.message))
        finally:
            self.completed_intents[intent.name] = True
            self.remove_intent(intent.name)
            tx.completed_intent_name = intent.name

    def active_intent_completed(self, tx):
        if self.active_intent:
            self.add_completed_intent(tx, self.active_intent.name)
            self.active_intent = None

    def remove_completed_intent(self, intent_name):
        assert False, 'Probably shouldnt allow this'
        if intent_name in self.completed_intents:
            del self.completed_intents[intent_name]

    def get_message_context(self):
        context = {}
        if self.active_intent:
            slot_results = self.get_filled_slot_results_by_intent(self.active_intent)
            context = {x.name:x.value for x in slot_results.values()}
        return context

    def add_new_intent_message(self, tx, intent, response_type=None, entity_results=None):
        '''Gets the message(s) at the start of a new intent'''
        if not response_type:
            response_type = ResponseTypes.Active
        response = intent.responses.get_response(response_type)
        if not response:
            warn('No response for intent %s' % intent)

        prompt = ''
        if response_type in [ResponseTypes.Active, ResponseTypes.Resumed] and self.get_intent_slot_results(intent.name):
            remaining_questions = self.fill_intent_slot_results_with_entity_results(tx, intent, entity_results)
            if not remaining_questions:
                return # The intent was satisfied by data in collected entities
            assert not any([isinstance(x, FollowUp) for x in remaining_questions]),\
                'FollowUp found while adding message for new intent: %s' % remaining_questions

            remaining_questions = self.fill_intent_slot_results_with_filled_slots(tx, intent)
            if not remaining_questions:
                return # The intent was satisfied by existing slot data
            assert not any([isinstance(x, FollowUp) for x in remaining_questions]),\
                'FollowUp found while adding message for new intent: %s' % remaining_questions

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

    def get_common_message(self, bot_config, message_name):
        return bot_config.common_messages[message_name]

    def add_common_response_message(self, tx, bot_config, message_name, prepend=False):
        message = self.get_common_message(bot_config, message_name)
        message_text = None
        expected_entities = None
        expected_intents = None

        if not message:
            pass
        else:
            message_text = message.get_prompt()
            if isinstance(message, Question):
                expected_entities = message.entity_actions
                expected_intents = message.intent_actions
            if message.action:
                self.do_action(tx, message.action, skip_common_messages=bool(message_text))

        self.add_response_message(tx, message_name, message_text, expected_entities=expected_entities,
                                  expected_intents=expected_intents, prepend=prepend)

    # Yea, I don't know what else to call this right now.
    def add_help_or_why_message(self, tx, message_type):
        last_tx = self.get_last_transaction()
        if last_tx and last_tx.question and getattr(last_tx.question, message_type):
            msg = getattr(last_tx.question, 'get_%s' % message_type)()
            msg_name = '%s:%s' % (last_tx.question.name, message_type)
            if self.consecutive_message_count[msg_name] >= self.bot_config.max_consecutive_message_attempts:
                self.add_common_response_message(tx, self.bot_config, 'message_exhausted')
                return
            self.add_response_message(tx, msg_name, msg)
        elif self.active_intent and getattr(self.active_intent, message_type):
            msg = getattr(self.active_intent, 'get_%s' % message_type)()
            msg_name = '%s:%s' % (self.active_intent.name, message_type)
            if self.consecutive_message_count[msg_name] >= self.bot_config.max_consecutive_message_attempts:
                self.add_common_response_message(tx, self.bot_config, 'message_exhausted')
                return
            self.add_response_message(tx, msg_name, msg)
        else:
            if self.consecutive_message_count[message_type] >= self.bot_config.max_consecutive_message_attempts:
                self.add_common_response_message(tx, self.bot_config, 'message_exhausted')
                return
            self.add_common_response_message(tx, self.bot_config, message_type)

    def create_response_message(self, tx, intent_results, entity_results):
        last_tx = self.get_last_transaction()
        greeted = False
        common_intent_handled = False

        # Analyze new intents
        for i, intent_result in enumerate(intent_results):
            intent = self.get_intent(intent_result.name)
            if intent.is_common_intent:
                if common_intent_handled:
                    # Only allow a single common intent to be counted per transaction.
                    # The assumption is that there would never be a scenario where two
                    # common intents should be valid, so we just take the top one.
                    dbg('Skipping additional common intent: %s' % intent)
                    continue
                common_intent_handled = True

            if intent.is_greeting:
                if i > 0:
                    warn('greetings only allowed as top intent, skipping %s' % intent.name)
                    continue
                if last_tx:
                    warn('greetings only allowed on first transaction, skipping %s' % intent.name)
                    continue

            if intent.name in self.pending_intents and not intent.is_repeatable:
                warn('intent %s already pending' % intent.name)
                continue

            if intent.name in self.completed_intents and not intent.is_repeatable:
                warn('intent %s already completed' % intent.name)

            if intent.is_preemptive:
                self.prepend_pending_intent(intent.name)
                tx.prepend_new_intent(intent.name)

                if intent.name == CommonIntents.Cancel:
                    dbg('Cancel Intent')
                    self.do_action(tx, Action('ConfirmCancelIntent'))
                    return

                if intent.name == CommonIntents.Repeat:
                    dbg('Repeat')
                    self.do_action(tx, Action({'name':'Repeat', 'params': {'reason':'user request'}}))
                    return

                if intent.name == CommonIntents.Help:
                    dbg('Help Intent')
                    self.do_action(tx, Action('Help'))
                    if 'message_exhaused' in tx.response_messages:
                        return

                if intent.name == CommonIntents.Why:
                    dbg('Why Intent')
                    self.do_action(tx, Action('Why'))
                    if 'message_exhaused' in tx.response_messages:
                        return

                if intent.is_smalltalk:
                    if i > 0:
                        warn('smalltalk only allowed as top intent, skipping %s' % intent.name)
                        continue

                    if not self.bot_config.smalltalk:
                        # TODO: should bot say "i dont support smalltalk?"
                        warn('smalltalk disabled, skipping intent: %s' % intent.name)
                        continue

                    self.add_new_intent_message(tx, intent, response_type=ResponseTypes.Active,
                                                entity_results=entity_results)

            else:
                if intent.is_greeting:
                    greeted = True
                self.add_pending_intent(intent.name)
                tx.add_new_intent(intent.name)

        if (not greeted) and (not last_tx):
            dbg('Adding greeting on first transaction')
            self.add_common_response_message(tx, self.bot_config, 'greeting', prepend=True)
            if not intent_results:
                # It's the first message and we didn't recognize the intent of the user
                self.add_common_response_message(tx, self.bot_config, 'initial_prompt')
                return

        # Handle questions that require answers
        if last_tx and last_tx.requires_answer():
            is_answered, action = self.is_answered(last_tx, entity_results, intent_results, tx.input)
            if is_answered:
                self.do_action(tx, action, entity_results=entity_results, intent_results=intent_results)
                if self.completed or tx.response_messages:
                    return
            else:
                if self.transaction_repeatable(last_tx):
                    # repeat_transaction will overwrite messages, so copy these first
                    tx_messages = copy.deepcopy(tx.response_messages)
                    tx_requires_answer = tx.requires_answer()
                    self.repeat_transaction(tx, last_tx, reason='last transaction not answered', question_only=True)
                    if tx_messages:
                        # Messages were already added to this tx by some previously processed intent.
                        # The assertion below ensures that we arent asking two questions at once. That's
                        # not allowed, but we probably need a better way to handle this situation since this
                        # may be an easy trap for the bot designer to fall into.
                        assert not tx_requires_answer,\
                            'A question was asked while another unanswered question is in progress'
                        for msg_name, msg in reversed(list(tx_messages.items())):
                            self.add_response_message(tx, msg_name, msg, prepend=True)
                    else:
                        self.add_common_response_message(tx, self.bot_config, 'unanswered', prepend=True)
                else:
                    self.abort_intent(tx)
                return

        # All one-off and preemptive intents should have been handled before this
        for intent_name in list(self.pending_intents.keys()):
            intent = self.get_intent(intent_name)
            if intent.is_answer:
                dbg('Removing is_answer intent from active list: %s' % intent.name)
                self.remove_pending_intent(intent.name)
            elif intent.is_preemptive and not intent.slots:
                # We assume these already displayed any relevant message
                dbg('Removing preemptive intent with no slots from active list: %s' % intent.name)
                self.remove_pending_intent(intent.name)

        # Handle ongoing intent
        if self.active_intent:
            tx.active_intent_name = self.active_intent.name
            dbg('Active intent %s' % self.active_intent.name)
            remaining_questions = self.fill_intent_slot_results_with_entity_results(tx, self.active_intent,
                                                                                    entity_results)
            if remaining_questions:
                question, prompt = self.get_next_question_and_prompt(tx, remaining_questions)
                if not question:
                    # Can happen if we've exhausted the question/slot and the intent is aborted
                    return

                self.add_response_message(tx, '%s:%s' % (self.active_intent.name, question.name), prompt,
                                          expected_entities=question.entity_actions,
                                          expected_intents=question.intent_actions)
                return

            self.active_intent_completed(tx)

        # We've handled any one-off or active intents, move on to other pending intents
        for i, intent_name in enumerate(self.pending_intents.keys()):
            intent = self.get_intent(intent_name)
            message = None
            if i == 0:
                self.active_intent = intent
                tx.active_intent_name = intent.name
                response_type = ResponseTypes.Active
                if tx.completed_intent_name and (intent.name not in tx.new_intents):
                    # The user completed an intent with their most recent response
                    # but they have already queued up other intents from previous transactions
                    response_type = ResponseTypes.Resumed
            else:
                response_type = ResponseTypes.Deferred

            dbg('Handling %s intent: %s' % (response_type, intent.name))
            self.add_new_intent_message(tx, intent, response_type=response_type, entity_results=entity_results)

        if not tx.response_messages:
            if tx.completed_intent_name or (self.completed_intents and not self.pending_intents):
                self.add_common_response_message(tx, self.bot_config, 'intents_complete')
            else:
                if self.consecutive_message_count['fallback'] >= self.bot_config.max_consecutive_message_attempts:
                    self.add_common_response_message(tx, self.bot_config, 'message_exhausted')
                else:
                    self.add_common_response_message(tx, self.bot_config, 'fallback')

    def process_intent_prediction(self, tx, intent_prediction):
        intent_results, entity_results = intent_prediction.get_valid(
            intent_threshold=self.bot_config.intent_filter_threshold,
            entity_threshold=self.bot_config.entity_filter_threshold
        )
        if not intent_results:
            warn('no valid intent results found')
        if self.bot_config.new_intent_limit:
            dbg('Limiting processing to top %s intent(s)' % self.bot_config.new_intent_limit)
            intent_results = intent_results[:self.bot_config.new_intent_limit]
        self.create_response_message(tx, intent_results, entity_results)

    def create_transaction(self):
        tx = Transaction(self.id)
        self.transactions[tx.id] = tx
        return tx

    def reply(self, tx, input):
        input = Input(input)
        tx.input = input

        if input.type == 'action':
            self.do_action(tx, Action(input.value))
        else:
            intent_prediction = self.understand(tx, input)
            self.process_intent_prediction(tx, intent_prediction)

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
