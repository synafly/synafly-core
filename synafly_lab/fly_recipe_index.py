"""Fly-inspired locality tags for advisory access-recipe retrieval."""
from collections import Counter, defaultdict
import random
from .access_recipes import digest, materialize

METHODS = ('guarded-union', 'exact-descriptor', 'nearest', 'flyhash', 'stale-slots')


def features(context):
    data = context['data']; route = int.from_bytes(data[68:100].ljust(32, b'\0'), 'big')
    vector = []
    for bit in range(8): vector.extend((int(not (route >> bit & 1)), int(bool(route >> bit & 1))))
    length = min(4, len(data) // 32)
    vector.extend(int(i == length) for i in range(5))
    return tuple(vector)


class RecipeIndex:
    def __init__(self):
        rng = random.Random(317)
        self.projection = tuple(tuple(rng.sample(range(21), 6)) for _ in range(256))
        self.rows = []; self.namespaces = defaultdict(list); self.postings = defaultdict(list)
        self.exact = defaultdict(list); self.frozen = False

    def tag(self, vector):
        if len(vector) != 21 or any(type(v) is not int or v not in (0, 1) for v in vector): raise ValueError('Feature schema')
        scores = [(sum(vector[j] for j in inputs), i) for i, inputs in enumerate(self.projection)]
        return tuple(i for _, i in sorted(scores, key=lambda x: (-x[0], x[1]))[:16])

    def add(self, code_hash, selector, context, recipe, actual_slots):
        if self.frozen or len(self.rows) >= 4096: raise ValueError('Catalog frozen or full')
        vector = features(context); namespace = code_hash, selector
        entry = {'namespace': namespace, 'features': vector, 'recipe': recipe, 'slots': sorted(actual_slots)}
        index = len(self.rows); self.rows.append(entry); self.namespaces[namespace].append(index)
        self.exact[namespace, vector].append(index)
        for tag in self.tag(vector): self.postings[namespace, tag].append(index)

    def fingerprint(self): return digest({'projection': self.projection, 'rows': self.rows})

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
