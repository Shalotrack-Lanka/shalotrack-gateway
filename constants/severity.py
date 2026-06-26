"""
==========================================================
ShaloTrack Gateway

File:
    constants/severity.py

Purpose:
    Defines event severity levels.
==========================================================
"""

from enum import IntEnum


class Severity(IntEnum):

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4