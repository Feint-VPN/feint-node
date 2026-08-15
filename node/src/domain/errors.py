"""Domain exceptions."""


class UserAlreadyExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class InboundNotFoundError(Exception):
    pass


class ConfigRollbackError(Exception):
    pass


class SingBoxReloadError(Exception):
    pass
