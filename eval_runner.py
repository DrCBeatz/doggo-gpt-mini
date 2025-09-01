#!/usr/bin/env python3
import argparse, time, yaml, requests, json, statistics, os, math, re

def _normalize_endpoint(e):
    e = (e or "").strip()
    if not e:
        return "http://localhost/chat"
    e = e.rstrip("/")
    if not e.endswith("/chat") and not e.endswith("/chat_json"):
        e = e + "/chat"
    return e

def _read_body(resp, is_sse):
    """Return assistant text from /chat (NDJSON) or /chat_json (SSE)."""
    parts = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if is_sse:
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            parts.append(obj.get("content", ""))
        else:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message", {}).get("content", "")
            if msg:
                parts.append(msg)
    if not parts:
        try:
            return resp.text or ""
        except Exception:
            return ""
    return "".join(parts)

def _matches_expectations(text, case):
    t = text or ""
    ok = True
    if 'expected_contains' in case and case['expected_contains']:
        ok = ok and (case['expected_contains'].lower() in t.lower())
    if 'expected_regex' in case and case['expected_regex']:
        try:
            rx = re.compile(case['expected_regex'], re.I | re.S)
        except re.error:
            return False
        ok = ok and bool(rx.search(t))
    for s in (case.get('not_contains') or []):
        if s.lower() in t.lower():
            return False
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

def _slug(s):
    return re.sub(r'[^A-Za-z0-9._-]+', '-', s)

def run_case(endpoint, case, model=None, timeout_s=60):
    endpoint = _normalize_endpoint(endpoint)
    is_sse = endpoint.endswith("/chat_json")
    payload = {
        'message': case['message'],
        'direction': case['direction'],
    }
    used_model = case.get('model') or model
    if used_model:
        payload['model'] = used_model

    t0 = time.time()
    resp = requests.post(
        endpoint,
        data=payload,
        timeout=(15, timeout_s),
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
        'model': used_model or '(default)',
    }

def _percentile(sorted_ms, p):
    if not sorted_ms:
        return None
    k = max(0, math.ceil(p * len(sorted_ms)) - 1)
    return int(sorted_ms[k])

def run_suite(cases, endpoint, model=None, out_path=None, min_pass=0.0, max_p95=None, timeout_s=60):
    results = []
    for c in cases:
        r = run_case(endpoint, c, model=model, timeout_s=timeout_s)
        results.append({**c, **r})

    pass_rate = 100.0 * sum(1 for r in results if r['ok']) / len(results)
    latencies = sorted([r['latency_ms'] for r in results if r['status'] == 200])
    p50 = int(statistics.median(latencies)) if latencies else None
    p95 = _percentile(latencies, 0.95)

    out_dir = os.path.dirname(out_path) or '.'
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, 'w') as out:
        out.write('# Weekly GenAI Evaluation Report\n\n')
        out.write(f'- Model: {model or "(default)"}\n')
        out.write(f'- Pass-rate: {pass_rate:.1f}%\n')
        out.write(f'- p50 latency: {p50} ms · p95 latency: {p95} ms\n\n')
        out.write('## Cases\n\n')
        for r in results:
            out.write(f"- [{'PASS' if r['ok'] else 'FAIL'}] {r['direction']} | \"{r['message']}\" → {r['expectation']} | {r['latency_ms']} ms (status {r['status']}) | model {r['model']}\n")

    failed = False
    if min_pass > 0.0 and (pass_rate / 100.0) < min_pass:
        failed = True
    if max_p95 is not None and p95 is not None and p95 > max_p95:
        failed = True
    return failed

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', required=True)
    ap.add_argument('--out', required=True, help='Output path (if --models is used, this becomes the base name)')
    ap.add_argument('--endpoint', default=os.environ.get('DOGGO_ENDPOINT', 'http://localhost/chat'))
    ap.add_argument('--model', help='Single model to request (overridable per-case)', default=None)
    ap.add_argument('--models', help='Comma-separated list of models to evaluate in a matrix', default=None)
    ap.add_argument('--min-pass', type=float, default=0.0, help='Fail (exit 1) if pass-rate is below this (0.0-1.0)')
    ap.add_argument('--max-p95', type=int, default=None, help='Fail if p95 latency exceeds this (ms)')
    args = ap.parse_args()
    ap.add_argument('--client-timeout', type=int,
                default=int(os.environ.get('DOGGO_CLIENT_TIMEOUT', '180')),
                help='Read timeout per request (seconds)')

    with open(args.cases) as f:
        data = yaml.safe_load(f)

    cases = data['cases']
    failures = 0

    # Multi-model matrix
    if args.models:
        models = [m.strip() for m in args.models.split(',') if m.strip()]
        base_dir = os.path.dirname(args.out) or '.'
        base_name, ext = os.path.splitext(os.path.basename(args.out))
        ext = ext or '.md'
        for m in models:
            out_path = os.path.join(base_dir, f"{base_name}-{_slug(m)}{ext}")
            failed = run_suite(cases, endpoint=args.endpoint, model=m,
                               out_path=out_path, min_pass=args.min_pass,
                               max_p95=args.max_p95, timeout_s=args.client_timeout)
            failures += int(failed)
    else:
        # Single model (or default)
        out_path = args.out
        failed = run_suite(cases, endpoint=args.endpoint, model=args.model,
                           out_path=out_path, min_pass=args.min_pass,
                           max_p95=args.max_p95, timeout_s=args.client_timeout)

    if failures:
        raise SystemExit(1)

if __name__ == '__main__':
    main()