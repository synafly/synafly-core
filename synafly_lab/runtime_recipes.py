"""Standard-library runtime adapter for PR #7's frozen feature/projection index.

Historical evidence binds the original evaluator bytes (including its cast helper).
This bounded evaluator retains that expression language with in-process Keccak.
"""
from collections import Counter
from .access_recipes import MASK, UnsupportedTrace, word, digest
from .fly_recipe_index import RecipeIndex, METHODS, features
from .keccak import keccak_int as keccak

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



class RuntimeRecipeIndex(RecipeIndex):
    def query(self, method, code_hash, selector, context):
        if not self.frozen or method not in METHODS: raise ValueError('Frozen query policy')
        namespace = code_hash, selector; pool = self.namespaces.get(namespace, ())
        if not pool: return {'slots': [], 'candidates': 0, 'abstained': True, 'reason': 'unknown-code-or-selector'}
        vector = features(context)
        if method == 'stale-slots':
            return {'slots': list(self.rows[pool[-1]]['slots']), 'candidates': 1, 'abstained': False, 'reason': 'unparameterized-negative-control'}
        if method == 'guarded-union': candidates = pool
        elif method == 'exact-descriptor': candidates = self.exact.get((namespace, vector), ())
        elif method == 'nearest':
            candidates = sorted(pool, key=lambda i: (sum((a - b)**2 for a, b in zip(vector, self.rows[i]['features'])), i))[:8]
        else:
            overlap = Counter()
            for tag in self.tag(vector): overlap.update(self.postings.get((namespace, tag), ()))
            candidates = sorted(overlap, key=lambda i: (-overlap[i], i))[:8]
        # Repeated observations are not distinct recipes. Apply the same
        # de-duplication to all controls before charging/materializing templates.
        unique = {}; predicted = set(); matched = 0
        for i in candidates: unique.setdefault(digest(self.rows[i]['recipe']), i)
        for i in unique.values():
            slots = materialize(self.rows[i]['recipe'], context)
            if slots is not None: matched += 1; predicted.update(slots)
        if len(predicted) > 64: raise ValueError('Prefetch budget')
        return {'slots': sorted(predicted), 'candidates': len(unique), 'abstained': not matched,
                'reason': 'matched-guards' if matched else 'no-matching-candidate'}
