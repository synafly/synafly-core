"""Strict canonical JSON for a bounded integer-only protocol."""
import hashlib
import json

class Invalid(ValueError):
    pass

def integer(value, low, high, label='integer'):
    if type(value) is not int or not low<=value<=high: raise Invalid('Invalid '+label)
    return value

def keys(value, expected):
    if type(value) is not dict or set(value)!=set(expected): raise Invalid('Unexpected fields')

def canonical(value):
    def validate(v, depth=0):
        if depth>16: raise Invalid('Object too deep')
        if type(v) is int: integer(v,-(2**63-1),2**63-1)
        elif type(v) is str:
            if len(v)>4096: raise Invalid('String too long')
        elif type(v) is list:
            if len(v)>50000: raise Invalid('List too long')
            for item in v: validate(item,depth+1)
        elif type(v) is dict:
            if len(v)>32 or not all(type(k) is str for k in v): raise Invalid('Invalid object')
            for item in v.values(): validate(item,depth+1)
        else: raise Invalid('Only integer, string, list and object values are allowed')
    validate(value)
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()

def digest(value): return hashlib.sha256(canonical(value)).hexdigest()

def parse(raw):
    def pairs(items):
        d={}
        for k,v in items:
            if k in d: raise Invalid('Duplicate JSON key')
            d[k]=v
        return d
    try:
        value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(Invalid('Nonfinite JSON')))
        canonical(value); return value
    except (RecursionError,UnicodeError,json.JSONDecodeError) as e: raise Invalid('Malformed JSON') from e
