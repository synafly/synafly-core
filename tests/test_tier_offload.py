import asyncio
import copy
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest
from synafly_lab.async_edge_ingress import AsyncIngress
from synafly_lab.tier_cost import score, compare

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


class TierCostTests(unittest.TestCase):
    def test_units_are_per_megabyte_not_per_byte(self):
        self.assertEqual(score(1, 1_000_000), Decimal(201))
        self.assertEqual(score(1196, 4151932), Decimal('239204.151932'))
        result = compare(1314, 3502261, 1196, 4151932)
        self.assertEqual(result['origin_read_reduction_percent'], '8.980213')
        self.assertEqual(result['score_reduction_percent'], '8.979846')

    def test_bounds_and_revaluation_reproduction(self):
        for values in [(True, 0), (-1, 0), (1, -1)]:
            with self.assertRaises(ValueError): score(*values)
        for value in ['NaN', 'Infinity', 'invalid', '0', '-1', True]:
            with self.assertRaises(ValueError): score(1, 1, value)
        from reevaluate_tier_cost import evaluate
        self.assertEqual(evaluate(), json.loads((ROOT / 'results/tiered-cost-reevaluation.json').read_text()))

    def test_historical_counter_export_retains_every_control(self):
        from reevaluate_tier_cost import validate_snapshot
        snapshot = json.loads((ROOT / 'results/tiered-cost-reevaluation.json').read_text())['cooperative_snapshot']
        self.assertEqual(len(validate_snapshot(snapshot)), 640)
        missing = copy.deepcopy(snapshot); missing['rows'].pop()
        with self.assertRaisesRegex(ValueError, '640'): validate_snapshot(missing)
        duplicate = copy.deepcopy(snapshot); duplicate['rows'][1] = duplicate['rows'][0]
        with self.assertRaisesRegex(ValueError, 'Duplicate'): validate_snapshot(duplicate)
        changed = copy.deepcopy(snapshot); changed['rows'][0]['origin_reads'] -= 1
        with self.assertRaisesRegex(ValueError, 'export pin'): validate_snapshot(changed)

    def test_load_evidence_and_counter_corruption(self):
        from verify_tier_offload import verify
        report = json.loads((ROOT / 'results/tier-offload-1000.json').read_text())
        self.assertEqual(verify(report)['correct_responses'], 12000)
        report['overload_control']['origin_offload_percent'] = 99.9
        with self.assertRaisesRegex(ValueError, 'Rejections'): verify(report)

    def test_load_claims_cannot_silently_change_scope(self):
        from verify_tier_offload import verify
        report = json.loads((ROOT / 'results/tier-offload-1000.json').read_text())
        report['physical_nvme_io_measured'] = True
        with self.assertRaisesRegex(ValueError, 'Unmeasured'): verify(report)
        report['physical_nvme_io_measured'] = False
        report['rows'][0]['offered_clients'] = 999
        with self.assertRaisesRegex(ValueError, 'Equal-service'): verify(report)


class IngressTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, server, extra='', body=b'{}'):
        reader, writer = await asyncio.open_connection('127.0.0.1', server.port)
        writer.write(f'POST /rpc HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n{extra}\r\n'.encode() + body)
        await writer.drain(); raw = await reader.read(); writer.close(); await writer.wait_closed(); return raw

    async def test_loopback_origin_duplicate_header_and_notification_boundaries(self):
        server = await AsyncIngress({'/rpc': lambda value: None}).start()
        try:
            reply = await self.request(server)
            self.assertTrue(reply.startswith(b'HTTP/1.1 204'))
            self.assertTrue(reply.endswith(b'\r\n\r\n'))
            self.assertTrue((await self.request(server, 'Origin: https://example.org\r\n')).startswith(b'HTTP/1.1 403'))
            self.assertTrue((await self.request(server, 'Content-Length: 2\r\n')).startswith(b'HTTP/1.1 400'))
            self.assertTrue((await self.request(server, body=b'{')).startswith(b'HTTP/1.1 400'))
        finally: await server.close()

    async def test_admission_limit_returns_rejections_not_fake_success(self):
        gate = asyncio.Event(); server = await AsyncIngress({'/rpc': lambda value: {'ok': True}}, capacity=2, gate=gate).start()
        tasks = [asyncio.create_task(self.request(server)) for _ in range(4)]
        try:
            async with asyncio.timeout(3):
                while server.counts['received_requests'] != 4: await asyncio.sleep(.001)
            self.assertEqual(server.active, 2); gate.set()
            results = await asyncio.gather(*tasks)
            self.assertEqual(sum(r.startswith(b'HTTP/1.1 503') for r in results), 2)
            self.assertEqual(server.counts['admission_rejections'], 2)
        finally:
            gate.set(); await asyncio.gather(*tasks, return_exceptions=True); await server.close()
