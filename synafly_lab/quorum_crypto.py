"""Public-data secp256k1 recovery, not a private-key signer or audited crypto library.

Curve parameters: Standards for Efficient Cryptography 2 (SEC 2), section 2.4.1.
Ethereum's 27/28 recovery IDs and the registry's low-s rule are enforced.
"""
from .keccak import keccak256
from .rpc import hex_data

P = 0xfffffffffffffffffffffffffffffffffffffffffffffffffffffffefffffc2f
N = 0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141
G = (0x79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798,
     0x483ada7726a3c4655da4fbfc0e1108a8fd17b448a68554199c47d08ffb10d4b8)


def add(a, b):
    if a is None: return b
    if b is None: return a
    x, y = a; u, v = b
    if x == u and (y + v) % P == 0: return None
    slope = (3*x*x * pow(2*y, -1, P) if a == b else (v-y)*pow(u-x, -1, P)) % P
    out_x = (slope*slope - x - u) % P
    return out_x, (slope*(x-out_x)-y) % P


def multiply(k, point):
    result = None
    while k:
        if k & 1: result = add(result, point)
        point = add(point, point); k >>= 1
    return result


def recover_address(digest, signature):
    digest = bytes.fromhex(hex_data(digest, 32)[2:])
    raw = bytes.fromhex(hex_data(signature, 65)[2:])
    r, s, v = int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:64], 'big'), raw[64]
    if not 0 < r < N or not 0 < s <= N//2 or v not in (27, 28):
        raise ValueError('Signature scalar, low-s or v policy')
    square = (r*r*r + 7) % P
    y = pow(square, (P+1)//4, P)
    if y*y % P != square: raise ValueError('Invalid recovery point')
    if y % 2 != v-27: y = P-y
    inverse = pow(r, -1, N); z = int.from_bytes(digest, 'big')
    public = add(multiply(s*inverse % N, (r,y)), multiply(-z*inverse % N, G))
    if public is None: raise ValueError('Infinity signer')
    return '0x' + keccak256(public[0].to_bytes(32,'big') + public[1].to_bytes(32,'big'))[-20:].hex()
