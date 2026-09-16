"""User-directed tier weights with explicit units; not a measured price model."""
from decimal import Decimal, InvalidOperation


def score(origin_reads, peer_bytes, origin_weight=200, peer_weight=1):
    for value in (origin_reads, peer_bytes):
        if type(value) is not int or value < 0: raise ValueError('Nonnegative physical counts required')
    for weight in (origin_weight, peer_weight):
        if type(weight) not in (int, str, Decimal): raise ValueError('Weight type')
        try: value = Decimal(weight)
        except InvalidOperation: raise ValueError('Invalid weight') from None
        if not value.is_finite() or value <= 0: raise ValueError('Positive finite weights required')
    return Decimal(origin_weight) * origin_reads + Decimal(peer_weight) * Decimal(peer_bytes) / Decimal(1_000_000)


def compare(before_reads, before_bytes, after_reads, after_bytes, origin_weight=200):
    before = score(before_reads, before_bytes, origin_weight)
    after = score(after_reads, after_bytes, origin_weight)
    if before == 0: raise ValueError('Nonzero baseline required')
    return {'origin_weight_per_read': origin_weight, 'peer_weight_per_decimal_mb': 1,
            'before_score': str(before), 'after_score': str(after), 'score_reduction': str(before - after),
            'score_reduction_percent': str(((before - after) / before * 100).quantize(Decimal('.000001'))),
            'avoided_origin_reads': before_reads - after_reads,
            'origin_read_reduction_percent': str((Decimal(before_reads - after_reads) / before_reads * 100).quantize(Decimal('.000001'))) if before_reads else None}
