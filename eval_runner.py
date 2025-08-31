#!/usr/bin/env python3
import argparse, time, yaml, requests, json, statistics, os, math, re

def _normalize_endpoint(e):
    e = (e or "").strip()
    if not e:
        return "http://localhost/chat"
    e = e.rstrip("/")
    # If the caller passed a base URL, default to /chat
    if not e.endswith("/chat") and not e.endswith("/chat_json"):
        e = e + "/chat"
    return e

def _read_body(resp, is_sse):
    """Return assistant text from /chat (NDJSON) or /chat_json (SSE)."""
    parts = []
    # Always stream so iter_lines() works consistently
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if is_sse:
            # SSE lines are "data: {...}"
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            parts.append(obj.get("content", ""))
        else:
            # NDJSON lines are {"message":{"content":"..."}}
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message", {}).get("content", "")
            if msg:
                parts.append(msg)
    # Fallback to full body if nothing parsed (non-streaming servers)
    if not parts:
        try:
            return resp.text or ""
        except Exception:
            return ""
    return "".join(parts)

def _matches_expectations(text, case):
    t = text or ""
    ok = True
    # positive substring
    if 'expected_contains' in case and case['expected_contains']:
        ok = ok and (case['expected_contains'].lower() in t.lower())
    # positive regex
    if 'expected_regex' in case and case['expected_regex']:
        try:
            rx = re.compile(case['expected_regex'], re.I | re.S)
        except re.error:
            return False
        ok = ok and bool(rx.search(t))
    # negative substring(s)
    for s in (case.get('not_contains') or []):
        if s.lower() in t.lower():
            return False
    # negative regex(es)
    for rxs in (case.get('not_regex') or []):
        try:
            rxn = re.compile(rxs, re.I | re.S)
        except re.error:
            return False
        if rxn.search(t):
            return False
    return ok

def _expectation_summary(case):
    bits = []
    if case.get('expected_contains'):
        bits.append(f'contains "{case["expected_contains"]}"')
    if case.get('expected_regex'):
        bits.append(f'matches /{case["expected_regex"]}/i')
    for s in (case.get('not_contains') or []):
        bits.append(f'NOT contains "{s}"')
    for rx in (case.get('not_regex') or []):
        bits.append(f'NOT /{rx}/i')
    return " & ".join(bits) if bits else "(no expectation)"

def run_case(endpoint, case):
    endpoint = _normalize_endpoint(endpoint)
    is_sse = endpoint.endswith("/chat_json")
    t0 = time.time()
    resp = requests.post(
        endpoint,
        data={'message': case['message'], 'direction': case['direction']},
        timeout=60,
        stream=True
    )
    t1 = time.time()
    body = _read_body(resp, is_sse).strip()
    ok = (resp.status_code == 200) and _matches_expectations(body, case)
    return {
        'ok': ok,
        'status': resp.status_code,
        'latency_ms': int((t1 - t0) * 1000),
        'got': body[:400],
        'expectation': _expectation_summary(case),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--endpoint', default=os.environ.get('DOGGO_ENDPOINT', 'http://localhost/chat'))
    ap.add_argument('--min-pass', type=float, default=0.0, help='Fail (exit 1) if pass-rate is below this (0.0-1.0)')
    args = ap.parse_args()

    with open(args.cases) as f:
        data = yaml.safe_load(f)

    results = []
    for c in data['cases']:
        r = run_case(args.endpoint, c)
        results.append({**c, **r})

    pass_rate = 100.0 * sum(1 for r in results if r['ok']) / len(results)
    latencies = [r['latency_ms'] for r in results if r['status'] == 200]
    p50 = int(statistics.median(latencies)) if latencies else None
    if latencies:
        k = max(0, math.ceil(0.95 * len(latencies)) - 1)
        p95 = int(sorted(latencies)[k])
    else:
        p95 = None

    out_dir = os.path.dirname(args.out) or '.'
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, 'w') as out:
        out.write('# Weekly GenAI Evaluation Report\n\n')
        out.write(f'- Pass-rate: {pass_rate:.1f}%\n')
        out.write(f'- p50 latency: {p50} ms · p95 latency: {p95} ms\n\n')
        out.write('## Cases\n\n')
        for r in results:
            out.write(f"- [{'PASS' if r['ok'] else 'FAIL'}] {r['direction']} | \"{r['message']}\" → {r['expectation']} | {r['latency_ms']} ms (status {r['status']})\n")

    if args.min_pass > 0.0 and (pass_rate / 100.0) < args.min_pass:
        raise SystemExit(1)

if __name__ == '__main__':
    main()