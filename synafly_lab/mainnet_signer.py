"""Optional operator-invoked environment signer. Never used by default or by CI.

Uses eth-account, not the research public-key recovery code, to sign. No keys in
argv/files/logs. Python cannot guarantee cryptographic memory zeroization.
"""
import hashlib
import hmac
import importlib.metadata
import re
from .quorum_crypto import N
from .quorum_relayer import aggregate_signatures,validate_bundle
from .offload_receipts import digest

WITNESS_DOMAIN=b'SynaFly.BSC.Mainnet.ContinuityWitness.v1\x00'


def account_api():
    try:
        if importlib.metadata.version('eth-account')!='0.14.0':raise ValueError('version')
        from eth_account import Account
        from eth_account.messages import encode_defunct
        return Account,encode_defunct
    except Exception:raise ValueError('Install the pinned optional mainnet signing dependencies first') from None


def take_key(env,name):
    if type(name) is not str or not re.fullmatch('[A-Z][A-Z0-9_]{0,63}',name):raise ValueError('Invalid environment variable name')
    value=env.pop(name,None)
    if type(value) is not str or not re.fullmatch('(0x)?[0-9a-fA-F]{64}',value):raise ValueError('Signing key missing/invalid; value redacted')
    key=bytes.fromhex(value.removeprefix('0x'))
    if not 0<int.from_bytes(key,'big')<N:raise ValueError('Signing scalar invalid; value redacted')
    return key


def derive_witness(master,index):
    if type(master) is not bytes or len(master)!=32 or type(index) is not int or not 1<=index<=3:raise ValueError('Witness derivation input')
    for counter in range(256):
        value=hmac.new(master,WITNESS_DOMAIN+(56).to_bytes(8,'big')+bytes([index,counter]),hashlib.sha256).digest()
        if 0<int.from_bytes(value,'big')<N:return value
    raise ValueError('Witness derivation failed')


class EnvironmentSigner:
    def __repr__(self):return '<EnvironmentSigner secrets=REDACTED>'
    def __init__(self,env,name='BSC_MAINNET_PRIVATE_KEY',mode='derived'):
        if mode not in {'derived','loaded'}:raise ValueError('Choose derived or loaded witnesses')
        Account,self.encode_message=account_api()
        master=take_key(env,name)
        keys=[derive_witness(master,i) for i in range(1,4)] if mode=='derived' else [take_key(env,f'BSC_WITNESS_{i}_PRIVATE_KEY') for i in range(1,4)]
        try:
            self._deployer=Account.from_key(master)
            self._witnesses={Account.from_key(key).address.lower():Account.from_key(key) for key in keys}
        except Exception:raise ValueError('Account initialization failed; private values redacted') from None
        self.address=self._deployer.address.lower();self.committee=sorted(self._witnesses,key=lambda a:int(a,16));self.mode=mode
        if len(self.committee)!=3 or self.address in self.committee:raise ValueError('Three distinct dedicated witness accounts required')
    def certificate(self,bundle):
        validate_bundle(bundle)
        if bundle['chain_id']!=56:raise ValueError('Witness signing restricted to chain 56')
        if bundle['committee']!=self.committee:raise ValueError('Plan committee differs from signer')
        envelopes=[]
        for address in self.committee[:2]:
            try:signed=self._witnesses[address].sign_message(self.encode_message(hexstr=bundle['message_hash']))
            except Exception:raise ValueError('Witness signing failed; private values redacted') from None
            envelopes.append({'signer':address,'signature':'0x'+bytes(signed.signature).hex(),'bundle_hash':digest(bundle)})
        return aggregate_signatures(bundle,envelopes)
    def transaction(self,tx):
        if tx.get('chainId')!=56 or tx.get('value',0)!=0:raise ValueError('Signer only supports zero-value chain-56 infrastructure transactions')
        try:return '0x'+bytes(self._deployer.sign_transaction(tx).raw_transaction).hex()
        except Exception:raise ValueError('Transaction signing failed; private values redacted') from None
