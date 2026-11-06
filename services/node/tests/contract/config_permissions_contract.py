"""Keep the configured runtime reader across atomic writes and rollback."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[4] / "services" / "node" / "src")
)

from adapters.singbox_file_store import SingBoxFileStore
from domain.models import SingBoxConfig


@unittest.skipIf(os.name == "nt", "POSIX file permission contract")
class ConfigPermissionsContract(unittest.TestCase):
    def test_save_and_restore_preserve_only_explicit_shared_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.json")
            store = SingBoxFileStore(str(path), str(Path(directory, "backups")))
            config = SingBoxConfig.model_validate(
                {"log": {}, "route": {}, "inbounds": [], "outbounds": []}
            )
            asyncio.run(store.save(config))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for initial, expected in ((0o640, 0o640), (0o600, 0o600), (0o666, 0o600)):
                with self.subTest(initial=initial):
                    path.chmod(initial)
                    backup = asyncio.run(store.backup())
                    self.assertEqual(Path(backup).stat().st_mode & 0o777, 0o600)
                    asyncio.run(store.save(config))
                    self.assertEqual(path.stat().st_mode & 0o777, expected)
                    asyncio.run(store.restore(backup))
                    self.assertEqual(path.stat().st_mode & 0o777, expected)


if __name__ == "__main__":
    unittest.main()
