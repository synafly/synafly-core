"""Offline cryptographic/recorded-wire tests. No Anvil, cast, keys or public RPC."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from synafly_lab.offload_receipts import digest,ZERO
from synafly_lab.quorum_relayer import (build_checkpoint,validate_bundle,validate_receipts,
    merkle,verify_inclusion,aggregate_signatures,signing_digest,message_hash,abi_commitment)
from synafly_lab.quorum_crypto import recover_address,N
from synafly_lab.quorum_rpc import ReadRPC,preflight,verify_transaction,selector
from synafly_lab.quorum_local_evm import OwnedAnvil
from synafly_lab.rpc import encode
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from verify_quorum_pipeline import audit,WireReplay,verify_release_lock


class QuorumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.saved=json.loads((ROOT/'results/quorum-pipeline.json').read_text())
    def setUp(self):
        self.report=copy.deepcopy(self.saved);self.row=self.report['checkpoints'][0]
        self.bundle=self.row['bundle'];self.envelopes=self.row['witness_envelopes']
        self.selected=[e for e in self.envelopes if e['signer'] in self.row['certificate']['witnesses']]
    def rebuild(self,rows=None,previous=None,**changes):
        fields={k:self.bundle[k] for k in ('chain_id','registry','committee','threshold','runtime_code_hash')};fields.update(changes)
        return build_checkpoint(rows or self.bundle['receipts'],previous=previous,**fields)
    def check_receipt(self,row=None,confirmations=1):
        row=row or self.row;wire=WireReplay(row['verification_wire'])
        value=verify_transaction(wire,row['bundle'],row['certificate'],row['verification']['receipt']['transactionHash'],confirmations=confirmations)
        wire.complete();return value

    def test_release_sources_and_recorded_evidence_are_locked(self):
        self.assertEqual(verify_release_lock()['source_bindings'],'matched')

    def test_full_recorded_audit_is_offline_and_cryptographic(self):
        with patch('socket.create_connection',side_effect=AssertionError('No network')),patch('subprocess.run',side_effect=AssertionError('No cast')),patch('subprocess.Popen',side_effect=AssertionError('No Anvil')):
            result=audit(self.report)
        self.assertEqual(result['checkpoints'],2);self.assertEqual(result['receipts'],self.report['checkpointed_receipts'])
        self.assertFalse(result['real_evm_rerun'])

    def test_merkle_determinism_order_and_count_binding(self):
        rows=self.bundle['receipts'][:3];root,proofs=merkle(rows)
        self.assertEqual(merkle(copy.deepcopy(rows)),(root,proofs))
        self.assertNotEqual(merkle(list(reversed(rows)))[0],root)
        self.assertNotEqual(merkle(rows+[rows[-1]])[0],root)
        for row,proof in zip(rows,proofs):self.assertTrue(verify_inclusion(row,proof,root))

    def test_merkle_proof_mutation_position_and_depth(self):
        rows=self.bundle['receipts'];root,proofs=merkle(rows)
        for mutate in [lambda p:p.update(index=1),lambda p:p['siblings'].__setitem__(0,ZERO),lambda p:p['siblings'].append(ZERO),lambda p:p.update(count=True)]:
            proof=copy.deepcopy(proofs[0]);mutate(proof)
            with self.assertRaises(ValueError):verify_inclusion(rows[0],proof,root)
        with self.assertRaises(ValueError):merkle([])
        with self.assertRaises(ValueError):merkle(rows*257)

    def test_receipt_hash_tampering_and_embedded_commitment(self):
        for mutate in [lambda r:r.update(upstream_saved=2),lambda r:r.update(timestamp=True),lambda r:r.update(target_slot='0x08'),lambda r:r.update(source='prefetch-miracle'),lambda r:r['commitment'].update(checkpoint=ZERO)]:
            rows=copy.deepcopy(self.bundle['receipts']);mutate(rows[0])
            with self.assertRaises(ValueError):self.rebuild(rows)

    def test_missing_reordered_duplicated_and_mixed_receipts(self):
        rows=self.bundle['receipts']
        for changed in [rows[1:],list(reversed(rows)),[rows[0],rows[0]],rows[:1]+rows[2:]]:
            with self.assertRaises(ValueError):self.rebuild(changed)
        changed=copy.deepcopy(rows);changed[-1]['lineage']='0x'+'ff'*32
        with self.assertRaises(ValueError):self.rebuild(changed)

    def test_successor_uses_checkpoint_parent_not_receipt_parent(self):
        next_row=self.report['checkpoints'][1];bundle=next_row['bundle']
        self.assertEqual(validate_bundle(bundle,self.bundle),bundle)
        self.assertEqual(bundle['commitment']['parent'],self.bundle['merkle_root'])
        self.assertNotEqual(bundle['commitment']['parent'],bundle['receipts'][0]['parent'])
        self.assertGreater(bundle['commitment']['tick'],self.bundle['commitment']['tick'])

    def test_successor_replay_and_predecessor_tampering_rejected(self):
        with self.assertRaises(ValueError):self.rebuild(previous=self.bundle)
        second=self.report['checkpoints'][1]['bundle']
        for mutate in [lambda p:p.update(last_receipt_sequence=999),lambda p:p['receipts'][-1].update(receipt_hash=ZERO),lambda p:p['commitment'].update(checkpoint=ZERO),lambda p:p.update(runtime_code_hash=ZERO)]:
            previous=copy.deepcopy(self.bundle);mutate(previous)
            with self.assertRaises(ValueError):validate_bundle(second,previous)

    def test_third_checkpoint_can_follow_verified_suffix(self):
        previous=self.report['checkpoints'][1]['bundle'];row=copy.deepcopy(previous['receipts'][-1])
        row['sequence']+=1;row['parent']=row['receipt_hash'];row['timestamp']+=1
        row['receipt_hash']=digest({k:v for k,v in row.items() if k not in {'receipt_hash','commitment'}})
        row['commitment'].update(sequence=row['sequence'],tick=row['sequence'],parent=row['parent'],checkpoint=row['receipt_hash'])
        result=self.rebuild([row],previous=previous)
        self.assertEqual(result['commitment']['sequence'],2)
        self.assertEqual(validate_bundle(result,previous),result)

    def test_actual_eip191_signatures_recover_distinct_members(self):
        recovered=[recover_address(self.bundle['signing_digest'],e['signature']) for e in self.envelopes]
        self.assertEqual(recovered,self.bundle['committee'])
        self.assertEqual(signing_digest(self.bundle['message_hash']),self.bundle['signing_digest'])
        self.assertNotEqual(self.bundle['message_hash'],self.bundle['signing_digest'])

    def test_quorum_sorts_input_but_rejects_duplicate_signers(self):
        result=aggregate_signatures(self.bundle,list(reversed(self.selected)))
        self.assertEqual(result,self.row['certificate'])
        with self.assertRaisesRegex(ValueError,'Duplicate'):aggregate_signatures(self.bundle,[self.selected[0]]*2)
        all_three=aggregate_signatures(self.bundle,list(reversed(self.envelopes)))
        self.assertEqual(all_three['witnesses'],self.bundle['committee'])

    def test_insufficient_unknown_and_wrong_claimed_signer(self):
        with self.assertRaisesRegex(ValueError,'quorum'):aggregate_signatures(self.bundle,self.selected[:1])
        changed=copy.deepcopy(self.selected);changed[0]['signer']='0x'+'00'*20
        with self.assertRaisesRegex(ValueError,'witness'):aggregate_signatures(self.bundle,changed)
        changed=copy.deepcopy(self.selected);changed[0]['bundle_hash']=ZERO
        with self.assertRaisesRegex(ValueError,'envelope'):aggregate_signatures(self.bundle,changed)

    def test_low_s_v_r_and_length_rules_match_contract(self):
        original=bytes.fromhex(self.selected[0]['signature'][2:])
        bad=[b'',original[:-1],b'\0'*32+original[32:],original[:32]+b'\0'*32+original[64:],original[:64]+b'\x00',original[:32]+(N-int.from_bytes(original[32:64],'big')).to_bytes(32,'big')+original[64:]]
        for raw in bad:
            with self.assertRaises(ValueError):recover_address(self.bundle['signing_digest'],'0x'+raw.hex())

    def test_cross_chain_and_cross_contract_signature_replay(self):
        for changes in [{'chain_id':98},{'registry':'0x'+'12'*20}]:
            bundle=self.rebuild(**changes);signatures=copy.deepcopy(self.selected)
            for envelope in signatures:envelope['bundle_hash']=digest(bundle)
            with self.assertRaisesRegex(ValueError,'witness'):aggregate_signatures(bundle,signatures)

    def test_runtime_committee_and_threshold_are_signed_model_inputs(self):
        for changes in [{'runtime_code_hash':'0x'+'ab'*32},{'threshold':3}]:
            bundle=self.rebuild(**changes)
            self.assertNotEqual(bundle['commitment']['model'],self.bundle['commitment']['model'])
            signatures=copy.deepcopy(self.envelopes)
            for envelope in signatures:envelope['bundle_hash']=digest(bundle)
            with self.assertRaises(ValueError):aggregate_signatures(bundle,signatures)

    def test_bundle_extra_fields_or_unsigned_mutation_rejected(self):
        for key,value in [('extra',1),('runtime_code_hash',ZERO),('validation_scope','proven savings'),('first_receipt_sequence',100)]:
            bundle=copy.deepcopy(self.bundle);bundle[key]=value
            with self.assertRaises(ValueError):validate_bundle(bundle)

    def test_abi_dynamic_offsets_and_signature_bytes(self):
        payload=bytes.fromhex(self.row['certificate']['transaction']['data'][2:])
        self.assertEqual(payload[:4].hex(),'de1046e7');body=payload[4:]
        word_at=lambda n:int.from_bytes(body[n*32:(n+1)*32],'big')
        self.assertEqual(word_at(7),256);self.assertEqual(word_at(8),2)
        self.assertEqual([word_at(9),word_at(10)],[64,192])
        for i,signature in enumerate(self.row['certificate']['signatures']):
            start=9*32+word_at(9+i);size=int.from_bytes(body[start:start+32],'big')
            self.assertEqual(size,65);self.assertEqual(body[start+32:start+32+size].hex(),signature[2:])
        self.assertEqual(len(payload),612)

    def test_preflight_exact_runtime_membership_threshold_and_head(self):
        wire=WireReplay(self.row['preflight_wire']);self.assertEqual(preflight(wire,self.bundle),self.row['preflight']);wire.complete()
        for signature,replacement in [('threshold()','0x'+format(3,'064x')),('isWitness(address)',ZERO),('heads(bytes32)','0x'+'00'*160+format(1,'064x'))]:
            rows=copy.deepcopy(self.row['preflight_wire'])
            entry=next(r for r in rows if r['method']=='eth_call' and r['params'][0]['data'].startswith('0x'+selector(signature)))
            entry['response']['result']=replacement
            with self.assertRaises(ValueError):preflight(WireReplay(rows),self.bundle)
        rows=copy.deepcopy(self.row['preflight_wire']);next(r for r in rows if r['method']=='eth_getCode')['response']['result']='0x00'
        with self.assertRaisesRegex(ValueError,'runtime'):preflight(WireReplay(rows),self.bundle)

    def test_preflight_rejects_unproven_committee_length(self):
        rows=copy.deepcopy(self.row['preflight_wire'])
        entry=next(r for r in rows if 'error' in r['response'])
        entry['response']['error']['code']=-32002
        with self.assertRaisesRegex(ValueError,'committee length'):preflight(WireReplay(rows),self.bundle)
        rows=copy.deepcopy(self.row['preflight_wire']);entry=next(r for r in rows if 'error' in r['response'])
        entry['response']={'jsonrpc':'2.0','id':entry['response']['id'],'result':ZERO}
        with self.assertRaisesRegex(ValueError,'additional witnesses'):preflight(WireReplay(rows),self.bundle)

    def test_real_replay_transaction_failed_and_has_no_checkpoint_event(self):
        rejected=self.report['rejected_replay_transaction']
        self.assertEqual(rejected['receipt']['status'],'0x0');self.assertEqual(rejected['receipt']['logs'],[])
        self.assertGreater(int(rejected['receipt']['gasUsed'],16),0)
        self.assertEqual(rejected['head_before'],rejected['head_after'])
        changed=copy.deepcopy(self.report);changed['rejected_replay_transaction']['receipt']['status']='0x1'
        with self.assertRaisesRegex(ValueError,'Rejected replay'):audit(changed)

    def test_checkpoint_receipts_must_match_captured_daemon_stream(self):
        changed=copy.deepcopy(self.report);changed['source_cluster']['receipts'][0]['retained'].pop()
        with self.assertRaisesRegex(ValueError,'daemon capture'):audit(changed)

    def test_wire_replay_checks_exact_requests_and_complete_consumption(self):
        wire=WireReplay(self.row['preflight_wire'])
        with self.assertRaises(ValueError):wire.call('eth_sign',[])
        with self.assertRaises(ValueError):wire.complete()

    def test_full_receipt_gas_event_transaction_and_head(self):
        actual=self.check_receipt();self.assertEqual(actual,self.row['verification'])
        self.assertGreater(actual['gas_used'],0)
        self.assertEqual(actual['receipt']['status'],'0x1')

    def test_receipt_status_removed_event_wrong_destination(self):
        for mutate in [lambda r:r.update(status='0x0'),lambda r:r.update(to='0x'+'ff'*20),lambda r:r.update(gasUsed='0x0'),lambda r:r['logs'][0].update(removed=True),lambda r:r['logs'][0].update(data='0x00')]:
            row=copy.deepcopy(self.row);entry=next(r for r in row['verification_wire'] if r['method']=='eth_getTransactionReceipt');mutate(entry['response']['result'])
            with self.assertRaises(ValueError):self.check_receipt(row)

    def test_transaction_calldata_chain_and_block_mismatch(self):
        for key,value in [('input','0x00'),('value','0x1'),('chainId','0x38'),('blockHash',ZERO)]:
            row=copy.deepcopy(self.row);entry=next(r for r in row['verification_wire'] if r['method']=='eth_getTransactionByHash');entry['response']['result'][key]=value
            with self.assertRaises(ValueError):self.check_receipt(row)

    def test_receipt_confirmation_depth_and_reorg(self):
        with self.assertRaisesRegex(ValueError,'confirmations'):self.check_receipt(confirmations=2)
        row=copy.deepcopy(self.row);entries=[r for r in row['verification_wire'] if r['method']=='eth_getBlockByNumber'];entries[-1]['response']['result']['hash']=ZERO
        with self.assertRaisesRegex(ValueError,'Reorg'):self.check_receipt(row)

    def test_negative_revert_counterfactual_is_detected(self):
        report=copy.deepcopy(self.report);report['negative_evm_cases']['duplicate_signer']['expected_error']='UnknownWitness'
        with self.assertRaisesRegex(ValueError,'Negative'):audit(report)

    def test_write_boundaries_reject_without_process_or_transport(self):
        client=ReadRPC('https://example.org')
        with patch.object(client,'_exchange',side_effect=AssertionError('No network')):
            for method in ['eth_sign','personal_sign','eth_sendTransaction','eth_sendRawTransaction']:
                with self.assertRaises(ValueError):client.call(method,[])
        with self.assertRaises(ValueError):OwnedAnvil().call('eth_sendTransaction',[])
        with self.assertRaises(TypeError):OwnedAnvil('https://example.org')

    def test_read_rpc_codec_uses_offline_wire_and_rejects_bool_id(self):
        reply={'jsonrpc':'2.0','id':True,'result':'0x61'}
        class Connection:
            def __init__(self,*args,**kwargs):self.sock=SimpleNamespace(shutdown=lambda *_:None)
            def connect(self):pass
            def request(self,*args,**kwargs):pass
            def getresponse(self):return SimpleNamespace(status=200,headers=SimpleNamespace(get_content_type=lambda:'application/json'),read=lambda n:encode(reply))
            def close(self):pass
        with patch('http.client.HTTPConnection',Connection):
            with self.assertRaisesRegex(ValueError,'envelope'):ReadRPC('http://127.0.0.1:1').call('eth_chainId',[])
            reply['id']=1;self.assertEqual(ReadRPC('http://127.0.0.1:1').call('eth_chainId',[]),'0x61')

    def test_cli_collect_writes_only_unsigned_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'bundle.json').write_bytes(encode(self.bundle));(root/'signatures.json').write_bytes(encode(self.selected))
            result=subprocess.run([sys.executable,str(ROOT/'scripts/relay_offload_commitments.py'),'collect','--bundle',str(root/'bundle.json'),'--signatures',str(root/'signatures.json'),'--out',str(root/'out.json')],capture_output=True,text=True,timeout=10,check=True)
            self.assertFalse(json.loads(result.stdout)['public_broadcast_performed'])
            self.assertEqual(json.loads((root/'out.json').read_text()),self.row['certificate'])


class ClusterTests(unittest.TestCase):
    def test_three_real_processes_with_owned_offline_wire(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'report.json'
            subprocess.run([sys.executable,str(ROOT/'scripts/run_mesh_cluster.py'),'--demo','--ports','0','0','0','--out',str(output)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True,timeout=20)
            report=json.loads(output.read_text());self.assertEqual(report['daemon_processes'],3)
            self.assertTrue(report['peer_values_match']);self.assertGreaterEqual(report['metrics'][0]['mesh']['hits'],1)
            self.assertGreaterEqual(report['metrics'][2]['mesh']['hits'],1)
            self.assertGreater(len(report['receipts'][0]['retained']),0)

if __name__=='__main__':unittest.main()
