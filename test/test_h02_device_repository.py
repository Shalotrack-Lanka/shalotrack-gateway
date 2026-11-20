"""Unit tests for explicit H02 wire-ID to canonical-IMEI resolution."""
import unittest
from unittest.mock import AsyncMock, patch
from repositories.device_repository import DeviceRepository


class H02IdentifierRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_wire_id_resolves_to_canonical_imei(self):
        conn = AsyncMock()
        conn.fetchrow.return_value = {"ImeiNumber": "866126037773336"}
        with (
            patch("repositories.device_repository.get_db_connection", new=AsyncMock(return_value=conn)),
            patch("repositories.device_repository.release_db_connection", new=AsyncMock()) as release,
            patch.object(DeviceRepository, "get_device_by_imei", new=AsyncMock(return_value="device-52")) as canonical,
        ):
            result = await DeviceRepository.get_device_by_h02_id("4208298730")
        self.assertEqual(result, "device-52")
        conn.fetchrow.assert_awaited_once()
        self.assertIn('"H02DeviceIdentifiers"', conn.fetchrow.call_args.args[0])
        self.assertEqual(conn.fetchrow.call_args.args[1], "4208298730")
        canonical.assert_awaited_once_with("866126037773336", protocol_type="H02", default_model="H02")
        release.assert_awaited_once_with(conn)

    async def test_unknown_short_wire_id_is_rejected(self):
        conn = AsyncMock()
        conn.fetchrow.return_value = None
        with (
            patch("repositories.device_repository.get_db_connection", new=AsyncMock(return_value=conn)),
            patch("repositories.device_repository.release_db_connection", new=AsyncMock()),
            patch.object(DeviceRepository, "get_device_by_imei", new=AsyncMock()) as canonical,
            patch("repositories.device_repository.log"),
        ):
            result = await DeviceRepository.get_device_by_h02_id("4200000000")
        self.assertIsNone(result)
        canonical.assert_not_awaited()

    async def test_15_digit_wire_imei_uses_exact_lookup(self):
        with patch.object(DeviceRepository, "get_device_by_imei", new=AsyncMock(return_value="device-1")) as canonical:
            result = await DeviceRepository.get_device_by_h02_id("866126037773336")
        self.assertEqual(result, "device-1")
        canonical.assert_awaited_once_with("866126037773336", protocol_type="H02", default_model="H02")

    async def test_mapping_database_error_fails_closed(self):
        conn = AsyncMock()
        conn.fetchrow.side_effect = RuntimeError("mapping table unavailable")
        with (
            patch("repositories.device_repository.get_db_connection", new=AsyncMock(return_value=conn)),
            patch("repositories.device_repository.release_db_connection", new=AsyncMock()),
            patch.object(DeviceRepository, "get_device_by_imei", new=AsyncMock()) as canonical,
            patch("repositories.device_repository.log"),
        ):
            result = await DeviceRepository.get_device_by_h02_id("4208298730")
        self.assertIsNone(result)
        canonical.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
