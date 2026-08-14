"""Domain exceptions."""


class UserAlreadyExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class InboundNotFoundError(Exception):
    pass


class ConfigSaveError(Exception):
    pass


class SingBoxReloadError(Exception):
    pass
