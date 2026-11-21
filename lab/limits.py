"""The exceptions that stop a whole run. In a module of their own so that lab.protocol and
lab.budget can both use them without importing each other."""


class UsageLimit(RuntimeError):
    """The Claude account behind the agents has run out of usage. The run must stop, not carry on."""
