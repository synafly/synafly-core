"""Bounded symbolic recipes from concrete EVM traces. Hints, never state values."""
from functools import lru_cache
import hashlib
import json
import re
import subprocess

MASK = 2**256 - 1
UNKNOWN = ('unknown',)


class UnsupportedTrace(ValueError): pass


@lru_cache(maxsize=4096)
def keccak(data):
    if type(data) is not bytes or len(data) > 4096: raise ValueError('Keccak input bound')
    result = subprocess.run(['cast', 'keccak', '--threads', '1'], input='0x' + data.hex(),
                            text=True, capture_output=True, timeout=5, check=True)
    value = result.stdout.strip()
    if not re.fullmatch(r'0x[0-9a-f]{64}', value): raise ValueError('Keccak provider result')
    return int(value, 16)


def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
def digest(value): return hashlib.sha256(canonical(value)).hexdigest()
def freeze(value): return tuple(freeze(x) for x in value) if isinstance(value, (list, tuple)) else value
def word(value): return (value & MASK).to_bytes(32, 'big')


def call_context(data, caller, value=0):
    if type(data) is not bytes or len(data) > 256: raise ValueError('Calldata bound')
    if type(caller) is not int or not 0 <= caller < 2**160: raise ValueError('Caller bound')
    if type(value) is not int or not 0 <= value <= MASK: raise ValueError('Call value')
    return {'data': data, 'caller': caller, 'value': value}


def evaluate(expr, context, depth=0, budget=None):
    if budget is None: budget = [512]
    budget[0] -= 1
    if budget[0] < 0: raise UnsupportedTrace('Expression work bound')
    if depth > 16 or type(expr) not in (list, tuple) or not expr: raise UnsupportedTrace('Expression bound')
    op, *args = expr
    arity = {'const': 1, 'arg': 1, 'caller': 0, 'callvalue': 0, 'size': 0,
             'add': 2, 'sub': 2, 'mul': 2, 'and': 2, 'or': 2, 'xor': 2, 'eq': 2,
             'lt': 2, 'gt': 2, 'shr': 2, 'shl': 2, 'iszero': 1, 'not': 1}
    if op == 'hash':
        if not 1 <= len(args) <= 4: raise UnsupportedTrace('Hash word bound')
        return keccak(b''.join(word(evaluate(a, context, depth + 1, budget)) for a in args))
    if op not in arity or len(args) != arity[op]: raise UnsupportedTrace('Unknown expression')
    if op == 'const':
        if type(args[0]) is not int or not 0 <= args[0] <= MASK: raise UnsupportedTrace('Constant bound')
        return args[0]
    if op == 'arg':
        if type(args[0]) is not int or not 0 <= args[0] <= 224: raise UnsupportedTrace('Argument offset')
        return int.from_bytes(context['data'][args[0]:args[0] + 32].ljust(32, b'\0'), 'big')
    if op in {'caller', 'callvalue', 'size'}:
        return len(context['data']) if op == 'size' else context['caller' if op == 'caller' else 'value']
    values = [evaluate(a, context, depth + 1, budget) for a in args]; a = values[0]
    if op == 'iszero': return int(a == 0)
    if op == 'not': return MASK ^ a
    b = values[1]
    if op == 'add': return (a + b) & MASK
    if op == 'sub': return (a - b) & MASK
    if op == 'mul': return a * b & MASK
    if op == 'and': return a & b
    if op == 'or': return a | b
    if op == 'xor': return a ^ b
    if op == 'eq': return int(a == b)
    if op == 'lt': return int(a < b)
    if op == 'gt': return int(a > b)
    if op == 'shr': return b >> a if a < 256 else 0
    if op == 'shl': return (b << a) & MASK if a < 256 else 0
    raise UnsupportedTrace('Unhandled expression')


def numeric(value):
    if type(value) is not str or not re.fullmatch(r'(?:0x)?[0-9a-fA-F]{1,64}', value):
        raise UnsupportedTrace('Trace word')
    return int(value, 16)


def memory_bytes(log):
    memory = log.get('memory')
    if type(memory) is not list or len(memory) > 16: raise UnsupportedTrace('Trace memory bound')
    return b''.join(word(numeric(x)) for x in memory)


def extract(code, trace, context):
    """Executed-path extraction; supported branch guards remain advisory filters."""
    if type(code) is not bytes or len(code) > 4096: raise UnsupportedTrace('Code bound')
    logs = trace.get('structLogs')
    if trace.get('failed') is not False or type(logs) is not list or not 1 <= len(logs) <= 512:
        raise UnsupportedTrace('Failed/unbounded trace')
    stack = []; memory = {}; accesses = []; guards = []; pcs = []
    binary = {'ADD', 'SUB', 'MUL', 'AND', 'OR', 'XOR', 'EQ', 'LT', 'GT', 'SHR', 'SHL'}
    opcodes = {'STOP': 0, 'ADD': 1, 'MUL': 2, 'SUB': 3, 'LT': 0x10, 'GT': 0x11,
        'EQ': 0x14, 'ISZERO': 0x15, 'AND': 0x16, 'OR': 0x17, 'XOR': 0x18, 'NOT': 0x19,
        'SHL': 0x1b, 'SHR': 0x1c, 'SHA3': 0x20, 'KECCAK256': 0x20,
        'CALLER': 0x33, 'CALLVALUE': 0x34, 'CALLDATALOAD': 0x35, 'CALLDATASIZE': 0x36,
        'POP': 0x50, 'MLOAD': 0x51, 'MSTORE': 0x52, 'SLOAD': 0x54, 'SSTORE': 0x55,
        'JUMP': 0x56, 'JUMPI': 0x57, 'JUMPDEST': 0x5b, 'PUSH0': 0x5f, 'RETURN': 0xf3}
    if logs[0].get('pc') != 0: raise UnsupportedTrace('Incomplete trace prefix')
    for index, log in enumerate(logs):
        pc, op = log.get('pc'), log.get('op')
        if type(pc) is not int or not 0 <= pc < len(code) or log.get('depth') != 1:
            raise UnsupportedTrace('Trace frame/pc')
        if type(op) is not str: raise UnsupportedTrace('Opcode name')
        opcode = opcodes.get(op)
        for prefix, base, limit in [('PUSH', 0x5f, 32), ('DUP', 0x7f, 16), ('SWAP', 0x8f, 16)]:
            if op.startswith(prefix) and op[len(prefix):].isdigit():
                n = int(op[len(prefix):])
                if 1 <= n <= limit: opcode = base + n
        if opcode is None: raise UnsupportedTrace('Unsupported opcode: ' + op)
        if code[pc] != opcode: raise UnsupportedTrace('Opcode/code disagreement')
        concrete = log.get('stack')
        if type(concrete) is not list or len(concrete) != len(stack) or len(stack) > 64:
            raise UnsupportedTrace('Trace stack alignment')
        for expr, actual in zip(stack, concrete):
            if expr != UNKNOWN and evaluate(expr, context) != numeric(actual):
                raise UnsupportedTrace('Symbolic/concrete stack disagreement')
        pcs.append(pc)
        def pop():
            if not stack: raise UnsupportedTrace('Stack underflow')
            return stack.pop()
        def constant(expr):
            if expr[0] != 'const': raise UnsupportedTrace('Dynamic memory or calldata offset')
            return expr[1]
        if op == 'PUSH0': stack.append(('const', 0))
        elif op.startswith('PUSH') and op[4:].isdigit():
            n = int(op[4:])
            if not 1 <= n <= 32 or code[pc] != 0x5f + n or pc + n >= len(code): raise UnsupportedTrace('Push encoding')
            stack.append(('const', int.from_bytes(code[pc + 1:pc + 1 + n], 'big')))
        elif op.startswith('DUP') and op[3:].isdigit():
            n = int(op[3:])
            if not 1 <= n <= 16 or len(stack) < n: raise UnsupportedTrace('DUP depth')
            stack.append(stack[-n])
        elif op.startswith('SWAP') and op[4:].isdigit():
            n = int(op[4:])
            if not 1 <= n <= 16 or len(stack) <= n: raise UnsupportedTrace('SWAP depth')
            stack[-1], stack[-1 - n] = stack[-1 - n], stack[-1]
        elif op == 'CALLDATALOAD': stack.append(('arg', constant(pop())))
        elif op in {'CALLER', 'CALLVALUE', 'CALLDATASIZE'}:
            stack.append(({'CALLER': 'caller', 'CALLVALUE': 'callvalue', 'CALLDATASIZE': 'size'}[op],))
        elif op == 'POP': pop()
        elif op in binary:
            a, b = pop(), pop(); stack.append(UNKNOWN if UNKNOWN in (a, b) else (op.lower(), a, b))
        elif op in {'ISZERO', 'NOT'}:
            a = pop(); stack.append(UNKNOWN if a == UNKNOWN else (op.lower(), a))
        elif op == 'MSTORE':
            offset, value = constant(pop()), pop()
            if offset % 32 or not 0 <= offset <= 480: raise UnsupportedTrace('Memory offset bound')
            memory[offset] = value
        elif op == 'MLOAD':
            offset = constant(pop())
            if offset % 32 or not 0 <= offset <= 480: raise UnsupportedTrace('Memory offset bound')
            stack.append(memory.get(offset, ('const', 0)))
        elif op in {'SHA3', 'KECCAK256'}:
            offset, size = constant(pop()), constant(pop())
            if offset % 32 or size % 32 or not 32 <= size <= 128 or offset + size > 512:
                raise UnsupportedTrace('Hash memory slice')
            words = tuple(memory.get(i, ('const', 0)) for i in range(offset, offset + size, 32))
            if UNKNOWN in words: raise UnsupportedTrace('Hash depends on unknown state')
            expected = b''.join(word(evaluate(x, context)) for x in words)
            if memory_bytes(log)[offset:offset + size] != expected: raise UnsupportedTrace('Hash preimage mismatch')
            stack.append(('hash', *words))
        elif op in {'SLOAD', 'SSTORE'}:
            key = pop(); evaluate(key, context)
            accesses.append((op.lower(), key))
            if op == 'SLOAD': stack.append(UNKNOWN)
            else: pop()
        elif op in {'JUMP', 'JUMPI'}:
            destination = constant(pop())
            if op == 'JUMPI':
                condition = pop(); taken = bool(evaluate(condition, context))
                guards.append((condition, taken))
            else: taken = True
            following = logs[index + 1]['pc'] if index + 1 < len(logs) else None
            if following != (destination if taken else pc + 1): raise UnsupportedTrace('Control-flow disagreement')
        elif op == 'JUMPDEST': pass
        elif op == 'RETURN': pop(); pop()
        elif op == 'STOP': pass
        else: raise UnsupportedTrace('Unsupported opcode: ' + str(op))
        if op in {'RETURN', 'STOP'}:
            if index != len(logs) - 1: raise UnsupportedTrace('Trace after halt')
        elif op not in {'JUMP', 'JUMPI'}:
            size = int(op[4:]) if op.startswith('PUSH') else 0
            if index + 1 == len(logs) or logs[index + 1]['pc'] != pc + 1 + size:
                raise UnsupportedTrace('Trace instruction sequence')
        if len(accesses) > 16 or len(guards) > 16: raise UnsupportedTrace('Recipe bound')
    if logs[-1]['op'] not in {'STOP', 'RETURN'}: raise UnsupportedTrace('Incomplete trace termination')
    if not accesses: raise UnsupportedTrace('No storage footprint')
    recipe = {'accesses': freeze(accesses), 'guards': freeze(guards)}
    if len(canonical(recipe)) > 16384: raise UnsupportedTrace('Recipe encoding bound')
    return recipe


def materialize(recipe, context):
    accesses, guards = recipe.get('accesses'), recipe.get('guards')
    if not 1 <= len(accesses) <= 16 or len(guards) > 16: raise UnsupportedTrace('Recipe collection bound')
    for expr, expected in guards:
        if type(expected) is not bool: raise UnsupportedTrace('Guard type')
        if bool(evaluate(expr, context)) != expected: return None
    result = set()
    for kind, expr in accesses:
        if kind not in {'sload', 'sstore'}: raise UnsupportedTrace('Access kind')
        result.add(evaluate(expr, context))
    return result


def footprint(trace):
    if trace.get('failed') is not False: raise UnsupportedTrace('Failed authoritative call')
    return {numeric(log['stack'][-1]) for log in trace['structLogs'] if log['depth'] == 1 and log['op'] in {'SLOAD', 'SSTORE'}}
