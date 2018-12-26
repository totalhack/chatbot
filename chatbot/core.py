class CommonIntents(object):
    Cancel = 'Cancel'
    ConfirmYes = 'ConfirmYes'
    ConfirmNo = 'ConfirmNo'
    Help = 'Help'
    NoIntent = 'None' # TODO: for NLUs to convert intent names to canon values
    Repeat = 'Repeat'
    Welcome = 'Welcome'

class ResponseType(object):
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
