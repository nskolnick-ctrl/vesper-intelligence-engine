"""Exceptions raised by the VIE output layer."""


class ReportWriteError(Exception):
    """Raised when a report cannot be written to disk.

    Covers a missing permission, a full disk, or an output path that is a
    file rather than a directory. The message names the path.
    """
