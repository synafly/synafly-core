"""Offline transport fixtures; none of these tests contact a public RPC."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import probe_pancakeswap_live as probe
from synafly_lab.rpc import BSC_GENESIS, HttpOrigin, RpcError, encode

BLOCK = '0x' + 'ab' * 32
WORD = '0x' + format(123 + (456 << 112) + (789 << 224), '064x')


class Wire:
    def __init__(self, fault=None):
        self.fault = fault
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, raw):
        request = json.loads(raw)
        method, params = request['method'], request['params']
        with self.lock:
            self.calls.append((method, params))
            reserve_calls = sum(m == 'eth_getStorageAt' and p[1] == '0x8'
                                for m, p in self.calls)
        if method == 'eth_chainId':
            value = '0x1' if self.fault == 'chain' else '0x38'
        elif method == 'eth_getBlockByNumber':
            value = {'number': '0x0', 'hash': BSC_GENESIS} if params[0] == '0x0' else {
                'number': '0x123', 'hash': BLOCK, 'timestamp': '0x400'}
            if self.fault == 'genesis' and params[0] == '0x0':
                value['hash'] = BLOCK
            if self.fault == 'block' and params[0] == 'latest':
                value = None
            if self.fault == 'reorg' and params[0] == '0x123':
                value['hash'] = '0x' + 'cd' * 32
        elif method == 'eth_getCode':
            value = '0x' if self.fault == 'no-code' else '0x6000'
        elif method == 'eth_getStorageAt':
            if self.fault == 'rpc-error' or (self.fault == 'burst-error' and params[1] == '0x8'):
                return json.dumps({'jsonrpc': '2.0', 'id': request['id'],
                                   'error': {'code': -32000, 'message': 'no state'}}).encode()
            slot = int(params[1], 16)
            if slot == 8:
                time.sleep(0.08)  # Fixture overlap only; the live path has no delay.
                value = WORD
                if self.fault == 'mismatch' and reserve_calls > 4:
                    value = '0x' + '00' * 32
                if self.fault == 'malformed':
                    value = '0x12'
            else:
                address = {5: probe.FACTORY, 6: probe.USDT, 7: probe.WBNB}[slot]
                value = '0x' + '00' * 12 + address[2:]
                if self.fault == 'token' and slot == 6:
                    value = '0x' + '00' * 32
        else:
            raise AssertionError('Unexpected RPC method: ' + method)
        return json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': value}).encode()


class ProbeTests(unittest.TestCase):
    def run_fixture(self, fault=None, **kwargs):
        wire = Wire(fault)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report.json'
            output.write_text('{"status":"passed","stale":true}')
            with patch.object(HttpOrigin, '_exchange', side_effect=wire), \
                    patch('urllib.request.getproxies', return_value={}):
                report = probe.run_probe(output, **kwargs)
            self.assertEqual(json.loads(output.read_text()), report)
            self.assertNotIn('stale', report)
        return report, wire

    def test_decode_packed_reserves_exactly_without_floats(self):
        self.assertEqual(probe.decode_reserves(WORD),
                         {'reserve0_raw': '123', 'reserve1_raw': '456',
                          'block_timestamp_last': 789})
        maximum = probe.decode_reserves('0x' + 'ff' * 32)
        self.assertEqual(maximum['reserve0_raw'], str(2**112 - 1))
        self.assertEqual(maximum['reserve1_raw'], str(2**112 - 1))
        self.assertEqual(maximum['block_timestamp_last'], 2**32 - 1)
        self.assertEqual(probe.decode_reserves('0x' + '00' * 32)['reserve0_raw'], '0')
        for bad in [None, True, '0x1', '0x' + 'ff' * 33, '0x' + 'zz' * 32]:
            with self.subTest(bad=bad), self.assertRaises(RpcError):
                probe.decode_reserves(bad)

    def test_live_path_with_fake_transport_and_full_accounting(self):
        report, wire = self.run_fixture()
        self.assertEqual(report['status'], 'passed')
        self.assertTrue(report['all_sampled_responses_match'])
        self.assertTrue(report['coalescing_observed'])
        self.assertEqual(report['matched_responses'], 16)
        self.assertEqual(report['measured_origin_reads'], {'direct': 8, 'single-flight': 2})
        self.assertEqual(report['reserves'], probe.decode_reserves(WORD))
        self.assertEqual(report['rpc_attempts_total'], len(wire.calls))
        self.assertEqual(report['origin_metrics']['rpc_calls'], len(wire.calls))
        self.assertEqual(len(wire.calls), 24)
        self.assertEqual(report['controls']['origin_reads'], 6)
        self.assertEqual(report['controls']['edge_metrics']['cache_hits'], 0)
        for method, params in wire.calls:
            self.assertIn(method, probe.ALLOWED_METHODS)
            if method in {'eth_getStorageAt', 'eth_getCode'}:
                self.assertEqual(params[-1]['blockHash'], BLOCK)
        for row in report['bursts']:
            self.assertEqual(row['completed_requests'], 4)
            self.assertEqual(row['edge_metrics']['cache_hits'], 0)
            self.assertEqual(row['edge_metrics']['cache_entries'], 0)
            self.assertEqual(row['edge_metrics']['inflight_keys'], 0)

    def test_failure_reports_replace_stale_success(self):
        for fault in ['chain', 'genesis', 'block', 'no-code', 'token',
                      'rpc-error', 'burst-error', 'malformed', 'mismatch', 'reorg']:
            with self.subTest(fault=fault):
                report, wire = self.run_fixture(fault)
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['rpc_attempts_total'], len(wire.calls))
                self.assertEqual(report['blockchain_transactions_sent'], 0)
                self.assertIn('failure', report)

    def test_missing_overlap_is_not_reported_as_success(self):
        def serial_burst(edge, params, clients):
            return [probe.read_outcome(edge, item) for item in params]
        with patch.object(probe, 'burst', side_effect=serial_burst):
            report, _ = self.run_fixture()
        self.assertEqual(report['status'], 'inconclusive')
        self.assertTrue(report['all_sampled_responses_match'])
        self.assertFalse(report['coalescing_observed'])
        self.assertEqual(report['measured_origin_reads']['single-flight'], 8)

    def test_configuration_is_bounded_before_network(self):
        for kwargs in [{'clients': 1}, {'clients': 9}, {'clients': True},
                       {'rounds': 0}, {'rounds': 4}, {'rounds': True},
                       {'timeout': 0}, {'timeout': float('nan')}]:
            with self.subTest(kwargs=kwargs):
                report, wire = self.run_fixture(**kwargs)
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(wire.calls, [])

    def test_timeout_and_bootstrap_failure_are_counted(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(HttpOrigin, '_exchange', side_effect=RpcError(-32002, 'Upstream unavailable')), \
                patch('urllib.request.getproxies', return_value={}):
            report = probe.run_probe(Path(directory) / 'failure.json')
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['rpc_attempts_total'], 1)
        self.assertEqual(report['rpc_attempts'][0]['outcome'], 'error')
        self.assertEqual(report['phase'], 'bootstrap')

    def test_read_allowlist_rejects_writes_before_transport(self):
        wire = Wire()
        with patch.object(HttpOrigin, '_exchange', side_effect=wire), \
                patch('urllib.request.getproxies', return_value={}):
            origin = probe.ObservedOrigin([], {})
            for method in ['eth_sendTransaction', 'eth_sendRawTransaction',
                           'eth_call', 'personal_unlockAccount', 'anvil_setBalance']:
                with self.assertRaises(RpcError):
                    origin.call(method, [])
        self.assertEqual(len(wire.calls), 2)

    def test_cli_exit_status_tracks_evidence_not_just_output_creation(self):
        for status, expected in [('passed', 0), ('failed', 1), ('inconclusive', 1), ('interrupted', 130)]:
            with self.subTest(status=status), patch.object(sys, 'argv', ['probe']), \
                    patch.object(probe, 'run_probe', return_value={'status': status}), redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(), expected)

    def test_committed_live_evidence_bindings_and_denominators(self):
        report = json.loads((probe.ROOT / 'results/bsc-pancakeswap-live-probe.json').read_text())
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['upstream'], probe.UPSTREAM)
        self.assertEqual(report['chain_id'], 56)
        self.assertEqual(report['pair'], probe.PAIR)
        self.assertEqual(report['reserves'], probe.decode_reserves(report['storage_word']))
        for name, expected in report['implementation_sha256'].items():
            self.assertEqual(hashlib.sha256((probe.ROOT / name).read_bytes()).hexdigest(), expected)
        rows = report['bursts']
        clients, rounds = (report['workload'][key] for key in ['clients_per_burst', 'rounds'])
        self.assertEqual(len(rows), 2 * rounds)
        self.assertEqual(report['sampled_responses'], 2 * clients * rounds)
        self.assertEqual(report['matched_responses'], report['sampled_responses'])
        self.assertEqual(report['measured_origin_reads'], {
            mode: sum(row['origin_metrics']['state_reads'] for row in rows if row['mode'] == mode)
            for mode in ['direct', 'single-flight']})
        for row in rows:
            self.assertEqual(row['outcomes'], [{'result': report['storage_word']}] * clients)
            phase = f"{row['mode']}-round-{row['round']}"
            attempts = [item for item in report['rpc_attempts'] if item['phase'] == phase]
            self.assertEqual(len(attempts), row['origin_metrics']['state_reads'])
            self.assertEqual(len(attempts) + row['edge_metrics']['coalesced_waiters'], clients)
            self.assertEqual(row['edge_metrics']['cache_hits'], 0)
            for item in attempts:
                self.assertEqual(item['result_sha256'], hashlib.sha256(encode(report['storage_word'])).hexdigest())
        self.assertTrue(report['canonicality_recheck']['matched'])
        self.assertEqual(report['rpc_attempts_total'], len(report['rpc_attempts']))
        self.assertEqual(report['rpc_attempts_total'], report['origin_metrics']['rpc_calls'])
        self.assertEqual(report['rpc_attempts_total'], 14 + sum(report['measured_origin_reads'].values()))
        self.assertEqual(report['controls']['origin_reads'], 6)
        for item in report['rpc_attempts']:
            self.assertEqual(item['outcome'], 'returned')
            self.assertIn(item['method'], probe.ALLOWED_METHODS)
            if item['method'] in {'eth_getCode', 'eth_getStorageAt'}:
                self.assertEqual(item['params'][-1]['blockHash'], report['queried_block']['hash'])

    def test_retained_failed_live_attempt_is_not_success_evidence(self):
        report = json.loads((probe.ROOT / 'results/bsc-pancakeswap-live-probe-attempt-1.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['phase'], 'pool-identity')
        self.assertEqual(report['rpc_attempts_total'], 4)
        self.assertEqual(report['rpc_attempts'][-1]['outcome'], 'error')
        self.assertNotIn('all_sampled_responses_match', report)


if __name__ == '__main__':
    unittest.main()
