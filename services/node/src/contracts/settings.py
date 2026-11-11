"""Persistence for the operator-editable subscription label."""

from abc import ABC, abstractmethod


class SubscriptionSettings(ABC):
    @abstractmethod
    def save_template(self, template: str) -> None: ...
