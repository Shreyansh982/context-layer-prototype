"""
Date and Time Tool

Returns the current date and time.
"""
from datetime import datetime

class DateTimeTool:
    """
    Provides the current system date and time.
    """
    def now(self) -> str:
        """
        Return the current date and time.
        """
        return datetime.now().strftime(
            "%d %B %Y, %I:%M:%S %p"
        )

datetime_tool = DateTimeTool()