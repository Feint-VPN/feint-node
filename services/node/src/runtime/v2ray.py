"""V2Ray-compatible gRPC counters and owned channel lifecycle."""

import grpc
from contracts.statistics import CounterSource
from exceptions import StatisticsUnavailableError
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from models.traffic import TrafficSources


def _normalize_v2ray_api_address(address: str) -> str:
    normalized = (address or "127.0.0.1:10085").strip().rstrip("/")
    for prefix in ("grpc://", "http://", "https://"):
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def _build_v2ray_stats_messages() -> tuple[type, type]:
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "v2ray_stats.proto"
    file_proto.package = "v2ray.core.app.stats.command"
    file_proto.syntax = "proto3"

    query_request = file_proto.message_type.add()
    query_request.name = "QueryStatsRequest"

    field = query_request.field.add()
    field.name = "pattern"
    field.number = 1
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    field = query_request.field.add()
    field.name = "reset"
    field.number = 2
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_BOOL

    field = query_request.field.add()
    field.name = "patterns"
    field.number = 3
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    field = query_request.field.add()
    field.name = "regexp"
    field.number = 4
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_BOOL

    stat = file_proto.message_type.add()
    stat.name = "Stat"

    field = stat.field.add()
    field.name = "name"
    field.number = 1
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    field = stat.field.add()
    field.name = "value"
    field.number = 2
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_INT64

    query_response = file_proto.message_type.add()
    query_response.name = "QueryStatsResponse"

    field = query_response.field.add()
    field.name = "stat"
    field.number = 1
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    field.type_name = ".v2ray.core.app.stats.command.Stat"

    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_proto)

    return (
        message_factory.GetMessageClass(
            pool.FindMessageTypeByName("v2ray.core.app.stats.command.QueryStatsRequest")
        ),
        message_factory.GetMessageClass(
            pool.FindMessageTypeByName(
                "v2ray.core.app.stats.command.QueryStatsResponse"
            )
        ),
    )


_QUERY_STATS_REQUEST, _QUERY_STATS_RESPONSE = _build_v2ray_stats_messages()


def _parse_user_stat_name(name: str) -> tuple[str | None, str | None]:
    prefix = "user>>>"
    uplink_suffix = ">>>traffic>>>uplink"
    downlink_suffix = ">>>traffic>>>downlink"

    if not name.startswith(prefix):
        return None, None
    if name.endswith(uplink_suffix):
        return name[len(prefix) : -len(uplink_suffix)], "upload"
    if name.endswith(downlink_suffix):
        return name[len(prefix) : -len(downlink_suffix)], "download"
    return None, None


async def fetch_v2ray_user_counters(
    address: str,
    request_timeout: float,
    channel: grpc.aio.Channel | None = None,
) -> dict[str, dict[str, int]]:
    if channel is None:
        async with grpc.aio.insecure_channel(
            _normalize_v2ray_api_address(address)
        ) as owned:
            return await fetch_v2ray_user_counters(address, request_timeout, owned)
    request = _QUERY_STATS_REQUEST()
    request.patterns.append("user>>>")
    request.regexp = False
    request.reset = False

    query_stats = channel.unary_unary(
        "/v2ray.core.app.stats.command.StatsService/QueryStats",
        request_serializer=_QUERY_STATS_REQUEST.SerializeToString,
        response_deserializer=_QUERY_STATS_RESPONSE.FromString,
    )
    response = await query_stats(request, timeout=request_timeout)

    counters: dict[str, dict[str, int]] = {}
    for stat in response.stat:
        username, direction = _parse_user_stat_name(stat.name)
        if not username or not direction:
            continue
        entry = counters.setdefault(username, {"upload": 0, "download": 0})
        entry[direction] = int(stat.value)

    return counters


class V2RayCounterSource(CounterSource):
    def __init__(
        self,
        address: str,
        additional_addresses: tuple[str, ...] = (),
        timeout: float = 5,
    ) -> None:
        self.addresses = tuple(
            dict.fromkeys(
                _normalize_v2ray_api_address(a)
                for a in (address, *additional_addresses)
            )
        )
        self.timeout = timeout
        self.channels: dict[str, grpc.aio.Channel] = {}

    async def start(self) -> None:
        await self.close()
        self.channels = {a: grpc.aio.insecure_channel(a) for a in self.addresses}

    async def close(self) -> None:
        for channel in self.channels.values():
            await channel.close()
        self.channels.clear()

    async def fetch_primary(self) -> dict[str, dict[str, int]]:
        address = self.addresses[0]
        return await fetch_v2ray_user_counters(
            address, self.timeout, self.channels.get(address)
        )

    async def read(self) -> TrafficSources:
        try:
            primary = await self.fetch_primary()
            values = [(self.addresses[0], primary)]
            for address in self.addresses[1:]:
                values.append(
                    (
                        address,
                        await fetch_v2ray_user_counters(
                            address, self.timeout, self.channels.get(address)
                        ),
                    )
                )
            return values
        except grpc.RpcError as error:
            raise StatisticsUnavailableError(str(error)) from error
