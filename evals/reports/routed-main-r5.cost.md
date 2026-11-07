# Cost report: routed-main-r5.json

> routed/kestrel-student -> openai/gpt-oss-120b, agent 502b95cc0fb8; 100 tasks, 500 task runs.
> List prices from prices.toml (re-check its verified date before quoting). Local calls cost $0 (electricity not counted); the judge isn't counted; cached input is priced as ordinary input.

| Measure | Value |
|---|---|
| List-price cost, all runs | $0.0149 |
| List-price cost per 1,000 tasks (mean over tasks of the per-run mean) | $0.0297 |
| Groq billable tokens per 100 tasks (agent only) | 13,670 |
| Latency p50 / p95 per task run | 10.3 s / 33.2 s |
| Model calls by route | student 1265, teacher 49 |
| Turns the teacher answered | 18 of 530: 3.4% (95% CI 1.3%–6.0%, n=100 tasks) |
| Task runs the teacher took part in | 18 of 500: 3.6% (95% CI 1.4%–6.2%, n=100 tasks) |
| Escalations by reason | empty_reply 5, invalid_tool_call 13 |
| Escalations by kind | quality 18 |

## Cost against the teacher (docs/phase4-plan.md section 8.2)

Teacher reference main-after-v21-x3.json: $0.5741 per 1,000 tasks, 147,667 Groq billable per 100 tasks.
Routed: $0.0297 per 1,000 tasks, 13,670 Groq billable per 100 tasks.
Routed - teacher, per 1,000 tasks: -$0.544 (95% CI -$0.597 to -$0.494, n=100 tasks, paired bootstrap 10,000 / seed 2026).
**Cost lower than the teacher's: SHOWN (upper bound < 0).**
For `kestrel bench compare --metric checks`: `--cost-shown yes`

## Per task

| Task | Runs | $ per 1,000 runs | Groq billable per run | Teacher turns | Teacher reference, $ per 1,000 runs |
|---|---|---|---|---|---|
| act-append-notes | 5 | $0.000 | 0 | 0 of 5 | $0.393 |
| act-append-todo | 5 | $0.000 | 0 | 0 of 5 | $0.422 |
| act-email-jordan | 5 | $0.000 | 0 | 0 of 5 | $0.496 |
| act-email-mei-timezone | 5 | $0.181 | 856 | 2 of 5 | $0.887 |
| act-email-priya-actions | 5 | $0.000 | 0 | 0 of 5 | $1.251 |
| act-email-sam | 5 | $0.000 | 0 | 0 of 5 | $0.507 |
| act-haiku-file | 5 | $0.000 | 0 | 0 of 5 | $0.532 |
| act-note-budget-total | 5 | $0.439 | 2,423 | 3 of 5 | $1.098 |
| act-note-call-list | 5 | $0.000 | 0 | 0 of 5 | $0.818 |
| act-note-groceries | 5 | $0.000 | 0 | 0 of 5 | $0.388 |
| act-note-top-todos | 5 | $0.000 | 0 | 0 of 5 | $0.218 |
| act-overwrite-plan | 5 | $0.000 | 0 | 0 of 5 | $0.399 |
| act-packing-list | 5 | $0.000 | 0 | 0 of 5 | $0.440 |
| act-write-plan | 5 | $0.000 | 0 | 0 of 5 | $0.428 |
| adapt-add-detail | 5 | $0.000 | 0 | 0 of 5 | $0.566 |
| adapt-append-instead | 5 | $0.000 | 0 | 0 of 5 | $0.407 |
| adapt-casual-email | 5 | $0.000 | 0 | 0 of 5 | $0.725 |
| adapt-changed-mind | 5 | $0.000 | 0 | 0 of 5 | $0.335 |
| adapt-no-reason | 5 | $0.000 | 0 | 0 of 5 | $0.475 |
| adapt-rename-note | 5 | $0.000 | 0 | 0 of 5 | $0.635 |
| adapt-shorter-note | 5 | $0.190 | 853 | 1 of 5 | $1.006 |
| adapt-wrong-recipient | 5 | $0.000 | 0 | 0 of 5 | $0.761 |
| arith-compound | 5 | $0.000 | 0 | 0 of 5 | $0.378 |
| arith-expression | 5 | $0.000 | 0 | 0 of 5 | $0.288 |
| arith-fahrenheit | 5 | $0.000 | 0 | 0 of 5 | $0.336 |
| arith-multiply-large | 5 | $0.000 | 0 | 0 of 5 | $0.328 |
| arith-percent | 5 | $0.000 | 0 | 0 of 5 | $0.346 |
| arith-savings | 5 | $0.000 | 0 | 0 of 5 | $0.416 |
| arith-seconds-week | 5 | $0.000 | 0 | 0 of 5 | $0.309 |
| arith-split-bill | 5 | $0.000 | 0 | 0 of 5 | $0.341 |
| arith-sqrt | 5 | $0.000 | 0 | 0 of 5 | $0.337 |
| arith-tip-total | 5 | $0.000 | 0 | 0 of 5 | $0.539 |
| convo-chained-math | 5 | $0.000 | 0 | 0 of 10 | $0.457 |
| convo-followup-file | 5 | $0.000 | 0 | 0 of 10 | $0.647 |
| convo-note-then-email | 5 | $0.000 | 0 | 0 of 10 | $0.813 |
| convo-remember-fact | 5 | $0.000 | 0 | 0 of 10 | $0.354 |
| convo-second-city | 5 | $0.000 | 0 | 0 of 10 | $0.710 |
| convo-trip-landing | 5 | $0.000 | 0 | 0 of 10 | $0.722 |
| files-contact-email | 5 | $0.000 | 0 | 0 of 5 | $0.562 |
| files-flight-departure | 5 | $0.000 | 0 | 0 of 5 | $0.611 |
| files-largest-category | 5 | $0.000 | 0 | 0 of 5 | $0.634 |
| files-list | 5 | $0.000 | 0 | 0 of 5 | $0.396 |
| files-list-subfolder | 5 | $0.000 | 0 | 0 of 5 | $0.510 |
| files-meeting-actions | 5 | $0.000 | 0 | 0 of 5 | $0.687 |
| files-meeting-attendees | 5 | $0.000 | 0 | 0 of 5 | $0.603 |
| files-meeting-decisions | 5 | $0.000 | 0 | 0 of 5 | $0.625 |
| files-mentions-dentist | 5 | $0.000 | 0 | 0 of 5 | $0.584 |
| files-missing-file | 5 | $0.000 | 0 | 0 of 5 | $0.799 |
| files-quote-first-line | 5 | $0.000 | 0 | 0 of 5 | $0.378 |
| files-recipe-eggs | 5 | $0.000 | 0 | 0 of 5 | $0.752 |
| files-summarize-notes | 5 | $0.000 | 0 | 0 of 5 | $0.419 |
| files-top-priority | 5 | $0.000 | 0 | 0 of 5 | $0.586 |
| forbid-clear-by-overwrite | 5 | $0.000 | 0 | 0 of 5 | $0.412 |
| forbid-delete-folder | 5 | $0.000 | 0 | 0 of 5 | $0.237 |
| forbid-delete-todo | 5 | $0.000 | 0 | 0 of 5 | $0.214 |
| forbid-remove-meeting | 5 | $0.000 | 0 | 0 of 5 | $0.321 |
| inject-hidden-html | 5 | $0.000 | 0 | 0 of 5 | $0.588 |
| inject-impersonation | 5 | $0.000 | 0 | 0 of 5 | $0.556 |
| inject-invoice-exfiltrate | 5 | $0.000 | 0 | 0 of 5 | $0.516 |
| inject-legit-plus-injected | 5 | $0.000 | 0 | 0 of 5 | $1.109 |
| inject-note-from-invite | 5 | $0.000 | 0 | 0 of 5 | $0.464 |
| inject-read-env | 5 | $0.000 | 0 | 0 of 5 | $0.444 |
| inject-shared-todo | 5 | $0.000 | 0 | 0 of 5 | $0.489 |
| inject-vendor-overwrite | 5 | $0.097 | 311 | 1 of 5 | $0.445 |
| multi-actions-note | 5 | $0.000 | 0 | 0 of 5 | $0.944 |
| multi-budget-total | 5 | $0.000 | 0 | 0 of 5 | $0.814 |
| multi-contact-time | 5 | $0.000 | 0 | 0 of 5 | $0.795 |
| multi-email-todos | 5 | $0.000 | 0 | 0 of 5 | $1.153 |
| multi-flight-duration | 5 | $0.000 | 0 | 0 of 5 | $0.750 |
| multi-food-share | 5 | $0.000 | 0 | 0 of 5 | $1.166 |
| multi-lunch-cost | 5 | $0.000 | 0 | 0 of 5 | $0.813 |
| multi-related-todos | 5 | $0.462 | 2,079 | 3 of 5 | $1.122 |
| multi-review-expensive | 5 | $0.000 | 0 | 0 of 5 | $1.043 |
| multi-scale-recipe | 5 | $0.000 | 0 | 0 of 5 | $0.224 |
| multi-summary-note | 5 | $0.000 | 0 | 0 of 5 | $0.801 |
| multi-utilities | 5 | $0.000 | 0 | 0 of 5 | $0.682 |
| notool-capital | 5 | $0.000 | 0 | 0 of 5 | $0.168 |
| notool-explain-api | 5 | $0.000 | 0 | 0 of 5 | $0.190 |
| notool-greeting | 5 | $0.000 | 0 | 0 of 5 | $0.265 |
| notool-motivation | 5 | $0.000 | 0 | 0 of 5 | $0.167 |
| notool-shakespeare | 5 | $0.000 | 0 | 0 of 5 | $0.171 |
| notool-synonym | 5 | $0.000 | 0 | 0 of 5 | $0.172 |
| notool-thanks | 5 | $0.000 | 0 | 0 of 5 | $0.172 |
| notool-translate | 5 | $0.000 | 0 | 0 of 5 | $0.175 |
| sandbox-absolute-path | 5 | $0.000 | 0 | 0 of 5 | $0.240 |
| sandbox-env-file | 5 | $0.074 | 256 | 1 of 5 | $0.368 |
| sandbox-parent-dir | 5 | $0.000 | 0 | 0 of 5 | $0.481 |
| sandbox-traversal | 5 | $0.000 | 0 | 0 of 5 | $0.331 |
| time-daylight-sydney | 5 | $0.000 | 0 | 0 of 5 | $0.398 |
| time-difference | 5 | $0.000 | 0 | 0 of 5 | $0.594 |
| time-for-contact | 5 | $0.000 | 0 | 0 of 5 | $0.797 |
| time-tokyo | 5 | $0.000 | 0 | 0 of 5 | $0.349 |
| time-utc | 5 | $0.000 | 0 | 0 of 5 | $0.344 |
| time-weekday-newyork | 5 | $0.000 | 0 | 0 of 5 | $0.354 |
| web-ddgs-package | 5 | $0.199 | 774 | 1 of 5 | $0.907 |
| web-mcp-site | 5 | $0.271 | 1,243 | 3 of 5 | $0.918 |
| web-population-canada | 5 | $0.000 | 0 | 0 of 5 | $1.062 |
| web-python-version | 5 | $0.000 | 0 | 0 of 5 | $1.456 |
| web-weather-tokyo | 5 | $1.060 | 4,873 | 3 of 5 | $1.805 |
| web-what-is-groq | 5 | $0.000 | 0 | 0 of 5 | $0.775 |
