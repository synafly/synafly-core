"""Bounded Ethereum Keccak-256 (legacy 0x01 suffix, NOT FIPS SHA3-256).

Keccak-f[1600], little-endian lanes. No native extension or subprocess needed.
Reference: https://keccak.team/keccak_specs_summary.html
"""
MASK = (1 << 64) - 1
RC = (0x0000000000000001, 0x0000000000008082, 0x800000000000808a,
      0x8000000080008000, 0x000000000000808b, 0x0000000080000001,
      0x8000000080008081, 0x8000000000008009, 0x000000000000008a,
      0x0000000000000088, 0x0000000080008009, 0x000000008000000a,
      0x000000008000808b, 0x800000000000008b, 0x8000000000008089,
      0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
      0x000000000000800a, 0x800000008000000a, 0x8000000080008081,
      0x8000000000008080, 0x0000000080000001, 0x8000000080008008)
ROT = ((0, 36, 3, 41, 18), (1, 44, 10, 45, 2), (62, 6, 43, 15, 61),
       (28, 55, 25, 21, 56), (27, 20, 39, 8, 14))


def rotate(value, count):
    return ((value << count) | (value >> (64 - count))) & MASK


def keccak256(data):
    if type(data) is not bytes or len(data) > 131072:
        raise ValueError('Keccak input bound')
    padded = bytearray(data)
    padded.append(1)
    padded.extend(b'\0' * ((-len(padded)) % 136))
    padded[-1] |= 0x80
    state = [0] * 25
    for offset in range(0, len(padded), 136):
        for i in range(17):
            state[i] ^= int.from_bytes(padded[offset + i*8:offset + i*8 + 8], 'little')
        for rc in RC:
            c = [state[x] ^ state[x+5] ^ state[x+10] ^ state[x+15] ^ state[x+20] for x in range(5)]
            d = [c[(x-1) % 5] ^ rotate(c[(x+1) % 5], 1) for x in range(5)]
            b = [0] * 25
            for x in range(5):
                for y in range(5):
                    b[y + 5*((2*x + 3*y) % 5)] = rotate(state[x + 5*y] ^ d[x], ROT[x][y])
            for x in range(5):
                for y in range(5):
                    state[x + 5*y] = b[x + 5*y] ^ ((~b[(x+1) % 5 + 5*y]) & b[(x+2) % 5 + 5*y])
            state[0] ^= rc
    return b''.join(x.to_bytes(8, 'little') for x in state)[:32]


def keccak_int(data):
    return int.from_bytes(keccak256(data), 'big')
