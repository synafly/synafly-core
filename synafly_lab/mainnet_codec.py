"""Bounded canonical RLP / EIP-155 / EIP-1559 verification, never private-key signing."""
from .keccak import keccak256
from .quorum_crypto import recover_address
from .rpc import hex_data


def rlp(value):
    if type(value) is int:
        if value<0 or value>=2**256:raise ValueError('RLP integer bound')
        value=value.to_bytes((value.bit_length()+7)//8,'big')
    if type(value) is list:
        data=b''.join(rlp(item) for item in value);offset=0xc0
    elif type(value) is bytes:
        if len(value)==1 and value[0]<0x80:return value
        data=value;offset=0x80
    else:raise ValueError('RLP type')
    if len(data)<=55:return bytes([offset+len(data)])+data
    length=len(data).to_bytes((len(data).bit_length()+7)//8,'big')
    return bytes([offset+55+len(length)])+length+data


def unrlp(raw):
    if type(raw) is not bytes or not 0<len(raw)<=65536:raise ValueError('RLP size')
    items=[0]
    def read(index,depth=0):
        items[0]+=1
        if index>=len(raw) or depth>4 or items[0]>64:raise ValueError('RLP nesting/truncation')
        first=raw[index];start=index+1
        if first<0x80:return bytes([first]),start
        is_list=first>=0xc0;base=0xc0 if is_list else 0x80
        if first-base<=55:length=first-base
        else:
            size=first-base-55
            if size>4 or start+size>len(raw) or raw[start]==0:raise ValueError('RLP length encoding')
            length=int.from_bytes(raw[start:start+size],'big');start+=size
            if length<=55:raise ValueError('Noncanonical long RLP')
        end=start+length
        if end>len(raw):raise ValueError('RLP truncated item')
        if not is_list:
            value=raw[start:end]
            if len(value)==1 and value[0]<0x80:raise ValueError('Noncanonical RLP byte')
            return value,end
        value=[];cursor=start
        while cursor<end:
            item,cursor=read(cursor,depth+1);value.append(item)
        if cursor!=end:raise ValueError('RLP list boundary')
        return value,end
    value,end=read(0)
    if end!=len(raw) or rlp(value)!=raw:raise ValueError('Trailing/noncanonical RLP')
    return value


def integer(raw):
    if type(raw) is not bytes or len(raw)>32 or raw and raw[0]==0:raise ValueError('Canonical transaction integer')
    return int.from_bytes(raw,'big')


def create_address(sender,nonce):
    sender=bytes.fromhex(hex_data(sender,20)[2:])
    if type(nonce) is not int or not 0<=nonce<2**64:raise ValueError('Nonce bound')
    return '0x'+keccak256(rlp([sender,nonce]))[-20:].hex()


def decode_transaction(value):
    raw=bytes.fromhex(hex_data(value,maximum=65536)[2:])
    if not raw:raise ValueError('Empty transaction')
    if raw[0]==2:
        fields=unrlp(raw[1:])
        if type(fields) is not list or len(fields)!=12 or fields[8]!=[]:raise ValueError('Type2 profile requires empty access list')
        chain,nonce,tip,fee,gas=map(integer,fields[:5]);to,amount,data=fields[5:8]
        parity,r,s=map(integer,fields[9:]);unsigned=b'\x02'+rlp(fields[:9]);kind=2
        if tip>fee:raise ValueError('Priority fee exceeds cap')
    elif raw[0]>=0xc0:
        fields=unrlp(raw)
        if type(fields) is not list or len(fields)!=9:raise ValueError('Legacy transaction fields')
        nonce,fee,gas=map(integer,fields[:3]);to,amount,data=fields[3:6]
        v,r,s=map(integer,fields[6:])
        if v<35:raise ValueError('Unprotected legacy signature')
        chain=(v-35)//2;parity=(v-35)%2;tip=fee
        unsigned=rlp(fields[:6]+[chain,0,0]);kind=0
    else:raise ValueError('Only EIP-155 and type2 transactions supported')
    if parity not in (0,1) or not 0<chain<2**63 or not 0<=nonce<2**64:raise ValueError('Transaction domain/nonce')
    if type(to) is not bytes or len(to) not in (0,20) or type(data) is not bytes:raise ValueError('Destination/data')
    value=integer(amount)
    signature='0x'+r.to_bytes(32,'big').hex()+s.to_bytes(32,'big').hex()+bytes([27+parity]).hex()
    sender=recover_address('0x'+keccak256(unsigned).hex(),signature)
    return {'type':kind,'chain_id':chain,'nonce':nonce,'max_fee_per_gas':fee,'priority_fee':tip,
            'gas_limit':gas,'to':None if not to else '0x'+to.hex(),'value':value,'data':'0x'+data.hex(),
            'sender':sender,'hash':'0x'+keccak256(raw).hex(),'raw': '0x'+raw.hex()}
