"""Offline chain-56 codecs, permissioned signer configuration and guarded wire flow."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from synafly_lab.mainnet_codec import rlp,unrlp,decode_transaction,create_address
from synafly_lab.mainnet_plan import (IDENTITY,TOKEN_CA,NOTICE,SOURCE_SHA,CONFIG_SHA,constructor_args,
    validate_plan,check_build,plan)
from synafly_lab.mainnet_pipeline import (check_pair,fee_wei,broadcast,run_stages,network_check,
    wait_confirmed,journal_lock)
from synafly_lab.mainnet_signer import EnvironmentSigner,derive_witness
from synafly_lab.quorum_relayer import message_hash,signing_digest,aggregate_signatures,abi_commitment
from synafly_lab.quorum_crypto import multiply,G
from synafly_lab.quorum_rpc import selector,RegistryRPCError
from synafly_lab.anchor import word,tuple_data,EVENT_TOPIC
from synafly_lab.keccak import keccak256
from synafly_lab.rpc import BSC_GENESIS
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=json.loads((ROOT/'tests/fixtures/genesis_checkpoint.json').read_text())
ARTIFACT=json.loads((ROOT/'tests/fixtures/mainnet-build.json').read_text())['artifact']


class MainnetWire:
    """Deterministic RPC fixture, NOT a blockchain or evidence of a real deployment."""
    url='https://offline.invalid'
    def __init__(self,p,cert,first,second):
        self.p,self.cert=p,cert;self.transactions={first['hash']:first,second['hash']:second}
        self.first,self.second=first,second;self.sent=[];self.receipts={};self.height=0
        self.nonce=p['nonce'];self.deployed=False;self.committed=False;self.balance=10**18
        self.wrong_chain=False;self.reorg=False;self.uncertain=False;self.no_accept=False
        runtime=bytearray.fromhex(ARTIFACT['deployedBytecode']['object'][2:])
        for positions in ARTIFACT['deployedBytecode']['immutableReferences'].values():
            for pos in positions:runtime[pos['start']:pos['start']+32]=(2).to_bytes(32,'big')
        self.code='0x'+runtime.hex()
    def block_hash(self,n):return '0x'+format(n,'064x')
    def call(self,method,params):
        p=self.p;c=p['genesis_bundle']['commitment']
        if method=='eth_chainId':return '0x61' if self.wrong_chain else '0x38'
        if method=='eth_getBlockByNumber':
            if params[0]=='0x0':return {'number':'0x0','hash':BSC_GENESIS}
            n=self.height if params[0]=='latest' else int(params[0],16)
            return {'number':hex(n),'hash':self.block_hash(n+1 if self.reorg else n)}
        if method=='eth_blockNumber':return hex(self.height)
        if method=='eth_getTransactionCount':return hex(self.nonce)
        if method=='eth_getBalance':return hex(self.balance)
        if method=='eth_getCode':return self.code if self.deployed else '0x'
        if method=='eth_sendRawTransaction':
            tx=decode_transaction(params[0]);assert tx['hash'] in self.transactions
            self.sent.append(tx['hash'])
            if self.no_accept:raise TimeoutError('Simulated lost connection before acceptance')
            deploy=tx['to'] is None;n=10 if deploy else 14;self.height=n+2;self.nonce=tx['nonce']+1
            if deploy:self.deployed=True
            else:self.committed=True
            logs=[] if deploy else [{'address':p['registry_address'],'topics':[EVENT_TOPIC,c['lineage']],
                'data':'0x'+word(c['sequence'])+word(c['tick'])+''.join(c[k][2:] for k in ('graph','model','parent','checkpoint')),
                'removed':False,'transactionHash':tx['hash'],'blockHash':self.block_hash(n)}]
            self.receipts[tx['hash']]={'transactionHash':tx['hash'],'status':'0x1','from':p['deployer'],'to':tx['to'],
                'contractAddress':p['registry_address'] if deploy else None,'gasUsed':hex(800000 if deploy else 136000),
                'effectiveGasPrice':hex(tx['max_fee_per_gas']),'blockNumber':hex(n),'blockHash':self.block_hash(n),'logs':logs}
            if self.uncertain:self.uncertain=False;raise TimeoutError('Simulated accepted write with lost response')
            return tx['hash']
        if method=='eth_getTransactionReceipt':return self.receipts.get(params[0])
        if method=='eth_getTransactionByHash':
            tx=self.transactions[params[0]];r=self.receipts[params[0]]
            return {'hash':tx['hash'],'from':tx['sender'],'to':tx['to'],'input':tx['data'],'value':hex(tx['value']),
                    'chainId':'0x38','nonce':hex(tx['nonce']),'gas':hex(tx['gas_limit']),'blockNumber':r['blockNumber'],'blockHash':r['blockHash']}
        if method=='eth_call':
            data=params[0]['data'];sel=data[2:10];arguments=data[10:]
            if data==self.cert['transaction']['data']:
                if self.committed:raise RegistryRPCError(3,'0x998f0c35')
                return '0x'
            if sel==selector('threshold()'):return '0x'+word(2)
            if sel==selector('isWitness(address)'):return '0x'+word(int('0x'+arguments[-40:] in p['committee']))
            if sel==selector('witnesses(uint256)'):
                index=int(arguments,16)
                if index==3:raise RegistryRPCError(3,'0x')
                return '0x'+p['committee'][index][2:].rjust(64,'0')
            if sel==selector('heads(bytes32)'):
                if not self.committed:return '0x'+'00'*192
                return '0x'+''.join(c[k][2:] for k in ('graph','model','checkpoint'))+word(c['sequence'])+word(c['tick'])+word(1)
            if sel==selector('messageHash((bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64))'):
                assert arguments==tuple_data(abi_commitment(c));return p['genesis_bundle']['message_hash']
            if sel==selector('signingDigest((bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64))'):return p['genesis_bundle']['signing_digest']
        raise AssertionError('Unexpected offline RPC: '+method)


class MainnetTests(unittest.TestCase):
    def setUp(self):
        self.f=copy.deepcopy(FIXTURE);self.p=self.f['plan'];self.cert=self.f['certificate']
        self.first,self.second,self.cap=check_pair(self.p,self.cert,self.f['raw_deploy'],self.f['raw_genesis'],fee_wei('0.002'))
    def test_release_source_lock_and_offline_mainnet_fixture(self):
        import sys
        sys.path.insert(0,str(ROOT/'scripts'))
        from verify_mainnet_tooling import audit
        result=audit();self.assertEqual(result['source_bindings'],'matched');self.assertFalse(result['live_mainnet_verified'])

    def test_chain56_eip191_and_deterministic_calldata(self):
        b=self.p['genesis_bundle'];self.assertEqual(b['chain_id'],56)
        self.assertEqual(message_hash(b['commitment'],56,self.p['registry_address']),b['message_hash'])
        self.assertEqual(signing_digest(b['message_hash']),b['signing_digest'])
        envelopes=[{'signer':a,'signature':s,'bundle_hash':self.cert['bundle_hash']} for a,s in zip(self.cert['witnesses'],self.cert['signatures'])]
        self.assertEqual(aggregate_signatures(b,list(reversed(envelopes))),self.cert)
        self.assertEqual(self.second['data'],self.cert['transaction']['data'])
    def test_create_address_and_exact_constructor(self):
        self.assertEqual(create_address(self.p['deployer'],0),self.p['registry_address'])
        args=constructor_args(self.p['committee'])
        self.assertEqual(args[:66],'0x'+word(64));self.assertEqual(args[66:130],word(2))
        self.assertEqual(args[130:194],word(3));self.assertEqual(len(bytes.fromhex(args[2:])),192)
        with self.assertRaises(ValueError):constructor_args(list(reversed(self.p['committee'])))
        with self.assertRaises(ValueError):constructor_args([self.p['committee'][0]]*3)
    def test_rlp_canonical_and_invalid_forms(self):
        for value in [b'',b'a',b'cat',[b'a',b'b'],[0,56,b'x'*60],[]]:self.assertEqual(rlp(unrlp(rlp(value))),rlp(value))
        for raw in [b'',b'\x81\x01',b'\xb8\x01x',b'\xc1',b'\x80\x80',b'\xf8\x00']:
            with self.assertRaises(ValueError):unrlp(raw)
    def test_signed_legacy_and_type2_independent_recovery(self):
        for raw,kind in [(self.f['raw_deploy'],0),(self.f['raw_genesis'],0),(self.f['raw_type2'],2)]:
            tx=decode_transaction(raw);self.assertEqual(tx['chain_id'],56);self.assertEqual(tx['type'],kind);self.assertEqual(tx['sender'],self.p['deployer'])
        with self.assertRaises(ValueError):decode_transaction('0x03c0')
    def test_type2_genesis_uses_same_cap_and_destination_checks(self):
        first,second,cost=check_pair(self.p,self.cert,self.f['raw_deploy'],self.f['raw_type2'],fee_wei('0.002'))
        self.assertEqual(second['type'],2);self.assertEqual(second['nonce'],self.p['nonce']+1)
        self.assertLessEqual(cost,fee_wei('0.002'))

    def test_unsigned_unprotected_and_noncanonical_transactions_rejected(self):
        with self.assertRaises(ValueError):decode_transaction('0x'+rlp([0,1,21000,b'',0,b'',27,1,1]).hex())
        value=unrlp(bytes.fromhex(self.f['raw_deploy'][2:]));value[0]=b'\0'
        with self.assertRaises(ValueError):decode_transaction('0x'+rlp(value).hex())
    def test_exact_build_contract_config_and_runtime(self):
        self.assertEqual(check_build(ARTIFACT,ROOT),self.p['runtime_code_hash'])
        self.assertEqual(hashlib.sha256((ROOT/'contracts/ContinuityRegistry.sol').read_bytes()).hexdigest(),SOURCE_SHA)
        self.assertEqual(hashlib.sha256((ROOT/'foundry.toml').read_bytes()).hexdigest(),CONFIG_SHA)
        bad=copy.deepcopy(ARTIFACT);bad['bytecode']['object']+='00'
        with self.assertRaises(ValueError):check_build(bad,ROOT)
    def test_plan_token_isolation_and_no_hidden_changes(self):
        self.assertEqual(validate_plan(self.p,ARTIFACT,ROOT),self.p)
        self.assertIn('NOT A TOKEN',self.p['notice']);self.assertNotEqual(self.p['registry_address'],TOKEN_CA)
        bad=copy.deepcopy(self.p);bad['creation_data']+='00'
        with self.assertRaises(ValueError):validate_plan(bad,ARTIFACT,ROOT)
        with self.assertRaises(ValueError):plan(ARTIFACT,ROOT,self.p['genesis_bundle']['receipts'],TOKEN_CA,0,self.p['committee'])
    def test_fee_cap_exact_decimal_and_budget(self):
        self.assertEqual(fee_wei('0.002'),2000000000000000)
        for value in ['NaN','-1','1e-3','0.0000000000000000001','0']:
            with self.assertRaises(ValueError):fee_wei(value)
        with self.assertRaisesRegex(ValueError,'fees'):check_pair(self.p,self.cert,self.f['raw_deploy'],self.f['raw_genesis'],1)
    def test_wrong_nonce_sender_destination_cannot_be_broadcast(self):
        for key,value in [('nonce',1),('deployer',self.p['committee'][0]),('registry_address',TOKEN_CA)]:
            p={**self.p,key:value}
            with self.assertRaises(ValueError):check_pair(p,self.cert,self.f['raw_deploy'],self.f['raw_genesis'],fee_wei('0.002'))
    def test_public_fixture_and_loopback_broadcast_are_denied(self):
        for url in ['https://offline.invalid','http://127.0.0.1:8545','https://127.0.0.1']:
            rpc=SimpleNamespace(url=url)
            with self.assertRaises(ValueError):broadcast(rpc,self.p,self.cert,self.f['raw_deploy'],self.f['raw_genesis'],fee_wei('0.002'),'unused','unused',ARTIFACT,ROOT)
    def state_machine(self,rpc,journal,output,**kwargs):
        # Exercise the internal wire state machine, not the outer fixture broadcast
        # guard. The real CLI's guard is tested above and never disabled in production.
        return run_stages(rpc,self.p,self.cert,self.first,self.second,fee_wei('0.002'),journal,output,timeout=.04,poll=.001,**kwargs)
    def test_complete_offline_wire_flow_checks_three_confirmations(self):
        rpc=MainnetWire(self.p,self.cert,self.first,self.second)
        with tempfile.TemporaryDirectory() as tmp:
            result=self.state_machine(rpc,Path(tmp)/'journal.json',Path(tmp)/'deployment.json')
            self.assertEqual(result['status'],'deployed');self.assertGreaterEqual(result['confirmations_at_check'],3)
            self.assertEqual(result['notice'],NOTICE);self.assertEqual(result['official_token_ca'],TOKEN_CA)
            self.assertEqual(result['bscscan_verification_status'],'not_requested');self.assertLessEqual(result['total_fee_paid_wei'],fee_wei('0.002'))
            self.assertEqual(rpc.sent,[self.first['hash'],self.second['hash']])
    def test_lost_response_resume_never_resends(self):
        rpc=MainnetWire(self.p,self.cert,self.first,self.second);rpc.uncertain=True
        with tempfile.TemporaryDirectory() as tmp:
            journal=Path(tmp)/'journal.json';out=Path(tmp)/'out.json'
            with self.assertRaises(TimeoutError):self.state_machine(rpc,journal,out)
            self.assertEqual(json.loads(journal.read_text())['deploy_state'],'sending')
            self.state_machine(rpc,journal,out)
            self.assertEqual(rpc.sent,[self.first['hash'],self.second['hash']])
            self.state_machine(rpc,journal,out);self.assertEqual(len(rpc.sent),2)
    def test_unknown_submission_cannot_be_retried_automatically(self):
        rpc=MainnetWire(self.p,self.cert,self.first,self.second);rpc.no_accept=True
        with tempfile.TemporaryDirectory() as tmp:
            journal=Path(tmp)/'journal.json';out=Path(tmp)/'out.json'
            with self.assertRaises(TimeoutError):self.state_machine(rpc,journal,out)
            with self.assertRaises(TimeoutError):self.state_machine(rpc,journal,out)
            self.assertEqual(rpc.sent,[self.first['hash']]);self.assertFalse(out.exists())
    def test_wrong_chain_nonce_balance_stop_before_send(self):
        for key,value in [('wrong_chain',True),('nonce',55),('balance',0)]:
            rpc=MainnetWire(self.p,self.cert,self.first,self.second);setattr(rpc,key,value)
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):self.state_machine(rpc,Path(tmp)/'j',Path(tmp)/'o')
            self.assertEqual(rpc.sent,[])
    def test_reorg_fails_without_genesis_send(self):
        rpc=MainnetWire(self.p,self.cert,self.first,self.second);rpc.reorg=True
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'reorg'):self.state_machine(rpc,Path(tmp)/'j',Path(tmp)/'o')
            self.assertEqual(len(rpc.sent),1)
    def test_journal_mismatch_and_concurrent_operator_fail_closed(self):
        rpc=MainnetWire(self.p,self.cert,self.first,self.second)
        with tempfile.TemporaryDirectory() as tmp:
            j=Path(tmp)/'j';j.write_text('{"plan_hash":"different"}')
            with self.assertRaisesRegex(ValueError,'Journal'):self.state_machine(rpc,j,Path(tmp)/'o')
            with journal_lock(j):
                with self.assertRaises(ValueError):
                    with journal_lock(j):pass
    def test_failed_receipt_cannot_publish_deployed_status(self):
        rpc=MainnetWire(self.p,self.cert,self.first,self.second)
        rpc.call('eth_sendRawTransaction',[self.first['raw']]);rpc.receipts[self.first['hash']]['status']='0x0'
        with self.assertRaisesRegex(ValueError,'failed'):wait_confirmed(rpc,self.first['hash'],timeout=.01,poll=0)

    def test_bscscan_command_pins_exact_compiler_and_is_not_verification(self):
        command=self.p['verify_command']
        for flag in ['--chain 56','--verifier etherscan','--compiler-version v0.8.30+commit.73712a01','--num-of-optimizations 200','--evm-version paris']:
            self.assertIn(flag,command)
        self.assertNotIn('--private-key',command);self.assertNotIn('--etherscan-api-key',command)

    def test_deployment_record_not_faked_by_preparation(self):
        record=json.loads((ROOT/'deployments/bsc-mainnet.json').read_text())
        if record['status']=='not_deployed':
            self.assertIsNone(record['registry_address']);self.assertIsNone(record['genesis_commit_tx_hash'])
        self.assertEqual(record['contract_type'],'INFRASTRUCTURE_CONTINUITY_LOG');self.assertEqual(record['official_token_ca'],TOKEN_CA)


class SignerConfigurationTests(unittest.TestCase):
    def seed(self):return hashlib.sha256(b'SynaFly PR10 PUBLIC CODEC FIXTURE - NEVER FUND').digest()
    def fake_api(self):
        class Account:
            @staticmethod
            def from_key(key):
                point=multiply(int.from_bytes(key,'big'),G)
                address='0x'+keccak256(point[0].to_bytes(32,'big')+point[1].to_bytes(32,'big'))[-20:].hex()
                return SimpleNamespace(address=address)
        return Account,lambda **kw:kw
    def test_hmac_derivation_matches_sdk_generated_public_addresses(self):
        env={'PUBLIC_TEST_KEY':self.seed().hex()}
        with patch('synafly_lab.mainnet_signer.account_api',self.fake_api):signer=EnvironmentSigner(env,'PUBLIC_TEST_KEY')
        self.assertEqual(signer.address,FIXTURE['plan']['deployer']);self.assertEqual(signer.committee,FIXTURE['plan']['committee'])
        self.assertEqual(env,{});self.assertIn('REDACTED',repr(signer))
    def test_explicit_loaded_witnesses_are_equivalent_and_pop_secrets(self):
        env={'PUBLIC_TEST_KEY':self.seed().hex(),**{f'BSC_WITNESS_{i}_PRIVATE_KEY':derive_witness(self.seed(),i).hex() for i in range(1,4)}}
        with patch('synafly_lab.mainnet_signer.account_api',self.fake_api):signer=EnvironmentSigner(env,'PUBLIC_TEST_KEY','loaded')
        self.assertEqual(signer.committee,FIXTURE['plan']['committee']);self.assertEqual(env,{})
    def test_bad_missing_partial_keys_redact_values(self):
        secret='do-not-print-this-invalid-value'
        with patch('synafly_lab.mainnet_signer.account_api',self.fake_api):
            with self.assertRaises(ValueError) as err:EnvironmentSigner({'PUBLIC_TEST_KEY':secret},'PUBLIC_TEST_KEY')
            self.assertNotIn(secret,str(err.exception))
            with self.assertRaises(ValueError):EnvironmentSigner({'PUBLIC_TEST_KEY':self.seed().hex()},'PUBLIC_TEST_KEY','loaded')
    def test_claims_update_requires_confirmed_genesis(self):
        spec=importlib.util.spec_from_file_location('claim_cli',ROOT/'scripts/deploy_bsc_mainnet.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'claims.md';original='| BSC public-chain checkpoint exists | Not deployed | No receipt |\n';path.write_text(original)
            with self.assertRaises(ValueError):module.publish_claims({'status':'prepared'},path)
            self.assertEqual(path.read_text(),original)
            module.publish_claims({'status':'deployed','chain_id':56,'confirmations_at_check':3},path)
            self.assertIn('Deployed permissioned log, no economic claim',path.read_text())

    def test_invalid_cli_arguments_do_not_echo_accidental_secret_values(self):
        import subprocess,sys
        marker='not-a-real-key-but-never-echo-this'
        result=subprocess.run([sys.executable,str(ROOT/'scripts/deploy_bsc_mainnet.py'),'broadcast','--private-key',marker],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(result.returncode,0);self.assertNotIn(marker,result.stdout+result.stderr)

    def test_cli_dry_run_offline_and_compiler_secret_filter(self):
        spec=importlib.util.spec_from_file_location('mainnet_cli',ROOT/'scripts/deploy_bsc_mainnet.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch.dict('os.environ',{'BSC_MAINNET_PRIVATE_KEY':'never-forward-to-compiler'}),patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run,patch.object(module,'load',return_value=ARTIFACT):
            module.artifact();self.assertNotIn('BSC_MAINNET_PRIVATE_KEY',run.call_args.kwargs['env'])
        with tempfile.TemporaryDirectory() as tmp:
            import subprocess,sys
            output=Path(tmp)/'plan.json'
            subprocess.run([sys.executable,str(ROOT/'scripts/deploy_bsc_mainnet.py'),'dry-run','--fixture','--artifact',str(ROOT/'tests/fixtures/mainnet-build.json'),'--out',str(output)],capture_output=True,check=True,timeout=10)
            result=json.loads(output.read_text());self.assertEqual(result['status'],'dry_run_only');self.assertEqual(result['public_transactions_sent'],0)

if __name__=='__main__':unittest.main()
