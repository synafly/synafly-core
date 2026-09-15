import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from synafly_lab.rpc import RpcError

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('probe_bsc_rpc',ROOT/'scripts/probe_bsc_rpc.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)

class ProbeReportTests(unittest.TestCase):
    def test_failed_rerun_cannot_leave_previous_success_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'probe.json';output.write_text('{"status":"passed"}')
            with patch.object(probe,'_probe',side_effect=RpcError(-32002,'Upstream unavailable')):
                with self.assertRaises(RpcError):probe.run_probe('https://example.com',output)
            result=json.loads(output.read_text())
            self.assertEqual(result['status'],'failed');self.assertEqual(result['error_code'],-32002)
            self.assertEqual(result['blockchain_transactions_sent'],0)
    def test_success_report_binds_implementation_without_live_network(self):
        report={'status':'passed','queried_block':{'number':'0x1'},'total_public_rpc_calls':27,
                'results':[{'mode':'fixture','origin_metrics':{'state_reads':4}}]}
        with tempfile.TemporaryDirectory() as temp,patch.object(probe,'_probe',return_value=report),contextlib.redirect_stdout(io.StringIO()):
            output=Path(temp)/'probe.json';probe.run_probe('https://example.com',output)
            stored=json.loads(output.read_text())
        for name,digest in stored['implementation_sha256'].items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),digest)

class PublishedEvidenceTests(unittest.TestCase):
    def test_report_bindings_and_accounting(self):
        evidence_spec=importlib.util.spec_from_file_location('verify_evidence',ROOT/'scripts/verify_evidence.py')
        evidence=importlib.util.module_from_spec(evidence_spec);evidence_spec.loader.exec_module(evidence)
        self.assertEqual(evidence.verify()['accounting'],'matched')
    def test_negative_graph_results_and_provider_observations_are_retained(self):
        report=json.loads((ROOT/'results/routing-benchmark.json').read_text())
        rows={row['strategy']:row for row in report['results']}
        hits=lambda row:sum(t['cache_shard_hits'] for t in row['trials'] if t['failed_nodes']==0)
        observed=hits(rows['observed-malecns'])
        controls=[hits(row) for name,row in rows.items() if name.startswith('degree-matched-')]
        self.assertLess(observed,min(controls))
        observations=json.loads((ROOT/'results/bsc-probe-observations.json').read_text())
        self.assertTrue(any(row['status']=='failed' for row in observations['observations']))
