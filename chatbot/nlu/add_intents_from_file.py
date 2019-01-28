#!/usr/bin/env python

from chatbot import app
from chatbot.configs import *
from chatbot.utils import *

load_bot_configs(app.config)

@climax.command(parents=[cli])
@climax.argument('version', type=str, help='Bot version to load changes to')
@climax.argument('bot', type=str, help='Name of bot to load to')
@climax.argument('f', type=str, help='JSON file to parse for intents')
@climax.argument('--train', action='store_true', help='Perform model training', default=False)
@climax.argument('--publish', action='store_true', help='Publish model after training', default=False)
def main(f, bot, version, train, publish, dry_run, force):
    schema = IntentConfigFileSchema()
    intent_configs = parse_schema_file(f, schema)

    bot_config = get_bot_config(bot)
    nlu = get_nlu(bot_config)

    versions = {x['version']:x for x in nlu.get_application_versions()}

    if version not in versions:
        print 'Version "%s" is not in current versions. A new version will be created' % version
        if not force:
            answer = prompt_user('Are you sure you want to create a new app version?', ['y', 'n'])
            if answer == 'n':
                print 'Exiting.'
                return

        nlu.clone_current_version(version)
    else:
        if not force:
            answer = prompt_user('Are you sure you want to add intents to existing version "%s"?' % version, ['y', 'n'])
            if answer == 'n':
                print 'Exiting.'
                return

    current_intents = {x['name']:x for x in nlu.get_intents(app_version=version)}

    for intent_name, intent_data in bot_config.intent_configs.iteritems():
        dbg('---- Processing intent "%s"' % intent_name)

        intent = current_intents.get(intent_name, None)
        if not intent:
            intent = nlu.add_intent(intent_name, app_version=version)

        if intent_data.utterances:
            current_utterances = {x['text'].lower():x for x in nlu.get_utterances(intent, app_version=version)}
            for utterance in intent_data.utterances:
                if utterance.lower() not in current_utterances:
                    nlu.add_utterance(intent, utterance, app_version=version)

    if train or publish:
        nlu.train(app_version=version)
    if publish:
        nlu.publish(app_version=version, is_staging=True)
        nlu.publish(app_version=version, is_staging=False)

if __name__ == '__main__':
    main()
