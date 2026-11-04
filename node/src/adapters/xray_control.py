"""Private Xray HandlerService client, including Hysteria2 accounts.

Xray's `api adu` CLI does not import Hysteria users in 26.7.28. Use the
same protobuf messages as the core instead; no credentials enter shell arguments.
"""

from typing import cast

import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory


def _messages() -> dict[str, type]:
    pool = descriptor_pool.DescriptorPool()
    schemas = {
        "xray.common.serial": {
            "TypedMessage": [("type", 1, "string"), ("value", 2, "bytes")],
        },
        "xray.common.protocol": {
            "User": [
                ("level", 1, "uint32"),
                ("email", 2, "string"),
                ("account", 3, "xray.common.serial.TypedMessage"),
            ],
        },
        "xray.proxy.hysteria.account": {"Account": [("auth", 1, "string")]},
        "xray.proxy.vless": {
            "Account": [
                ("id", 1, "string"),
                ("flow", 2, "string"),
                ("encryption", 3, "string"),
            ],
        },
        "xray.core": {"OutboundHandlerConfig": [("tag", 1, "string")]},
        "xray.app.proxyman.command": {
            "AddUserOperation": [("user", 1, "xray.common.protocol.User")],
            "RemoveUserOperation": [("email", 1, "string")],
            "AlterInboundRequest": [
                ("tag", 1, "string"),
                ("operation", 2, "xray.common.serial.TypedMessage"),
            ],
            "AlterInboundResponse": [],
            "GetInboundUserRequest": [("tag", 1, "string"), ("email", 2, "string")],
            "GetInboundUserResponse": [("users", 1, "xray.common.protocol.User")],
            "ListOutboundsRequest": [],
            "ListOutboundsResponse": [
                ("outbounds", 1, "xray.core.OutboundHandlerConfig")
            ],
            "RemoveOutboundRequest": [("tag", 1, "string")],
            "RemoveOutboundResponse": [],
        },
    }
    scalars = {"string": 9, "bytes": 12, "uint32": 13}
    names = []
    for package, messages in schemas.items():
        file = descriptor_pb2.FileDescriptorProto(
            name=package + ".proto", package=package, syntax="proto3"
        )
        dependencies = set()
        for name, fields in messages.items():
            message = file.message_type.add(name=name)
            names.append(package + "." + name)
            for field_name, number, kind in fields:
                field = message.field.add(name=field_name, number=number, label=1)
                field.type = cast(
                    descriptor_pb2.FieldDescriptorProto.Type.ValueType,
                    scalars.get(kind, 11),
                )
                if name in {"GetInboundUserResponse", "ListOutboundsResponse"}:
                    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
                if kind not in scalars:
                    field.type_name = "." + kind
                    dependency = kind.rsplit(".", 1)[0]
                    if dependency != package:
                        dependencies.add(dependency + ".proto")
        file.dependency.extend(sorted(dependencies))
        pool.Add(file)
    return {
        name: message_factory.GetMessageClass(pool.FindMessageTypeByName(name))
        for name in names
    }


MESSAGES = _messages()
COMMAND = "xray.app.proxyman.command."


class XrayControl:
    def __init__(self, address: str, timeout: float = 10) -> None:
        self.address = address
        self.timeout = timeout

    async def _alter(self, tag: str, operation) -> None:
        request_type = MESSAGES[COMMAND + "AlterInboundRequest"]
        response_type = MESSAGES[COMMAND + "AlterInboundResponse"]
        request = request_type(tag=tag)
        request.operation.type = operation.DESCRIPTOR.full_name
        request.operation.value = operation.SerializeToString()
        async with grpc.aio.insecure_channel(self.address) as channel:
            alter = channel.unary_unary(
                "/" + COMMAND + "HandlerService/AlterInbound",
                request_serializer=request_type.SerializeToString,
                response_deserializer=response_type.FromString,
            )
            await alter(request, timeout=self.timeout)

    async def add_user(self, tag: str, protocol: str, client: dict) -> None:
        account_name = {
            "hysteria": "xray.proxy.hysteria.account.Account",
            "vless": "xray.proxy.vless.Account",
        }[protocol]
        fields = ("auth",) if protocol == "hysteria" else ("id", "flow", "encryption")
        account = MESSAGES[account_name](
            **{field: client[field] for field in fields if field in client}
        )
        operation = MESSAGES[COMMAND + "AddUserOperation"]()
        operation.user.email = client["email"]
        operation.user.level = client.get("level", 0)
        operation.user.account.type = account_name
        operation.user.account.value = account.SerializeToString()
        await self._alter(tag, operation)

    async def remove_user(self, tag: str, email: str) -> None:
        await self._alter(tag, MESSAGES[COMMAND + "RemoveUserOperation"](email=email))

    async def has_user(self, tag: str, email: str) -> bool:
        request_type = MESSAGES[COMMAND + "GetInboundUserRequest"]
        response_type = MESSAGES[COMMAND + "GetInboundUserResponse"]
        async with grpc.aio.insecure_channel(self.address) as channel:
            query = channel.unary_unary(
                "/" + COMMAND + "HandlerService/GetInboundUsers",
                request_serializer=request_type.SerializeToString,
                response_deserializer=response_type.FromString,
            )
            response = await query(
                request_type(tag=tag, email=email), timeout=self.timeout
            )
        return any(user.email == email for user in response.users)

    async def outbound_tags(self) -> set[str]:
        request_type = MESSAGES[COMMAND + "ListOutboundsRequest"]
        response_type = MESSAGES[COMMAND + "ListOutboundsResponse"]
        async with grpc.aio.insecure_channel(self.address) as channel:
            query = channel.unary_unary(
                "/" + COMMAND + "HandlerService/ListOutbounds",
                request_serializer=request_type.SerializeToString,
                response_deserializer=response_type.FromString,
            )
            response = await query(request_type(), timeout=self.timeout)
        return {outbound.tag for outbound in response.outbounds}

    async def remove_outbound(self, tag: str) -> None:
        request_type = MESSAGES[COMMAND + "RemoveOutboundRequest"]
        response_type = MESSAGES[COMMAND + "RemoveOutboundResponse"]
        async with grpc.aio.insecure_channel(self.address) as channel:
            remove = channel.unary_unary(
                "/" + COMMAND + "HandlerService/RemoveOutbound",
                request_serializer=request_type.SerializeToString,
                response_deserializer=response_type.FromString,
            )
            await remove(request_type(tag=tag), timeout=self.timeout)
