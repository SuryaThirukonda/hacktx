# D6 to D11: openai versus rules **simulated cases from the world models; agreement is with the rules, not with field outcomes.**
Engine requested openai, used openai (openai (gpt-6-luna), rules baseline on every call). | Task | Seeds | Agreement with rules | Acted | Fell back to rules | Rejected answers | Unsafe options removed | p50 ms |
|---|---|---|---|---|---|---|---|
| D6_obstacle | 6 | 0.5 | 0.0 | 0 | 0 | 5 | 154.95 |
| D7_lead_triage | 6 | 0.833 | 0.667 | 0 | 0 | 0 | 159.14999999999998 |
| D8_next_sector | 6 | 0.833 | 0.5 | 0 | 0 | 0 | 143.95 |
| D9_route | 6 | 1.0 | 0.667 | 0 | 0 | 0 | 148.85000000000002 |
| D10_dispatch | 6 | 1.0 | 1.0 | 1 | 1 | 0 | 144.2 |
| D11_opening | 6 | 0.167 | 0.5 | 0 | 0 | 0 | 138.3 | Model calls 35, tokens 14011.
