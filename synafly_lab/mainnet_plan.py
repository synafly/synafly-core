"""Exact build/constructor/genesis plans for an infrastructure log, not a token."""
import hashlib
import json
from pathlib import Path
import shlex
from .anchor import word
from .keccak import keccak256
from .mainnet_codec import create_address
from .quorum_relayer import build_checkpoint,committee_policy,strict_hex,integer

TOKEN_CA='0x259dd071f40d96e61f2bcc663bfac6898d957777'
NOTICE='THIS IS AN APPEND-ONLY INFRASTRUCTURE STATE LOG, NOT A TOKEN. CANNOT BE TRADED OR SWAPPED.'
IDENTITY={'contract_type':'INFRASTRUCTURE_CONTINUITY_LOG','official_token_ca':TOKEN_CA,'notice':NOTICE,
          'governance':'single-operator permissioned log; not decentralized multi-party consensus'}
DEFAULT_RPC='https://bsc-dataseed.binance.org/'
SOURCE_SHA='f89ea3ccc5b576ff454c775d57f9c7ca0ccdd8c9b45c3867a935274c97045113'
CONFIG_SHA='26a025c9009741105cdf0897602d147dc20e8efb907ef73f4330656958bdeed1'
COMPILER='0.8.30+commit.73712a01'
CREATION_SHA='c846b357da36f231f43148963794ed05a9343577edddff53ab82963a98eac82e'
RUNTIME_SHA='7d2383c88007a1b55a084bf8e845da4935f5bca8ac5185f6e226d7680e105822'


def constructor_args(committee):
    committee_policy(committee,2)
    if len(committee)!=3 or TOKEN_CA in committee:raise ValueError('Exactly three dedicated witnesses; never use the token contract')
    return '0x'+word(64)+word(2)+word(3)+''.join(address[2:].rjust(64,'0') for address in committee)


def check_build(artifact,root):
    root=Path(root)
    if hashlib.sha256((root/'contracts/ContinuityRegistry.sol').read_bytes()).hexdigest()!=SOURCE_SHA or hashlib.sha256((root/'foundry.toml').read_bytes()).hexdigest()!=CONFIG_SHA:raise ValueError('Solidity/config drift')
    meta=artifact['metadata'];meta=json.loads(meta) if isinstance(meta,str) else meta
    settings=meta['settings']
    if meta['compiler']['version']!=COMPILER or settings.get('optimizer')!={'enabled':True,'runs':200} or settings.get('evmVersion')!='paris' or settings.get('viaIR',False):raise ValueError('Compiler settings drift')
    if hashlib.sha256(bytes.fromhex(artifact['bytecode']['object'][2:])).hexdigest()!=CREATION_SHA or hashlib.sha256(bytes.fromhex(artifact['deployedBytecode']['object'][2:])).hexdigest()!=RUNTIME_SHA:raise ValueError('Exact bytecode digest drift')
    pin=json.loads((root/'tests/fixtures/mainnet-build.json').read_text())
    if artifact['bytecode']['object']!=pin['artifact']['bytecode']['object'] or artifact['deployedBytecode']['object']!=pin['artifact']['deployedBytecode']['object']:raise ValueError('Pinned bytecode mismatch')
    functions={v['name'] for v in artifact['abi'] if v['type']=='function'}
    if functions!={'DOMAIN','commit','heads','isWitness','messageHash','signingDigest','threshold','witnesses'}:raise ValueError('Unexpected interface/token functionality')
    if artifact['deployedBytecode']['immutableReferences']!=pin['artifact']['deployedBytecode']['immutableReferences']:raise ValueError('Immutable offset drift')
    runtime=bytearray.fromhex(artifact['deployedBytecode']['object'][2:])
    for positions in artifact['deployedBytecode']['immutableReferences'].values():
        for position in positions:
            if position['length']!=32:raise ValueError('Immutable layout')
            runtime[position['start']:position['start']+32]=(2).to_bytes(32,'big')
    return '0x'+keccak256(bytes(runtime)).hex()


def plan(artifact,root,receipts,deployer,nonce,committee,*,kind='operator-plan'):
    strict_hex(deployer,20);integer(nonce,0,2**64-2)
    if deployer==TOKEN_CA or deployer in committee:raise ValueError('Deployer/token/witness roles must be distinct')
    committee=sorted(committee,key=lambda a:int(a,16));arguments=constructor_args(committee)
    address=create_address(deployer,nonce)
    if address==TOKEN_CA:raise ValueError('Registry must not be the ecosystem token')
    code_hash=check_build(artifact,root)
    bundle=build_checkpoint(receipts,chain_id=56,registry=address,committee=committee,threshold=2,runtime_code_hash=code_hash)
    command=['forge','verify-contract','--chain','56','--verifier','etherscan','--compiler-version','v'+COMPILER,'--num-of-optimizations','200','--evm-version','paris','--constructor-args',arguments,'--watch',address,'contracts/ContinuityRegistry.sol']
    return {**IDENTITY,'schema':'synafly.mainnet-plan.v1','kind':kind,'network':'bsc-mainnet','chain_id':56,
            'deployer':deployer,'nonce':nonce,'registry_address':address,'committee':committee,'threshold':2,
            'constructor_args':arguments,'creation_data':artifact['bytecode']['object']+arguments[2:],
            'runtime_code_hash':code_hash,'genesis_bundle':bundle,'source_sha256':SOURCE_SHA,'config_sha256':CONFIG_SHA,
            'compiler':COMPILER,'optimizer_runs':200,'evm_version':'paris','verify_command':shlex.join(command),
            'genesis_scope':'Fixed verification receipts, not live-mainnet offload or economic proof'}


def validate_plan(value,artifact,root):
    if value.get('kind') not in {'operator-plan','codec-test-fixture'}:raise ValueError('Plan kind')
    expected=plan(artifact,root,value['genesis_bundle']['receipts'],value['deployer'],value['nonce'],value['committee'],kind=value['kind'])
    if expected!=value:raise ValueError('Plan differs from deterministic reconstruction')
    return value
