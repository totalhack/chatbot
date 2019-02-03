# chatbot

> Note: This project is still in its infancy and is subject to rapid/breaking
  changes.

The goal of this project is to provide a simple but powerful way of designing
application-specific chatbots. By application-specific I mean the
conversations have a particular context and goal, such as gathering
information from a consumer or providing answers to domain-specific questions.

This project sprung out of a disappointment with the flexibility allowed by
today's popular tools such as DialogFlow and Amazon Lex. The aim is to go
beyond the features they offer in terms of controlling the flow of the
conversation. At the same time, the project aims to provide the flexibility to
rely on parts of those services. In particular, you can roll your own NLU (see
note below) and add text-to-speech capabilities as needed with whatever
service you want.

> Note: currently only LUIS is supported. I plan to add support for
  DialogFlow, Lex, and Rasa at some point, but LUIS exhibits solid performance
  and has the ability to flag multiple intents at once.

# Documentation
Documentation is quite limited at this time. Stay tuned.

# License
MIT