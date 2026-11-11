"""Raw cumulative counters grouped by their independent runtime source."""

UserCounters = dict[str, dict[str, int]]
TrafficSources = list[tuple[str, UserCounters]]
