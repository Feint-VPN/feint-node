"""Route rollback must preserve users and unrelated changes made after cutover."""

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "node/src"), str(ROOT / "scripts")]
from adapters.xray_socks import socks_outbound
from cutover_interconnect import replace_route, retained_handler
from service_contract import configuration


class CutoverContract(unittest.TestCase):
    def test_retained_handler_requires_same_core_route_peer_and_live_generation(self):
        old = {"tag": "outbound:selected", "server": "127.0.0.1", "server_port": 1234}
        native = socks_outbound(
            old["tag"], old | {"username": "ops", "password": "test"}
        )
        journal = {
            "phase": "rolled-back",
            "core_identity": ["core", "start"],
            "route": old["tag"],
            "canonical_before": old,
            "runtime_before": {"tag": old["tag"]},
        }
        prepared = {"outbounds": [native]}
        live = {old["tag"], native["tag"]}
        self.assertEqual(
            retained_handler(journal, ("core", "start"), old, native, prepared, live),
            native,
        )
        for proof, identity, route, candidate, handlers in (
            (journal, ("other", "start"), old, native, live),
            (journal | {"phase": "prepared"}, ("core", "start"), old, native, live),
            (journal, ("core", "start"), old | {"server_port": 2}, native, live),
            (journal, ("core", "start"), old, native | {"settings": {}}, live),
            (journal, ("core", "start"), old, native, {old["tag"]}),
            (journal, ("core", "start"), old, native, {native["tag"]}),
        ):
            with (
                self.subTest(identity=identity, route=route, handlers=handlers),
                self.assertRaises(ValueError),
            ):
                retained_handler(proof, identity, route, candidate, prepared, handlers)

    def test_apply_and_rollback_preserve_latest_users_and_other_routes(self):
        canonical = configuration().model_dump(exclude_none=True)
        old = {
            "type": "socks",
            "tag": "outbound:selected",
            "server": "127.0.0.1",
            "server_port": 48265,
        }
        other = {
            "type": "socks",
            "tag": "outbound:other",
            "server": "127.0.0.1",
            "server_port": 39183,
        }
        canonical["outbounds"] = [old, other]
        canonical["route"]["rules"] = [{"outbound": old["tag"], "auth_user": ["alice"]}]
        runtime = {
            "inbounds": [{"users": ["alice"]}],
            "outbounds": [socks_outbound(o["tag"], o) for o in (old, other)],
            "routing": {"rules": [{"outboundTag": old["tag"], "user": ["alice"]}]},
        }
        snapshot = copy.deepcopy((canonical, runtime))
        native = old | {
            "server": "100.88.69.67",
            "server_port": 55181,
            "username": "technical",
            "password": "test-only",
        }
        desired, after = replace_route(canonical, runtime, old["tag"], native)
        self.assertEqual((canonical, runtime), snapshot)
        self.assertEqual(after["inbounds"], runtime["inbounds"])
        self.assertEqual(after["outbounds"][1], runtime["outbounds"][1])
        self.assertIn(".native-", after["routing"]["rules"][0]["outboundTag"])
        desired["route"]["rules"][0]["auth_user"].append("bob")
        after["routing"]["rules"][0]["user"].append("bob")
        after["inbounds"][0]["users"].append("bob")
        restored, rendered = replace_route(desired, after, old["tag"], old)
        self.assertEqual(restored["outbounds"][0], old)
        self.assertEqual(restored["route"]["rules"][0]["auth_user"], ["alice", "bob"])
        self.assertEqual(
            rendered["routing"]["rules"][0],
            {"outboundTag": old["tag"], "user": ["alice", "bob"]},
        )
        self.assertEqual(rendered["inbounds"][0]["users"], ["alice", "bob"])

    def test_ambiguous_active_handlers_are_rejected_without_mutation(self):
        canonical = configuration().model_dump(exclude_none=True)
        old = {
            "type": "socks",
            "tag": "outbound:selected",
            "server": "127.0.0.1",
            "server_port": 48265,
        }
        canonical["outbounds"] = [old]
        handler = socks_outbound(old["tag"], old)
        runtime = {
            "outbounds": [handler, handler | {"tag": old["tag"] + ".native-old"}]
        }
        snapshot = copy.deepcopy((canonical, runtime))
        with self.assertRaises(ValueError):
            replace_route(canonical, runtime, old["tag"], old)
        self.assertEqual((canonical, runtime), snapshot)


if __name__ == "__main__":
    unittest.main()
