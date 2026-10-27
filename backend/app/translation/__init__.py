"""Translation module: the LLM translators, their prompts, and the glossary.

- ``llm``: ``LLMTranslator``, for a session with one target language.
- ``fanout``: ``FanOutTranslator``, for a room or several target languages.
- ``prompts``: the system prompt and chat messages both translators send.
- ``terminology``: glossary term lookup for each utterance.
"""
