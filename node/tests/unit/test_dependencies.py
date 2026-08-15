"""Dependency lifecycle contracts."""

import api.depends.initialization as initialization_dependencies
import api.depends.statistics as statistics_dependencies
import api.depends.telemetry as telemetry_dependencies
import api.depends.user as user_dependencies


def test_user_service_is_shared_between_requests(monkeypatch) -> None:
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.setattr(user_dependencies, "user_service", None)

    assert user_dependencies.get_user_service() is user_dependencies.get_user_service()


def test_other_stateful_dependencies_are_shared(monkeypatch) -> None:
    monkeypatch.setattr(initialization_dependencies, "init_service", None)
    monkeypatch.setattr(telemetry_dependencies, "node_telemetry_service", None)
    monkeypatch.setattr(statistics_dependencies, "stats_backend", None)
    monkeypatch.setattr(statistics_dependencies, "traffic_tracker", None)

    getters = (
        initialization_dependencies.get_init_service,
        telemetry_dependencies.get_node_telemetry_service,
        statistics_dependencies.get_stats_backend,
        statistics_dependencies.get_traffic_tracker,
    )
    for get_dependency in getters:
        assert get_dependency() is get_dependency()
