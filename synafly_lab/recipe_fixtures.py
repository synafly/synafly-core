"""Original small EVM programs, not real-world contract or traffic coverage."""
SELECTOR = bytes.fromhex('a1b2c3d4')


def push(value):
    if not 0 <= value < 65536: raise ValueError('Fixture immediate')
    return bytes([0x60, value]) if value < 256 else b'\x61' + value.to_bytes(2, 'big')


def mapping(base, source='arg'):
    key = push(4) + b'\x35' if source == 'arg' else b'\x33'
    return key + push(0) + b'\x52' + push(base) + push(32) + b'\x52' + push(64) + push(0) + b'\x20'


RETURN = bytes.fromhex('60005260206000f3')


def fixture_codes():
    direct = mapping(7) + b'\x54' + RETURN
    # Inner hash becomes the second word of an outer mapping preimage.
    nested = mapping(11) + push(32) + b'\x52' + push(36) + b'\x35' + push(0) + b'\x52' + push(64) + push(0) + b'\x20\x54' + RETURN
    caller = mapping(13, 'caller') + b'\x54' + RETURN
    write = mapping(9) + bytes.fromhex('8054600101905500')
    code = bytearray(); patches = []
    for branch in range(3):
        code.extend(push(68) + b'\x35' + push(3) + b'\x16' + push(branch) + b'\x14\x61')
        patches.append(len(code)); code.extend(b'\x00\x00\x57')
    code.extend(mapping(23) + b'\x54' + RETURN)
    for branch, base in enumerate((15, 17, 19)):
        location = len(code); code[patches[branch]:patches[branch] + 2] = location.to_bytes(2, 'big')
        code.extend(b'\x5b' + mapping(base) + b'\x54' + RETURN)
    unsupported = bytes.fromhex('600160005360005400')  # MSTORE8 is deliberately outside the grammar.
    return {'direct': direct, 'nested': nested, 'caller': caller, 'write': write,
            'branch': bytes(code), 'unsupported': unsupported,
            'state-dependent': bytes.fromhex('60005454') + RETURN,
            'upgrade': mapping(29) + b'\x54' + RETURN}


def calldata(first, second, route):
    return SELECTOR + b''.join(x.to_bytes(32, 'big') for x in (first, second, route))


def owner(index): return '0x' + f'{0xa0001000 + index:040x}'
