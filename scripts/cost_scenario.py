#!/usr/bin/env python3
"""Arithmetic for explicitly hypothetical edge-cost scenarios; not a forecast."""
import argparse,json
from decimal import Decimal,InvalidOperation
from pathlib import Path
FIELDS={'nodes','monthly_node_cost','rpc_cost_share','offload_share','realization_share','annual_relay_cost','annual_verification_cost','annual_operations_cost'}
def calculate(values):
    if type(values) is not dict or set(values)!=FIELDS:raise ValueError('Provide all scenario fields')
    if type(values['nodes']) is not int or values['nodes']<0:raise ValueError('Node count must be a nonnegative integer')
    nums={}
    for k,v in values.items():
        if isinstance(v,bool):raise ValueError('Boolean is not a scenario number')
        try:n=Decimal(str(v))
        except InvalidOperation:raise ValueError('Invalid scenario number') from None
        if not n.is_finite() or n<0:raise ValueError('Scenario values must be finite and nonnegative')
        if k.endswith('_share') and n>1:raise ValueError('Shares must be between zero and one')
        nums[k]=n
    baseline=12*nums['nodes']*nums['monthly_node_cost']
    gross=baseline*nums['rpc_cost_share']*nums['offload_share']*nums['realization_share']
    added=sum(nums[k] for k in ['annual_relay_cost','annual_verification_cost','annual_operations_cost'])
    money=lambda n:str(n.quantize(Decimal('0.01')))
    return {'kind':'illustrative_scenario_not_measured','inputs':values,'baseline_annual':money(baseline),'gross_avoidable_annual':money(gross),'added_annual_cost':money(added),'net_annual':money(gross-added),'currency':'USD','evidence':'Arithmetic only; no BSC node-count, traffic or fleet-cost measurement is supplied.'}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('inputs');a=p.parse_args();print(json.dumps(calculate(json.loads(Path(a.inputs).read_text())),indent=2))
