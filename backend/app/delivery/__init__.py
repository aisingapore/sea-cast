"""Delivery: getting a session's captions to its audience.

- ``rooms``: rooms (one per speaker session), their replay history and
  listeners, and the audience join tokens.
- ``captions``: the hub that fans each caption out to every audience socket.

Both keep their state in this process's memory, behind the ``RoomStore`` and
``CaptionHub`` interfaces.
"""
