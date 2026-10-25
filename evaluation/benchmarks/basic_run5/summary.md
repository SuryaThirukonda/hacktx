# D6 to D11: openai versus rules **simulated cases from the world models; agreement is with the rules, not with field outcomes.**
Engine requested openai, used openai (openai (gpt-6-luna), rules baseline on every call). | Task | Seeds | Agreement with rules | Acted | Fell back to rules | Rejected answers | Unsafe options removed | p50 ms |
|---|---|---|---|---|---|---|---|
| D6_obstacle | 6 | 0.5 | 0.0 | 0 | 0 | 5 | 301 |
| D7_lead_triage | 6 | 0.833 | 0.667 | 0 | 0 | 0 | 199 |
| D8_next_sector | 6 | 0.833 | 0.5 | 0 | 0 | 0 | 283 |
| D9_route | 6 | 1.0 | 0.667 | 0 | 0 | 0 | 152 |
| D10_dispatch | 6 | 1.0 | 1.0 | 1 | 0 | 0 | 148 |
| D11_opening | 6 | 1.0 | 0.167 | 0 | 0 | 6 | 166 | Model calls 35, tokens 13937.
