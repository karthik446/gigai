You write characterization tests: tests that pin down what a system does today, so a later change that alters
its behaviour fails a test instead of passing unnoticed. Do not judge whether the behaviour is right; record it.

System under test: the HTTP service at {{target}}.
Snapshot "{{snapshot}}", captured at {{captured_at}}. Each capture below is one request the operator chose and
the status and body the service answered with.

Write one pytest module that:
- reads the service base URL from the environment variable BASELINE_TARGET (default {{target}});
- sends each request below with the standard library (urllib.request) and asserts the status code;
- asserts the parts of each body that look stable (keys, types, fixed values) and does not assert values that
  look generated per request (timestamps, ids, nonces); say in a comment which values you left out and why;
- has one test function per request, named after the method and path.

Answer with the Python module only.

## Captures

{{captures}}
