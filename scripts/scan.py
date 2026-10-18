#!/usr/bin/env python3
"""Run one bounded public-source scan and persist an auditable update report."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET

from catalog import ROOT, DATA, arxiv_entry, fetch, now, read_json, read_jsonl, write_json, write_jsonl
from arxiv_metadata import consider_paper, parse_search_feed, timestamp
from network_budget import (RoundBudget, budget_from_environment, ENV_PHASE,
                            DEFAULT_REQUESTS, DEFAULT_SECONDS)


def counts():
    rows=read_jsonl(DATA/'catalog.jsonl')
    report=read_json(DATA/'coverage.json',{})
    return {'resources':len(rows),'bibliographic_records':sum(row['id'].startswith('arxiv:') for row in rows),
        'arxiv_archive_records':len(read_jsonl(DATA/'papers.jsonl')),'excluded_records':report.get('excluded_records',0),
        'curated_papers':sum(r['kind'] in {'paper','survey'} and r['review_status']=='curated' for r in rows),
        'curated_resources':sum(r['kind'] not in {'paper','survey'} and r['review_status']=='curated' for r in rows),
        'candidates':len(read_jsonl(DATA/'candidates.jsonl'))}


# Phase caps never extend the single network-round deadline. Offline build/validation
# retain separate time, leaving room for the workflow's remaining quality gate.
STAGE_TIMEOUTS={'discovery':300,'huggingface':300,'recent_arxiv':600,'harvest':300,'build':180,'validate':180}
NETWORK_STAGES=frozenset({'discovery','huggingface','recent_arxiv','harvest'})


def stage(name,command):
    started=now();timeout=STAGE_TIMEOUTS[name]
    budget=budget_from_environment()
    if budget and name in NETWORK_STAGES:
        timeout=min(timeout,budget.remaining_seconds())
        if timeout<=0:
            return {'name':name,'started_at':started,'finished_at':now(),'ok':False,'deferred':True,
                    'request_attempted':False,'error':'Network round wall budget exhausted; no phase started.'}
    env={**os.environ,ENV_PHASE:name}
    try:
        result=subprocess.run([sys.executable,*command],cwd=ROOT,encoding='utf-8',errors='replace',stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout,env=env)
        print(result.stdout[-6000:],flush=True)
        return {'name':name,'started_at':started,'finished_at':now(),'ok':result.returncode==0,'exit_code':result.returncode,
            'error':result.stdout[-1600:] if result.returncode else None}
    except subprocess.TimeoutExpired:
        return {'name':name,'started_at':started,'finished_at':now(),'ok':False,
                'error':f'Stage exceeded {timeout:g} seconds; last verified records retained.'}


def recent_result_path(budget):
    return budget.path.with_name(budget.run_id+'-recent-result.json')


def recent_phase():
    """Run the recent search under the same hard outer timeout as other network phases."""
    budget=budget_from_environment()
    if budget is None:raise ValueError('Recent scan phase requires a network-round context')
    output=recent_result_path(budget)
    output.unlink(missing_ok=True)
    result=stage('recent_arxiv',['scripts/scan.py','--recent-only'])
    if not result['ok']:return result
    receipt=read_json(output,{})
    phase=receipt.get('phase')
    if (receipt.get('round_id')!=budget.run_id or not isinstance(phase,dict)
            or phase.get('name')!='recent_arxiv' or type(phase.get('ok')) is not bool):
        return {**result,'ok':False,'error':'No complete recent-search receipt for this network round.'}
    return {**result,**phase}


def reusable_recent_snapshot(report, query, since, cutoff, day):
    """Reuse only a complete, successful snapshot for this UTC publication cycle."""
    if not isinstance(report,dict) or report.get('schema_version')!=2 or report.get('scan_day_utc')!=day:
        return False
    if (report.get('query'),report.get('since_utc'),report.get('cutoff_utc'))!=(query,since,cutoff):
        return False
    total=report.get('reported_total')
    pages=report.get('pages')
    def valid_page(page):
        if not isinstance(page,dict) or page.get('ok') is not True:return False
        try:retrieved=timestamp(page.get('retrieved_at'))
        except (ValueError,TypeError,AttributeError):return False
        return bool(retrieved and retrieved[:10]==day and isinstance(page.get('sha256'),str)
                    and re.fullmatch('[0-9a-f]{64}',page['sha256']))
    return (report.get('complete') is True and not report.get('warnings') and type(total) is int and total>=0
        and type(report.get('unique_returned')) is int and report['unique_returned']==total and isinstance(pages,list) and bool(pages)
        and all(valid_page(page) for page in pages))


def recent_papers(config):
    current=dt.datetime.now(dt.timezone.utc)
    cycle=current.replace(hour=0,minute=0,second=0,microsecond=0)
    begin=cycle-dt.timedelta(days=config['recent_lookback_days'])
    cutoff=cycle.strftime('%Y%m%d%H%M');since=begin.strftime('%Y%m%d%H%M');day=cycle.date().isoformat()
    terms='ti:"computer use" OR ti:"computer-use" OR ti:"GUI agent" OR ti:"GUI agents" OR ti:"GUI grounding" OR ti:"web agent" OR ti:"browser agent"'
    query=f'({terms}) AND submittedDate:[{since} TO {cutoff}]'
    previous=read_json(DATA/'recent-paper-search.json',{})
    if reusable_recent_snapshot(previous,query,since,cutoff,day):
        return {'name':'recent_arxiv','ok':True,'complete':True,'new_count':0,'warnings':[],
                'cache_used':True,'source_retrieved_at':max(page['retrieved_at'] for page in previous['pages'])}
    records={r['id']:r for r in read_jsonl(DATA/'papers.jsonl')}
    candidates={r['id']:r for r in read_jsonl(DATA/'candidates.jsonl')}
    proposals={r['id']:r for r in read_jsonl(DATA/'metadata-updates.jsonl')}
    start=0;total=None;seen=set();new_ids=[];pages=[];warnings=[]
    ceiling=config['recent_max_results']
    while start<ceiling and (total is None or start<total):
        batch_size=min(200,ceiling-start)
        url='https://export.arxiv.org/api/query?'+urllib.parse.urlencode({'search_query':query,'start':start,'max_results':batch_size,'sortBy':'submittedDate','sortOrder':'descending'})
        info,body=fetch(url,refresh=True)
        if not info['ok']:
            pages.append(info);warnings.append(info.get('error','arXiv fetch failed'));break
        try:
            entries,total=parse_search_feed(body,total,batch_size)
        except (ET.ParseError,ValueError) as error:
            pages.append({**info,'ok':False,'error':str(error)});warnings.append(str(error));break
        pages.append({**info,'start':start,'returned':len(entries)})
        for entry in entries:
            try:
                row=arxiv_entry(entry,{k:info[k] for k in ('url','retrieved_at','sha256')})
                if not row:raise ValueError('unrecognized article identity')
                submitted=row['details']['arxiv_snapshot']['submitted_at']
                if not submitted:raise ValueError('missing submission timestamp')
                submitted=dt.datetime.fromisoformat(submitted.replace('Z','+00:00'))
            except (ValueError,TypeError,KeyError) as error:
                warnings.append('Ignored an invalid arXiv entry: '+str(error));continue
            if not begin<=submitted<=cycle:
                warnings.append('Ignored an out-of-window search result: '+row['id']);continue
            seen.add(row['id'])
            if consider_paper(row,records,proposals,info['retrieved_at']):new_ids.append(row['id'])
            c=candidates.setdefault(row['id'],{'id':row['id'],'url':row['url'],'provider':'arxiv','first_discovered_at':info['retrieved_at'],'discovered_in':[]})
            c['discovered_in']=[o for o in c['discovered_in'] if o['source_id']!='arxiv-recent-scan']
            c['discovered_in'].append({'source_id':'arxiv-recent-scan','line':None,'section':'Recent title search; relevance awaits review','snapshot_sha256':info['sha256']})
        start+=len(entries)
        if not entries:break
        if start<min(total,ceiling):time.sleep(3.1)
    complete=total is not None and len(seen)==total and bool(pages) and all(p.get('ok') for p in pages) and not warnings
    if total is not None and not complete:warnings.append(f'Recent search delivered {len(seen)} unique in-window records out of {total} reported; completeness is not claimed.')
    write_jsonl(DATA/'papers.jsonl',records.values());write_jsonl(DATA/'candidates.jsonl',candidates.values());write_jsonl(DATA/'metadata-updates.jsonl',proposals.values())
    report={'schema_version':2,'scan_day_utc':day,'query':query,'since_utc':since,'cutoff_utc':cutoff,
        'polling_policy':'Complete arXiv snapshots are reused within one UTC publication day; failed or incomplete searches are retried by a later scheduled scan.',
        'reported_total':total,'unique_returned':len(seen),'complete':complete,'pages':pages,'new_ids':new_ids,'warnings':warnings}
    write_json(DATA/'recent-paper-search.json',report)
    return {'name':'recent_arxiv','ok':any(p.get('ok') for p in pages),'complete':complete,'new_count':len(new_ids),'warnings':warnings,'cache_used':False,
            'rate_limited':any(p.get('status')==429 for p in pages)}


def summarize_phases(phases,source_runs,hf_runs,harvest_failures):
    problems=[]
    for phase in phases:
        if not phase.get('ok'):problems.append(phase['name']+': '+phase.get('error','failed'))
        problems.extend(phase.get('warnings',[]))
    failed_sources=[r['id'] for r in source_runs if not r.get('ok')]
    successful_query=lambda r:r.get('state')=='success' and r.get('evidence',{}).get('ok') and not r.get('parse_error')
    failed_queries=[r['id'] for r in hf_runs if not successful_query(r)]
    if failed_sources:problems.append('Discovery sources failed: '+', '.join(failed_sources))
    if failed_queries:problems.append('Paper searches failed: '+', '.join(failed_queries))
    if harvest_failures:problems.append(f'{len(harvest_failures)} arXiv identifiers remain unresolved in the retained harvest ledger.')
    phase_ok={p['name']:p.get('ok',False) for p in phases}
    usable=(phase_ok.get('discovery') and any(r.get('ok') and not r.get('parse_error') for r in source_runs)) or (phase_ok.get('huggingface') and any(successful_query(r) for r in hf_runs)) or phase_ok.get('recent_arxiv')
    fatal=not usable or any(p['name'] in {'build','validate'} and not p.get('ok') for p in phases)
    return ('failed' if fatal else 'partial' if problems else 'success'),problems


def workflow_message(text):
    """Escape text for a GitHub workflow command, which ends at the first newline."""
    return text.replace('%','%25').replace('\r','%0D').replace('\n','%0A')


def run_scan():
    config=read_json(DATA/'scan-config.json')
    if not 1<=config['recent_lookback_days']<=31 or not 1<=config['recent_max_results']<=2000:raise ValueError('Unbounded recent-search configuration')
    cooldowns=read_json(DATA/'scan-status.json',{}).get('network_budget',{}).get('provider_cooldowns',{})
    budget=RoundBudget.create(ROOT/'.cache/scan-rounds',
        max_requests=config.get('network_max_requests',DEFAULT_REQUESTS),
        max_seconds=config.get('network_max_seconds',DEFAULT_SECONDS),cooldowns=cooldowns)
    with budget.activate():
        return run_round(config,budget)


def run_round(config,budget):
    started=now();before=counts();old_papers={r['id'] for r in read_jsonl(DATA/'papers.jsonl')};old_candidates={r['id'] for r in read_jsonl(DATA/'candidates.jsonl')}
    repo=os.getenv('GITHUB_REPOSITORY','');run_id=os.getenv('GITHUB_RUN_ID','')
    run_url=f'https://github.com/{repo}/actions/runs/{run_id}' if repo and run_id else None
    # Replaced when the scan finishes; a run killed before then leaves this receipt instead of the previous run's.
    write_json(DATA/'scan-status.json',{'id':'scan:'+started,'schema_version':1,'status':'running','started_at':started,'finished_at':None,'run_url':run_url,
        'network_budget':budget.summary()})
    phases=[stage('discovery',['scripts/catalog.py','discover','--refresh'])]
    source_runs=read_json(DATA/'discovery-runs.json',[])
    hf_limited=phases[0]['ok'] and any(r.get('status')==429 and r.get('provider_host')=='huggingface.co'
        and r.get('run_started_at','')>=phases[0].get('started_at',started) for r in source_runs)
    if hf_limited:
        phases.append({'name':'huggingface','started_at':now(),'ok':False,'deferred':True,
            'request_attempted':False,'error':'Deferred after the publisher-source phase returned Hugging Face HTTP 429.'})
    else:
        phases.append(stage('huggingface',['scripts/discover_papers.py','--refresh']))
    try:phases.append(recent_phase())
    except Exception as error:phases.append({'name':'recent_arxiv','ok':False,'error':str(error)})
    if phases[-1].get('rate_limited'):
        phases.append({'name':'harvest','ok':False,'deferred':True,
            'error':'Deferred after the recent arXiv query returned HTTP 429; discovered identifiers remain queued.'})
    else:
        phases.append(stage('harvest',['scripts/catalog.py','harvest','--limit',str(config['harvest_limit'])]))
    phases.append(stage('build',['scripts/catalog.py','build']))
    phases.append(stage('validate',['scripts/catalog.py','validate']))
    source_runs=read_json(DATA/'discovery-runs.json',[])
    hf_report=read_json(DATA/'paper-discovery-status.json',{})
    hf_current=(hf_report.get('schema_version')==1 and hf_report.get('finished_at')
                and hf_report.get('started_at','')>=phases[1].get('started_at',started))
    hf_runs=hf_report.get('queries',[]) if hf_current else []
    if phases[1]['ok'] and not hf_current:
        phases[1].update(ok=False,error='No complete receipt for the current paper-discovery run; previous successes are not reused.')
    status,problems=summarize_phases(phases,source_runs,hf_runs,read_jsonl(DATA/'harvest-failures.jsonl'))
    papers=read_jsonl(DATA/'papers.jsonl');candidates=read_jsonl(DATA/'candidates.jsonl');finished=now()
    new_papers=[{'id':r['id'],'name':r['name'],'url':r['url'],'review_status':'metadata_verified'} for r in papers if r['id'] not in old_papers]
    report={'id':'scan:'+started,'schema_version':1,'status':status,'started_at':started,'finished_at':finished,'run_url':run_url,
        'schedule':{'timezone':config['timezone'],'times_local':config['scan_times_local']},'before':before,'after':counts(),
        'new_papers':new_papers,'new_candidate_count':sum(r['id'] not in old_candidates for r in candidates),
        'pending_metadata_changes':len(read_jsonl(DATA/'metadata-updates.jsonl')),'source_fetches':{'successful':sum(bool(r.get('ok')) for r in source_runs) if phases[0]['ok'] else 0,'total':len(source_runs)},
        'paper_searches':{'successful':sum(r.get('state')=='success' for r in hf_runs),'total':hf_report.get('configured_query_count',0) if hf_current else 0,
            'unique_identifiers':hf_report.get('unique_identifiers',0) if hf_current else 0,'new_candidates':hf_report.get('new_candidate_count',0) if hf_current else 0},
        'phases':phases,'warnings':problems,'policy':config['policy'],'network_budget':budget.summary()}
    write_json(DATA/'scan-status.json',report)
    history=read_jsonl(DATA/'scan-history.jsonl');history.append({k:v for k,v in report.items() if k!='phases'})
    write_jsonl(DATA/'scan-history.jsonl',history[-config['history_limit']:])
    summary=f'Scan {status}: {len(new_papers)} new paper metadata records; {report["new_candidate_count"]} new candidates; {report["after"]["curated_papers"]} reviewed papers.'
    streak=next((i for i,r in enumerate(reversed(history)) if r.get('status')!='partial'),len(history))
    repeated=streak>=config.get('max_consecutive_partial',float('inf'))
    notes=problems+([f'The last {streak} scans were all partial; the recorded gaps are not clearing on their own.'] if repeated else [])
    print(summary);print(''.join('- '+n+'\n' for n in notes),end='')
    summary_file=os.getenv('GITHUB_STEP_SUMMARY')
    if summary_file:
        # In Actions, annotations put each gap on the run page; a partial scan still exits 0 so its verified data is committed.
        for problem in problems:print(f'::{"error" if status=="failed" else "warning"} title=Scan {status}::'+workflow_message(problem))
        if repeated:print('::error title=Repeated partial scans::'+notes[-1])
        with open(summary_file,'a',encoding='utf-8') as f:
            f.write('## AwesomeCUA scan\n\n'+summary+'\n\n'+f'Scheduled at {", ".join(config["scan_times_local"])} {config["timezone"]}.\n\n')
            f.write('\n'.join('- '+n for n in notes)+'\n')
    # scan.yml fails a separate job on this output, after the scan job has committed this run's data.
    if repeated and os.getenv('GITHUB_OUTPUT'):
        with open(os.getenv('GITHUB_OUTPUT'),'a',encoding='utf-8') as f:f.write('repeated_partial=true\n')
    return 1 if status=='failed' else 0


def check_freshness(current=None):
    """Exit status 1 unless a completed scan finished within max_scan_age_hours; GitHub can delay or drop the scheduled runs."""
    limit=read_json(DATA/'scan-config.json')['max_scan_age_hours']
    parse=lambda value:dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    # A failed scan commits nothing, and a running receipt has no finish time; only success and partial scans refreshed the catalog.
    finished=[r['finished_at'] for r in [read_json(DATA/'scan-status.json',{}),*read_jsonl(DATA/'scan-history.jsonl')] if r.get('status') in {'success','partial'} and r.get('finished_at')]
    last=max(finished,key=parse,default=None)
    age=((current or dt.datetime.now(dt.timezone.utc))-parse(last)).total_seconds()/3600 if last else None
    if age is not None and age<=limit:
        print(f'The last completed scan finished at {last}, {age:.1f} h ago, within the {limit} h limit.');return 0
    message=(f'The last completed scan finished at {last}, {age:.1f} h ago' if last else 'No completed scan is recorded')+f'; scans are expected at least every {limit} h, so a scheduled scan was delayed or dropped.'
    print(('::error title=Stale scan::' if os.getenv('GITHUB_STEP_SUMMARY') else '')+message)
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.getenv('GITHUB_OUTPUT'),'a',encoding='utf-8') as f:f.write('stale=true\n')
    return 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-freshness',action='store_true',help='Only check that a completed scan finished within max_scan_age_hours of data/scan-config.json; writes no data.')
    parser.add_argument('--recent-only',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.recent_only:
        if args.check_freshness:parser.error('Recent phase and freshness check are mutually exclusive')
        budget=budget_from_environment()
        if budget is None:parser.error('Recent phase requires a network-round context')
        phase=recent_papers(read_json(DATA/'scan-config.json'))
        write_json(recent_result_path(budget),{'round_id':budget.run_id,'phase':phase})
        return 0
    return check_freshness() if args.check_freshness else run_scan()


if __name__=='__main__':
    raise SystemExit(main())
